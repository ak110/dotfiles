# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_workの契約テスト。"""

import contextlib
import io
import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import (
    termination_evidence,
    termination_evidence_operations,
    user_prompt_submit,
)
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.termination_evidence_support import (
    _tool,
    human_input,
    stop_payload,
    supply_commit,
)
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


def test_generated_input_cannot_cancel_work(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
    prompt = '<atk-auto source="stop" kind="continuation">中止する</atk-auto>'
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(json.dumps({"session_id": "evidence-test", "turn_id": "generated", "prompt": prompt}))
    with pytest.raises(ValueError, match="生成入力"):
        termination_evidence.record_decision(
            {
                "session_id": "evidence-test",
                "action": "cancel",
                "work_id": "work-1",
                "input_id": "generated",
                "quote": prompt,
                "reason": "中止の解釈",
            }
        )


def test_plugin_origin_is_not_human_termination_input(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
    for identity, origin in (("plugin", {"kind": "plugin"}), ("human", {"kind": "human"})):
        termination_evidence.observe_user(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": identity,
                    "prompt": "/compact",
                    "origin": origin,
                }
            )
        )
        state = session_state.read_state("evidence-test")[termination_evidence.STATE_KEY]
        assert state["inputs"][identity]["human"] is (identity == "human")


def test_cancel_and_new_start_do_not_share_same_path_results(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path, "commit-1")
    human_input(tmp_path, "human", "この作業を中止し、次の作業を始める")
    document = {
        "session_id": "evidence-test",
        "input_id": "human",
        "quote": "この作業を中止し、次の作業を始める",
        "reason": "作業の切替",
    }
    termination_evidence.record_decision({**document, "action": "cancel", "work_id": "work-1"})
    new_id = termination_evidence.record_decision({**document, "action": "start"})
    supply_commit(tmp_path, "commit-2")
    works = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "")))
    assert [work_id for work_id, _ in works] == [new_id]
    assert works[0][1]["deliverables"] == ["commit"]


def test_cancelling_current_work_keeps_other_work(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
    prompt = "別の作業を始めたが、それは中止する"
    human_input(tmp_path, "switch", prompt)
    document = {"session_id": "evidence-test", "input_id": "switch", "quote": prompt, "reason": "後の作業の中止"}
    later_id = termination_evidence.record_decision({**document, "action": "start"})
    termination_evidence.record_decision({**document, "action": "cancel", "work_id": later_id})
    works = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "中止した。")))
    assert [work_id for work_id, _ in works] == ["work-1"]
    assert works[0][1]["deliverables"] == ["commit"]


@pytest.mark.parametrize("kind", ["isolated", "linked", "external"])
def test_managed_temp_exemption_preserves_linked_external_repository(tmp_path, monkeypatch, kind):
    temporary = tmp_path / "managed"
    temporary.mkdir()
    monkeypatch.setattr(termination_evidence_operations, "temp_root_path", lambda: temporary)
    if kind == "isolated":
        target = git_repository.init_repository(temporary / "clone")
    else:
        target = git_repository.init_repository(tmp_path / "normal")
        if kind == "linked":
            git_repository.run_git(target, "commit", "--allow-empty", "-m", "初期")
            linked = temporary / "linked"
            git_repository.run_git(target, "worktree", "add", "--detach", str(linked))
            target = linked
    human_input(tmp_path, "request", "作業を頼む")
    _tool(tmp_path, "edit", "Write", {"file_path": str(target / "file.txt")}, {"success": True}, codex=False)
    works = termination_evidence.session_works({"session_id": "evidence-test"})
    if kind == "isolated":
        assert works == []
    else:
        assert len(works) == 1
        assert works[0][1]["repositories"] == [str(target)]
        assert works[0][1]["edits"] == {str(target): ["file.txt"]}
