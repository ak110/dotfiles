"""受領前・別主体・保存本文の境界を保って判断資料を選択する。"""

import json

import pytest

from agent_toolkit._agents_server import responses, result_projection, wait_summary
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._hooks import notice, result_reads
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.fixture
def _memory(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(result_reads, "read_state", lambda _session: state)

    def update(_session, mutate, **_kwargs):
        return mutate(state)

    monkeypatch.setattr(result_reads, "update_state", update)
    return state


def _review(label="exec-review"):
    return result_projection.with_result_next_action(
        {"session_id": "child", "status": "completed", "agent_message": "指摘なし"}, label
    )


@pytest.mark.parametrize(
    "label,expected",
    [
        ("exec-review", "reviewee.md"),
        ("reader-fit-review", "reader-fit-review.parent.md#「受領」"),
    ],
)
@pytest.mark.usefixtures("_memory")
def test_received_result_uses_the_same_definition_as_next_action(label, expected):
    result = _review(label)
    documents = result_reads.observe("s", "main", {"structuredContent": result}, deferred=False)
    assert "next_action" in result and documents == result["read_requirements"]
    assert any(expected in document for document in documents)


@pytest.mark.parametrize("previous_result", [False, True])
@pytest.mark.usefixtures("_memory")
def test_codex_inner_result_waits_for_matching_outer_delivery_and_actor(previous_result):
    result = (
        responses.public_send_message_response(
            {
                "delivery": "reply_started",
                "label": "exec-review",
                "previous_result": SessionState(
                    "child", "/unused", status="completed", label="exec-review", agent_message="指摘なし"
                ).previous_result(),
            }
        )
        if previous_result
        else _review()
    )
    assert not result_reads.observe("s", "main", result, deferred=True)
    assert not result_reads.observe_outer("s", "other", [json.dumps(result)])
    assert result_reads.observe_outer("s", "main", [json.dumps({"output": json.dumps(result)})])
    assert not result_reads.observe_outer("s", "main", [json.dumps(result)])


@pytest.mark.usefixtures("_memory")
def test_cli_without_body_waits_for_the_registered_file(tmp_path, monkeypatch):
    path = tmp_path / "result.md"
    path.write_text("指摘本文" * 100, encoding="utf-8")
    result = {**_review(), "agent_message_path": str(path)}
    monkeypatch.setattr(wait_summary, "BODY_BUDGET_BYTES", 50)
    summary = wait_summary.render([result])
    assert "本文開始:" not in summary
    assert not result_reads.observe("s", "main", {"stdout": summary}, deferred=False)
    assert not result_reads.observe_file("s", "other", path)
    assert result_reads.observe_file("s", "main", path) == result["read_requirements"]
    assert not result_reads.observe_file("s", "main", tmp_path / "unrelated.md")


@pytest.mark.usefixtures("_memory")
def test_cli_delivered_body_selects_requirements(tmp_path):
    path = tmp_path / "result.md"
    path.write_text("指摘なし", encoding="utf-8")
    result = {**_review(), "agent_message_path": str(path)}
    summary = wait_summary.render([result])
    assert result_reads.observe("s", "main", {"stdout": summary}, deferred=False) == result["read_requirements"]


@pytest.mark.usefixtures("_memory")
def test_failed_and_nonreview_results_do_not_select_judgment_documents():
    failed = result_projection.with_result_next_action(
        {"status": "failed", "session_id": "child", "agent_message": "失敗"}, "exec-review"
    )
    assert not result_reads.observe("s", "main", failed, deferred=False)
    ordinary = result_projection.with_result_next_action(
        {"status": "completed", "session_id": "child", "agent_message": "完了"}, "implementation"
    )
    assert not result_reads.observe("s", "main", ordinary, deferred=False)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("improvement", [False, True])
@pytest.mark.usefixtures("_memory")
def test_failed_result_selects_only_its_improvement_guidance(tmp_path, status, improvement):
    """終了状態によらず、実配送した改善点案内だけを判断資料へ接続する。"""
    result = result_projection.with_result_next_action(
        {"status": status, "session_id": "child", "agent_message": "気付いた改善点: 観測した問題" if improvement else "失敗"},
        "exec-review",
    )
    path = tmp_path / "result.md"
    path.write_text(result["agent_message"], encoding="utf-8")
    result["agent_message_path"] = str(path)
    summary = wait_summary.render([result])
    documents = result_reads.observe("s", "main", {"structuredContent": result}, deferred=False)
    if status == "failed":
        assert result_reads.observe("s", "main", {"stdout": summary}, deferred=False) == documents
    if improvement:
        assert documents == ["agent-toolkit:delegation/references/receiving.md#「受領後の扱い」"]
    else:
        assert not documents


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_public_hook_receipt_guides_before_next_delegation(tmp_path, monkeypatch, _memory, status):
    """MCP実回収のPostから、次のstartの初回案内まで通す。"""
    del _memory
    for module in (read_delivery, notice):
        monkeypatch.setattr(module, "read_state", result_reads.read_state)
        monkeypatch.setattr(module, "update_state", result_reads.update_state)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    child = SessionState(
        "child",
        str(tmp_path),
        status=status,
        label="exec-review",
        agent_message="指摘なし" if status == "completed" else "気付いた改善点: 観測した問題",
    )
    response = responses.public_send_message_response(
        {"delivery": "reply_started", "label": child.label, "previous_result": child.previous_result()}
    )
    received = _run_hook_in_process(
        "posttooluse",
        {
            "session_id": "test",
            "cwd": str(tmp_path),
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__agents_server__send_message",
            "tool_input": {"session_id": "child"},
            "tool_response": {"structuredContent": response},
        },
    )
    assert received.returncode == 0
    operation = {
        "session_id": "test",
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__agents_server__start",
        "tool_input": {"cwd": str(tmp_path), "mode": "explore", "prompt": "次の調査"},
    }
    guided = _run_hook_in_process("pretooluse", operation)
    assert guided.returncode == 0
    expected = "reviewee.md" if status == "completed" else "receiving.md"
    assert expected in guided.stdout
    if status != "completed":
        assert "reviewee.md" not in guided.stdout
    assert '"permissionDecision": "deny"' not in guided.stdout
    repeated = _run_hook_in_process("pretooluse", operation)
    assert repeated.returncode == 0
    assert expected not in repeated.stdout
