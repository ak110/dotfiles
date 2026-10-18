"""libarchiveのDLL解決条件を、導入（`install_libarchive`）と消費（`repack_archive`）で共有する。

本体DLLの存在は、動的依存のDLLまで揃っていることを示さない。
導入済みの判定は、消費側と同じ探索条件で実際にロードできるかで行う。
"""

import ctypes
import ctypes.util
import os
import pathlib
import sys

INSTALL_DIR = pathlib.Path.home() / ".local" / "lib" / "libarchive"
DLL_NAME = "libarchive-13.dll"


def prepare_dll_search(install_dir: pathlib.Path | None = None) -> None:
    """Windowsで自動配置先の本体DLLを`LIBARCHIVE`とDLL探索ディレクトリへ加える。

    libarchive-cの`ffi.py`は`LIBARCHIVE`を最優先で参照し、無ければ`find_library("archive")`を使う。
    本体DLLは複数の依存DLLを持つため、同じディレクトリを`os.add_dll_directory`でDLL loaderへ渡す。
    既に`LIBARCHIVE`が設定されていれば上書きしない（別の導入先を保つ）。
    """
    if sys.platform != "win32":
        return
    directory = INSTALL_DIR if install_dir is None else install_dir
    dll = directory / DLL_NAME
    if dll.exists():
        os.environ.setdefault("LIBARCHIVE", str(dll))
        os.add_dll_directory(str(directory))


def library_path() -> str | None:
    """libarchive-cと同じ優先順で、ロードするDLLのパスか名前を返す。"""
    return os.environ.get("LIBARCHIVE") or ctypes.util.find_library("archive")


def _load_library(path: str) -> None:
    """libarchive-cと同じく`ctypes`でDLLを読み込む。依存DLLが欠けると`OSError`を送出する。"""
    ctypes.CDLL(path)


def is_loadable(install_dir: pathlib.Path | None = None) -> bool:
    """消費側と同じ探索条件でlibarchiveをロードできるかを返す。

    依存DLLが欠けた配置では本体が存在してもロードが失敗するため、偽を返す。
    """
    prepare_dll_search(install_dir)
    path = library_path()
    if not path:
        return False
    try:
        _load_library(path)
    except OSError:
        return False
    return True
