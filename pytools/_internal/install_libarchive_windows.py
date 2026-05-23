"""Windows向けlibarchive.dllインストーラー。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
MSYS2リポジトリからlibarchiveと動的依存パッケージのDLLを取得・配置する。
"""

import ctypes
import ctypes.util
import io
import logging
import pathlib
import re
import sys
import tarfile

from pytools._internal import log_format, winutils
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

# MSYS2 の mingw64 リポジトリ。カーリング追跡が容易なように定数化する。
_MSYS2_REPO = "https://repo.msys2.org/mingw/mingw64/"

# libarchive 本体と動的依存パッケージの prefix 一覧。
# ファイル名は `<prefix>-<version>-<rel>-any.pkg.tar.zst` の形式で、
# リポジトリのディレクトリインデックスから最新版を動的に発見する。
_REQUIRED_PACKAGES = [
    "mingw-w64-x86_64-libarchive",
    "mingw-w64-x86_64-bzip2",
    "mingw-w64-x86_64-xz",
    "mingw-w64-x86_64-zstd",
    "mingw-w64-x86_64-libxml2",
    "mingw-w64-x86_64-libiconv",
    "mingw-w64-x86_64-libb2",
    "mingw-w64-x86_64-zlib",
    "mingw-w64-x86_64-expat",
    "mingw-w64-x86_64-lz4",
    "mingw-w64-x86_64-openssl",
]

_INSTALL_DIR = pathlib.Path.home() / ".local" / "lib" / "libarchive"
_HTTP_TIMEOUT = 60.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """libarchive.dllを配置する（Windowsのみ）。

    DLLダウンロードはべき等とし、既に配置済みの場合はスキップする。
    ``LIBARCHIVE`` 環境変数の永続化は、DLLの有無にかかわらず毎回実施する。
    libarchive-cはWindowsでDLLを解決する際に ``LIBARCHIVE`` 環境変数を最優先で
    参照するため、DLLだけ配置されて環境変数が未設定の環境でも正しく動作させるためのワークアラウンドである。

    Returns:
        DLLの新規ダウンロードまたは環境変数の書き換えを1つでも行った場合True。
    """
    if sys.platform != "win32":
        return False
    changed = False
    try:
        if _is_already_available():
            logger.info(log_format.format_status("libarchive", f"既に利用可能 ({_INSTALL_DIR})"))
        else:
            # Windows専用処理の関数内ローカル依存のため遅延import。
            import httpx  # pylint: disable=import-outside-toplevel
            import zstandard  # pylint: disable=import-outside-toplevel

            _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
                index_html = client.get(_MSYS2_REPO).text
                for prefix in _REQUIRED_PACKAGES:
                    filename = _pick_latest(index_html, prefix)
                    if filename is None:
                        logger.info(
                            log_format.format_status(
                                "libarchive", f"{prefix} の最新版を MSYS2 リポジトリから検出できませんでした"
                            )
                        )
                        return False
                    logger.info(log_format.format_status("libarchive", f"downloading {filename}"))
                    data = client.get(f"{_MSYS2_REPO}{filename}").content
                    _extract_dlls(data, zstandard)
            winutils.append_user_path(str(_INSTALL_DIR))
            logger.info(log_format.format_status("libarchive", f"インストール完了: {_INSTALL_DIR}"))
            changed = True
        if _persist_libarchive_env_var():
            changed = True
        return changed
    except Exception as e:  # noqa: BLE001
        logger.info(log_format.format_status("libarchive", f"自動インストールに失敗 (手動インストール推奨): {e}"))
        return False


def _is_already_available() -> bool:
    """libarchive.dll が既に解決可能か判定する。"""
    if ctypes.util.find_library("archive"):
        return True
    return (_INSTALL_DIR / "libarchive-13.dll").exists() or (_INSTALL_DIR / "archive.dll").exists()


def _pick_latest(index_html: str, prefix: str) -> str | None:
    """リポジトリのディレクトリインデックスHTMLから最新のpkg.tar.zstファイル名を返す。

    MSYS2は同じprefixで複数バージョンを保持することがあるため、
    正規表現で該当するファイル名を全件抽出してから文字列順で最大を取る
    （ファイル名にバージョンが含まれ昇順性が期待できる）。
    """
    escaped = re.escape(prefix)
    pattern = re.compile(rf'href="({escaped}-[^"]+\.pkg\.tar\.zst)"')
    matches = pattern.findall(index_html)
    if not matches:
        return None
    return max(matches)


def _extract_dlls(pkg_data: bytes, zstandard_mod) -> None:
    """pkg.tar.zst のバイト列を展開し、DLL だけを `_INSTALL_DIR` へ配置する。"""
    decompressor = zstandard_mod.ZstdDecompressor()
    raw = decompressor.decompress(pkg_data, max_output_size=512 * 1024 * 1024)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            if not member.name.lower().endswith(".dll"):
                continue
            if "/bin/" not in member.name.replace("\\", "/"):
                continue
            dll_name = pathlib.PurePosixPath(member.name).name
            fp = tar.extractfile(member)
            if fp is None:
                continue
            (_INSTALL_DIR / dll_name).write_bytes(fp.read())


def _persist_libarchive_env_var() -> bool:
    """ユーザースコープの ``LIBARCHIVE`` 環境変数を永続化する。

    libarchive-cの ``ffi.py`` はDLL探索時にまず ``LIBARCHIVE`` 環境変数を参照する。
    `_INSTALL_DIR` 配下の ``libarchive-13.dll`` を直接指すことで、Windowsの
    ``find_library("archive")`` で解決できないファイル名でも解決できる。

    挙動:

    - DLLが未配置なら何もしない（``False`` を返す）。
    - 既に同じ値が設定されていればスキップする。
    - 既存値が別パスを指している場合は上書きせず警告ログのみ出力する
      （ユーザーが別途libarchiveを導入している可能性に配慮）。
    - 書き換えに成功したら ``True`` を返す。

    Returns:
        レジストリを実際に書き換えた場合に ``True``。
    """
    target_dll = _INSTALL_DIR / "libarchive-13.dll"
    if not target_dll.exists():
        return False
    target = str(target_dll)
    current, reg_type = winutils.read_user_env_var("LIBARCHIVE")
    if current is None:
        current = ""
        reg_type = winutils.import_winreg().REG_SZ
    if current == target:
        return False
    if current:
        logger.info(
            log_format.format_status(
                "libarchive",
                f"LIBARCHIVE は既に {current} を指しているため上書きしない",
            )
        )
        return False
    winutils.write_user_env_var("LIBARCHIVE", target, reg_type)
    logger.info(log_format.format_status("libarchive", f"LIBARCHIVE 環境変数を設定: {target}"))
    return True


if __name__ == "__main__":
    main()
