from __future__ import annotations

from agent_toolkit._agents_server import subagent_contract
from agent_toolkit._atk import managed_temp


def _body(session_id: str) -> str:
    directory = managed_temp.find_session_temp(session_id)
    assert directory is not None
    return (directory / subagent_contract.RECORD_NAME).read_text(encoding="utf-8")


def _request(purpose: str, tasks: str = "", constraints: str = "") -> str:
    return f"## 目的\n\n{purpose}\n\n## 完遂事項\n\n{tasks}\n\n## 守る制約\n\n{constraints}"


def test_current_request_replaces_and_cancels_delivered_requirements() -> None:
    request = _request("対象の検査", "- 対象: x.py\n  - 追加検査を行う", "- 公開しない")
    assert subagent_contract.create("contract-a", request) is None
    changes = {"対象: x.py": "対象: y.py", "\n  - 追加検査を行う": "", "": "- 型検査も行う"}
    assert subagent_contract.apply_followup("contract-a", changes, "steered") is None
    assert subagent_contract.apply_followup("contract-a", {"": "失敗した訂正"}, "reply_failed") is None
    uncertain = {"対象: y.py": "対象: z.py"}
    assert subagent_contract.apply_followup("contract-a", uncertain, "reply_ambiguous") is None
    pending, _, active = _body("contract-a").partition("## 目的")
    assert "配送結果が不明" in pending and "対象: z.py" in pending
    assert "対象: y.py" in active and "対象: z.py" not in active
    tasks, _, constraints = active.partition("## 守る制約")
    assert "対象の検査" in tasks and "型検査も行う" in tasks and "公開しない" in constraints
    assert "対象: x.py" not in active and "追加検査を行う" not in active
    assert "失敗した訂正" not in pending + active
    assert subagent_contract.apply_followup("contract-a", uncertain, "reply_started") is None
    body = _body("contract-a")
    assert "対象: z.py" in body and "対象: y.py" not in body and "配送結果が不明" not in body


def test_nonunique_change_does_not_apply_any_of_the_changes() -> None:
    assert subagent_contract.create("contract-a", _request("目的A", "- 検査 aaa")) is None
    before = _body("contract-a")
    warning = subagent_contract.apply_followup("contract-a", {"目的A": "目的B", "aa": "bb"}, "reply_started")
    assert warning is not None
    assert _body("contract-a") == before


def test_overlapping_change_does_not_apply_any_of_the_changes() -> None:
    assert subagent_contract.create("contract-a", _request("目的A", "- 担当A: 書式確認")) is None
    before = _body("contract-a")
    changes = {"目的A": "目的B", "担当A": "担当B", "担当A: 書式確認": ""}
    assert subagent_contract.apply_followup("contract-a", changes, "reply_started") is not None
    assert _body("contract-a") == before


def test_sessions_do_not_share_contracts() -> None:
    assert subagent_contract.create("contract-b", _request("目的B")) is None
    assert subagent_contract.create("contract-c", _request("目的C")) is None
    assert subagent_contract.apply_followup("contract-b", {"": "- Bへの訂正"}, "reply_started") is None
    assert "目的C" not in _body("contract-b")
    assert "Bへの訂正" not in _body("contract-c")


def test_followup_without_contract_creates_nothing() -> None:
    assert subagent_contract.apply_followup("contract-none", {"": "訂正"}, "reply_started") is not None
    directory = managed_temp.find_session_temp("contract-none")
    assert directory is None or not (directory / subagent_contract.RECORD_NAME).exists()


def test_handoff_inherits_predecessor_contract() -> None:
    assert subagent_contract.create("contract-old", _request("目的D")) is None
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
        assert subagent_contract.create(session_id, _request("原稿を整える", "- 担当A: 書式確認\n- 担当B: 内容確認")) is None
        assert subagent_contract.apply_followup(session_id, {"": "未確定の追加"}, "reply_ambiguous") is None
        assert subagent_contract.apply_followup(session_id, changes, "reply_ambiguous") is None
        assert subagent_contract.apply_followup(session_id, reverse, "reply_failed") is None
        assert subagent_contract.apply_followup(session_id, confirmed, "reply_started") is None
        pending, _, active = _body(session_id).partition("## 目的")
        assert "未確定の追加" in pending and "担当A" not in pending and "担当C" not in pending
        assert "- 担当B: 書式確認\n- 担当C: 内容確認" in active


def test_unstructured_task_keeps_request_separate_until_classified() -> None:
    session_id = "unstructured"
    request = subagent_contract.task_request("review", {"対象": "x.py", "条件": "公開しない"})
    assert subagent_contract.create(session_id, request) is None
    initial = _body(session_id)
    assert "公開しない" not in initial and "役割: review" not in initial
    assert [line for line in initial.splitlines() if line.startswith("## ")] == ["## 目的", "## 完遂事項", "## 守る制約"]
    assert subagent_contract.apply_followup(session_id, {"x.py": "y.py"}, "reply_started") is None
    assert subagent_contract.inherit(session_id, "successor") is None
    directory = managed_temp.find_session_temp("successor")
    assert directory is not None
    source = directory / "startup-request.md"
    assert source.read_text(encoding="utf-8") == request.replace("x.py", "y.py") + "\n"
    assert source.name in _body("successor")
    classified = _request("対象のレビュー", "- y.pyをレビューする", "- 公開しない")
    path = directory / subagent_contract.RECORD_NAME
    path.write_text(initial.partition("## 目的")[0] + classified + "\n", encoding="utf-8")
    assert subagent_contract.apply_followup("successor", {"y.py": "z.py"}, "reply_started") is None
    assert classified.replace("y.py", "z.py") in _body("successor")
    assert "y.py" in source.read_text(encoding="utf-8")
