"""Windows 専用のユーティリティ関数。

Windows専用標準モジュール（winreg・msvcrt・ctypes.windll等）はLinux上の型チェッカ
（pyright・ty・mypy）が属性アクセスを`reportAttributeAccessIssue`等として誤検出するため、
`importlib.import_module()`経由で`typing.Any`型として取り扱う方針とする。
実行はWindows限定のため、`Any`型で動的アクセスして型解析を回避する。
"""

import logging
import typing

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
        logger.info("環境変数変更のブロードキャストに失敗: %s", e)


def append_user_path(entry: str) -> bool:
    """ユーザースコープのPATH環境変数に `entry` を追記する（重複は追加しない）。

    Returns:
        実際に追記した場合True、既に含まれていればFalse。
    """
    current, reg_type = read_user_env_var("Path")
    if current is None:
        current = ""
        reg_type = import_winreg().REG_EXPAND_SZ
    entries = [e for e in current.split(";") if e]
    if entry in entries:
        return False
    entries.append(entry)
    write_user_env_var("Path", ";".join(entries), reg_type)
    return True
