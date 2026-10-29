"""明示的な--pruneで、現在の導入モードの対象外になった実体を削除する。

実行中のエージェントと依存環境を先に照合し、使用中なら実体を保持して再実行を案内する。
fullへの更新と依存準備は旧環境を回収しない。
"""

import json
import logging
import os
import shutil
import tomllib
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
    paths = [
        Path(os.environ.get("RUSTUP_HOME", home / ".rustup")),
        Path(os.environ.get("CARGO_HOME", home / ".cargo")),
        Path(os.environ.get("MISE_DOTNET_ROOT", data / "dotnet-root")),
        home / ".dotnet",
        home / ".local/share/claude",
        claude_home / "plugins/cache",
        home / ".cache/claude",
        home / ".antigravity_cli",
        home / ".local/share/herdr",
        home / ".cache/opencode",
        home / ".cache/herdr",
        codex_home / "packages",
        codex_home / "plugins/cache",
        codex_home / "agent-toolkit-codex",
        plugin_environment.runtime_data(os.environ) / "environments",
        local / "Programs/OpenAI/Codex",
        local / "Programs/Antigravity",
        local / "agy",
        local / "Programs/Herdr",
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

    排他ロックを取るuvの操作（`uv cache prune`など）がロック待ちで失敗した場合は、
    他のuvプロセスがキャッシュを使用中と判定して`_InUseError`を送出する。
    """
    result = common.run_subprocess(
        command,
        timeout=600,
        cwd=Path.home(),
        tag="prune",
        env_overrides={"MISE_AUTO_INSTALL": "0", "UV_LOCK_TIMEOUT": _UV_LOCK_TIMEOUT_SECONDS},
    )
    if result is not None and result.returncode != 0 and _UV_LOCK_TIMEOUT_MARKER in (result.stderr or ""):
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
    for process in psutil.process_iter(["name", "exe", "cmdline", attribute, "status"]):
        if process.pid in ignored:
            continue
        info = process.info
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
        if uses_path or uses_pyfltr or name in {"claude", "codex", "agy", "herdr", "opencode", "claude-statusline"}:
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


def run() -> post_apply_outcome.PostApplyOutcome:
    """実体を保護判定してから削除し、撤去失敗をpost-applyの失敗として返す。"""
    root = common.find_dotfiles_root()
    if root is None:
        return post_apply_outcome.PostApplyOutcome(
            failure="dotfilesルートを解決できません", next_action="作業ツリーでupdate-dotfiles --pruneを再実行してください。"
        )
    changed = False
    try:
        minimal = dotfiles_mode.is_minimal()
        excluded = excluded_tools(root, minimal=minimal)
        paths = (*removal_paths(), root / "agent-toolkit-codex", root / "agent-toolkit/.venv") if minimal else ()
        for path in paths:
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
            raise RuntimeError("miseが見つからず、導入済みツールの削除を開始できません")
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
        for command in ([str(mise), "prune", "-y"], [str(mise), "reshim", "--force"], [str(mise), "cache", "clear"]):
            _command(command)
        if minimal:
            uv = common.resolve_executable("uv")
            if uv is None:
                raise RuntimeError("uvが見つからずキャッシュ整理を実行できません")
            _command([str(uv), "cache", "prune"])
    except _InUseError as error:
        logger.warning(
            "prune: 他のuvプロセスがキャッシュを使用中のため整理を保留した: %s\n"
            "uvを使う他のプロセス（エディター、MCPサーバー、`uv run`で起動した常駐プロセスなど）を終了してから"
            "`update-dotfiles --prune`を再実行する。"
            "それらを終了できない場合は再実行を見送ってよい（uvキャッシュの整理は最後の工程であり、"
            "それより前の削除は完了している。未整理のキャッシュがディスク容量を使い続けるだけである）",
            error,
        )
        return post_apply_outcome.PostApplyOutcome(changed=changed, deferred=True)
    except (OSError, ValueError, RuntimeError, psutil.Error) as error:
        return post_apply_outcome.PostApplyOutcome(
            changed=changed,
            failure=str(error),
            next_action="失敗理由に示された環境設定と使用中のプロセスを確認し、update-dotfiles --pruneを再実行してください。",
        )
    return post_apply_outcome.PostApplyOutcome(changed=True)
