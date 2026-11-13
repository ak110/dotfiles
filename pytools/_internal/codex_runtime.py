"""導入済みのCodex公式実行体を選ぶ。

非対話の公開コマンドは`bin/codex`経由でhookから繰り返し起動されるため、
起動管理（`pytools/codex_launcher.py`）が実行体を選ぶ処理は復旧用の依存（psutil・pydanticなど）を読み込まない。
導入処理（`setup_codex_cli.py`）も同じ選択を使い、導入先と起動先の判定を1か所に保つ。
"""

import errno
import ntpath
import os
import stat
import sys
from pathlib import Path, PureWindowsPath

from pytools._internal import common


def codex_home() -> Path:
    """公式インストーラーと同じ実効CODEX_HOMEを返す。"""
    value = os.environ.get("CODEX_HOME")
    return Path(value) if value else Path.home() / ".codex"


def standalone_root() -> Path:
    """スタンドアローン版のパッケージキャッシュのルートを返す。"""
    return codex_home() / "packages" / "standalone"


def visible_bin_dir() -> Path:
    """公式インストーラーが可視コマンドを置くディレクトリを返す。"""
    value = os.environ.get("CODEX_INSTALL_DIR")
    if value:
        return Path(value)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin"
    return Path.home() / ".local" / "bin"


def wrapper_launchers() -> set[Path]:
    """dotfilesが配置したCodexの起動管理（`bin/codex`と`bin/codex.cmd`）を返す。"""
    root = common.find_dotfiles_root()
    return {root / "bin" / name for name in ("codex", "codex.cmd")} if root is not None else set()


def _comparison_path(path: Path) -> PureWindowsPath:
    """実体検証後のWindowsパスをnamespace表記に依存せず比較する。"""
    value = str(path)
    if value[:8].lower() == "\\\\?\\unc\\":
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return PureWindowsPath(ntpath.normpath(value))


def find_standalone_launcher() -> Path | None:
    """管理対象のスタンドアローン版ランチャーを返す。

    公式インストーラーが導入済み版の判定に使う`current/bin/codex`と`current/codex`を同じ順で確認する。
    """
    if sys.platform == "win32":
        return _find_windows_standalone_launcher(standalone_root())
    current = standalone_root() / "current"
    return next((candidate for candidate in (current / "bin/codex", current / "codex") if candidate.is_file()), None)


def _find_windows_standalone_launcher(root: Path) -> Path | None:
    """指定した公式packageルートのcurrentから検証済み実体を選ぶ。"""
    current = root / "current"
    try:
        try:
            stored = current.readlink()
        except OSError as error:
            if error.errno != errno.EINVAL:
                raise
            stored = None
        if stored is None:
            # 旧公式配置はcurrent自体が通常ディレクトリで、直下にも本体を置く。
            if not stat.S_ISDIR(current.lstat().st_mode):
                return None
            current = current.resolve(strict=True)
            if _comparison_path(current.parent) != _comparison_path(root.resolve(strict=True)):
                return None
        else:
            current = stored if stored.is_absolute() else current.parent / stored
            current = current.resolve(strict=True)
            releases = (root / "releases").resolve(strict=True)
            selected_path = _comparison_path(current)
            releases_path = _comparison_path(releases)
            if not selected_path.is_relative_to(releases_path) or selected_path == releases_path:
                return None
    except OSError as error:
        if error.errno not in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            raise
        return None
    except RuntimeError:
        return None
    for candidate in (current / "bin/codex.exe", current / "codex.exe"):
        try:
            executable = candidate.resolve(strict=True)
            if _comparison_path(executable).is_relative_to(_comparison_path(current)) and stat.S_ISREG(
                executable.stat().st_mode
            ):
                return executable
        except OSError as error:
            if error.errno not in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                raise
            continue
        except RuntimeError:
            continue
    return None


def _find_visible_windows_launcher() -> Path | None:
    """可視binの格納先を公式packageのcurrentと照合し、別設定ホームからも選ぶ。"""
    visible = visible_bin_dir()
    try:
        stored = visible.readlink()
    except OSError as error:
        if error.errno not in {errno.ENOENT, errno.ENOTDIR, errno.EINVAL, errno.ELOOP}:
            raise
        return None
    try:
        target = stored if stored.is_absolute() else visible.parent / stored
        # 任意PATHや単なるexeの存在を信頼せず、公式配置とactive currentの両方を要求する。
        for root in target.parents:
            if root.name.lower() != "standalone" or root.parent.name.lower() != "packages":
                continue
            launcher = _find_windows_standalone_launcher(root)
            if launcher is not None:
                alias = root / "current" / "bin" if launcher.parent.name.lower() == "bin" else root / "current"
                if _comparison_path(target) == _comparison_path(alias) or _comparison_path(
                    target.resolve(strict=True)
                ) == _comparison_path(launcher.parent):
                    return launcher
            return None
    except OSError as error:
        if error.errno not in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
            raise
    except RuntimeError:
        return None
    return None


def find_runtime_launcher(*, windows: bool) -> Path | None:
    """Windowsでは所有currentの実体、POSIXでは既存のnative参照を選ぶ。"""
    if windows:
        return find_standalone_launcher() or _find_visible_windows_launcher()
    wrappers = wrapper_launchers()
    directories = [
        visible_bin_dir(),
        *(Path(entry) for entry in os.environ.get("PATH", "").split(";" if windows else os.pathsep) if entry),
    ]
    for directory in dict.fromkeys(directories):
        reference = directory / ".dotfiles-codex-native"
        candidates = [directory / "codex.exe", directory / "codex.cmd"] if windows else [reference, directory / "codex"]
        for candidate in candidates:
            if candidate == reference and not reference.is_symlink():
                continue
            if candidate.is_file() and not any(candidate.samefile(wrapper) for wrapper in wrappers if wrapper.is_file()):
                return candidate
    candidate = find_standalone_launcher()
    if candidate is not None and not any(candidate.samefile(wrapper) for wrapper in wrappers if wrapper.is_file()):
        return candidate
    return None
