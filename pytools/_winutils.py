"""Windows 専用のユーティリティ関数。"""

import typing


def import_winreg() -> typing.Any:
    """Winreg モジュールを Any 型で読み込む。

    winreg は Windows 専用の標準モジュールで、Linux 上で pyright を実行すると
    全属性アクセスが `reportAttributeAccessIssue` として検出されてしまう。
    実行は Windows 限定のため、`Any` 経由でアクセスするのが最も簡潔。
    """
    import importlib  # noqa: PLC0415

    return importlib.import_module("winreg")


def append_user_path(entry: str) -> None:
    """ユーザースコープの PATH 環境変数に `entry` を追記する（重複は追加しない）。"""
    wr = import_winreg()
    with wr.OpenKey(wr.HKEY_CURRENT_USER, "Environment", 0, wr.KEY_READ | wr.KEY_WRITE) as key:
        try:
            current, reg_type = wr.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, reg_type = "", wr.REG_EXPAND_SZ
        entries = [e for e in current.split(";") if e]
        if entry in entries:
            return
        entries.append(entry)
        wr.SetValueEx(key, "Path", 0, reg_type, ";".join(entries))
