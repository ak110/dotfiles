"""UserPromptSubmitの同時通知が必要な指示を1回届けることを検証する。"""

import json

import pytest

from agent_toolkit._hooks import user_prompt_submit


@pytest.mark.parametrize("prompt", ["変更を続ける", "$agent-toolkit:atk-mode"])
def test_initial_preparation_does_not_receive_missing_skill_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prompt: str,
) -> None:
    session_id = "quiet-output"
    monkeypatch.setattr(user_prompt_submit, "_is_machine_injected", lambda *_args: False)
    monkeypatch.setattr(user_prompt_submit.session_title, "proposed_title", lambda *_args: None)

    payload = json.dumps({"session_id": session_id, "prompt": prompt, "turn_id": "codex"})
    assert user_prompt_submit.main(payload) == 0

    for _ in range(2):
        output = capsys.readouterr().out
        assert "未取得" not in output and 'kind="warn"' not in output
        assert user_prompt_submit.main(payload) == 0
    output = capsys.readouterr().out
    assert "未取得" not in output and 'kind="warn"' not in output


@pytest.mark.parametrize("failure", ["observe_user", "observe_input"])
def test_record_failure_delivers_recovery_through_public_hook(monkeypatch, capsys, failure):
    def fail(*_args, **_kwargs):
        raise OSError("記録先へ書けない")

    module = user_prompt_submit.termination_evidence if failure == "observe_user" else user_prompt_submit.work_contract
    monkeypatch.setattr(module, failure, fail)
    assert user_prompt_submit.main(json.dumps({"session_id": "record-" + failure, "prompt": "要求を保持して進めて"})) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "記録先へ書けない" in context
    assert "原文" in context and "委譲元またはユーザー" in context
