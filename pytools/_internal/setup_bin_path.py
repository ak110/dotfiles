r"""ユーザーPATHへの`bin`ディレクトリ群の登録（Windowsのみ）。

`%USERPROFILE%\dotfiles\bin`・`%USERPROFILE%\dotfiles\agent-toolkit\bin`と、`uv tool install`が実行ファイルを置く
`%USERPROFILE%\.local\bin`を登録する。
Linuxでは`~/.bashrc`が同じディレクトリをPATHへ追加する。
Windowsには対応する自動投入経路がないため、`chezmoi apply`後処理で
`HKCU\Environment`の`Path`へ冪等に追記する。
`.local\bin`は`uv tool update-shell`へ任せず本工程で登録し、登録済みの判定を`winutils.append_user_path`の1か所にそろえる。
"""

import logging

from pytools._internal import log_format, post_apply_outcome, winutils

logger = logging.getLogger(__name__)

# %USERPROFILE% を残した相対表記で登録する。REG_EXPAND_SZ なら展開され、
# プロファイルパス変更にも追従しやすい。
# Linux側 .chezmoi-source/dot_bashrc の追加順序（dotfiles/bin → dotfiles/agent-toolkit/bin）と揃える。
_BIN_ENTRIES: tuple[str, ...] = (
    r"%USERPROFILE%\dotfiles\bin",
    r"%USERPROFILE%\dotfiles\agent-toolkit\bin",
    r"%USERPROFILE%\.local\bin",
)


def run() -> post_apply_outcome.PostApplyOutcome:
    r"""`HKCU\Environment` の `Path` に dotfiles配下のbinディレクトリを冪等に追記する。"""
    any_appended = False
    failures: list[str] = []
    for entry in _BIN_ENTRIES:
        try:
            appended = winutils.append_user_path(entry)
        except OSError as e:
            failures.append(f"{entry} の登録に失敗: {e}")
            continue
        if appended:
            logger.info(log_format.format_status("bin PATH", f"ユーザー PATH に追記: {entry}"))
            any_appended = True
    if any_appended:
        # 追記時のみ環境変数変更をブロードキャストし、新規プロセスで即時反映させる。
        winutils.broadcast_environment_change()
    return post_apply_outcome.PostApplyOutcome(changed=any_appended, failure=" / ".join(failures) or None)
