r"""Windows 向け libarchive.dll の自動インストーラー。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
MSYS2 リポジトリから libarchive と動的依存の pkg.tar.zst を取得し、必要な
DLL だけを `%USERPROFILE%\\.local\\lib\\libarchive\\` へ展開して User scope
の `PATH` へ追記する。既に導入済みの場合は何もしない。

`_install_claude_plugins.py` と同じ「想定される失敗は自前で吸収する」方針を
踏襲している。ネットワーク障害・MSYS2 側のレイアウト変更・権限不足などは
すべてログ警告を出したうえで False を返し、post_apply 全体は継続させる。
"""

import ctypes
import ctypes.util
import importlib
import io
import logging
import pathlib
import re
import sys
import tarfile
import typing

from pytools import _log_format

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


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run() -> bool:
    """libarchive.dll を導入する (Windows のみ)。

    DLL ダウンロードはべき等とし、既に配置済みの場合はスキップする。
    一方 ``LIBARCHIVE`` 環境変数の永続化は、DLL の有無にかかわらず毎回実施する。
    libarchive-c は Windows で DLL を解決する際 ``LIBARCHIVE`` 環境変数を最優先
    に見るため、旧バージョンの本モジュールが DLL だけ配置して環境変数を設定して
    いなかったケースを救う必要があるため。

    Returns:
        DLL の新規ダウンロードまたは環境変数の書き換えを 1 つでも行った場合 True。
    """
    if sys.platform != "win32":
        return False
    changed = False
    try:
        if _is_already_available():
            logger.info(_log_format.format_status("libarchive", f"既に利用可能 ({_INSTALL_DIR})"))
        else:
            import httpx
            import zstandard

            _INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            with httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
                index_html = client.get(_MSYS2_REPO).text
                for prefix in _REQUIRED_PACKAGES:
                    filename = _pick_latest(index_html, prefix)
                    if filename is None:
                        logger.info(
                            _log_format.format_status(
                                "libarchive", f"{prefix} の最新版を MSYS2 リポジトリから検出できませんでした"
                            )
                        )
                        return False
                    logger.info(_log_format.format_status("libarchive", f"downloading {filename}"))
                    data = client.get(f"{_MSYS2_REPO}{filename}").content
                    _extract_dlls(data, zstandard)
            _append_user_path(_INSTALL_DIR)
            logger.info(_log_format.format_status("libarchive", f"インストール完了: {_INSTALL_DIR}"))
            changed = True
        if _persist_libarchive_env_var():
            changed = True
        return changed
    except Exception as e:  # noqa: BLE001
        logger.info(_log_format.format_status("libarchive", f"自動インストールに失敗 (手動インストール推奨): {e}"))
        return False


def _is_already_available() -> bool:
    """libarchive.dll が既に解決可能か判定する。"""
    if ctypes.util.find_library("archive"):
        return True
    return (_INSTALL_DIR / "libarchive-13.dll").exists() or (_INSTALL_DIR / "archive.dll").exists()


def _pick_latest(index_html: str, prefix: str) -> str | None:
    """リポジトリのディレクトリインデックス HTML から最新の pkg.tar.zst ファイル名を選ぶ。

    MSYS2 は同じ prefix で複数バージョンを保持することがあるため、
    正規表現で該当するファイル名を全件抽出してから文字列順で最大を取る
    (ファイル名にバージョンが含まれ昇順性が期待できる)。
    """
    escaped = re.escape(prefix)
    pattern = re.compile(rf'href="({escaped}-[^"]+\.pkg\.tar\.zst)"')
    matches = pattern.findall(index_html)
    if not matches:
        return None
    return max(matches)


def _extract_dlls(pkg_data: bytes, zstandard_mod) -> None:
    """pkg.tar.zst のバイト列を展開し、DLL だけを `_INSTALL_DIR` へ書き出す。"""
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


def _import_winreg() -> typing.Any:
    """Windows 専用の winreg を Any 型で取り込む (`_setup_mise._import_winreg` と同方針)。"""
    return importlib.import_module("winreg")


def _append_user_path(directory: pathlib.Path) -> None:
    """User スコープの PATH 環境変数に `directory` を追記する (重複は追加しない)。"""
    wr = _import_winreg()
    key_path = "Environment"
    target = str(directory)
    with wr.OpenKey(wr.HKEY_CURRENT_USER, key_path, 0, wr.KEY_READ | wr.KEY_WRITE) as key:
        try:
            current, reg_type = wr.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, reg_type = "", wr.REG_EXPAND_SZ
        entries = [e for e in current.split(";") if e]
        if target in entries:
            return
        entries.append(target)
        wr.SetValueEx(key, "Path", 0, reg_type, ";".join(entries))


def _persist_libarchive_env_var() -> bool:
    """User スコープの ``LIBARCHIVE`` 環境変数を永続化する。

    libarchive-c の ``ffi.py`` は DLL 探索時にまず ``LIBARCHIVE`` 環境変数を参照する。
    `_INSTALL_DIR` 配下の ``libarchive-13.dll`` を直接指せば、Windows の
    ``find_library("archive")`` で解決できないファイル名でも解決できる。

    挙動:

    - DLL が未配置なら何もしない (false を返す)。
    - 既に同じ値が設定されていればスキップする。
    - 既存値が別パスを指している場合は上書きせず警告ログのみ出す
      (ユーザーが別途 libarchive を導入している可能性に配慮)。
    - 書き換えに成功したら true を返す。

    Returns:
        レジストリを実際に書き換えた場合に true。
    """
    target_dll = _INSTALL_DIR / "libarchive-13.dll"
    if not target_dll.exists():
        return False
    target = str(target_dll)
    wr = _import_winreg()
    with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_READ | wr.KEY_WRITE) as key:
        try:
            current, reg_type = wr.QueryValueEx(key, "LIBARCHIVE")
        except FileNotFoundError:
            current, reg_type = "", wr.REG_SZ
        if current == target:
            return False
        if current:
            logger.info(
                _log_format.format_status(
                    "libarchive",
                    f"LIBARCHIVE は既に {current} を指しているため上書きしない",
                )
            )
            return False
        wr.SetValueEx(key, "LIBARCHIVE", 0, reg_type, target)
    logger.info(_log_format.format_status("libarchive", f"LIBARCHIVE 環境変数を設定: {target}"))
    return True


if __name__ == "__main__":
    _main()
