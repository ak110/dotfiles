from __future__ import annotations

from agent_toolkit._agents_server import subagent_contract
from agent_toolkit._atk import managed_temp


def _body(session_id: str) -> str:
    directory = managed_temp.find_session_temp(session_id)
    assert directory is not None
    return (directory / subagent_contract.RECORD_NAME).read_text(encoding="utf-8")


def test_followups_keep_order_and_separate_unconfirmed_delivery() -> None:
    assert subagent_contract.create("contract-a", subagent_contract.task_request("review", {"対象": "x.py"})) is None
    subagent_contract.append_followup("contract-a", "訂正1: 対象をy.pyへ", "steered")
    subagent_contract.append_followup("contract-a", "失敗した訂正", "reply_failed")
    subagent_contract.append_followup("contract-a", "訂正2: 範囲を広げる", "reply_ambiguous")
    body = _body("contract-a")
    assert "役割: review" in body
    assert "対象: x.py" in body
    assert "失敗した訂正" not in body
    first = body.index("### 追送1（配送確定）\n\n訂正1: 対象をy.pyへ")
    assert body.index("### 追送2（配送結果が不明）\n\n訂正2: 範囲を広げる") > first


def test_sessions_do_not_share_contracts() -> None:
    assert subagent_contract.create("contract-b", "目的B") is None
    assert subagent_contract.create("contract-c", "目的C") is None
    subagent_contract.append_followup("contract-b", "Bへの訂正", "reply_started")
    assert "目的C" not in _body("contract-b")
    assert "Bへの訂正" not in _body("contract-c")


def test_followup_without_contract_creates_nothing() -> None:
    subagent_contract.append_followup("contract-none", "訂正", "reply_started")
    directory = managed_temp.find_session_temp("contract-none")
    assert directory is None or not (directory / subagent_contract.RECORD_NAME).exists()


def test_handoff_inherits_predecessor_contract() -> None:
    assert subagent_contract.create("contract-old", "目的D") is None
    subagent_contract.append_followup("contract-old", "訂正D", "reply_started")
    assert subagent_contract.inherit("contract-old", "contract-new") is None
    assert _body("contract-new") == _body("contract-old")
    subagent_contract.append_followup("contract-new", "後任への訂正", "reply_started")
    assert "### 追送2（配送確定）" in _body("contract-new")
    assert "後任への訂正" not in _body("contract-old")
