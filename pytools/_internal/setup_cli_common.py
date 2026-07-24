"""CodexとClaude CodeのCLI導入処理が共有する安全確認。"""

import json
import logging
import os
import re
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

import psutil

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)


def prepend_path(path: Path) -> None:
    """ディレクトリを現プロセスのPATH先頭へ重複なく追加する。"""
    value = str(path)
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    normalized = os.path.normcase(os.path.abspath(value))
    remaining = [entry for entry in entries if os.path.normcase(os.path.abspath(entry)) != normalized]
    os.environ["PATH"] = os.pathsep.join([value, *remaining])


def migrate_npm_launchers(
    cli_name: str,
    package_name: str,
    canonical_launcher: Path,
    canonical_prefix: Path,
) -> bool:
    """PATH上の帰属確認済み非正規npmパッケージを削除する。"""
    changed = False
    for launcher in _iter_noncanonical_launchers(cli_name, canonical_launcher, canonical_prefix):
        npm = _adjacent_npm(launcher.parent)
        if npm is None:
            logger.warning(log_format.format_status(cli_name, f"同じディレクトリにnpmがないため保持: {launcher}"))
            continue
        package_dir = _npm_package_dir(npm, package_name)
        if package_dir is None or not _launcher_belongs_to_package(launcher, package_dir, package_name):
            logger.warning(log_format.format_status(cli_name, f"npm packageへの帰属を確認できないため保持: {launcher}"))
            continue
        result = claude_common.run_subprocess(
            [str(npm), "uninstall", "--global", package_name],
            timeout=claude_common.CLAUDE_TIMEOUT,
            tag=cli_name,
        )
        if result is not None and result.returncode == 0:
            changed = True
        else:
            message = f"旧npm版の削除に失敗: {claude_common.format_cli_error(result)}"
            logger.warning(log_format.format_status(cli_name, message))
            raise RuntimeError(message)
    return changed


def is_windows_cli_running(cli_name: str, package_name: str, launchers: Iterable[Path] = ()) -> bool:
    """Windowsで対象CLIのプロセスが実行中かを安全側で判定する。"""
    if sys.platform != "win32":
        return False
    tokens = {cli_name.lower(), package_name.lower()}
    tokens.update(str(path).lower() for path in launchers)
    for process in psutil.process_iter():
        try:
            name = (process.name() or "").lower()
            exe = (process.exe() or "").lower()
            cmdline = [part.lower() for part in (process.cmdline() or [])]
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            try:
                inaccessible_name = Path((process.name() or "").lower()).name
                if inaccessible_name in {
                    cli_name.lower(),
                    f"{cli_name.lower()}.exe",
                    "node",
                    "node.exe",
                    "mise",
                    "mise.exe",
                }:
                    return True
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            continue
        haystacks = [name, exe, *cmdline]
        if _process_matches(cli_name, tokens, haystacks):
            return True
    return False


def _iter_noncanonical_launchers(
    cli_name: str,
    canonical_launcher: Path,
    canonical_prefix: Path,
) -> Iterable[Path]:
    seen: set[str] = set()
    names = (cli_name, f"{cli_name}.cmd", f"{cli_name}.exe") if sys.platform == "win32" else (cli_name,)
    canonical_real = _safe_resolve(canonical_launcher)
    prefix_real = _safe_resolve(canonical_prefix)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        directory = Path(entry)
        for name in names:
            launcher = directory / name
            if not launcher.exists():
                continue
            key = os.path.normcase(str(_safe_resolve(launcher)))
            if key in seen:
                continue
            seen.add(key)
            resolved = _safe_resolve(launcher)
            if resolved == canonical_real or _is_relative_to(resolved, prefix_real):
                continue
            yield launcher


def _adjacent_npm(directory: Path) -> Path | None:
    names = ("npm.cmd", "npm") if sys.platform == "win32" else ("npm",)
    return next((candidate for name in names if (candidate := directory / name).is_file()), None)


def _npm_package_dir(npm: Path, package_name: str) -> Path | None:
    prefix_result = claude_common.run_subprocess(
        [str(npm), "prefix", "--global"], timeout=claude_common.CLAUDE_TIMEOUT, tag=npm.name
    )
    root_result = claude_common.run_subprocess(
        [str(npm), "root", "--global"], timeout=claude_common.CLAUDE_TIMEOUT, tag=npm.name
    )
    if (
        prefix_result is None
        or prefix_result.returncode != 0
        or not (prefix_result.stdout or "").strip()
        or root_result is None
        or root_result.returncode != 0
        or not (root_result.stdout or "").strip()
    ):
        return None
    prefix = _safe_resolve(Path(prefix_result.stdout.strip()))
    root = _safe_resolve(Path(root_result.stdout.strip()))
    if not _is_relative_to(root, prefix):
        return None
    package_dir = root.joinpath(*package_name.split("/"))
    return package_dir if package_dir.is_dir() else None


def _launcher_belongs_to_package(launcher: Path, package_dir: Path, package_name: str) -> bool:
    resolved = _safe_resolve(launcher)
    package_real = _safe_resolve(package_dir)
    if resolved != launcher.absolute() and _is_relative_to(resolved, package_real):
        return True
    if launcher.suffix.lower() == ".cmd":
        try:
            content = launcher.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        entrypoint = _package_bin_entrypoint(package_dir, launcher.stem)
        if entrypoint is None:
            return False
        entrypoint_real = _safe_resolve(entrypoint)
        return any(_safe_resolve(candidate) == entrypoint_real for candidate in _cmd_referenced_paths(content, launcher.parent))
    if launcher.suffix.lower() == ".exe":
        # mise shimはmiseの解決情報が対象packageを指す場合だけ対象とする。
        mise_name = shutil.which("mise")
        if mise_name is None:
            return False
        mise = Path(mise_name).absolute()
        result = claude_common.run_subprocess(
            [str(mise), "which", launcher.stem], timeout=claude_common.CLAUDE_TIMEOUT, tag="mise"
        )
        output = (result.stdout or "").strip() if result is not None else ""
        normalized_output = output.lower().replace("\\", "/")
        package_slug = package_name.lower().removeprefix("@").replace("/", "-")
        return bool(
            result is not None
            and result.returncode == 0
            and (package_name.lower() in normalized_output or package_slug in normalized_output)
            and _is_relative_to(_safe_resolve(Path(output)), package_real)
        )
    return False


def _process_matches(cli_name: str, tokens: set[str], haystacks: list[str]) -> bool:
    haystacks = [value.replace("\\", "/") for value in haystacks]
    tokens = {token.replace("\\", "/") for token in tokens}
    executable_names = {cli_name, f"{cli_name}.exe"}
    if Path(haystacks[0]).name in executable_names or Path(haystacks[1]).name in executable_names:
        return True
    if Path(haystacks[0]).name not in {"node", "node.exe", "mise", "mise.exe", cli_name, f"{cli_name}.exe"}:
        return False
    return any(token in value for token in tokens for value in haystacks)


def _cmd_referenced_paths(content: str, launcher_dir: Path) -> Iterable[Path]:
    for raw in re.findall(r'(?:"([^"]+)"|([^\s"]+))', content):
        token = raw[0] or raw[1]
        normalized = token.replace("%~dp0", str(launcher_dir) + os.sep).replace("%dp0%", str(launcher_dir) + os.sep)
        normalized = normalized.replace("\\", os.sep)
        candidate = Path(normalized)
        if not candidate.is_absolute():
            candidate = launcher_dir / candidate
        yield candidate


def _package_bin_entrypoint(package_dir: Path, cli_name: str) -> Path | None:
    try:
        metadata = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    bin_value = metadata.get("bin")
    relative: object
    if isinstance(bin_value, str):
        relative = bin_value
    elif isinstance(bin_value, dict):
        relative = bin_value.get(cli_name)
    else:
        return None
    if not isinstance(relative, str) or not relative:
        return None
    entrypoint = _safe_resolve(package_dir / relative)
    package_real = _safe_resolve(package_dir)
    return entrypoint if entrypoint.is_file() and _is_relative_to(entrypoint, package_real) else None


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
