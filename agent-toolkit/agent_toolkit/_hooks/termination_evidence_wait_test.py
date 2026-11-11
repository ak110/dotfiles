# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_waitの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._atk.wi import entries, uwi_scan
from agent_toolkit._atk.wi.public_layout_test import _run
from agent_toolkit._common import session_state
from agent_toolkit._hooks import termination_evidence, termination_order_advisor
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session
from agent_toolkit._testing.termination_evidence_support import public_decision, stop_payload, supply_commit

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


def test_monthly_waiting_uwi_public_command(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開保存の年月UWIを待機判断と走査で共有し、回答後は残工程へ戻る。"""
    repo_id = "github.com/example/repo"
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    body = tmp_path / "question.md"
    body.write_text("今回の変更を公開してよいか。\n", encoding="utf-8")
    _run(
        notes,
        "add",
        "--type=uwi",
        "--question-type=yes-no",
        "--source=agent",
        f"--target-repo={repo_id}",
        "--body-file",
        str(body),
    )
    paths: list[pathlib.Path] = list(entries.iter_entry_paths(notes))
    assert len(paths) == 1
    question: pathlib.Path = paths[0]
    assert question == notes / question.name[:4] / question.name[4:6] / question.name
    (tmp_path / "transcript.jsonl").write_text("", encoding="utf-8")
    supply_commit(tmp_path)
    document = {
        "session_id": "evidence-test",
        "action": "wait",
        "work_id": "work-1",
        "uwi_file": str(question),
        "reason": "公開可否の回答を待つ",
    }
    for state in ("inbox", "processing"):
        if state == "processing":
            _run(notes, "start-processing", question.name, f"--target-repo={repo_id}")
        status, reason = public_decision(tmp_path, document)
        assert status == 0, reason
        assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答を待つ。"))[0] == "approve"
        scanned = uwi_scan.scan_active_uwis(notes, repo_id)
        assert scanned.complete
        assert scanned.entries == [uwi_scan.ActiveUwi(question.name, False, "evidence-test")]
    original = question.read_text(encoding="utf-8")
    question.write_text(original + "\n公開してよい。\n", encoding="utf-8")
    assert public_decision(tmp_path, document)[0] != 0
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答を受け取った。"))[0] == "notify"


def test_waiting_for_answer_is_scoped_and_returns_to_remaining_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supply_commit(tmp_path)
    queue = tmp_path / "private-notes"
    inbox = queue / "2026" / "10"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    question = inbox / "20261003-000000-001.md"
    original = (
        "---\ntype: uwi\nstate: inbox\nsubmitter_session: evidence-test\n---\n\n## 確認\n\n目的を選んでください。\n\n## 回答\n"
    )
    question.write_text(original, encoding="utf-8")
    termination_evidence.record_decision(
        {
            "session_id": "evidence-test",
            "action": "wait",
            "work_id": "work-1",
            "uwi_file": str(question),
            "quote": original,
            "reason": "選好の回答を待つ",
        }
    )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答を待つ。"))[0] == "approve"
    question.write_text(original + "今日だけの残量を示す。\n", encoding="utf-8")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答を受け取った。"))[0] == "notify"


def test_unrelated_or_invented_wait_target_does_not_clear_remaining_work(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
    with pytest.raises(ValueError, match="作業に対応しません"):
        termination_evidence.record_decision(
            {
                "session_id": "evidence-test",
                "action": "wait",
                "work_id": "work-1",
                "target_session_id": "存在しない委譲先",
                "reason": "待機したという申告",
            }
        )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "待機中"))[0] == "notify"


@pytest.mark.parametrize(
    "problem",
    [
        "outside",
        "wrong-month",
        "relative",
        "hold",
        "adopted",
        "rejected",
        "missing-state",
        "foreign",
        "awi",
        "answered",
        "quote",
    ],
)
def test_waiting_uwi_rejects_invalid_membership_and_metadata(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    """年月所属、処理可能状態、投入会話、未回答、全文引用の境界を公開判断CLIで検証する。"""
    supply_commit(tmp_path)
    queue = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    question = queue / "2026/10/20261030-120000-001.md"
    if problem == "outside":
        question = tmp_path / "other/2026/10/20261030-120000-001.md"
    elif problem == "wrong-month":
        question = queue / "2026/09/20261030-120000-001.md"
    question.parent.mkdir(parents=True)
    state = problem if problem in {"hold", "adopted", "rejected"} else "inbox"
    metadata = (
        f"type: {'awi' if problem == 'awi' else 'uwi'}\n"
        f"submitter_session: {'other' if problem == 'foreign' else 'evidence-test'}\n"
    )
    if problem != "missing-state":
        metadata += f"state: {state}\n"
    text = f"---\n{metadata}---\n\n## 確認\n\n公開してよいか。\n\n## 回答\n"
    question.write_text(text + ("はい。\n" if problem == "answered" else ""), encoding="utf-8")
    document = {
        "session_id": "evidence-test",
        "action": "wait",
        "work_id": "work-1",
        "uwi_file": question.name if problem == "relative" else str(question),
        "reason": "回答待ち",
    }
    if problem == "quote":
        document["quote"] = "本文と異なる引用"
    assert public_decision(tmp_path, document)[0] != 0
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答を待つ。"))[0] == "notify"


def test_corrupt_or_foreign_state_does_not_block(tmp_path: pathlib.Path) -> None:
    """証拠の状態が別の版・形式で読み取れない場合は、呼び忘れと決め付けずに許可する。"""
    supply_commit(tmp_path)

    def corrupt(state: dict) -> dict:
        state[termination_evidence.STATE_KEY]["version"] = 0
        return state

    session_state.update_state("evidence-test", corrupt)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "")) == ("approve", "")


def test_unreadable_transcript_does_not_block(tmp_path: pathlib.Path) -> None:
    """待機対象を確かめるtranscriptを読み取れない場合は遮断しない。"""
    supply_commit(tmp_path)
    (tmp_path / "transcript.jsonl").unlink()
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "")) == ("approve", "")


def test_async_wait_is_scoped_to_real_child_and_returns_after_end(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
    start = {
        "session_id": "evidence-test",
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
        "tool_use_id": "start-1",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
        "tool_input": {"prompt": "調査する"},
    }
    termination_evidence.observe_tool(json.dumps(start), after=False)
    start["tool_response"] = {"structuredContent": {"session_id": "child-1"}}
    termination_evidence.observe_tool(json.dumps(start), after=True)

    def register(state: dict) -> dict:
        state.setdefault("agents_server_sessions", {})["child-1"] = {"owner_agent_id": "main", "pending_observation": True}
        return state

    session_state.update_state("evidence-test", register)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "待機中"))[0] == "approve"
    termination_evidence.record_decision(
        {
            "session_id": "evidence-test",
            "action": "wait",
            "work_id": "work-1",
            "target_session_id": "child-1",
            "reason": "調査結果を待つ",
        }
    )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "待機中"))[0] == "approve"

    def finish(state: dict) -> dict:
        state["agents_server_sessions"]["child-1"]["pending_observation"] = False
        return state

    session_state.update_state("evidence-test", finish)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "結果を受け取った"))[0] == "notify"


def test_blocked_requires_actual_failure_of_same_work(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
    failure = {
        "session_id": "evidence-test",
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash",
        "tool_use_id": "network",
        "tool_input": {"command": "uv sync"},
        "tool_response": {"stdout": "", "stderr": "接続できない", "exit_code": 1},
    }
    termination_evidence.observe_tool(json.dumps(failure), after=True)
    base = {"session_id": "evidence-test", "action": "blocked", "work_id": "work-1", "reason": "依存を取得できない"}
    with pytest.raises(ValueError, match="失敗応答"):
        termination_evidence.record_decision({**base, "call_id": "network", "quote": "成功した"})
    quote = json.dumps(failure["tool_response"], ensure_ascii=False, sort_keys=True)
    termination_evidence.record_decision({**base, "call_id": "network", "quote": quote})
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "取得できない"))[0] == "approve"


@pytest.mark.parametrize("action", ["blocked", "wait"])
def test_rejected_host_stop_guides_to_continue_or_confirmation(tmp_path: pathlib.Path, action: str) -> None:
    """通知による停止は受理条件を保ち、拒否の後の実行可能な工程へ案内する。"""
    supply_commit(tmp_path)
    if action == "wait":

        def register(state: dict) -> dict:
            state[termination_evidence.STATE_KEY]["works"]["work-1"]["async_targets"]["child"] = "started"
            state["agents_server_sessions"] = {"child": {"owner_agent_id": "main", "pending_observation": False}}
            return state

        session_state.update_state("evidence-test", register)
    document = {"session_id": "evidence-test", "work_id": "work-1", "action": action, "reason": "ホストの通知"}
    document.update({"call_id": "commit"} if action == "blocked" else {"target_session_id": "child"})
    code, reason = public_decision(tmp_path, document)
    assert code == 2
    assert "猶予通知" in reason and "usage-limit.md" in reason and "続けよ" in reason
    assert "AskUserQuestion" in reason and "uwi_file" in reason
