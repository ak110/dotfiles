"""明示された現在時刻だけを警告し、引用や正しい観測を保つ。"""

import datetime

from agent_toolkit._hooks import clock_claims


def test_masked_clock_does_not_match_quoted_or_fenced_material():
    assert clock_claims.warning(["記録に 13:5x と書いた"])
    assert clock_claims.warning(["時刻は 1x:30"])
    assert clock_claims.warning(["> 記録に 13:5x と書いた\n```\n13:5x\n```\n予定 13:50"]) is None


def test_explicit_claim_uses_matching_actor_observation(monkeypatch):
    source: dict[str, object] = {}
    monkeypatch.setattr(clock_claims, "read_state", lambda _session_id: source)
    monkeypatch.setattr(clock_claims, "update_state", lambda _session_id, modify: source.update(modify(source)))
    moment = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    payload = {"session_id": "test-clock", "tool_response": {"stdout": moment}}
    text = f"dateで確認: {moment}"
    assert clock_claims.warning([text], payload)
    assert clock_claims.warning(["dateで確認: 17時48分"], payload)
    clock_claims.observe(payload, "date -Iseconds", "test-clock")
    assert clock_claims.warning([text], payload) is None
    assert clock_claims.warning([f"dateで確認: {moment[11:16]}"], payload) is None
    assert clock_claims.warning([text], {**payload, "agent_id": "other"})
    assert clock_claims.warning(["現在時刻: 2000-01-01T01:00:00+00:00"], payload)
    assert clock_claims.warning(["予定: 2000-01-01T01:00:00+00:00"], payload) is None
