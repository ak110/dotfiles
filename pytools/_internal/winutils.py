"""Windows 専用のユーティリティ関数。"""

import logging
import typing

logger = logging.getLogger(__name__)


def import_winreg() -> typing.Any:
    """Winreg モジュールを Any 型で読み込む。

    winreg は Windows 専用の標準モジュールで、Linux 上で pyright を実行すると
    全属性アクセスが `reportAttributeAccessIssue` として検出されてしまう。
    実行は Windows 限定のため、`Any` 経由でアクセスするのが最も簡潔。
    """
    import importlib  # noqa: PLC0415

    return importlib.import_module("winreg")


def read_user_env_var(name: str) -> tuple[str | None, int]:
    r"""`HKCU\\Environment` からユーザー環境変数を読み出す。

    戻り値は `(値, 値型)` のタプル。値が存在しない場合は `(None, winreg.REG_SZ)`。
    """
    wr = import_winreg()
    with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_READ) as key:
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
    """Explorer 等に環境変数変更を通知する (`WM_SETTINGCHANGE` / `Environment`)。

    `ctypes.windll` は Windows 専用で、Linux で pyright/ty などの型チェッカに
    かけると `ctypes has no member windll` と誤検出される。getattr 経由で
    取得して型チェック対象から外す（実行は Windows でのみ）。

    通知失敗は致命ではないため、例外は吸収してログ出力に留める。
    """
    try:
        import ctypes  # noqa: PLC0415

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


def append_user_path(entry: str) -> None:
    """ユーザースコープの PATH 環境変数に `entry` を追記する（重複は追加しない）。"""
    current, reg_type = read_user_env_var("Path")
    if current is None:
        current = ""
        reg_type = import_winreg().REG_EXPAND_SZ
    entries = [e for e in current.split(";") if e]
    if entry in entries:
        return
    entries.append(entry)
    write_user_env_var("Path", ";".join(entries), reg_type)
