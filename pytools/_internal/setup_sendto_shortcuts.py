r"""SendTo フォルダーへのショートカット配置（Windowsのみ）。

Windowsの「送る」メニュー（`%APPDATA%\Microsoft\Windows\SendTo`配下）に
pytools 系コマンドへの `.lnk` ショートカットを冪等に配置する。
ターゲット未配置・SendTo フォルダー未存在の環境ではスキップする。
"""

import dataclasses
import logging
import os
import pathlib
import sys

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class _Shortcut:
    """SendTo 配置するショートカット 1 件の定義。"""

    lnk_name: str
    # %USERPROFILE% からの相対パス
    target_relative: pathlib.PurePath


_SHORTCUTS: list[_Shortcut] = [
    _Shortcut(
        lnk_name="TouchFile.lnk",
        target_relative=pathlib.PurePath(".local") / "bin" / "touch-file.exe",
    ),
]


def run() -> bool:
    """SendTo に対象ショートカットを冪等配置する。

    Returns:
        ショートカットを 1 件でも新規作成または更新した場合 True、
        非 Windows・SendTo 未存在・全件 no-op の場合 False。
    """
    if sys.platform != "win32":
        return False

    sendto_dir = _sendto_dir()
    if not sendto_dir.is_dir():
        logger.info(log_format.format_status("SendTo", f"配置先が存在しません: {sendto_dir}"))
        return False

    changed = False
    home = pathlib.Path.home()
    for shortcut in _SHORTCUTS:
        target = home / shortcut.target_relative
        lnk = sendto_dir / shortcut.lnk_name
        if not target.is_file():
            logger.info(log_format.format_status("SendTo", f"ターゲット未配置のためスキップ: {target}"))
            continue
        if _is_up_to_date(lnk, target):
            continue
        if _create_shortcut(lnk, target):
            logger.info(log_format.format_status("SendTo", f"ショートカット配置: {lnk}"))
            changed = True
    return changed


def _sendto_dir() -> pathlib.Path:
    """SendTo 配置先ディレクトリを返す。"""
    appdata = os.environ.get("APPDATA")
    base = pathlib.Path(appdata) if appdata else pathlib.Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "SendTo"


def _is_up_to_date(lnk: pathlib.Path, target: pathlib.Path) -> bool:
    """既存 .lnk のターゲットが期待値と一致するか判定する。"""
    if not lnk.is_file():
        return False
    actual = _read_shortcut_target(lnk)
    if actual is None:
        return False
    # Windows のファイルシステムは case-insensitive のため大小文字を揃えて比較する。
    return actual.lower() == str(target).lower()


def _read_shortcut_target(lnk: pathlib.Path) -> str | None:
    """PowerShell で `WScript.Shell` COM 経由で .lnk の TargetPath を読み取る。"""
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(lnk))}'); "
        "[Console]::Out.Write($s.TargetPath)"
    )
    result = claude_common.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30.0,
        tag="SendTo",
    )
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _create_shortcut(lnk: pathlib.Path, target: pathlib.Path) -> bool:
    """PowerShell で `WScript.Shell` COM 経由で .lnk を生成・上書きする。"""
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(lnk))}'); "
        f"$s.TargetPath = '{_ps_escape(str(target))}'; "
        "$s.Save()"
    )
    result = claude_common.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30.0,
        tag="SendTo",
    )
    if result is None or result.returncode != 0:
        logger.warning(log_format.format_status("SendTo", f"ショートカット生成に失敗: {lnk}"))
        return False
    return True


def _ps_escape(value: str) -> str:
    """PowerShell シングルクオート文字列内のエスケープ（`'` は `''` で表現する）。"""
    return value.replace("'", "''")
