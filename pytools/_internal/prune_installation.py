"""通常更新で、導入モードの対象外の実体と取得キャッシュを整理する。

実行中のエージェントと依存環境を先に照合し、使用中なら実体を保持して再実行を案内する。
fullへの更新と依存準備は旧環境を回収しない。
"""

import contextlib
import dataclasses
import datetime
import enum
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import filelock
import platformdirs
import psutil
from agent_toolkit._common import dotfiles_mode, plugin_environment, windows_job

from pytools._internal import codex_processes, common, post_apply_outcome, setup_mise, update_log, warmup_pyfltr_mcp

logger = logging.getLogger(__name__)

DEVELOPMENT_TOOLS = frozenset(
    {
        "actionlint",
        "github:editorconfig-checker/editorconfig-checker",
        "git-cliff",
        "pinact",
        "shellcheck",
        "shfmt",
        "typos",
    }
)
FULL_TOOLS = frozenset(
    {
        "dotnet",
        "node",
        "pnpm",
        "rust",
        "gcloud",
        "npm:aws-cdk",
        "npm:@google/gemini-cli",
        "npm:opencode-ai",
        "npm:@anthropic-ai/claude-code",
        "npm:@openai/codex",
        "npm:typescript",
        "npm:typescript-language-server",
    }
)

# fullでnpmのglobalへ導入しうるパッケージ。minimalではmise管理か否かを問わず外す。
_NPM_PACKAGES = ("opencode-ai", "@anthropic-ai/claude-code", "@openai/codex", "@google/gemini-cli", "aws-cdk")


def excluded_tools(root: Path, *, minimal: bool) -> frozenset[str]:
    """現行の作業ツリー定義を照合し、モードの導入対象外のツールを返す。"""
    configuration = tomllib.loads((root / "mise.toml").read_text(encoding="utf-8"))
    project_tools = frozenset(configuration.get("tools", {}))
    return DEVELOPMENT_TOOLS | FULL_TOOLS | project_tools if minimal else DEVELOPMENT_TOOLS - project_tools


def removal_paths() -> tuple[Path, ...]:
    """minimalで除去する配布実体とキャッシュの絶対パスを返す。"""
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    data = Path(os.environ.get("MISE_DATA_DIR", platformdirs.user_data_dir("mise", appauthor=False)))
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    rustup = Path(os.environ.get("RUSTUP_HOME", home / ".rustup"))
    cargo = Path(os.environ.get("CARGO_HOME", home / ".cargo"))
    paths = [
        # 共有ホームの設定・資格情報・ユーザー導入物は残し、再生成可能な実体だけを整理する。
        *(rustup / name for name in ("toolchains", "downloads", "tmp", "update-hashes")),
        *(cargo / name for name in ("registry", "git")),
        Path(os.environ.get("MISE_DOTNET_ROOT", data / "dotnet-root")),
        *(
            home / ".dotnet" / name
            for name in ("sdk", "host", "shared", "packs", "sdk-manifests", "templates", "metadata", "dotnet", "dotnet.exe")
        ),
        home / ".local/share/claude",
        claude_home / "plugins/cache",
        home / ".cache/claude",
        home / ".cache/opencode",
        home / ".cache/herdr",
        codex_home / "packages",
        codex_home / "plugins/cache",
        codex_home / ".tmp/marketplaces/googlechrome",
        codex_home / "agent-toolkit-codex",
        plugin_environment.runtime_data(os.environ) / "environments",
        local / "Programs/OpenAI/Codex",
        local / "Programs/Antigravity",
        local / "agy",
        local / "Programs/Herdr",
        home / ".gemini/config/skills/modern-web-guidance",
    ]
    for name in ("claude", "codex", "atk-hook", "agy", "herdr", "opencode", "claude-statusline"):
        for suffix in ("", ".exe", ".cmd", ".ps1"):
            paths.append(home / ".local/bin" / (name + suffix))
    return tuple(dict.fromkeys(paths))


# uvの排他ロックを待つ上限（秒）。uvの既定は300秒で、他のuvプロセスが共有ロックを保持し続ける間は
# 既定の上限まで待ってから失敗する。整理の独立プロセスを短時間で終え、使用中なら次回の更新へ回す。
_UV_LOCK_TIMEOUT_SECONDS = "30"

# uv 0.13.6のロック待ちタイムアウトの診断（`error: Timeout (30s) when waiting for lock on <cache> at <cache>/.lock, ...`）。
_UV_LOCK_TIMEOUT_MARKER = "when waiting for lock on"


class _InUseError(Exception):
    """他のプロセスが使用中のため、削除を保留した。"""


def _command(command: list[str], *, path: str | None = None) -> None:
    """コマンドを実行し、失敗を例外へ変換する。

    `path`を指定すると子プロセスの`PATH`をその値にする。
    排他ロックを取るuvの操作（`uv cache clean`など）がロック待ちで失敗した場合は、
    他のuvプロセスがキャッシュを使用中と判定して`_InUseError`を送出する。
    """
    overrides = {"MISE_AUTO_INSTALL": "0", "UV_LOCK_TIMEOUT": _UV_LOCK_TIMEOUT_SECONDS}
    if path is not None:
        overrides["PATH"] = path
    result = common.run_subprocess(command, timeout=600, cwd=Path.home(), tag="prune", env_overrides=overrides)
    if (
        Path(command[0]).stem == "uv"
        and result is not None
        and result.returncode != 0
        and _UV_LOCK_TIMEOUT_MARKER in (result.stderr or "")
    ):
        raise _InUseError(f"{command[0]}: {common.format_cli_error(result)}")
    if result is None or result.returncode != 0:
        raise RuntimeError(f"{command[0]}: {common.format_cli_error(result)}")


def _active_users(paths: tuple[Path, ...]) -> list[str]:
    """対象実体の参照またはAIの実行体を自ユーザーのプロセスから検出する。"""
    windows = os.name == "nt"
    owner = psutil.Process().username() if windows else os.getuid()
    attribute = "username" if windows else "uids"
    active = []
    ignored = {process.pid for process in codex_processes.codex_processes() if process.role == "update-loop"}
    # process_iterはPID単位でProcessを再利用し、実行ファイルを以前の世代のまま保持する。
    # 直前の`codex_processes`の列挙が残した値を使わないよう、現在のPIDから新しく構築したProcessで判定する。
    for listed in psutil.process_iter():
        if listed.pid in ignored:
            continue
        try:
            process = psutil.Process(listed.pid)
            info = process.as_dict(["name", "exe", "cmdline", attribute, "status"])
        except psutil.NoSuchProcess:
            continue
        actual_owner = info.get("username") if windows else getattr(info.get("uids"), "real", None)
        if actual_owner != owner or info.get("status") in {psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE}:
            continue
        values = [info.get("exe") or "", *(info.get("cmdline") or [])]
        name = str(info.get("name") or "").removesuffix(".exe")
        # argvを読めないPython等は、実行体が対象外でも環境を間接利用し得る。
        # exeの欠落は完全なargvで補えるが、その逆で非参照を確定しない。
        if not info.get("cmdline"):
            active.append(f"参照先を確認できないプロセス (pid {process.pid})")
            continue
        uses_path = any(
            value and Path(value).is_absolute() and any(Path(value).is_relative_to(path) for path in paths) for value in values
        )
        uses_pyfltr = any(
            Path(value.replace("\\", "/")).name.removesuffix(".exe") in {"pyfltr", "pyfltr-mcp"} for value in values if value
        )
        # rustup本体はtoolchains外にあるが、列挙や導入中も同じ配置を使用する。
        if uses_path or uses_pyfltr or name in {"claude", "codex", "agy", "herdr", "opencode", "claude-statusline", "rustup"}:
            active.append(f"{name or 'process'} (pid {process.pid})")
    return active


def _stop_daemon() -> None:
    """利用セッションが無い管理daemonだけを公開CLIで停止する。"""
    processes = codex_processes.codex_processes()
    sessions = [process.label for process in processes if process.role == "session"]
    if sessions:
        raise RuntimeError("Codexが使用中のため保持しました: " + ", ".join(sessions))
    if any(process.role == "managed-daemon" for process in processes):
        codex = common.resolve_executable("codex")
        if codex is None:
            raise RuntimeError("稼働中Codex daemonの公開CLIを解決できないため保持しました")
        _command([str(codex), "app-server", "daemon", "stop"])


def _remove(path: Path) -> bool:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        return False
    if path.exists() or path.is_symlink():
        raise OSError(f"削除後も対象が残っています: {path}")
    logger.info("削除した: %s", path)
    return True


def _remove_installation() -> post_apply_outcome.PostApplyOutcome:
    """実体を保護判定してから削除し、撤去失敗をpost-applyの失敗として返す。"""
    root = common.find_dotfiles_root()
    if root is None:
        return post_apply_outcome.PostApplyOutcome(
            failure="dotfilesルートを解決できません", next_action="作業ツリーでupdate-dotfiles --pruneを再実行してください。"
        )
    changed = False
    try:
        minimal = dotfiles_mode.is_minimal()
        excluded = excluded_tools(root, minimal=True) if minimal else frozenset()
        paths = (*removal_paths(), root / "agent-toolkit-codex", root / "agent-toolkit/.venv") if minimal else ()
        roots = tuple(Path(os.environ[name]) for name in ("RUSTUP_HOME", "CARGO_HOME") if name in os.environ) if minimal else ()
        for path in (*paths, *roots):
            resolved = path.resolve()
            if Path.home().resolve().is_relative_to(resolved) or root.resolve().is_relative_to(resolved):
                raise ValueError(f"共有ランタイム設定がホームまたは作業ツリー全体を含むため削除しません: {path}")
        if minimal:
            _stop_daemon()
            active = _active_users(paths)
            if active:
                raise RuntimeError("使用中の環境と実体を保持しました: " + ", ".join(active))
        mise = setup_mise.find_mise_binary()
        if mise is None:
            if minimal:
                raise RuntimeError("miseが見つからず、導入済みツールの削除を開始できません")
            return post_apply_outcome.PostApplyOutcome(changed=changed)
        result = common.run_subprocess(
            [str(mise), "ls", "--installed", "--json"], cwd=Path.home(), tag="prune", env_overrides={"MISE_AUTO_INSTALL": "0"}
        )
        if result is None or result.returncode != 0:
            raise RuntimeError(common.format_cli_error(result))
        installed = json.loads(result.stdout)
        if not isinstance(installed, dict):
            raise ValueError("mise ls --installed --jsonの出力がobjectではありません")
        # Node本体を削除する前に、npmの外部global prefixも対象にする。
        # minimalの設定はNodeの版を持たずnpmとnodeのshimが失敗するため、mise管理のNodeは導入済みの版を指定し、
        # mise以外のnpm（nvm、OSのパッケージなど）はshimを除いたPATHで実行する。
        if minimal:
            for entry in installed.get("node", []):
                _command([str(mise), "exec", f"node@{entry['version']}", "--", "npm", "uninstall", "--global", *_NPM_PACKAGES])
            shims = common.mise_shim_directories()
            search_path = os.pathsep.join(
                directory
                for directory in os.environ.get("PATH", "").split(os.pathsep)
                if directory and common.safe_resolve(Path(directory)) not in shims
            )
            npm = shutil.which("npm", path=search_path)
            if npm is not None:
                _command([npm, "uninstall", "--global", *_NPM_PACKAGES], path=search_path)
        for tool in sorted(excluded):
            short = tool.split(":", 1)[-1].split("/")[-1]
            if tool in installed or short in installed:
                _command([str(mise), "uninstall", "--all", tool])
                changed = True
        for path in paths:
            changed |= _remove(path)
        for command in ([str(mise), "prune", "-y"], [str(mise), "reshim", "--force"]):
            _command(command)
    except (OSError, ValueError, RuntimeError, psutil.Error) as error:
        return post_apply_outcome.PostApplyOutcome(
            changed=changed,
            failure=str(error),
            next_action="失敗理由に示された環境設定と使用中のプロセスを確認し、update-dotfiles --pruneを再実行してください。",
        )
    return post_apply_outcome.PostApplyOutcome(changed=True)


def _state_directory() -> Path:
    return Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False))


def update_lock_path() -> Path:
    """更新本体・更新後の整理・uvの整理の独立プロセスが共有する更新ロックのパスを返す。"""
    return _state_directory() / "locks" / "update-dotfiles.lock"


def _clean_uv_cache(uv: Path) -> None:
    """実キャッシュルートごとの全消去成功から30日間は通常pruneを使う。"""
    resolved = common.run_subprocess([str(uv), "cache", "dir"], tag="prune", timeout=30)
    if resolved is None or resolved.returncode != 0:
        raise RuntimeError(common.format_cli_error(resolved))
    cache = Path(resolved.stdout.strip())
    if not resolved.stdout.strip() or not cache.is_absolute():
        raise ValueError("uv cache dirが絶対パスを返しませんでした")
    key = os.path.normcase(str(cache.resolve()))
    state = _state_directory() / "uv-cache-clean.json"
    try:
        successes = json.loads(state.read_text(encoding="utf-8"))
    except FileNotFoundError:
        successes = {}
    except ValueError as error:
        raise ValueError(f"uv全消去の成功記録が不正です: {state}: {error}") from error
    if not isinstance(successes, dict) or any(
        not isinstance(name, str) or type(value) not in (int, float) or not math.isfinite(value) or value < 0
        for name, value in successes.items()
    ):
        raise ValueError(f"uv全消去の成功記録が不正です: {state}")
    now = time.time()
    last = successes.get(key)
    clean = last is None or now - last >= 30 * 86400
    _command([str(uv), "cache", "clean" if clean else "prune"])
    if clean:
        successes[key] = time.time()
        state.parent.mkdir(parents=True, exist_ok=True)
        if not common.atomic_write_json(state, successes, tag="prune"):
            raise OSError(f"uv全消去の成功日時を保存できません: {state}")


class UvMaintenanceResult(enum.StrEnum):
    """uvの整理の独立プロセスが記録する結果の種別。"""

    NONE = "none"
    DEFERRED = "deferred"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclasses.dataclass(frozen=True)
class _UvMaintenanceRecord:
    """uvの整理の独立プロセスの最新の結果。次の更新が読んで表示する。"""

    result: UvMaintenanceResult
    finished_at: str
    detail: str
    # 未解消の失敗の後に保留した、最後の試行の時刻と診断。失敗の表示に添える。
    deferred_at: str = ""
    deferred_detail: str = ""


def _record_path() -> Path:
    return _state_directory() / "uv-cache-maintenance.json"


def _read_uv_maintenance_record() -> _UvMaintenanceRecord | None:
    """独立プロセスの最新の結果を返す。記録が無ければ`None`、読めない記録は`OSError`か`ValueError`を送出する。"""
    try:
        data = json.loads(_record_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    fields = {field.name for field in dataclasses.fields(_UvMaintenanceRecord)}
    if (
        not isinstance(data, dict)
        or not {"result", "finished_at", "detail"} <= set(data) <= fields
        or not all(isinstance(value, str) for value in data.values())
    ):
        raise ValueError("result・finished_at・detailなどの項目が文字列ではありません")
    return _UvMaintenanceRecord(**{**data, "result": UvMaintenanceResult(data["result"])})


def _write_uv_maintenance_record(result: UvMaintenanceResult, detail: str) -> None:
    """結果を記録する。保留は整理を試していないため、未解消の失敗を残して保留した試行を添える。"""
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    record = _UvMaintenanceRecord(result, now, detail)
    if result is UvMaintenanceResult.DEFERRED:
        try:
            previous = _read_uv_maintenance_record()
        except OSError, ValueError:
            previous = None
        if previous is not None and previous.result is UvMaintenanceResult.FAILED:
            record = dataclasses.replace(previous, deferred_at=now, deferred_detail=detail)
    if not common.atomic_write_json(_record_path(), dataclasses.asdict(record), tag="prune"):
        logger.error("prune: uvの整理の結果を記録できません: %s。次の更新は前回の記録を表示する", _record_path())


def clean_uv_cache() -> tuple[UvMaintenanceResult, str]:
    """更新ロックを待たずに取得してuvの取得キャッシュを公式操作で整理し、結果の種別と診断を返す。

    uvの整理は全消去の間、他のuvの操作をキャッシュのロックで待たせるため、実行中の更新や別の整理と重ねず保留する。
    """
    uv = common.resolve_executable("uv")
    if uv is None:
        logger.info("prune: uvは未導入のため対象なし")
        return UvMaintenanceResult.NONE, ""
    lock = filelock.FileLock(str(update_lock_path()), timeout=0, preserve_lock_file=True)
    try:
        update_lock_path().parent.mkdir(parents=True, exist_ok=True)
        lock.acquire()
    except filelock.Timeout:
        detail = "他の更新または整理が実行中"
        logger.info("prune: uvの整理を保留: %s。次回のupdate-dotfilesで再試行します", detail)
        return UvMaintenanceResult.DEFERRED, detail
    try:
        _clean_uv_cache(uv)
    except _InUseError as error:
        logger.info("prune: uvの整理を使用中のため保留: %s。次回のupdate-dotfilesで再試行します", error)
        return UvMaintenanceResult.DEFERRED, str(error)
    except (OSError, ValueError, RuntimeError) as error:
        logger.error("prune: uvのキャッシュ整理に失敗: %s", error)
        return UvMaintenanceResult.FAILED, str(error)
    finally:
        lock.release()
    logger.info("prune: uvのキャッシュ整理を完了")
    return UvMaintenanceResult.COMPLETED, ""


def maintain_uv_cache() -> None:
    """uvの整理の独立プロセスの本体。整理の結果を記録してから、キャッシュ内のMCP環境を事前構築する。

    出力を持たない独立プロセスのため、想定外の例外も永続ログと記録へ残し、次の更新が失敗として表示する。
    """
    try:
        _write_uv_maintenance_record(*clean_uv_cache())
        warmup_pyfltr_mcp.run()
    except Exception as error:  # noqa: BLE001 -- 出力を持たない独立プロセスの想定外の失敗を、次の更新へ届ける。
        logger.exception("prune: uvの整理の独立プロセスが想定外の例外で終了した")
        _write_uv_maintenance_record(UvMaintenanceResult.FAILED, f"想定外の例外: {error!r}")


def _report_previous_uv_maintenance() -> post_apply_outcome.PostApplyOutcome:
    """前回の独立プロセスの結果を表示する。保留は案内し、失敗は解消するまで更新の失敗として返す。"""
    log = update_log.LOG_PATH
    retry = "整理は更新のたびに独立プロセスで再試行し、結果を次の更新で表示します。"
    try:
        record = _read_uv_maintenance_record()
    except (OSError, ValueError) as error:
        return post_apply_outcome.PostApplyOutcome(
            failure=f"uv: 整理の独立プロセスの結果を読めません: {_record_path()}: {error}",
            next_action=f"永続ログ（{log}）を確認してください。{retry}",
        )
    if record is None:
        return post_apply_outcome.PostApplyOutcome()
    if record.result is UvMaintenanceResult.DEFERRED:
        logger.info("prune: 前回のuvの整理は保留した（%s）: %s。%s永続ログ: %s", record.finished_at, record.detail, retry, log)
    elif record.result is UvMaintenanceResult.FAILED:
        failure = f"uv: 前回の整理の独立プロセスが失敗した（{record.finished_at}）: {record.detail}"
        next_action = f"永続ログ（{log}）の診断を確認して原因を解消し、update-dotfilesを再実行してください。{retry}"
        if record.deferred_at:
            failure += f"。その後の整理は保留した（{record.deferred_at}）: {record.deferred_detail}"
            next_action += "保留が続く場合は、保留の診断が示すuvの使用を終えてから再実行してください。"
        return post_apply_outcome.PostApplyOutcome(failure=failure, next_action=next_action)
    return post_apply_outcome.PostApplyOutcome()


def start_uv_cache_maintenance() -> int:
    """uvの取得キャッシュ整理を、更新の終了を待たせない独立プロセスで開始し、そのPIDを返す。

    uvの整理は他のuvプロセスの使用中はロック待ちの上限まで待ち、全消去はキャッシュの量に比例する時間かかる。
    更新とprocess-loopをその間止めないよう、端末・出力・所有groupから切り離して起動し、結果は永続ログと記録へ残す。
    """
    command = [sys.executable, "-m", "pytools.post_apply", "--internal-uv-cache-maintenance"]
    attempts: list[dict[str, Any]] = [{"start_new_session": True}]
    if os.name == "nt":
        # 離脱を許さないJobの中では離脱を指定した起動が拒否されるため、所属を継承して起動し直す。
        flags = windows_job.HIDDEN_DETACHED_CREATIONFLAGS
        attempts = [{"creationflags": flags | windows_job.CREATE_BREAKAWAY_FROM_JOB}, {"creationflags": flags}]
    refused = False
    for index, options in enumerate(attempts):
        try:
            # 時間上限なし: 待たずに切り離す起動であり、uvの操作は`_command`の上限で終わる。
            process = subprocess.Popen(  # pylint: disable=consider-using-with
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=Path.home(),
                **options,
            )
        except PermissionError:
            if index + 1 == len(attempts):
                raise
            refused = True
            continue
        if refused:
            logger.warning(
                "prune: 起動元のWindows Jobが離脱を許可しないため、離脱せずにuvの整理の独立プロセスを開始した。"
                "起動元のJobが終了すると整理も止まり、次の更新で再試行する"
            )
        # 起動元が存続する間に終えた子をzombieとして残さない。起動元が先に終えればOSが引き取る。
        threading.Thread(target=process.wait, daemon=True).start()
        return process.pid
    raise AssertionError("起動の試行が空です")


def _start_uv_maintenance(*, clean: bool) -> post_apply_outcome.PostApplyOutcome:
    """uvの整理を独立プロセスで開始する。開始しない・できない場合は、キャッシュ内のMCP環境をこのプロセスで事前構築する。"""
    failure = None
    if clean:
        try:
            pid = start_uv_cache_maintenance()
        except OSError as error:
            failure = f"uv: 整理の独立プロセスを起動できません: {error}"
        else:
            logger.info(
                "prune: uvのキャッシュ整理を独立プロセス（pid %d）で開始した。結果は次の更新で表示し、永続ログ（%s）へ記録する",
                pid,
                update_log.LOG_PATH,
            )
            return post_apply_outcome.PostApplyOutcome()
    warmup_pyfltr_mcp.run()
    return post_apply_outcome.PostApplyOutcome(
        failure=failure, next_action="診断を確認し、update-dotfilesを再実行してください。" if failure else None
    )


def clean_caches() -> post_apply_outcome.PostApplyOutcome:
    """uv以外の取得キャッシュを公式操作で整理し、1対象の失敗でも残りを続ける。"""
    failures: list[str] = []
    changed = False
    commands: list[tuple[str, list[str]]] = []
    mise = setup_mise.find_mise_binary()
    if mise is not None:
        commands.append(("mise", [str(mise), "cache", "prune"]))
    for name, arguments in (("npm", ["cache", "verify"]), ("pnpm", ["store", "prune"])):
        executable = shutil.which(name)
        if executable is not None:
            commands.append((name, [str(executable), *arguments]))
        else:
            logger.info("prune: %sは未導入のため対象なし", name)
    for name, command in commands:
        try:
            _command(command)
            changed = True
            logger.info("prune: %sのキャッシュ整理を完了", name)
        except (OSError, ValueError, RuntimeError) as error:
            failures.append(f"{name}: {error}")
    return post_apply_outcome.PostApplyOutcome(
        changed=changed,
        failure=" / ".join(failures) or None,
        next_action="診断を確認し、update-dotfilesを再実行してください。" if failures else None,
    )


def _merge(*outcomes: post_apply_outcome.PostApplyOutcome) -> post_apply_outcome.PostApplyOutcome:
    failures = [outcome.failure for outcome in outcomes if outcome.failure is not None]
    next_actions = dict.fromkeys(outcome.next_action for outcome in outcomes if outcome.next_action is not None)
    return post_apply_outcome.PostApplyOutcome(
        changed=any(outcome.changed for outcome in outcomes),
        deferred=any(outcome.deferred for outcome in outcomes),
        failure=" / ".join(failures) or None,
        next_action=" ".join(next_actions) or None,
    )


def run(lock: contextlib.AbstractContextManager[object] | None = None) -> post_apply_outcome.PostApplyOutcome:
    """前回のuvの整理の結果を表示し、導入実体の保護判定と取得キャッシュの公式整理を分けて実行する。

    `lock`には呼び出し元が保持する更新ロックを渡す。uvの整理の独立プロセスは更新ロックを待たずに取得し、
    取れなければ保留するため、`lock`を解放してから開始する。導入実体の整理に失敗した場合は取得キャッシュを整理しない。
    """
    with lock or contextlib.nullcontext():
        previous = _report_previous_uv_maintenance()
        installation = _remove_installation()
        caches = clean_caches() if installation.failure is None else post_apply_outcome.PostApplyOutcome()
    return _merge(previous, installation, caches, _start_uv_maintenance(clean=installation.failure is None))
