r"""ユーザー PATH への `%USERPROFILE%\dotfiles\bin` 登録 (Windows のみ)。

bin/ をリポジトリ直下へ移し chezmoi 管理外にしたため、Linux は `~/.bashrc` で
`$HOME/dotfiles/bin` を PATH に追加する。Windows には対応する自動投入経路がないため、
本モジュールが `chezmoi apply` 後処理で `HKCU\Environment` の `Path` に冪等に追記する。

Linux/macOS では no-op (False 返却)。
"""

import logging
import sys

from pytools._internal import log_format, winutils

logger = logging.getLogger(__name__)

# %USERPROFILE% を残した相対表記で登録する。REG_EXPAND_SZ なら展開され、
# プロファイルパス変更にも追従しやすい。
_BIN_ENTRY = r"%USERPROFILE%\dotfiles\bin"


def run() -> bool:
    r"""`HKCU\Environment` の `Path` に dotfiles/bin を冪等に追記する。"""
    if sys.platform != "win32":
        return False

    try:
        appended = winutils.append_user_path(_BIN_ENTRY)
    except Exception as e:  # noqa: BLE001
        logger.warning(log_format.format_status("bin PATH", f"登録に失敗: {e}"))
        return False
    if not appended:
        return False
    # 追記時のみ環境変数変更をブロードキャストし、新規プロセスで即時反映させる。
    winutils.broadcast_environment_change()
    logger.info(log_format.format_status("bin PATH", f"ユーザー PATH に追記: {_BIN_ENTRY}"))
    return True
