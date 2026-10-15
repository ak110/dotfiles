"""公式Codexパッケージの選択中・使用中の版を保ち、完成済み旧版を整理する。"""

import collections.abc
import contextlib
import ctypes
import dataclasses
import json
import logging
import os
import pathlib
import re
import shutil
import sys

import psutil

from pytools._internal import codex_processes, post_apply_outcome

logger = logging.getLogger(__name__)
_PACKAGES = ("standalone", "app-server-daemon")
_RELEASE_NAME = re.compile(
    r"(?:\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?|local-[0-9a-f]+)-"
    r"(?:x86_64|aarch64)-(?:unknown-linux-(?:musl|gnu)|apple-darwin|pc-windows-msvc)"
)
_NEXT_ACTION = "原因を解消し、`prune-codex-packages`を再実行する"


@dataclasses.dataclass
class PruneResult:
    """削除・保持理由・延期・失敗を呼び出し元へ渡す。"""

    removed: list[pathlib.Path] = dataclasses.field(default_factory=list)
    retained: dict[pathlib.Path, list[str]] = dataclasses.field(default_factory=dict)
    deferred: list[str] = dataclasses.field(default_factory=list)
    failures: list[str] = dataclasses.field(default_factory=list)


class _DeferredError(RuntimeError):
    """使用版の保護に必要な情報または排他を取得できない。"""


def codex_home() -> pathlib.Path:
    """公式インストーラーと同じ実効CODEX_HOMEを返す。"""
    value = os.environ.get("CODEX_HOME")
    return pathlib.Path(value) if value else pathlib.Path.home() / ".codex"


def prune(*, home: pathlib.Path | None = None, windows: bool | None = None, dry_run: bool = False) -> PruneResult:
    """両パッケージを公式排他の内側で整理する。延期は再実行可能な成功、削除失敗はfailureとする。"""
    result = PruneResult()
    packages = (home if home is not None else codex_home()).absolute() / "packages"
    windows = sys.platform == "win32" if windows is None else windows
    for name in _PACKAGES:
        root = packages / name
        try:
            if not root.exists():
                if root.is_symlink():
                    raise _DeferredError("パッケージのリンク先が不在")
                continue
            if any(path.is_symlink() or path.is_junction() for path in (packages, root, root / "releases")):
                raise _DeferredError("パッケージ配置が外部へのリンク")
            # CODEX_HOME自体がリンクの場合も、列挙と選択先を同じ実体パスで比較する。
            root = root.resolve(strict=True)
            with _install_lock(root, windows=windows):
                _prune_package(root, result, windows=windows, dry_run=dry_run)
        except (_DeferredError, OSError, UnicodeError, psutil.Error) as error:
            result.deferred.append(f"{root}: {error}。{_NEXT_ACTION}")
    return result


def run() -> post_apply_outcome.PostApplyOutcome:
    """post_applyから独立した整理ステップとして起動する。"""
    result = prune()
    for path in result.removed:
        logger.info("Codex旧パッケージを削除: %s", path)
    for message in result.deferred:
        logger.warning("Codexパッケージ整理を延期: %s", message)
    return post_apply_outcome.PostApplyOutcome(
        changed=bool(result.removed),
        failure=" / ".join(result.failures) if result.failures else None,
        next_action=_NEXT_ACTION if result.failures else None,
    )


def _prune_package(root: pathlib.Path, result: PruneResult, *, windows: bool, dry_run: bool) -> None:
    releases = root / "releases"
    entries = sorted(releases.iterdir())
    retained = _selected_releases(root)
    processes = codex_processes.codex_processes()
    identified = [process for process in processes if process.identified]
    if windows and identified:
        raise _DeferredError("Codex実行中。Codexを終了して次回に整理する")
    for process in identified:
        if not process.executable:
            raise _DeferredError(f"Codexの実行パスを取得できない (pid {process.pid})")
        # Linuxの/procは削除済み実行体に接尾辞を付ける。実際に動く旧版へ対応付ける。
        executable = pathlib.Path(process.executable.removesuffix(" (deleted)"))
        try:
            relative = executable.resolve().relative_to(releases.resolve())
        except ValueError:
            continue
        if relative.parts:
            retained.setdefault(releases / relative.parts[0], []).append(f"使用中 pid {process.pid} ({process.role})")
    result.retained.update(retained)
    for release in entries:
        if release in retained:
            continue
        if not _complete_release(release, package=root.name):
            result.retained[release] = ["完成済み公式パッケージと確認できない"]
            continue
        if dry_run:
            result.removed.append(release)
            continue
        try:
            shutil.rmtree(release)
        except OSError as error:
            result.failures.append(f"{release}の削除に失敗: {error}")
        else:
            result.removed.append(release)


def _selected_releases(root: pathlib.Path) -> dict[pathlib.Path, list[str]]:
    releases = (root / "releases").resolve(strict=True)
    retained: dict[pathlib.Path, list[str]] = {}
    for name in ("current", ".migration-current"):
        path = root / name
        try:
            path.lstat()
        except FileNotFoundError:
            if name == "current":
                raise _DeferredError("currentが不在。公式導入をupdate-dotfilesで修復する") from None
            continue
        target = path.resolve(strict=True)
        if target.parent != releases or not target.is_dir():
            raise _DeferredError(f"{name}の選択先がreleases直下のディレクトリではない")
        if not _complete_release(target, package=root.name):
            raise _DeferredError(f"{name}の選択先が不完全。update-dotfilesで公式導入を修復する")
        retained.setdefault(target, []).append(name)
    marker = root / "auto-update-version"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return retained
    if not value or pathlib.PurePath(value).name != value or "\\" in value or value in {".", ".."}:
        raise _DeferredError("auto-update-versionの選択先が不正")
    target = releases / value
    if not target.is_dir() or target.is_symlink():
        raise _DeferredError("auto-update-versionの選択先が不在またはリンク")
    if not _complete_release(target, package=root.name):
        raise _DeferredError("auto-update-versionの選択先が不完全。update-dotfilesで公式導入を修復する")
    retained.setdefault(target, []).append("auto-update-version")
    return retained


def _complete_release(release: pathlib.Path, *, package: str) -> bool:
    # install.shのpackage/legacy-platform-npm配置とprepare_install.rsのlocalコピーを扱う。
    if release.is_symlink() or release.is_junction() or not release.is_dir() or not _RELEASE_NAME.fullmatch(release.name):
        return False
    if release.name.startswith("local-") and package != "app-server-daemon":
        return False
    suffix = ".exe" if "windows" in release.name else ""
    manifest = release / "codex-package.json"
    if manifest.is_file():
        try:
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError, UnicodeError:
            return False
        if not isinstance(metadata, dict):
            return False
        target, version = metadata.get("target"), metadata.get("version")
        if not isinstance(target, str) or not isinstance(version, str) or not version:
            return False
        if not release.name.endswith(f"-{target}") or metadata.get("entrypoint") != f"bin/codex{suffix}":
            return False
        if not release.name.startswith("local-") and release.name != f"{version}-{target}":
            return False
        required = (f"bin/codex{suffix}", f"bin/codex-code-mode-host{suffix}", f"codex-path/rg{suffix}")
        if not suffix and not release.name.startswith("local-"):
            required = (*required, "codex")
    else:
        if release.name.startswith("local-"):
            return False
        required = (f"codex{suffix}", f"codex-resources/rg{suffix}")
    if suffix:
        required = (*required, "codex-resources/codex-command-runner.exe", "codex-resources/codex-windows-sandbox-setup.exe")
    if "linux" in release.name:
        required = (*required, "codex-resources/bwrap")
    return all((release / path).is_file() and (suffix or os.access(release / path, os.X_OK)) for path in required)


@contextlib.contextmanager
def _install_lock(root: pathlib.Path, *, windows: bool) -> collections.abc.Generator[None]:
    """公式install.lockを共有不可オープンまたはflockで保持する。"""
    path = root / "install.lock"
    if path.is_symlink() or (root / "install.lock.d").exists():
        raise _DeferredError("公式install.lockが使用中またはリンク")
    if windows:
        # 公式install.ps1のFileShare.Noneと同じ共有モードを指定する。
        # Linuxのctypes型情報にないWindows専用APIは、このOS分岐内で使用する。
        # pylint: disable-next=no-member
        kernel32 = ctypes.WinDLL(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            "kernel32", use_last_error=True
        )
        create = kernel32.CreateFileW
        create.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create.restype = ctypes.c_void_p
        handle = create(str(path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            # pylint: disable-next=no-member
            error = ctypes.get_last_error()  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            raise _DeferredError(f"公式install.lockを取得できない (Windows error {error})")
        close = kernel32.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        try:
            yield
        finally:
            close(handle)
    else:
        import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        with path.open("a+b") as lock:
            lock_operation = fcntl.lockf if sys.platform == "darwin" and shutil.which("lockf") else fcntl.flock
            lock_operation(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
