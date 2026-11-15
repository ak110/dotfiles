"""通常更新で、導入モードの対象外の実体と取得キャッシュを整理する。

実行中のエージェントと依存環境を先に照合し、使用中なら実体を保持して再実行を案内する。
fullへの更新と依存準備は旧環境を回収しない。
"""

import functools
import json
import logging
import math
import os
import shutil
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

import platformdirs
import psutil
from agent_toolkit._common import dotfiles_mode, plugin_environment

from pytools._internal import codex_processes, common, post_apply_outcome, setup_mise

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
        "rust",
        "gcloud",
        "npm:aws-cdk",
        "npm:@google/gemini-cli",
        "npm:opencode-ai",
        "npm:@anthropic-ai/claude-code",
        "npm:@openai/codex",
    }
)


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
# 既定の上限まで待ってから失敗する。使用中なら短時間で保留へ切り替え、dotfilesユーザーへ次の操作を早く示す。
_UV_LOCK_TIMEOUT_SECONDS = "30"

# uv 0.13.6のロック待ちタイムアウトの診断（`error: Timeout (30s) when waiting for lock on <cache> at <cache>/.lock, ...`）。
_UV_LOCK_TIMEOUT_MARKER = "when waiting for lock on"


class _InUseError(Exception):
    """他のプロセスが使用中のため、削除を保留した。"""


def _command(command: list[str]) -> None:
    """コマンドを実行し、失敗を例外へ変換する。

    排他ロックを取るuvの操作（`uv cache clean`など）がロック待ちで失敗した場合は、
    他のuvプロセスがキャッシュを使用中と判定して`_InUseError`を送出する。
    """
    result = common.run_subprocess(
        command,
        timeout=600,
        cwd=Path.home(),
        tag="prune",
        env_overrides={"MISE_AUTO_INSTALL": "0", "UV_LOCK_TIMEOUT": _UV_LOCK_TIMEOUT_SECONDS},
    )
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
        values = [info.get("exe") or "", *(info.get("cmdline") or [])[:4]]
        name = str(info.get("name") or "").removesuffix(".exe")
        if not info.get("cmdline") or not info.get("exe"):
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
            # Node本体を削除する前に、npmの外部global prefixも対象にする。
            npm = shutil.which("npm")
            if npm is not None:
                _command(
                    [
                        str(npm),
                        "uninstall",
                        "--global",
                        "opencode-ai",
                        "@anthropic-ai/claude-code",
                        "@openai/codex",
                        "@google/gemini-cli",
                        "aws-cdk",
                    ]
                )
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


def _clean_uv_cache(uv: Path) -> None:
    """実キャッシュルートごとの全消去成功から30日間は通常pruneを使う。"""
    resolved = common.run_subprocess([str(uv), "cache", "dir"], tag="prune", timeout=30)
    if resolved is None or resolved.returncode != 0:
        raise RuntimeError(common.format_cli_error(resolved))
    cache = Path(resolved.stdout.strip())
    if not resolved.stdout.strip() or not cache.is_absolute():
        raise ValueError("uv cache dirが絶対パスを返しませんでした")
    key = os.path.normcase(str(cache.resolve()))
    state = Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "uv-cache-clean.json"
    try:
        successes = json.loads(state.read_text(encoding="utf-8"))
    except FileNotFoundError:
        successes = {}
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


def clean_caches() -> post_apply_outcome.PostApplyOutcome:
    """公式操作で取得キャッシュを整理し、1対象の競合や失敗でも残りを続ける。"""
    failures: list[str] = []
    deferred = False
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
    uv = common.resolve_executable("uv")
    targets: list[tuple[str, Callable[[], None]]] = [(name, functools.partial(_command, command)) for name, command in commands]
    if uv is not None:
        targets.insert(0, ("uv", functools.partial(_clean_uv_cache, uv)))
    else:
        logger.info("prune: uvは未導入のため対象なし")
    for name, operation in targets:
        try:
            operation()
            changed = True
            logger.info("prune: %sのキャッシュ整理を完了", name)
        except _InUseError as error:
            deferred = True
            logger.warning("prune: %sの整理を使用中のため保留。次回のupdate-dotfilesで再試行: %s", name, error)
        except (OSError, ValueError, RuntimeError) as error:
            failures.append(f"{name}: {error}")
    return post_apply_outcome.PostApplyOutcome(
        changed=changed,
        deferred=deferred,
        failure=" / ".join(failures) or None,
        next_action="診断を確認し、update-dotfilesを再実行してください。" if failures else None,
    )


def run() -> post_apply_outcome.PostApplyOutcome:
    """導入実体の保護判定と取得キャッシュの公式整理を分けて実行する。"""
    installation = _remove_installation()
    if installation.failure is not None:
        return installation
    caches = clean_caches()
    return post_apply_outcome.PostApplyOutcome(
        changed=installation.changed or caches.changed,
        deferred=installation.deferred or caches.deferred,
        failure=caches.failure,
        next_action=caches.next_action,
    )
