"""導入済みのCodex公式実行体を選ぶ。

非対話の公開コマンドは`bin/codex`経由でhookから繰り返し起動されるため、
起動管理（`pytools/codex_launcher.py`）が実行体を選ぶ処理は復旧用の依存（psutil・pydanticなど）を読み込まない。
導入処理（`setup_codex_cli.py`）も同じ選択を使い、導入先と起動先の判定を1か所に保つ。
"""

import os
import sys
from pathlib import Path

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


def find_standalone_launcher() -> Path | None:
    """管理対象のスタンドアローン版ランチャーを返す。

    公式インストーラーが導入済み版の判定に使う`current/bin/codex`と`current/codex`を同じ順で確認する。
    """
    current = standalone_root() / "current"
    name = "codex.exe" if sys.platform == "win32" else "codex"
    return next((candidate for candidate in (current / "bin" / name, current / name) if candidate.is_file()), None)


def find_runtime_launcher(*, windows: bool) -> Path | None:
    """設定ホームと独立した可視binからnativeを選び、自身への再帰起動を除く。"""
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
