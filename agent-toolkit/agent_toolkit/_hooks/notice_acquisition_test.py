"""発話関連の通知と原本読取失敗が、実際の資料と回復の操作を示すことを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import antigravity, posttooluse, user_prompt_submit


def test_unreadable_transcript_separates_recovery_from_hook_retry(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "原本 ' [$value]; (record).jsonl"
    assert (
        antigravity.main(
            json.dumps({"conversationId": "notice-transcript", "transcriptPath": str(path)}), event="PreInvocation"
        )
        == 0
    )
    output = capsys.readouterr().err
    assert str(path) in output
    fact, action = output.split("次の操作:", 1)
    assert "hookが再読する" in fact
    assert "PreInvocation" not in action and "Stop" not in action
    assert "確認せよ" in action and "報告せよ" in action


@pytest.mark.parametrize("prompt", ["例えば表示を直して", "例えば設定を調査して"])
def test_content_notice_references_only_the_required_documents(prompt: str) -> None:
    bodies = posttooluse.content_notices(prompt, "notice-acquisition")
    assert len(bodies) == 1
    body = bodies[0]
    assert "`agent-toolkit:atk-mode/playbooks/user-utterance.md`" in body
    routing = "`explore`へ分担する案"
    if "調査" in prompt:
        assert routing in body
    else:
        assert routing not in body
    assert "cat --" not in body and "Get-Content" not in body


def test_first_prompt_does_not_warn_before_preparation(capsys: pytest.CaptureFixture[str]) -> None:
    session_id = "notice-confirmation"
    assert user_prompt_submit.main(json.dumps({"session_id": session_id, "prompt": "確認して"})) == 0
    captured = capsys.readouterr()
    assert "未取得" not in captured.out and 'kind="warn"' not in captured.out and not captured.err


@pytest.mark.parametrize("prompt", ["通常の依頼", "`例えば調査`は引用", "理解が違う！！"])
def test_acquisition_does_not_add_notice_triggers(prompt: str) -> None:
    assert not posttooluse.content_notices(prompt, "notice-acquisition-negative")
