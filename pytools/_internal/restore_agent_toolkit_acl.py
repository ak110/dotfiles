r"""agent-toolkitの状態ディレクトリを、継承するACLへ戻す（Windowsのみ）。

旧版のagent-toolkitはWindowsでも状態ディレクトリを0o700で作成し、OWNER RIGHTSだけに依存する保護付きDACLを付けた。
そのディレクトリへ昇格したプロセスが書いたファイルは、同じユーザーの非昇格のプロセスが読めない
（`agent_toolkit._common.private_directory`の説明を参照）。
本工程は`%LOCALAPPDATA%\agent-toolkit`配下で同じユーザーの通常のプロセスが読めない項目と、旧版のディレクトリを、
親から継承するACLへ戻す。
所有者がユーザーでない項目（昇格したプロセスが作成した項目）は、非昇格の実行では変更せず、昇格した`update-dotfiles`の実行を案内する。
昇格した実行で管理サービスの準備物を保護付きの一時rootの配下へ作らないよう、`post_apply.py`はwarmupを本工程の後に実行する。
"""

import logging

from agent_toolkit._common import state_paths, windows_security

from pytools._internal import log_format, post_apply_outcome

logger = logging.getLogger(__name__)

_TAG = "agent-toolkit ACL"
_SHOWN_BLOCKED = 5
"""失敗に表示する戻せない項目の数。昇格したプロセスが書いた配下は数千件になりうるため、残りは件数で示す。"""


def run() -> post_apply_outcome.PostApplyOutcome:
    """状態ディレクトリ配下の通常のプロセスが読めないACLを継承へ戻し、戻せない項目を失敗として返す。"""
    root = state_paths.state_dir()
    result = windows_security.restore_inheritance(root)
    if result.restored:
        logger.info(
            log_format.format_status(
                _TAG, f"{log_format.home_short(root)}配下の{len(result.restored)}項目を継承するACLへ戻した"
            )
        )
    if not result.blocked:
        return post_apply_outcome.PostApplyOutcome(changed=bool(result.restored))
    if windows_security.is_elevated():
        next_action = "表示した項目の所有者とACLを確認する（昇格した実行でも継承するACLへ戻せなかった）"
    else:
        next_action = (
            "管理者として起動した端末で`update-dotfiles`を実行して戻し、その後に非昇格の端末で`update-dotfiles`を再実行する"
        )
    shown = "、".join(f"{log_format.home_short(path)}（{reason}）" for path, reason in result.blocked[:_SHOWN_BLOCKED])
    rest = len(result.blocked) - _SHOWN_BLOCKED
    summary = f"非昇格のプロセスが読めない状態ファイルを継承するACLへ戻せない、または走査できない（{len(result.blocked)}件）"
    return post_apply_outcome.PostApplyOutcome(
        changed=bool(result.restored),
        failure=f"{summary}: {shown}" + (f"、ほか{rest}件" if rest > 0 else ""),
        next_action=next_action,
    )
