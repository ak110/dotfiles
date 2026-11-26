from __future__ import annotations

from agent_toolkit._agents_server import subagent_contract
from agent_toolkit._atk import managed_temp


def _body(session_id: str) -> str:
    directory = managed_temp.find_session_temp(session_id)
    assert directory is not None
    return (directory / subagent_contract.RECORD_NAME).read_text(encoding="utf-8")


def test_current_request_replaces_and_cancels_delivered_requirements() -> None:
    request = subagent_contract.task_request("review", {"対象": "x.py", "保つ条件": "公開しない"})
    assert subagent_contract.create("contract-a", request + "\n追加検査を行う") is None
    changes = {"対象: x.py": "対象: y.py", "追加検査を行う": "", "": "型検査も行う"}
    assert subagent_contract.apply_followup("contract-a", changes, "steered") is None
    assert subagent_contract.apply_followup("contract-a", {"": "失敗した訂正"}, "reply_failed") is None
    uncertain = {"対象: y.py": "対象: z.py"}
    assert subagent_contract.apply_followup("contract-a", uncertain, "reply_ambiguous") is None
    pending, _, active = _body("contract-a").partition("## 現在の依頼")
    assert "配送結果が不明" in pending and "対象: z.py" in pending
    assert "対象: y.py" in active and "対象: z.py" not in active
    assert "役割: review" in active and "公開しない" in active and "型検査も行う" in active
    assert "対象: x.py" not in active and "追加検査を行う" not in active
    assert "失敗した訂正" not in pending + active
    assert subagent_contract.apply_followup("contract-a", uncertain, "reply_started") is None
    body = _body("contract-a")
    assert "対象: z.py" in body and "対象: y.py" not in body and "配送結果が不明" not in body


def test_nonunique_change_does_not_apply_any_of_the_changes() -> None:
    assert subagent_contract.create("contract-a", "目的A\n検査 aaa") is None
    before = _body("contract-a")
    warning = subagent_contract.apply_followup("contract-a", {"目的A": "目的B", "aa": "bb"}, "reply_started")
    assert warning is not None
    assert _body("contract-a") == before


def test_overlapping_change_does_not_apply_any_of_the_changes() -> None:
    assert subagent_contract.create("contract-a", "目的A\n担当A: 書式確認") is None
    before = _body("contract-a")
    changes = {"目的A": "目的B", "担当A": "担当B", "担当A: 書式確認": ""}
    assert subagent_contract.apply_followup("contract-a", changes, "reply_started") is not None
    assert _body("contract-a") == before


def test_sessions_do_not_share_contracts() -> None:
    assert subagent_contract.create("contract-b", "目的B") is None
    assert subagent_contract.create("contract-c", "目的C") is None
    assert subagent_contract.apply_followup("contract-b", {"": "Bへの訂正"}, "reply_started") is None
    assert "目的C" not in _body("contract-b")
    assert "Bへの訂正" not in _body("contract-c")


def test_followup_without_contract_creates_nothing() -> None:
    assert subagent_contract.apply_followup("contract-none", {"": "訂正"}, "reply_started") is not None
    directory = managed_temp.find_session_temp("contract-none")
    assert directory is None or not (directory / subagent_contract.RECORD_NAME).exists()


def test_handoff_inherits_predecessor_contract() -> None:
    assert subagent_contract.create("contract-old", "目的D") is None
    assert subagent_contract.apply_followup("contract-old", {"目的D": "目的E"}, "reply_started") is None
    assert subagent_contract.inherit("contract-old", "contract-new") is None
    assert _body("contract-new") == _body("contract-old")
    assert subagent_contract.apply_followup("contract-new", {"目的E": "目的F"}, "reply_started") is None
    assert "目的F" in _body("contract-new") and "目的E" not in _body("contract-new")
    assert "目的E" in _body("contract-old") and "目的F" not in _body("contract-old")


def test_confirmed_batch_clears_only_its_uncertain_entry_regardless_of_key_order() -> None:
    changes = {"担当A": "担当B", "担当B": "担当C"}
    reverse = dict(reversed(tuple(changes.items())))
    for session_id, confirmed in (("same", changes), ("reverse", reverse)):
        assert subagent_contract.create(session_id, "担当A: 書式確認\n担当B: 内容確認") is None
        assert subagent_contract.apply_followup(session_id, {"": "未確定の追加"}, "reply_ambiguous") is None
        assert subagent_contract.apply_followup(session_id, changes, "reply_ambiguous") is None
        assert subagent_contract.apply_followup(session_id, reverse, "reply_failed") is None
        assert subagent_contract.apply_followup(session_id, confirmed, "reply_started") is None
        pending, _, active = _body(session_id).partition("## 現在の依頼")
        assert "未確定の追加" in pending and "担当A" not in pending and "担当C" not in pending
        assert active.strip() == "担当B: 書式確認\n担当C: 内容確認"
