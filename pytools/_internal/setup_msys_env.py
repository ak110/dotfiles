r"""ユーザー環境変数`MSYS`を`winsymlinks:nativestrict`に設定する（Windowsのみ）。

`HKCU\Environment`の`MSYS`を`winsymlinks:nativestrict`に冪等設定する。
chezmoiやGit for Windows等の同梱MSYSランタイムが本変数を読み取り、
`ln -s`相当の操作をWindowsネイティブのシンボリックリンクとして作成する挙動へ切り替える。
未設定または別値の場合のみ書き込み、設定追記時のみ環境変数変更をブロードキャストする。

本ステップは`post_apply`内で実行されるが、効果は次回以降の`chezmoi apply`に限られる。
初回apply時のシンボリックリンク作成はユーザーが別途bootstrap手順
（cmd/PowerShellでの手動`setx`等）で対処する前提とする。
"""

import logging
import sys

from pytools._internal import log_format, winutils

logger = logging.getLogger(__name__)

_MSYS_VAR_NAME = "MSYS"
_MSYS_VAR_VALUE = "winsymlinks:nativestrict"


def run() -> bool:
    r"""`HKCU\Environment`の`MSYS`を`winsymlinks:nativestrict`に冪等設定する。

    Returns:
        非Windowsまたは既に同値設定済みの場合は`False`。書き込みを実行した場合は`True`。
    """
    if sys.platform != "win32":
        return False

    try:
        current, _reg_type = winutils.read_user_env_var(_MSYS_VAR_NAME)
    except Exception as e:  # noqa: BLE001
        logger.warning(log_format.format_status("MSYS env", f"読み取りに失敗: {e}"))
        return False

    if current == _MSYS_VAR_VALUE:
        return False

    wr = winutils.import_winreg()
    try:
        winutils.write_user_env_var(_MSYS_VAR_NAME, _MSYS_VAR_VALUE, wr.REG_SZ)
    except Exception as e:  # noqa: BLE001
        logger.warning(log_format.format_status("MSYS env", f"書き込みに失敗: {e}"))
        return False
    winutils.broadcast_environment_change()
    logger.info(log_format.format_status("MSYS env", f"{_MSYS_VAR_NAME}={_MSYS_VAR_VALUE} を設定"))
    return True
