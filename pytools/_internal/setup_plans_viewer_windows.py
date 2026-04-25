r"""claude-plans-viewer の Windows 向け自動起動セットアップ。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
ログオン時に viewer を自動起動させる `.cmd` をスタートアップフォルダーに
冪等に配置し、post_apply 実行時に viewer が未起動なら併せてデタッチ起動する。

追加依存 (pywin32 など) を避けるため、起動用 `.cmd` は環境変数を解釈せず
単純に viewer 実行ファイルを起動するだけの内容とし、ポート番号などの環境別
設定は `setx CLAUDE_PLANS_VIEWER_PORT ...` などユーザー側で管理する前提とする。

Linux/macOS では no-op (False 返却) として、`install_libarchive_windows` と
同じ `sys.platform` 分岐形式にそろえる。
"""

import logging
import os
import pathlib
import subprocess
import sys

from pytools._internal import log_format
from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

_VIEWER_EXE_RELATIVE = pathlib.PurePath(".local") / "bin" / "claude-plans-viewer.exe"
_STARTUP_CMD_NAME = "claude-plans-viewer.cmd"

# スタートアップ配置する .cmd の中身。%USERPROFILE% は実行時に展開される。
# `start ""` の空タイトルは cmd の仕様上必須で、これを入れないと `start "..."`
# の `"..."` がタイトル扱いされ実行ファイルの起動に失敗する。
_STARTUP_CMD_CONTENT = '@echo off\r\nstart "" "%USERPROFILE%\\.local\\bin\\claude-plans-viewer.exe"\r\n'


def run() -> bool:
    """Viewer の自動起動セットアップを行う (Windows のみ)。

    Returns:
        スタートアップ用 `.cmd` の配置または viewer のバックグラウンド起動を
        いずれか 1 つでも実施した場合 True。
    """
    if sys.platform != "win32":
        return False

    changed = False
    try:
        if _ensure_startup_cmd():
            changed = True
        if _start_viewer_if_not_running():
            changed = True
        return changed
    except Exception as e:  # noqa: BLE001
        logger.info(log_format.format_status("plans-viewer", f"自動起動セットアップに失敗: {e}"))
        return False


def _ensure_startup_cmd() -> bool:
    """スタートアップフォルダーに起動用 .cmd を冪等に配置する。"""
    target = _startup_dir() / _STARTUP_CMD_NAME
    if target.is_file() and target.read_bytes() == _STARTUP_CMD_CONTENT.encode("utf-8"):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_STARTUP_CMD_CONTENT.encode("utf-8"))
    logger.info(log_format.format_status("plans-viewer", f"スタートアップに配置: {target}"))
    return True


def _startup_dir() -> pathlib.Path:
    """スタートアップフォルダーのパスを返す。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("環境変数 APPDATA が未設定のためスタートアップフォルダーを解決できない")
    return pathlib.Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _start_viewer_if_not_running() -> bool:
    """Viewer が未起動かつ実行ファイルが存在するならデタッチ起動する。"""
    exe = _viewer_exe()
    if not exe.is_file():
        logger.info(log_format.format_status("plans-viewer", f"実行ファイルが未配置: {exe}"))
        return False
    if _is_viewer_running():
        return False
    # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP により post_apply プロセスが
    # 終了しても viewer は残る。close_fds=True で標準入出力の引き継ぎも切る。
    # 両フラグは Windows 限定で、非 Windows の静的解析で属性未定義エラーになるため
    # getattr 経由で参照する (本関数は冒頭の sys.platform ガードで Windows でのみ実行される)。
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(  # pylint: disable=consider-using-with  # noqa: S603 -- デタッチ起動のため明示的に待たない
        [str(exe)],
        creationflags=detached | new_group,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info(log_format.format_status("plans-viewer", f"バックグラウンド起動: {exe}"))
    return True


def _viewer_exe() -> pathlib.Path:
    """Viewer 実行ファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _VIEWER_EXE_RELATIVE


def _is_viewer_running() -> bool:
    """Viewer プロセスの有無を tasklist で判定する。"""
    # tasklist は見つからなくても終了コード 0 を返すため、出力テキストで判定する。
    # /FI "IMAGENAME eq ..." で実行ファイル名による絞り込みを行う。
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq claude-plans-viewer.exe", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.info(log_format.format_status("plans-viewer", f"tasklist 実行に失敗: {e}"))
        return False
    return "claude-plans-viewer.exe" in result.stdout.lower()


if __name__ == "__main__":
    setup_logging()
    run()
