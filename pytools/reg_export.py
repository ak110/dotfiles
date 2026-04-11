"""Windows レジストリキーを .reg 形式でエクスポートする (C# regExport の Python 移植)。

元 C# 実装は本処理が未実装のため、本移植版で新規実装している。Windows 以外の
環境で起動された場合は起動直後にエラー終了する (非 Windows では意味がないため)。
"""

import argparse
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)

_HIVES = {
    "HKCR": "HKEY_CLASSES_ROOT",
    "HKCU": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKU": "HKEY_USERS",
    "HKCC": "HKEY_CURRENT_CONFIG",
}


def _main() -> None:
    if sys.platform != "win32":
        print("reg-export は Windows 専用です", file=sys.stderr)
        sys.exit(1)
    parser = argparse.ArgumentParser(description="レジストリキーを .reg 形式でエクスポートする")
    parser.add_argument("key", help="例: HKCU\\Software\\MyApp")
    parser.add_argument("-o", "--output", type=pathlib.Path, help="出力先 (.reg)。未指定なら stdout")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    text = export_key(args.key)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-16")


def export_key(key_path: str) -> str:
    """指定キー配下を Windows Registry Editor 互換の .reg テキストへ変換する。"""
    import importlib

    winreg = importlib.import_module("winreg")

    if "\\" not in key_path:
        raise ValueError(f"ハイブ名付きのパスを指定してください: {key_path}")
    hive_alias, sub = key_path.split("\\", 1)
    hive_name = _HIVES.get(hive_alias.upper())
    if hive_name is None:
        raise ValueError(f"未知のハイブ: {hive_alias}")
    hive_constant = getattr(winreg, hive_name)
    lines = ["Windows Registry Editor Version 5.00", ""]
    _dump_key(winreg, hive_constant, sub, f"{hive_name}\\{sub}", lines)
    return "\r\n".join(lines) + "\r\n"


def _dump_key(winreg_mod, root, sub: str, display: str, lines: list[str]) -> None:
    with winreg_mod.OpenKey(root, sub, 0, winreg_mod.KEY_READ) as key:
        lines.append(f"[{display}]")
        i = 0
        while True:
            try:
                name, value, vtype = winreg_mod.EnumValue(key, i)
            except OSError:
                break
            lines.append(f'"{name}"={_format_value(value, vtype, winreg_mod)}')
            i += 1
        lines.append("")
        j = 0
        while True:
            try:
                child = winreg_mod.EnumKey(key, j)
            except OSError:
                break
            _dump_key(winreg_mod, root, f"{sub}\\{child}", f"{display}\\{child}", lines)
            j += 1


def _format_value(value, vtype: int, winreg_mod) -> str:
    if vtype == winreg_mod.REG_SZ:
        return f'"{value}"'
    if vtype == winreg_mod.REG_DWORD:
        return f"dword:{value:08x}"
    return f"hex({vtype}):{value!r}"  # 簡易表現


if __name__ == "__main__":
    _main()
