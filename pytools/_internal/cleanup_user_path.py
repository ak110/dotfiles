"""ユーザー側 PATH からシステム側 PATH と重複するエントリを除去する (Windows のみ)。

Windows の環境変数 PATH はシステム (HKLM) 側とユーザー (HKCU) 側に分かれて
格納され、プロセス起動時に両者を連結したものが解決される。利用者の手作業や
過去のインストーラーの動作などで、システム側に既に存在するエントリが
ユーザー側にも重複登録されているケースがある。重複は実害が小さい一方で、
PATH 肥大化やエディター類の補完候補のノイズに繋がるため、本ステップで
自動的に整理する。

書き戻しは読み出し時の値型 (REG_EXPAND_SZ 等) を維持したまま行うため、
`%USERPROFILE%` 等のプレースホルダー表記を残せる。比較自体は環境変数を
展開した文字列を PureWindowsPath で表記正規化したキーで行うが、書き戻す
各エントリは元の生値（プレースホルダーを含む）のまま保持する。
"""

import logging
import ntpath
import sys
from pathlib import PureWindowsPath

from pytools._internal import log_format, winutils

logger = logging.getLogger(__name__)

_PATH_SEPARATOR = ";"


def run() -> bool:
    """ユーザー側 PATH からシステム側と重複するエントリを除外する (Windows のみ)。

    Returns:
        ユーザー側 PATH を実際に書き換えた場合 True。
    """
    if sys.platform != "win32":
        return False

    user_value, reg_type = winutils.read_user_env_var("Path")
    if not user_value:
        return False
    try:
        system_value, _ = winutils.read_system_env_var("Path")
    except OSError as e:
        logger.warning(log_format.format_status("PATH 重複整理", f"システム側 PATH の読み込みに失敗: {e}"))
        return False

    new_value, removed = _filter_user_path(user_value, system_value or "")
    if not removed:
        return False
    try:
        winutils.write_user_env_var("Path", new_value, reg_type)
    except OSError as e:
        logger.warning(log_format.format_status("PATH 重複整理", f"ユーザー PATH の書き込みに失敗: {e}"))
        return False
    for entry in removed:
        logger.info(log_format.format_status("PATH 重複整理", f"ユーザー PATH から除外: {entry}"))
    winutils.broadcast_environment_change()
    return True


def _filter_user_path(user_value: str, system_value: str) -> tuple[str, list[str]]:
    """ユーザー側 PATH からシステム側と重複するエントリを除いた文字列を返す。

    比較は各エントリを `ntpath.expandvars` で環境変数展開し `PureWindowsPath`
    で表記正規化したキーで行う。残すエントリは元の生値（プレースホルダーを含む）
    のまま保持する。

    Returns:
        `(整理後の PATH 文字列, 削除した元エントリ一覧)` のタプル。
    """
    system_keys = {key for key in (_normalize_entry(entry) for entry in _split(system_value)) if key}
    kept: list[str] = []
    removed: list[str] = []
    for entry in _split(user_value):
        key = _normalize_entry(entry)
        if key and key in system_keys:
            removed.append(entry)
            continue
        kept.append(entry)
    return _PATH_SEPARATOR.join(kept), removed


def _split(path_value: str) -> list[str]:
    """`;` 区切りの PATH 文字列をエントリ配列に分解する。空エントリは除く。"""
    return [entry for entry in path_value.split(_PATH_SEPARATOR) if entry]


def _normalize_entry(entry: str) -> str:
    """比較キーを返す。

    `ntpath.expandvars` で環境変数を展開し `PureWindowsPath` を通したうえで
    小文字化することで、区切り文字方向・末尾区切り・大文字小文字差を吸収する。
    展開後が空のエントリは空文字を返し比較対象から除く。
    """
    expanded = ntpath.expandvars(entry)
    if not expanded:
        return ""
    return str(PureWindowsPath(expanded)).lower()
