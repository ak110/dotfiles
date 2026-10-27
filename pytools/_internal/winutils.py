"""Windows 専用のユーティリティ関数。

Windows専用標準モジュール（winreg・msvcrt・ctypes.windll等）はLinux上の型チェッカ
（pyright・ty・mypy）が属性アクセスを`reportAttributeAccessIssue`等として誤検出するため、
`importlib.import_module()`経由で`typing.Any`型として取り扱う方針とする。
実行はWindows限定のため、`Any`型で動的アクセスして型解析を回避する。
"""

import logging
import ntpath
import typing
from pathlib import PureWindowsPath

logger = logging.getLogger(__name__)


def import_winreg() -> typing.Any:
    """winregモジュールをAny型で読み込む。詳細はモジュールdocstring参照。"""
    import importlib  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return importlib.import_module("winreg")


def import_msvcrt() -> typing.Any:
    """msvcrtモジュールをAny型で読み込む。詳細はモジュールdocstring参照。"""
    import importlib  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return importlib.import_module("msvcrt")


def read_user_env_var(name: str) -> tuple[str | None, int]:
    r"""`HKCU\\Environment` からユーザー環境変数を読み取る。

    戻り値は `(値, 値型)` のタプル。値が存在しない場合は `(None, winreg.REG_SZ)`。
    """
    wr = import_winreg()
    with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_READ) as key:
        try:
            value, reg_type = wr.QueryValueEx(key, name)
        except FileNotFoundError:
            return None, wr.REG_SZ
    return value, reg_type


# システム側環境変数の格納先。Windows のセッションマネージャーが起動時に参照する。
_SYSTEM_ENV_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


def read_system_env_var(name: str) -> tuple[str | None, int]:
    r"""`HKLM\\SYSTEM\\...\\Environment` からシステム環境変数を読み取る。

    戻り値は `(値, 値型)` のタプル。値が存在しない場合は `(None, winreg.REG_SZ)`。
    通常ユーザーでも読み取りは可能（書き込みは管理者権限が必要）。
    """
    wr = import_winreg()
    with wr.OpenKey(wr.HKEY_LOCAL_MACHINE, _SYSTEM_ENV_KEY, 0, wr.KEY_READ) as key:
        try:
            value, reg_type = wr.QueryValueEx(key, name)
        except FileNotFoundError:
            return None, wr.REG_SZ
    return value, reg_type


def write_user_env_var(name: str, value: str, reg_type: int) -> None:
    r"""`HKCU\\Environment` へユーザー環境変数を書き込む。"""
    wr = import_winreg()
    with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_SET_VALUE) as key:
        wr.SetValueEx(key, name, 0, reg_type, value)


def broadcast_environment_change() -> None:
    """Explorer等に環境変数変更を通知する（`WM_SETTINGCHANGE` / `Environment`）。

    `ctypes.windll`はWindows専用属性のためモジュールdocstringの方針に従いgetattrで取得する。
    通知失敗は致命的でないため、例外は吸収してログ出力に留める。
    """
    try:
        import ctypes  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        hwnd_broadcast = 0xFFFF
        wm_settingchange = 0x001A
        smto_abortifhung = 0x0002
        result = ctypes.c_long(0)
        windll = getattr(ctypes, "windll")  # noqa: B009
        windll.user32.SendMessageTimeoutW(
            hwnd_broadcast,
            wm_settingchange,
            0,
            "Environment",
            smto_abortifhung,
            5000,
            ctypes.byref(result),
        )
    except Exception as e:  # noqa: BLE001 -- 通知失敗は致命ではない
        logger.info("環境変数変更のブロードキャストに失敗: %s。変更を反映するため新しい端末を開き直す", e)


def _load_kernel32(ctypes_module: typing.Any) -> typing.Any:
    """Win32 APIを最後のエラーコードを保持する設定で取得する。"""
    return ctypes_module.WinDLL("kernel32", use_last_error=True)


def set_link_times(path: str, timestamp: float) -> None:
    """Windowsのリンク自身の atime/mtime を設定する。"""
    import ctypes  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    # Windows専用属性は、モジュールの方針に従って動的な型として扱う。
    windows_ctypes = typing.cast(typing.Any, ctypes)

    class _FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

    filetime_value = int((timestamp + 11_644_473_600) * 10_000_000)
    filetime = _FileTime(filetime_value & 0xFFFFFFFF, filetime_value >> 32)
    file_write_attributes = 0x00000100
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000

    # リパースポイントを開くことで、参照先でなくリンク自身へ時刻を設定する。
    kernel32 = _load_kernel32(ctypes)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        path,
        file_write_attributes,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())

    set_file_time = kernel32.SetFileTime
    set_file_time.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_file_time.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    try:
        if not set_file_time(handle, None, ctypes.byref(filetime), ctypes.byref(filetime)):
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
    finally:
        close_handle(handle)


def path_key(entry: str) -> str:
    """PATHのエントリーを、登録済みかどうかの比較に使う正規化キーへ変換する。

    `ntpath.expandvars`で環境変数を展開し`PureWindowsPath`を通したうえで小文字化し、
    `%USERPROFILE%`表記と展開済みの表記、区切り文字の向き、末尾の区切り、大文字小文字の差を吸収する。
    展開後が空のエントリーは空文字を返す。
    """
    expanded = ntpath.expandvars(entry)
    if not expanded:
        return ""
    return str(PureWindowsPath(expanded)).lower()


def append_user_path(entry: str) -> bool:
    """ユーザースコープのPATH環境変数の末尾へ`entry`を追記する。

    ユーザー側またはシステム側に`path_key`が同じエントリーが1件でもあれば登録済みとして書き込まない。
    PATHへ登録する処理は、独自の比較を持たずに本関数を使う。比較の方法が処理ごとに異なると、
    登録する処理とユーザーPATHを整理する処理（`cleanup_user_path`）が実行のたびに同じ値を書き換え合う。
    `%`を含む値を書き込む場合、既存の値型がREG_SZならREG_EXPAND_SZへ改めて展開させる。

    Returns:
        実際に追記した場合True、既に含まれていればFalse。
    """
    wr = import_winreg()
    current, reg_type = read_user_env_var("Path")
    if current is None:
        current = ""
        reg_type = wr.REG_EXPAND_SZ
    system_value, _ = read_system_env_var("Path")
    entries = [e for e in current.split(";") if e]
    registered = {path_key(e) for e in [*entries, *(system_value or "").split(";")] if e}
    if path_key(entry) in registered:
        return False
    entries.append(entry)
    new_value = ";".join(entries)
    if "%" in new_value and reg_type == wr.REG_SZ:
        reg_type = wr.REG_EXPAND_SZ
    write_user_env_var("Path", new_value, reg_type)
    return True
