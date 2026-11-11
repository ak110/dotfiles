# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_contextの契約テスト。"""

import argparse
import contextlib
import io
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._common import automated_prompt, session_state
from agent_toolkit._hooks import termination_evidence, termination_order_advisor
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.termination_evidence_support import (
    _omission_document,
    _queued_command,
    _register_child,
    automated_input,
    automated_session,
    human_input,
    public_context,
    public_decision,
    stop_payload,
    supply_commit,
)
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


WORK_COMPLETE = "## 作業完了報告\n\n完了した。\n\n### 成果\n\n- 変更した\n\n### 投入したWI\n\n- なし\n"


CONTINUATION = '<atk-auto source="stop" kind="continuation">判断を記録する</atk-auto>'


def test_context_without_human_input_returns_work_ids(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """自動起動の会話でも識別子を取得でき、生成入力を人間入力として返さない。"""
    automated_session(tmp_path, monkeypatch)
    termination_evidence.observe_user(
        json.dumps(
            {
                "session_id": "evidence-test",
                "prompt": "You are Codex, an agent based on GPT-6.",
            }
        )
    )
    status, context = public_context(capsys)
    assert status == 0
    assert context == {"session_id": "evidence-test", "input_id": None, "quote": None, "work_id": "work-1"}


def test_context_with_human_input_keeps_quote(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """人間入力を持つ会話では、従来どおり原入力の識別子と全文を返す。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    human_input(tmp_path, "human-1", "READMEを直してcommitする")
    supply_commit(tmp_path)
    automated_input(tmp_path, "continuation", CONTINUATION)
    status, context = public_context(capsys)
    assert status == 0
    assert context == {
        "session_id": "evidence-test",
        "input_id": "human-1",
        "quote": "READMEを直してcommitする",
        "work_id": "work-1",
    }


def test_notifications_sharing_the_prompt_id_keep_the_human_input(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude Codeが人間の入力と同じ`prompt_id`で届けるtask-notificationの後も、人間の本文と識別子で終了判断を記録できる。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    repository = git_repository.init_repository(tmp_path / "repo", files={"CLAUDE.md": "完了報告は指示まで省く。\n"})
    automated_input(tmp_path, "turn-1", "READMEを直してcommitする")
    supply_commit(repository)
    for index in range(2):
        automated_input(tmp_path, "turn-1", f"<task-notification>\n委譲先{index}が終了した\n</task-notification>")
    status, context = public_context(capsys)
    assert status == 0
    assert context == {
        "session_id": "evidence-test",
        "input_id": "turn-1",
        "quote": "READMEを直してcommitする",
        "work_id": "work-1",
    }
    status, reason = public_decision(tmp_path, _omission_document(repository, "turn-1"))
    assert status == 0, reason
    work = termination_evidence.session_works({"session_id": "evidence-test"})[0][1]
    assert work["omit_completion"]["input_id"] == "turn-1"


@pytest.mark.parametrize("human_request", [True, False])
def test_queued_human_input_is_the_current_request_and_machine_queued_input_is_not(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], human_request: bool
) -> None:
    """途中で届いた人間の指示を最新の入力として返し、機械の途中入力だけの作業は人間の承認として受理しない。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    repository = git_repository.init_repository(tmp_path / "repo", files={"CLAUDE.md": "完了報告は指示まで省く。\n"})
    if human_request:
        human_input(tmp_path, "request", "成果を作成する")
    else:
        automated_input(
            tmp_path,
            "goal",
            automated_prompt.wrap("WIを処理する", source=automated_prompt.SOURCE_PROCESS_LOOP, kind=automated_prompt.KIND_GOAL),
        )
    supply_commit(repository)
    # 公開hookの補助関数は最後に観測した呼び出しの作業ディレクトリのセッション記録を渡すため、同じ記録へ追記する。
    if human_request:
        _queued_command(repository, "queued-human", "READMEも直して", "human")
    _queued_command(
        repository, "queued-task", "<task-notification>\n委譲先が終了した\n</task-notification>", "task-notification"
    )
    status, context = public_context(capsys)
    assert status == 0 and context is not None
    if human_request:
        assert (context["input_id"], context["quote"]) == ("queued-queued-human", "READMEも直して")
        # 途中の指示に応じた作業を観測した後は、その指示を省略判断の人間入力として受理する。
        supply_commit(repository, "commit-after-queued")
        status, reason = public_decision(tmp_path, _omission_document(repository, "queued-queued-human"))
        assert status == 0, reason
    else:
        assert (context["input_id"], context["quote"]) == (None, None)
        status, reason = public_decision(tmp_path, _omission_document(repository, "queued-queued-task"))
        assert status != 0 and "input_id" in reason


@pytest.mark.parametrize(
    "case",
    [
        "wait",
        "blocked-without-failure",
        "unowned-wait",
        "cancel",
        "replace",
        "start",
        "resume",
        "publish-scope",
        "omit-completion",
        "no-evidence",
        "foreign-session",
        "delegated",
    ],
)
def test_context_without_human_input_keeps_rejections(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """人間入力の無い取得から委譲先待機は記録でき、証拠を欠く判断と無効な状態の拒否は維持される。"""
    if case == "no-evidence":
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
        assert public_context(capsys)[0] == 2
        return
    automated_session(tmp_path, monkeypatch)
    if case == "foreign-session":
        # 別会話から継承した証拠は現在の会話の識別子として返さない。
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "another-session")
        session_state.update_state("another-session", lambda _state: session_state.read_state("evidence-test"))
        assert public_context(capsys)[0] == 2
        return
    if case == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
        assert public_context(capsys)[0] == 2
        return
    if case in {"wait", "unowned-wait"}:
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
        _register_child("main" if case == "wait" else "another")
    status, context = public_context(capsys)
    assert status == 0 and context is not None and context["input_id"] is None
    document: dict[str, Any] = {"session_id": context["session_id"], "work_id": context["work_id"], "reason": "判断する"}
    if case in {"wait", "unowned-wait"}:
        document.update(action="wait", target_session_id="child-1")
    elif case == "blocked-without-failure":
        document.update(action="blocked", call_id="commit", quote=json.dumps({"exit_code": 0}))
    else:
        # 人間の原入力を要する判断へ、取得できた唯一の入力である生成入力を渡す。
        document.update(action=case, input_id="continuation", quote=CONTINUATION)
        if case == "publish-scope":
            document.update(scope="commitしない", origin="instruction")
        elif case == "omit-completion":
            document.update(policy_file=str(tmp_path / "CLAUDE.md"), target_repo=str(tmp_path))
    status, reason = public_decision(tmp_path, document)
    # 拒否理由は、actionごとの証拠条件（実際の失敗応答、所有する委譲先、人間の原入力）を示す。
    expected_reason = {
        "wait": "",
        "blocked-without-failure": "失敗call_id",
        "unowned-wait": "所有していません",
    }.get(case, "人間入力" if case == "omit-completion" else "生成入力は人間の判断の根拠にできない")
    assert (status, expected_reason in reason) == (0 if case == "wait" else 2, True)
    pending = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "待機中")))
    assert [work_id for work_id, _ in pending] == ([] if case == "wait" else ["work-1"])


def test_decision_cli_rejects_completion_claim(tmp_path: pathlib.Path) -> None:
    decision = tmp_path / "decision.json"
    decision.write_text(
        json.dumps({"session_id": "evidence-test", "action": "complete", "work_id": "work-1", "reason": "完了した"}),
        encoding="utf-8",
    )
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
        status = run_script.dispatch(
            argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(decision)])
        )
    assert status == 2


def test_notice_gives_identifiers_needed_for_decision(tmp_path: pathlib.Path) -> None:
    human_input(tmp_path, "human-1", "作業を頼む")
    supply_commit(tmp_path)
    reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))[1]
    assert "evidence-test" in reason and "human-1" in reason and "work-1" in reason
