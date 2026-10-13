"""post-applyの失敗理由と次の操作を組として受け取る契約を検証する。"""

import pytest

from pytools._internal import post_apply_outcome


def test_failure_requires_action() -> None:
    """理由だけの失敗が工程から返されることを、作成時に拒否する。"""
    with pytest.raises(ValueError, match="次の操作"):
        post_apply_outcome.PostApplyOutcome(failure="設定を書き込めなかった")


@pytest.mark.parametrize("action", ["", " ", "\n\t"])
def test_failure_rejects_empty_action(action: str) -> None:
    """操作を選べない空の案内を失敗結果として受け取らない。"""
    with pytest.raises(ValueError, match="次の操作"):
        post_apply_outcome.PostApplyOutcome(failure="設定を書き込めなかった", next_action=action)


def test_action_requires_failure() -> None:
    """成功の結果へ失敗用の操作を混ぜる指定を拒否する。"""
    with pytest.raises(ValueError, match="失敗理由"):
        post_apply_outcome.PostApplyOutcome(next_action="update-dotfilesを再実行する")


@pytest.mark.parametrize("reason", ["", " \n\t"])
def test_failure_rejects_empty_reason(reason: str) -> None:
    """操作があっても原因を判別できない失敗結果は拒否する。"""
    with pytest.raises(ValueError, match="失敗理由"):
        post_apply_outcome.PostApplyOutcome(failure=reason, next_action="update-dotfilesを再実行する")


@pytest.mark.parametrize("changed", [False, True])
def test_success_and_skip_need_no_failure_information(changed: bool) -> None:
    """更新と正常な省略は失敗用の情報なしで作成できる。"""
    outcome = post_apply_outcome.PostApplyOutcome(changed=changed)
    assert outcome.failure is None
    assert outcome.next_action is None
