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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import filelock
import platformdirs
import psutil
from agent_toolkit._common import dotfiles_mode, plugin_environment, windows_job

from pytools._internal import (
    codex_processes,
    common,
    log_format,
    post_apply_outcome,
    setup_cli_common,
    setup_mise,
    update_log,
    warmup_pyfltr_mcp,
)

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

# minimalで整理するnpmのglobal導入物。旧npm版CLIは整理せず、配置を案内する。
_NPM_PACKAGES = ("opencode-ai", "@google/gemini-cli", "aws-cdk")


def excluded_tools(root: Path, *, minimal: bool) -> frozenset[str]:
    configuration = tomllib.loads((root / "mise.toml").read_text(encoding="utf-8"))
    project_tools = frozenset(configuration.get("tools", {}))
    return DEVELOPMENT_TOOLS | FULL_TOOLS | project_tools if minimal else DEVELOPMENT_TOOLS - project_tools


def _rustup_home() -> Path:
    return Path(os.environ.get("RUSTUP_HOME", Path.home() / ".rustup"))


def removal_paths() -> tuple[Path, ...]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    data = Path(os.environ.get("MISE_DATA_DIR", platformdirs.user_data_dir("mise", appauthor=False)))
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    rustup = _rustup_home()
    cargo = Path(os.environ.get("CARGO_HOME", home / ".cargo"))
    paths = [
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

    paths.extend(home / ".local/bin" / name for name in ("claude.exe", "codex.exe"))
    for name in ("atk-hook", "agy", "herdr", "opencode", "claude-statusline"):
        for suffix in ("", ".exe", ".cmd", ".ps1"):
            paths.append(home / ".local/bin" / (name + suffix))
    return tuple(dict.fromkeys(paths))


# uvの排他ロックを待つ上限（秒）。uvの既定は300秒で、他のuvプロセスが共有ロックを保持し続ける間は
# 既定の上限まで待ってから失敗する。整理の独立プロセスを短時間で終え、使用中なら次回の更新へ回す。
_UV_LOCK_TIMEOUT_SECONDS = "30"

# uv 0.13.6のロック待ちタイムアウトの診断（`error: Timeout (30s) when waiting for lock on <cache> at <cache>/.lock, ...`）。
_UV_LOCK_TIMEOUT_MARKER = "when waiting for lock on"


class _InUseError(Exception):
    pass


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

        if uses_path or uses_pyfltr or name in {"claude", "codex", "agy", "herdr", "opencode", "claude-statusline", "rustup"}:
            active.append(f"{name or 'process'} (pid {process.pid})")
    return active


def _stop_daemon() -> None:
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


def _check_shared_runtime_root(path: Path, checkout: Path | None) -> None:
    resolved = path.resolve()
    if Path.home().resolve().is_relative_to(resolved) or (checkout is not None and checkout.resolve().is_relative_to(resolved)):
        raise ValueError(f"共有ランタイム設定がホームまたは作業ツリー全体を含むため削除しません: {path}")


@dataclasses.dataclass(frozen=True)
class _NpmRoot:
    modules: Path | None
    kept: bool = False


def _prune_global_npm(command: list[str], root: Path, *, path: str | None = None) -> _NpmRoot:
    overrides = {"MISE_AUTO_INSTALL": "0"}
    if path is not None:
        overrides["PATH"] = path
    result = common.run_subprocess(
        [*command, "root", "--global"], timeout=common.COMMAND_TIMEOUT, cwd=Path.home(), tag="prune", env_overrides=overrides
    )
    modules = Path((result.stdout or "").strip()) if result is not None and result.returncode == 0 else None
    if modules is None or not modules.is_absolute():
        logger.warning(
            "npmの導入先を確認できないため、旧npm版の有無を確かめずにそのnpmのパッケージとmise管理のNodeを保持: %s（%s）。"
            "%sが導入先の絶対パスを表示するようnpmを%s",
            setup_cli_common.format_command(command),
            common.format_cli_error(result),
            setup_cli_common.format_command([*command, "root", "--global"]),
            setup_cli_common.RERUN_AFTER_REPAIR,
        )
        return _NpmRoot(None, kept=True)
    kept = False
    targets = []
    candidates = (*setup_cli_common.LEGACY_NPM_CLIS, *_NPM_PACKAGES)
    for package_name in candidates:
        package = modules.joinpath(*package_name.split("/"))
        manifest = package / "package.json"
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            if package_name in setup_cli_common.LEGACY_NPM_CLIS and (package.exists() or package.is_symlink()):
                # package.jsonの無い旧版は、npmが一緒に削除する起動ファイルを特定できない。
                _warn_unconfirmed_package(manifest, "package.jsonが無い")
                kept = True
            continue
        except (OSError, ValueError) as error:
            _warn_unconfirmed_package(manifest, str(error))
            kept = True
            continue
        if not isinstance(metadata, dict):
            _warn_unconfirmed_package(manifest, "JSONのobjectではない")
            kept = True
            continue
        if setup_cli_common.protected_npm_package(package_name, metadata):
            kept = True
            bins = {Path(name.lower()).stem for name in setup_cli_common.normalize_npm_package_bins(metadata)}
            cli = setup_cli_common.LEGACY_NPM_CLIS.get(package_name) or ("claude" if "claude" in bins else "codex")
            windows = sys.platform == "win32"
            canonical = (
                root / "bin" / ("codex.cmd" if windows else "codex")
                if cli == "codex"
                else Path.home() / ".local/bin" / ("claude.exe" if windows else "claude")
            )
            setup_cli_common.warn_about_retained_npm_package(
                package, [*command, "uninstall", "--global", package_name], canonical, minimal=True
            )
        elif package_name in _NPM_PACKAGES:
            targets.append(package_name)
    if targets:
        _command([*command, "uninstall", "--global", *targets], path=path)
    return _NpmRoot(modules, kept=kept)


def _warn_unconfirmed_package(manifest: Path, reason: str) -> None:
    logger.warning(
        "npmのパッケージ情報を確認できないため、旧npm版かを確かめずにパッケージと同じprefixの起動ファイルを保持: %s（%s）。"
        "このパッケージ配下の内容とpackage.jsonを確認し、同じ版のpackage.jsonを復元するか読取権限を%s",
        manifest,
        reason,
        setup_cli_common.RERUN_AFTER_REPAIR,
    )


# Node.jsの配布物（Linux・macOSのtar、Windowsのzip）が導入先直下に置く項目（小文字）。
_NODE_DISTRIBUTION = frozenset(
    {
        "bin",
        "include",
        "lib",
        "share",
        "changelog.md",
        "license",
        "readme.md",
        "node.exe",
        "node_modules",
        "install_tools.bat",
        "nodevars.bat",
        "node_etw_provider.man",
        *(f"{name}{suffix}" for name in ("npm", "npx", "corepack") for suffix in ("", ".cmd", ".ps1")),
    }
)

_NODE_DISTRIBUTION_CHILDREN = {"lib": frozenset({"node_modules"}), "include": frozenset({"node"}), "share": None}
_MAX_LAUNCHER_BYTES = 64 * 1024
"""npmの起動スクリプト（数百バイト）より十分大きい上限。超えるファイルは実行ファイル本体などとみなして読まない。"""


def _node_unconfirmed_entries(install_path: Path) -> list[str]:
    """mise管理のNodeの配下のうち、削除してよいと確認できない項目を返す。空ならNodeを削除してよい。

    削除してよいのは、Node.jsの配布物と、旧npm版でないと確認できたnpmのglobalパッケージと、
    そのパッケージを指す起動ファイルだけである。旧版らしいものを探すのではなく、確認できたもの以外を全て残す。
    POSIX（`lib/node_modules`と`bin`）とWindows（`node_modules`と導入先直下）の両方の配置を調べる。
    """
    if not install_path.exists():
        return []
    reasons: list[str] = []
    confirmed: list[Path] = []
    bins: set[str] = set()
    for modules in (install_path / "lib" / "node_modules", install_path / "node_modules"):
        try:
            entries = sorted(modules.iterdir())
        except FileNotFoundError:
            continue
        except OSError as error:
            reasons.append(f"{modules}を読めない（{error}）")
            continue
        packages: list[Path] = []
        for entry in entries:
            if entry.name.startswith("@") and entry.is_dir() and not entry.is_symlink():
                try:
                    packages.extend(sorted(entry.iterdir()))
                except OSError as error:
                    reasons.append(f"{entry}を読めない（{error}）")
                continue
            packages.append(entry)
        for package in packages:
            try:
                metadata = json.loads((package / "package.json").read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as error:
                reasons.append(f"npmのパッケージと確認できない{package}（{error}）")
                continue
            if not isinstance(metadata, dict):
                reasons.append(f"npmのパッケージと確認できない{package}")
            elif setup_cli_common.protected_npm_package(package.relative_to(modules).as_posix(), metadata):
                reasons.append(f"旧npm版に関係するパッケージ{package}")
            else:
                confirmed.append(common.safe_resolve(package))
                bins |= {Path(name.lower()).stem for name in setup_cli_common.normalize_npm_package_bins(metadata)}
    try:
        top = sorted(install_path.iterdir())
    except OSError as error:
        return [*reasons, f"{install_path}を読めない（{error}）"]
    for entry in top:
        lowered = entry.name.lower()
        if lowered in _NODE_DISTRIBUTION:
            if lowered in _NODE_DISTRIBUTION_CHILDREN and entry.is_dir() and not entry.is_symlink():
                allowed = _NODE_DISTRIBUTION_CHILDREN[lowered]
                if allowed is not None:
                    try:
                        extra = [child for child in sorted(entry.iterdir()) if child.name.lower() not in allowed]
                    except OSError as error:
                        extra = []
                        reasons.append(f"{entry}を読めない（{error}）")
                    reasons.extend(f"Node.jsの配布物と確認できない{child}" for child in extra)
            continue
        # Windowsのnpmは導入先直下へ、globalパッケージの起動ファイル（拡張子なし・.cmd・.ps1）を置く。
        if lowered.removesuffix(".cmd").removesuffix(".ps1") in bins:
            continue
        reasons.append(f"Node.jsの配布物と確認できない{entry}")
    try:
        launchers = sorted((install_path / "bin").iterdir())
    except FileNotFoundError:
        launchers = []
    except OSError as error:
        launchers = []
        reasons.append(f"{install_path / 'bin'}を読めない（{error}）")
    for launcher in launchers:
        if launcher.name == "node" and launcher.is_file() and not launcher.is_symlink():
            continue
        target = common.safe_resolve(launcher)
        if launcher.is_symlink() and any(target.is_relative_to(package) for package in confirmed):
            continue
        reasons.append(f"確認できたnpmパッケージを指さない起動ファイル{launcher}")
    return reasons


def _unconfirmed_user_launcher(path: Path) -> str | None:
    """`~/.local/bin`の削除候補が、npmの起動ファイルでないと確認できない場合に理由を返す。

    npmのglobal prefixが`~/.local`なら、POSIXは`~/.local/lib/node_modules`、Windowsの配置なら
    `~/.local/bin/node_modules`を持つ。そのどちらも無く、候補が`node_modules`を参照しない場合だけ削除してよい。
    """
    local = Path.home() / ".local"
    for modules in (local / "lib" / "node_modules", local / "bin" / "node_modules"):
        if modules.exists() or modules.is_symlink():
            return f"{local}がnpmの導入先（{modules}）"
    if path.is_symlink():
        return "node_modulesを指すリンク" if "node_modules" in common.safe_resolve(path).parts else None
    try:
        if path.is_file() and path.stat().st_size <= _MAX_LAUNCHER_BYTES and b"node_modules" in path.read_bytes():
            return "node_modulesを参照する起動スクリプト"
    except OSError as error:
        return f"内容を読めない（{error}）"
    return None


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
            _check_shared_runtime_root(path, root)
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
        keep_node = False
        if minimal:
            queried: list[Path] = []
            retained: list[Path] = []
            node_reasons: list[str] = []

            def record(result: _NpmRoot) -> None:
                if result.modules is None:
                    node_reasons.append("npmの導入先を確認できない")
                    return
                queried.append(result.modules)
                if result.kept:
                    retained.append(result.modules)

            installed_nodes = installed.get("node", [])
            for entry in installed_nodes:
                record(_prune_global_npm([str(mise), "exec", f"node@{entry['version']}", "--", "npm"], root))
            shims = common.mise_shim_directories()
            search_path = os.pathsep.join(
                directory
                for directory in os.environ.get("PATH", "").split(os.pathsep)
                if directory and common.safe_resolve(Path(directory)) not in shims
            )
            npm = shutil.which("npm", path=search_path)
            if npm is not None:
                record(_prune_global_npm([npm], root, path=search_path))
            for entry in installed_nodes:
                install_path = entry.get("install_path")
                if not install_path:
                    node_reasons.append(f"node@{entry.get('version')}の導入先を確認できない")
                    continue
                node_reasons.extend(_node_unconfirmed_entries(Path(install_path)))
                node_reasons.extend(
                    f"旧npm版を保持したnpm root {modules}"
                    for modules in retained
                    if common.safe_resolve(modules).is_relative_to(common.safe_resolve(Path(install_path)))
                )
            keep_node = bool(installed_nodes) and bool(node_reasons)
            if keep_node:
                logger.warning(
                    "削除してよいと確認できない項目があるため、mise管理のNodeを保持: %s。"
                    "保持したままでも動作に支障はない。Nodeを削除する場合は、列挙した項目を確認する。"
                    "旧npm版などの不要な導入物は自分で整理し、使う項目は別の場所へ移してから"
                    "`update-dotfiles`を再実行すると、確認できたNodeを削除する",
                    "、".join(dict.fromkeys(node_reasons)),
                )
                excluded -= {"node"}
            user_bin = common.safe_resolve(Path.home() / ".local/bin")
            # 照会したnpmのprefixの起動ファイルの置き場（POSIXは`<prefix>/bin`、Windowsは`<prefix>`）。
            npm_bins = {
                common.safe_resolve(directory): modules
                for modules in queried
                for directory in (modules.parent, modules.parent.parent / "bin")
            }
            held: list[str] = []
            kept_paths: list[Path] = []
            for path in paths:
                target = common.safe_resolve(path)
                if any(common.safe_resolve(modules).is_relative_to(target) for modules in queried):
                    held.append(f"{path}（npmの導入先を含む）")
                elif common.safe_resolve(path.parent) in npm_bins:
                    held.append(f"{path}（npmの導入先{npm_bins[common.safe_resolve(path.parent)]}の起動ファイルの置き場）")
                elif common.safe_resolve(path.parent) == user_bin and (path.exists() or path.is_symlink()):
                    reason = _unconfirmed_user_launcher(path)
                    if reason is None:
                        kept_paths.append(path)
                    else:
                        held.append(f"{path}（{reason}）")
                else:
                    kept_paths.append(path)
            if held:
                logger.warning(
                    "npmの導入物でないと確認できないため、削除せず保持: %s。"
                    "PATHで先に解決される間はこちらが起動する。不要と確認できた場合だけ、"
                    "自分で配置・編集したファイルを退避してから手動で削除する",
                    "、".join(held),
                )
            paths = tuple(kept_paths)
        for tool in sorted(excluded):
            short = tool.split(":", 1)[-1].split("/")[-1]
            if tool in installed or short in installed:
                _command([str(mise), "uninstall", "--all", tool])
                changed = True
        for path in paths:
            changed |= _remove(path)
        if not keep_node:
            _command([str(mise), "prune", "-y"])
        _command([str(mise), "reshim", "--force"])
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
    try:
        _command([str(uv), "cache", "clean" if clean else "prune"])
    except _InUseError:
        if sys.platform == "linux":
            _log_uv_lock_holders(cache / ".lock")
        raise
    if clean:
        successes[key] = time.time()
        state.parent.mkdir(parents=True, exist_ok=True)
        if not common.atomic_write_json(state, successes, tag="prune"):
            raise OSError(f"uv全消去の成功日時を保存できません: {state}")


def _log_uv_lock_holders(lock: Path) -> None:
    """Linuxのロック表から、競合したファイルの保持者だけを永続ログに記録する。"""
    try:
        info = lock.stat()
        identity = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
        for line in Path("/proc/locks").read_text(encoding="ascii").splitlines():
            fields = line.split()
            # 待機者の行は第2列が矢印で、保持者の行とは列位置が異なる。
            if len(fields) >= 6 and fields[5] == identity and fields[4].isdigit():
                pid = int(fields[4])
                logger.info("prune: %s の保持者: PID %d (%s)", lock, pid, psutil.Process(pid).name(), extra=log_format.LOG_ONLY)
    except (OSError, psutil.Error) as error:
        logger.info("prune: %s の保持者を特定できません: %s", lock, error, extra=log_format.LOG_ONLY)


class _CacheGroup(enum.StrEnum):
    UV = "uv"
    TOOLS = "mise・npm・pnpm"


class MaintenanceResult(enum.StrEnum):
    NONE = "none"
    DEFERRED = "deferred"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclasses.dataclass(frozen=True)
class _MaintenanceRecord:
    result: MaintenanceResult
    finished_at: str
    detail: str
    # 未解消の失敗の後に保留した、最後の試行の時刻と診断。失敗の表示に添える。
    deferred_at: str = ""
    deferred_detail: str = ""


def _record_path(group: _CacheGroup) -> Path:
    return _state_directory() / f"{group.name.lower()}-cache-maintenance.json"


def _read_maintenance_record(group: _CacheGroup) -> _MaintenanceRecord | None:
    try:
        data = json.loads(_record_path(group).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    fields = {field.name for field in dataclasses.fields(_MaintenanceRecord)}
    if (
        not isinstance(data, dict)
        or not {"result", "finished_at", "detail"} <= set(data) <= fields
        or not all(isinstance(value, str) for value in data.values())
    ):
        raise ValueError("result・finished_at・detailなどの項目が文字列ではありません")
    return _MaintenanceRecord(**{**data, "result": MaintenanceResult(data["result"])})


def _write_maintenance_record(group: _CacheGroup, result: MaintenanceResult, detail: str) -> None:
    """結果を記録する。保留は整理を試していないため、未解消の失敗を残して保留した試行を添える。"""
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    record = _MaintenanceRecord(result, now, detail)
    if result is MaintenanceResult.DEFERRED:
        try:
            previous = _read_maintenance_record(group)
        except OSError, ValueError:
            return
        if previous is not None and previous.result is MaintenanceResult.FAILED:
            record = dataclasses.replace(previous, deferred_at=now, deferred_detail=detail)
    if not common.atomic_write_json(_record_path(group), dataclasses.asdict(record), tag="prune"):
        logger.error("prune: %sの整理の結果を記録できません: %s。次の更新は前回の記録を表示する", group, _record_path(group))


def clean_uv_cache() -> tuple[MaintenanceResult, str]:
    uv = common.resolve_executable("uv")
    if uv is None:
        logger.info("prune: uvは未導入のため対象なし")
        return MaintenanceResult.NONE, ""
    try:
        _clean_uv_cache(uv)
    except _InUseError as error:
        logger.info("prune: uvの整理を使用中のため保留: %s。次回のupdate-dotfilesで再試行します", error)
        return MaintenanceResult.DEFERRED, str(error)
    except (OSError, ValueError, RuntimeError) as error:
        logger.error("prune: uvのキャッシュ整理に失敗: %s", error)
        return MaintenanceResult.FAILED, str(error)
    logger.info("prune: uvのキャッシュ整理を完了")
    return MaintenanceResult.COMPLETED, ""


def _run_maintenance(group: _CacheGroup, clean: Callable[[], tuple[MaintenanceResult, str]]) -> None:
    try:
        result, detail = clean()
    except Exception as error:  # noqa: BLE001 -- 無端末の子プロセスの診断を次の更新へ届ける。
        logger.exception("prune: %sの整理で想定外の例外が発生した", group)
        result, detail = MaintenanceResult.FAILED, f"想定外の例外: {error!r}"
    _write_maintenance_record(group, result, detail)


def maintain_caches() -> None:
    update_path = update_lock_path()
    worker = filelock.FileLock(str(update_path.with_name("cache-maintenance.lock")), timeout=0, preserve_lock_file=True)
    with contextlib.ExitStack() as stack:
        try:
            update_path.parent.mkdir(parents=True, exist_ok=True)
            stack.enter_context(worker)
        except filelock.Timeout:
            logger.info("prune: 前回のキャッシュ整理が実行中のため、その結果を待たずに終了する")
            return
        except OSError as error:
            logger.exception("prune: キャッシュ整理の排他に失敗した")
            for group in _CacheGroup:
                _write_maintenance_record(group, MaintenanceResult.FAILED, str(error))
            return
        try:
            with filelock.FileLock(str(update_path), timeout=0, preserve_lock_file=True):
                _run_maintenance(_CacheGroup.UV, clean_uv_cache)
                _run_maintenance(_CacheGroup.TOOLS, clean_caches)
        except filelock.Timeout:
            for group in _CacheGroup:
                _write_maintenance_record(group, MaintenanceResult.DEFERRED, "他の更新が実行中")
        except OSError as error:
            logger.exception("prune: 更新ロックを取得・解放できない")
            for group in _CacheGroup:
                _write_maintenance_record(group, MaintenanceResult.FAILED, str(error))
        try:
            warmup_pyfltr_mcp.run()
        except Exception as error:  # noqa: BLE001 -- 事前構築の失敗も従来どおり次の更新へ届ける。
            logger.exception("prune: MCPの事前構築で想定外の例外が発生した")
            _write_maintenance_record(_CacheGroup.UV, MaintenanceResult.FAILED, f"MCPの事前構築: {error!r}")


def _report_previous_maintenance(group: _CacheGroup) -> post_apply_outcome.PostApplyOutcome:
    """前回の独立プロセスの結果を表示する。保留は案内し、失敗は解消するまで更新の失敗として返す。"""
    log = update_log.LOG_PATH
    retry = "整理は次回の更新時に再試行します。"
    try:
        record = _read_maintenance_record(group)
    except (OSError, ValueError) as error:
        return post_apply_outcome.PostApplyOutcome(
            failure=f"{group}: 整理の独立プロセスの結果を読めません: {_record_path(group)}: {error}",
            next_action=f"永続ログ（{log}）を確認してください。{retry}",
        )
    if record is None:
        return post_apply_outcome.PostApplyOutcome()
    if record.result is MaintenanceResult.DEFERRED:
        logger.info(
            "prune: 前回の%sの整理は保留した（%s）: %s", group, record.finished_at, record.detail, extra=log_format.LOG_ONLY
        )
        if group is _CacheGroup.UV and _UV_LOCK_TIMEOUT_MARKER in record.detail:
            return post_apply_outcome.PostApplyOutcome(
                notices=(
                    post_apply_outcome.PostApplyNotice(
                        f"prune: uvキャッシュが使用中のため整理を保留しました。{retry}",
                        log=_record_path(group),
                        warning=False,
                    ),
                )
            )
    elif record.result is MaintenanceResult.COMPLETED:
        logger.info("prune: 前回の%sのキャッシュ整理は完了（%s）", group, record.finished_at, extra=log_format.LOG_ONLY)
    elif record.result is MaintenanceResult.FAILED:
        failure = f"{group}: 前回の整理の独立プロセスが失敗した（{record.finished_at}）: {record.detail}"
        next_action = f"永続ログ（{log}）の診断を確認して原因を解消し、update-dotfilesを再実行してください。{retry}"
        if record.deferred_at:
            logger.info(
                "prune: %sの整理を保留（%s）: %s", group, record.deferred_at, record.deferred_detail, extra=log_format.LOG_ONLY
            )
            failure += f"。その後の整理は保留した（{record.deferred_at}）"
            next_action += "保留が続く場合は、永続ログの診断が示すツールの使用を終えてから再実行してください。"
        return post_apply_outcome.PostApplyOutcome(failure=failure, next_action=next_action)
    return post_apply_outcome.PostApplyOutcome()


def start_cache_maintenance() -> int:
    """取得キャッシュの整理を、更新の終了を待たせない独立プロセスで開始し、そのPIDを返す。

    uvの整理は他のuvプロセスの使用中はロック待ちの上限まで待ち、全消去はキャッシュの量に比例する時間かかる。
    更新とprocess-loopをその間止めないよう、端末・出力・所有groupから切り離して起動し、結果は永続ログと記録へ残す。
    """
    command = [sys.executable, "-m", "pytools.post_apply", "--internal-uv-cache-maintenance"]
    attempts: list[dict[str, Any]] = [{"start_new_session": True}]
    if os.name == "nt":
        flags = windows_job.HIDDEN_DETACHED_CREATIONFLAGS
        attempts = [{"creationflags": flags | windows_job.CREATE_BREAKAWAY_FROM_JOB}, {"creationflags": flags}]
    refused = False
    for index, options in enumerate(attempts):
        try:
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
                "prune: 起動元のWindows Jobが離脱を許可しないため、離脱せずにキャッシュ整理の独立プロセスを開始した。"
                "起動元のJobが終了すると整理も止まり、次の更新で再試行する"
            )

        threading.Thread(target=process.wait, daemon=True).start()
        return process.pid
    raise AssertionError("起動の試行が空です")


def _start_maintenance(*, clean: bool) -> post_apply_outcome.PostApplyOutcome:
    """キャッシュ整理を独立プロセスで開始する。開始しない・できない場合は、キャッシュ内のMCP環境をこのプロセスで事前構築する。"""
    failure = None
    if clean:
        try:
            start_cache_maintenance()
        except OSError as error:
            failure = f"キャッシュ整理の独立プロセスを起動できません: {error}"
        else:
            logger.info(
                "prune: キャッシュ整理を独立プロセスで開始した。結果は永続ログ（%s）へ記録する",
                update_log.LOG_PATH,
                extra=log_format.LOG_ONLY,
            )
            return post_apply_outcome.PostApplyOutcome()
    warmup_pyfltr_mcp.run()
    return post_apply_outcome.PostApplyOutcome(
        failure=failure, next_action="診断を確認し、update-dotfilesを再実行してください。" if failure else None
    )


def _clean_rustup_tmp() -> MaintenanceResult:
    home = _rustup_home()
    root = home / "tmp"
    if not root.is_absolute() or root.is_symlink() or root.is_junction():
        return MaintenanceResult.NONE
    try:
        _check_shared_runtime_root(home, common.find_dotfiles_root())
    except ValueError:
        return MaintenanceResult.NONE
    cutoff = time.time() - 86_400
    expired: list[Path] = []
    try:
        for child in root.iterdir():
            with contextlib.suppress(FileNotFoundError):
                if child.is_dir(follow_symlinks=False) and not child.is_junction() and child.stat().st_mtime < cutoff:
                    expired.append(child)
    except FileNotFoundError:
        return MaintenanceResult.NONE
    if not expired:
        return MaintenanceResult.NONE
    if any(
        str(process.info.get("name") or "").lower().removesuffix(".exe") == "rustup"
        for process in psutil.process_iter(["name"])
    ):
        return MaintenanceResult.DEFERRED

    for child in expired:
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(child)
    return MaintenanceResult.COMPLETED


def clean_caches() -> tuple[MaintenanceResult, str]:
    """uv以外の取得キャッシュとrustupの一時領域を整理し、1対象の失敗でも残りを続ける。"""
    failures: list[str] = []
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
            logger.info("prune: %sのキャッシュ整理を完了", name)
        except (OSError, ValueError, RuntimeError) as error:
            failures.append(f"{name}: {error}")
    rustup = MaintenanceResult.NONE
    try:
        rustup = _clean_rustup_tmp()
    except (OSError, ValueError) as error:
        failures.append(f"rustup/tmp: {error}")
    if failures:
        detail = " / ".join(failures)
        logger.error("prune: キャッシュ整理に失敗: %s", detail)
        return MaintenanceResult.FAILED, detail
    if rustup is MaintenanceResult.DEFERRED:
        return rustup, "rustupが使用中のため一時ディレクトリを保持"
    return (MaintenanceResult.COMPLETED if commands else rustup), ""


def _merge(*outcomes: post_apply_outcome.PostApplyOutcome) -> post_apply_outcome.PostApplyOutcome:
    failures = [outcome.failure for outcome in outcomes if outcome.failure is not None]
    next_actions = dict.fromkeys(outcome.next_action for outcome in outcomes if outcome.next_action is not None)
    return post_apply_outcome.PostApplyOutcome(
        changed=any(outcome.changed for outcome in outcomes),
        notices=tuple(notice for outcome in outcomes for notice in outcome.notices),
        deferred=any(outcome.deferred for outcome in outcomes),
        failure=" / ".join(failures) or None,
        next_action=" ".join(next_actions) or None,
    )


def run(lock: contextlib.AbstractContextManager[object] | None = None) -> post_apply_outcome.PostApplyOutcome:
    """前回のキャッシュ整理の結果を表示し、導入実体の保護判定と取得キャッシュの公式整理を分けて実行する。

    `lock`には呼び出し元が保持する更新ロックを渡す。uvの整理の独立プロセスは更新ロックを待たずに取得し、
    取れなければ保留するため、`lock`を解放してから開始する。導入実体の整理に失敗した場合は取得キャッシュを整理しない。
    """
    with lock or contextlib.nullcontext():
        previous = [_report_previous_maintenance(group) for group in _CacheGroup]
        installation = _remove_installation()
    return _merge(*previous, installation, _start_maintenance(clean=installation.failure is None))
