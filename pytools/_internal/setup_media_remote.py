r"""メディアリモコン自動起動セットアップ（Windows / ホスト名sthenoのみ）。

Windowsスタートアップフォルダー
（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`）配下に
`dotfiles-media-remote.lnk`を冪等配置する。

`.lnk`は`wscript.exe`経由でVBSラッパー
（`%LOCALAPPDATA%\dotfiles\media-remote\launch.vbs`）を起動し、
VBSラッパーが`dotfiles-media-remote.exe serve`を非表示ウィンドウで実行する。
これによりコンソール窓・タスクバーアイコンを抑止しつつ、
uv tool venvの`dotfiles-media-remote.exe`を確実に解決する。
sthenoホスト以外では既存のショートカットとVBSラッパーを削除する。
"""

import logging
import os
import pathlib
import socket
import sys

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

# 自動起動対象ホスト名（大文字小文字無視で比較する）。
TARGET_HOST = "stheno"
LNK_NAME = "dotfiles-media-remote.lnk"
WSCRIPT_PATH = r"C:\Windows\System32\wscript.exe"


def run() -> bool:
    """sthenoの場合のみショートカット配置、それ以外では既存ショートカットを削除する。

    Returns:
        ショートカットやVBSラッパーを新規作成・更新・削除した場合True、それ以外はFalse。
    """
    if sys.platform != "win32":
        return False

    startup_dir = _startup_dir()
    if not startup_dir.is_dir():
        logger.info(log_format.format_status("media-remote", f"スタートアップ未存在: {startup_dir}"))
        return False
    lnk = startup_dir / LNK_NAME
    vbs = _vbs_path()

    if socket.gethostname().lower() != TARGET_HOST:
        return _ensure_absent(lnk, vbs)

    exe = _find_media_remote_exe()
    if exe is None:
        logger.info(log_format.format_status("media-remote", "dotfiles-media-remote.exeが見つからないためスキップ"))
        return False
    vbs_changed = _ensure_vbs(vbs, exe)
    lnk_changed = _ensure_shortcut(lnk, vbs)
    return vbs_changed or lnk_changed


def _startup_dir() -> pathlib.Path:
    """スタートアップフォルダーのパスを返す。"""
    appdata = os.environ.get("APPDATA")
    base = pathlib.Path(appdata) if appdata else pathlib.Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _vbs_path() -> pathlib.Path:
    r"""VBSラッパー配置先（`%LOCALAPPDATA%\dotfiles\media-remote\launch.vbs`）。"""
    local = os.environ.get("LOCALAPPDATA")
    base = pathlib.Path(local) if local else pathlib.Path.home() / "AppData" / "Local"
    return base / "dotfiles" / "media-remote" / "launch.vbs"


def _find_media_remote_exe() -> pathlib.Path | None:
    r"""`uv tool install`が生成する`dotfiles-media-remote.exe`の絶対パスを返す。

    `~\.local\bin\dotfiles-media-remote.exe`のみを参照し、`shutil.which`系の
    フォールバックは設けない（誤ったPythonを掴む事故の再発防止）。
    """
    candidate = pathlib.Path.home() / ".local" / "bin" / "dotfiles-media-remote.exe"
    if candidate.is_file():
        return candidate
    return None


def _ensure_absent(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    """対象外ホストでは既存のショートカットとVBSラッパーを削除する。"""
    changed = False
    for path in (lnk, vbs):
        if not path.is_file():
            continue
        try:
            path.unlink()
            logger.info(log_format.format_status("media-remote", f"対象外ホストのため削除: {path}"))
            changed = True
        except OSError as e:
            logger.warning(log_format.format_status("media-remote", f"削除失敗: {e}"))
    return changed


def _build_vbs_content(exe: pathlib.Path) -> str:
    """VBSラッパー本文を生成する。

    `WScript.Shell.Run`の第1引数（コマンド文字列）でexe絶対パスをダブルクオートで
    囲む。VBS文字列リテラル内のダブルクオートは`""`でエスケープする。
    `WindowStyle=0`で非表示、`bWaitOnReturn=False`で即時復帰する。
    """
    exe_str = str(exe).replace('"', '""')
    return f'CreateObject("WScript.Shell").Run """{exe_str}"" serve", 0, False\n'


def _ensure_vbs(vbs: pathlib.Path, exe: pathlib.Path) -> bool:
    """VBSラッパーを冪等配置する。既存内容が一致する場合は書き換えない。"""
    desired = _build_vbs_content(exe)
    if vbs.is_file() and vbs.read_text(encoding="utf-8") == desired:
        return False
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(desired, encoding="utf-8")
    logger.info(log_format.format_status("media-remote", f"VBSラッパー配置: {vbs}"))
    return True


def _ensure_shortcut(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    """ショートカットを冪等配置する。"""
    if _is_up_to_date(lnk, vbs):
        return False
    if _create_shortcut(lnk, vbs):
        logger.info(log_format.format_status("media-remote", f"ショートカット配置: {lnk}"))
        return True
    return False


def _shortcut_arguments(vbs: pathlib.Path) -> str:
    """`.lnk`のArguments文字列（VBSパスをダブルクオートで囲んだ単一引数）を返す。"""
    return f'"{vbs}"'


def _is_up_to_date(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    """既存`.lnk`のTargetPath/Argumentsが期待値と一致するか判定する。"""
    if not lnk.is_file():
        return False
    actual = _read_shortcut(lnk)
    if actual is None:
        return False
    actual_target, actual_args = actual
    expected_args = _shortcut_arguments(vbs)
    # Windowsのファイルシステムはcase-insensitiveのため大小文字を揃えて比較する。
    return actual_target.lower() == WSCRIPT_PATH.lower() and actual_args.lower() == expected_args.lower()


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


def _create_shortcut(lnk: pathlib.Path, vbs: pathlib.Path) -> bool:
    """PowerShellで`WScript.Shell` COM経由でショートカットを生成・上書きする。"""
    home = str(pathlib.Path.home())
    arguments = _shortcut_arguments(vbs)
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_ps_escape(str(lnk))}'); "
        f"$s.TargetPath = '{_ps_escape(WSCRIPT_PATH)}'; "
        f"$s.Arguments = '{_ps_escape(arguments)}'; "
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
