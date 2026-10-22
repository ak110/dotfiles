"""UserPromptSubmitの同時通知が必要な指示を1回届けることを検証する。"""

import json

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import user_prompt_submit
from agent_toolkit._hooks.posttooluse import USER_CONFIRMATION_PENDING_KEY


@pytest.mark.parametrize("verification_due", [False, True])
def test_simultaneous_prompt_notices_deliver_bugfix_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verification_due: bool,
) -> None:
    session_id = f"quiet-output-{verification_due}"
    session_state.update_state(session_id, lambda _state: {USER_CONFIRMATION_PENDING_KEY: True})
    monkeypatch.setattr(user_prompt_submit, "_claim_verification_notice", lambda *_args: verification_due)
    monkeypatch.setattr(user_prompt_submit, "_is_machine_injected", lambda *_args: False)
    monkeypatch.setattr(user_prompt_submit.session_title, "proposed_title", lambda *_args: None)

    assert user_prompt_submit.main(json.dumps({"session_id": session_id, "prompt": "変更を続ける", "turn_id": "codex"})) == 0
    body = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert body.count("`agent-toolkit:bugfix`") == 1
    assert "認可を要する操作" in body
