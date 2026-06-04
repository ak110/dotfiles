r"""メディアリモコン自動起動セットアップ（Windows / ホスト名sthenoのみ）。

Windowsスタートアップフォルダー
（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`）配下に
`dotfiles-media-remote.lnk`を冪等配置する。

ターゲットはuv tool venvの`pythonw.exe`（コンソールウィンドウ抑止のため）、
引数は`-m pytools.media_remote serve`。
sthenoホスト以外では既存ショートカットを削除する。
"""

import logging
import os
import pathlib
import shutil
import socket
import sys

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

# 自動起動対象ホスト名（大文字小文字無視で比較する）。
TARGET_HOST = "stheno"
LNK_NAME = "dotfiles-media-remote.lnk"
PYTHON_ARGS = "-m pytools.media_remote serve"


def run() -> bool:
    """sthenoの場合のみショートカット配置、それ以外では既存ショートカットを削除する。

    Returns:
        ショートカットを新規作成・更新・削除した場合True、それ以外はFalse。
    """
    if sys.platform != "win32":
        return False

    startup_dir = _startup_dir()
    if not startup_dir.is_dir():
        logger.info(log_format.format_status("media-remote", f"スタートアップ未存在: {startup_dir}"))
        return False
    lnk = startup_dir / LNK_NAME

    if socket.gethostname().lower() != TARGET_HOST:
        return _ensure_absent(lnk)

    pythonw = _find_pythonw()
    if pythonw is None:
        logger.info(log_format.format_status("media-remote", "pythonw.exeが見つからないためスキップ"))
        return False
    return _ensure_shortcut(lnk, pythonw)


def _startup_dir() -> pathlib.Path:
    """スタートアップフォルダーのパスを返す。"""
    appdata = os.environ.get("APPDATA")
    base = pathlib.Path(appdata) if appdata else pathlib.Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _find_pythonw() -> pathlib.Path | None:
    """`pythonw.exe`をuv tool venv優先で解決し、なければ`shutil.which("pythonw")`にフォールバック。"""
    candidate = pathlib.Path.home() / ".local" / "share" / "uv" / "tools" / "pytools" / "Scripts" / "pythonw.exe"
    if candidate.is_file():
        return candidate
    fallback = shutil.which("pythonw")
    if fallback:
        return pathlib.Path(fallback)
    return None


def _ensure_absent(lnk: pathlib.Path) -> bool:
    """対象外ホストでは既存ショートカットを削除する。"""
    if not lnk.is_file():
        return False
    try:
        lnk.unlink()
        logger.info(log_format.format_status("media-remote", f"対象外ホストのため削除: {lnk}"))
    except OSError as e:
        logger.warning(log_format.format_status("media-remote", f"削除失敗: {e}"))
        return False
    return True


def _ensure_shortcut(lnk: pathlib.Path, target: pathlib.Path) -> bool:
    """ショートカットを冪等配置する。"""
    if _is_up_to_date(lnk, target):
        return False
    if _create_shortcut(lnk, target):
        logger.info(log_format.format_status("media-remote", f"ショートカット配置: {lnk}"))
        return True
    return False


def _is_up_to_date(lnk: pathlib.Path, target: pathlib.Path) -> bool:
    """既存`.lnk`のTargetPath/Argumentsが期待値と一致するか判定する。"""
    if not lnk.is_file():
        return False
    actual = _read_shortcut(lnk)
    if actual is None:
        return False
    actual_target, actual_args = actual
    # Windowsのファイルシステムはcase-insensitiveのため大小文字を揃えて比較する。
    return actual_target.lower() == str(target).lower() and actual_args == PYTHON_ARGS


def _read_shortcut(lnk: pathlib.Path) -> tuple[str, str] | None:
    """PowerShellで`WScript.Shell` COM経由でTargetPath/Argumentsを取得する。

    タブ文字（`[char]9`）をフィールド区切りに使う。
    Arguments・TargetPathにはタブが現れないため曖昧化しない。
    """
    sep = "\t"
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(lnk))}'); "
        "[Console]::Out.Write($s.TargetPath); "
        "[Console]::Out.Write([char]9); "
        "[Console]::Out.Write($s.Arguments)"
    )
    result = claude_common.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30.0,
        tag="media-remote",
    )
    if result is None or result.returncode != 0:
        return None
    parts = result.stdout.split(sep, 1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def _create_shortcut(lnk: pathlib.Path, target: pathlib.Path) -> bool:
    """PowerShellで`WScript.Shell` COM経由でショートカットを生成・上書きする。"""
    home = str(pathlib.Path.home())
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(lnk))}'); "
        f"$s.TargetPath = '{_ps_escape(str(target))}'; "
        f"$s.Arguments = '{_ps_escape(PYTHON_ARGS)}'; "
        f"$s.WorkingDirectory = '{_ps_escape(home)}'; "
        "$s.WindowStyle = 7; "
        "$s.Save()"
    )
    result = claude_common.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30.0,
        tag="media-remote",
    )
    if result is None or result.returncode != 0:
        logger.warning(log_format.format_status("media-remote", f"ショートカット生成に失敗: {lnk}"))
        return False
    return True


def _ps_escape(value: str) -> str:
    """PowerShellシングルクオート文字列内のエスケープ（`'`は`''`で表現する）。"""
    return value.replace("'", "''")
