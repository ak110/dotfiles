"""発話関連の通知と原本読取失敗が、実際の資料へ引用済みの取得操作を示すことを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common.read_triggers import ReadTarget
from agent_toolkit._hooks import antigravity, document_reads, posttooluse, user_prompt_submit
from agent_toolkit._hooks.pretooluse import read_delivery


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


@pytest.mark.parametrize("prompt", ["例えば表示を直して", "例えば設定を調査して", "理解が違う！！"])
def test_content_notice_references_only_the_required_documents(prompt: str) -> None:
    bodies = posttooluse.content_notices(prompt, "notice-acquisition")
    assert len(bodies) == 1
    body = bodies[0]
    if "！！" in prompt:
        assert "`agent-toolkit:user-confirmation-and-report`を起動し" in body
    else:
        assert "`references/user-utterance.md`" in body
    routing = "`references/routing.md`"
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


@pytest.mark.parametrize("prompt", ["通常の依頼", "`例えば調査`は引用"])
def test_acquisition_does_not_add_notice_triggers(prompt: str) -> None:
    assert not posttooluse.content_notices(prompt, "notice-acquisition-negative")


@pytest.mark.parametrize("subject", ["main", "subagent", "codex"])
@pytest.mark.parametrize("loadable", [False, True])
def test_skill_guidance_keeps_unloadable_copy_readable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, subject: str, loadable: bool
) -> None:
    path = tmp_path / ".claude/skills/local/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 固有本文\n", encoding="utf-8")
    monkeypatch.setattr(document_reads, "skill_loadable", lambda *_args, **_kwargs: loadable)
    body = read_delivery.acquisition_step(ReadTarget(path), subject)
    if loadable:
        assert body == "`local`"
        for forbidden in ("Skill(skill=", "cat --", "Get-Content", "Read", "それ以外", "使えない場合"):
            assert forbidden not in body
    else:
        assert str(path) in body and "起動せよ" not in body
