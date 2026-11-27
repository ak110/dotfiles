# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_decisionsの契約テスト。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._testing.termination_evidence_support import human_input, public_decision, supply_commit
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


@pytest.mark.parametrize(
    "action",
    ["cancel", "replace", "start", "resume", "instruction", "answer", "standing-authorization", "wait", "blocked"],
)
@pytest.mark.parametrize("quote_mode", ["omitted", "matching", "different"])
def test_decision_cli_reads_evidence_from_identifiers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, action: str, quote_mode: str
) -> None:
    human_input(tmp_path, "request", "公開せず作業を進める")
    supply_commit(tmp_path)
    document: dict[str, Any] = {
        "session_id": "evidence-test",
        "action": action,
        "work_id": "work-1",
        "reason": "実体に基づく判断",
        "input_id": "request",
    }
    text = "公開せず作業を進める"
    if action in {"instruction", "answer", "standing-authorization"}:
        document.update(action="publish-scope", origin=action, scope="commitしない")
        if action == "standing-authorization":
            policy = tmp_path / "policy.md"
            text = "この作業の公開範囲はcommitしない。\n"
            policy.write_text(text, encoding="utf-8")
            document["policy_file"] = str(policy)
    elif action == "wait":
        queue = tmp_path / "private-notes"
        inbox = queue / "2026" / "10"
        inbox.mkdir(parents=True)
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
        question = inbox / "20261016-000000-001.md"
        text = (
            "---\ntype: uwi\nstate: inbox\nsubmitter_session: evidence-test\n---\n\n## 確認\n\n選択してください。\n\n## 回答\n"
        )
        question.write_text(text, encoding="utf-8")
        document["uwi_file"] = str(question)
    elif action == "blocked":
        response = {"exit_code": 1, "stderr": "接続できない"}
        termination_evidence.observe_tool(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "tool_name": "Bash",
                    "tool_use_id": "failed-call",
                    "tool_input": {"command": "false"},
                    "tool_response": response,
                }
            ),
            after=True,
        )
        document["call_id"] = "failed-call"
        text = json.dumps(response, ensure_ascii=False, sort_keys=True)
    if quote_mode != "omitted":
        document["quote"] = text if quote_mode == "matching" else "実体と異なる本文"
    status, reason = public_decision(tmp_path, document)
    assert status == (2 if quote_mode == "different" else 0), reason
    if quote_mode != "different":
        works = termination_evidence.session_works({"session_id": "evidence-test"})
        work = works[-1][1] if action == "start" else works[0][1]
        if action in {"instruction", "answer", "standing-authorization"}:
            assert work["publish_scope"]["origin"] == action
            if action == "standing-authorization":
                assert work["publish_scope"]["policy_text"] == text
        else:
            assert work["decisions"][-1]["action"] == action
