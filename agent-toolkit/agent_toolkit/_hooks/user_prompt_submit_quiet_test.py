"""UserPromptSubmitの同時通知が必要な指示を1回届けることを検証する。"""

import json

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import user_prompt_submit
from agent_toolkit._hooks.posttooluse import USER_CONFIRMATION_PENDING_KEY


@pytest.mark.parametrize("pending", [False, True])
def test_simultaneous_prompt_notices_deliver_bugfix_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pending: bool,
) -> None:
    session_id = f"quiet-output-{pending}"
    session_state.update_state(session_id, lambda _state: {USER_CONFIRMATION_PENDING_KEY: pending})
    monkeypatch.setattr(user_prompt_submit, "_is_machine_injected", lambda *_args: False)
    monkeypatch.setattr(user_prompt_submit.session_title, "proposed_title", lambda *_args: None)

    payload = json.dumps({"session_id": session_id, "prompt": "変更を続ける", "turn_id": "codex"})
    assert user_prompt_submit.main(payload) == 0
    output = capsys.readouterr().out
    if pending:
        body = json.loads(output)["hookSpecificOutput"]["additionalContext"]
        assert body.count("`agent-toolkit:bugfix`") == 1
        assert "認可を要する操作" in body
    else:
        assert not output
    assert user_prompt_submit.main(payload) == 0
    assert not capsys.readouterr().out
    session_state.update_state(session_id, lambda state: {**state, USER_CONFIRMATION_PENDING_KEY: True})
    assert user_prompt_submit.main(payload) == 0
    renewed = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert renewed.count("`agent-toolkit:bugfix`") == 1
