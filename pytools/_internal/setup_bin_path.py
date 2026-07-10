r"""ユーザーPATHへの`%USERPROFILE%\dotfiles\bin`および`%USERPROFILE%\dotfiles\agent-toolkit\bin`登録（Windowsのみ）。

`bin/`はリポジトリ直下にあるchezmoi管理外ディレクトリで、Linuxでは`~/.bashrc`で
`$HOME/dotfiles/bin`および`$HOME/dotfiles/agent-toolkit/bin`をPATHへ追加する。
Windowsには対応する自動投入経路がないため、`chezmoi apply`後処理で
`HKCU\Environment`の`Path`へ冪等に追記する。
"""

import logging
import sys

from pytools._internal import log_format, winutils

logger = logging.getLogger(__name__)

# %USERPROFILE% を残した相対表記で登録する。REG_EXPAND_SZ なら展開され、
# プロファイルパス変更にも追従しやすい。
# Linux側 .chezmoi-source/dot_bashrc の追加順序（dotfiles/bin → dotfiles/agent-toolkit/bin）と揃える。
_BIN_ENTRIES: tuple[str, ...] = (
    r"%USERPROFILE%\dotfiles\bin",
    r"%USERPROFILE%\dotfiles\agent-toolkit\bin",
)


def run() -> bool:
    r"""`HKCU\Environment` の `Path` に dotfiles配下のbinディレクトリを冪等に追記する。"""
    if sys.platform != "win32":
        return False

    any_appended = False
    for entry in _BIN_ENTRIES:
        try:
            appended = winutils.append_user_path(entry)
        except Exception as e:  # noqa: BLE001
            logger.warning(log_format.format_status("bin PATH", f"{entry} の登録に失敗: {e}"))
            continue
        if appended:
            logger.info(log_format.format_status("bin PATH", f"ユーザー PATH に追記: {entry}"))
            any_appended = True
    if any_appended:
        # 追記時のみ環境変数変更をブロードキャストし、新規プロセスで即時反映させる。
        winutils.broadcast_environment_change()
    return any_appended
