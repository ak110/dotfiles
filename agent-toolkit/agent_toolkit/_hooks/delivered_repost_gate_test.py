"""送達後の終了応答と、未送達の本文への切替をStop入力で確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import delivered_repost_gate, stop
from agent_toolkit._hooks.pretooluse import dispatch

_SEND_TO_USER = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
_HUMAN = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "進捗を教えて"}}
_SEND_NOTICE = {
    "type": "attachment",
    "attachment": {
        "type": "hook_additional_context",
        "hookEvent": "UserPromptSubmit",
        "hookName": "prompt.submit",
        "content": ['<atk-auto source="send-to-user-contract" kind="notice">\n送達の案内\n</atk-auto>'],
    },
}
_TASK_NOTIFICATION = {"type": "user", "isMeta": True, "message": {"content": "<task-notification>完了</task-notification>"}}

_COMPACT = [
    {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"},
    {
        "type": "user",
        "isCompactSummary": True,
        "message": {"content": "This session is being continued from a previous conversation."},
    },
]
_SEND = {
    "type": "assistant",
    "message": {"content": [{"type": "tool_use", "id": "s1", "name": _SEND_TO_USER, "input": {"message": "進捗は順調です。"}}]},
}


def _call(name: str, identifier: str, inputs: dict) -> dict:
    block = {"type": "tool_use", "id": identifier, "name": name, "input": inputs}
    return {"type": "assistant", "message": {"content": [block]}}


def _result(identifier: str = "s1", *, error: bool = False, content: str | None = None) -> dict:
    block = {
        "type": "tool_result",
        "tool_use_id": identifier,
        "content": content if content is not None else ("対応画面なし" if error else "表示した"),
        "is_error": error,
    }
    return {"type": "user", "message": {"content": [block]}}


def _text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


@pytest.fixture(autouse=True)
def _main_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_HANDOFF_PATH",
        "AGENT_TOOLKIT_RUN_SKILL_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def _stop(entries: list[dict], tmp_path: pathlib.Path, **extra: object) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    return stop.evaluate(json.dumps({"session_id": "repost-gate", "transcript_path": str(path), **extra}))


@pytest.mark.parametrize(
    ("entries", "body", "blocked"),
    [
        ([_HUMAN, _SEND_NOTICE], "進捗を報告する。", True),
        ([_HUMAN, _SEND_NOTICE, _SEND], "進捗を報告する。", True),
        ([_HUMAN, _SEND_NOTICE, _SEND, _result("other")], "進捗を報告する。", True),
        ([_HUMAN, _SEND_NOTICE, _SEND, _result(error=True)], "進捗を報告する。", False),
        ([_HUMAN, _SEND_NOTICE, _SEND, _result()], "…", False),
        ([_HUMAN], "進捗を報告する。", False),
        ([_HUMAN, _SEND_NOTICE, _HUMAN], "進捗を報告する。", True),
        ([_HUMAN, _SEND_NOTICE, _TASK_NOTIFICATION], "進捗を報告する。", True),
        ([_HUMAN, _SEND_NOTICE], "", False),
        ([_HUMAN, _SEND_NOTICE], "…", False),
        ([_HUMAN, _SEND_NOTICE, _call("AskUserQuestion", "q", {"questions": [{"question": "どうする？"}]})], "", False),
        (
            [_HUMAN, _SEND_NOTICE, _call("AskUserQuestion", "q", {"questions": [{"question": "どうする？"}]})],
            "通常の本文。",
            True,
        ),
        ([_HUMAN, _SEND_NOTICE, _SEND, _result(error=True)], "", False),
    ],
    ids=[
        "missing",
        "pending",
        "unrelated-result",
        "failed",
        "delivered",
        "no-notice",
        "later-human",
        "task-notification",
        "no-text",
        "ellipsis",
        "question",
        "question-and-body",
        "failed-no-text",
    ],
)
def test_send_route_is_required_by_conversation(entries: list[dict], body: str, blocked: bool, tmp_path: pathlib.Path) -> None:
    result = _stop([*entries, _text(body)], tmp_path, last_assistant_message=body)
    assert (result.get("decision") == "block") is blocked
    if blocked:
        assert f"tools['{_SEND_TO_USER}']({{message}})" in str(result["reason"])


def test_subagent_is_not_required_to_send_to_user(tmp_path: pathlib.Path) -> None:
    result = _stop([_HUMAN, _SEND_NOTICE, _text("結果。")], tmp_path, agent_id="child", last_assistant_message="結果。")
    assert result.get("decision") != "block"


@pytest.mark.parametrize(
    "body",
    [
        "進捗は順調です。検証も通りました。",
        "> 進捗は順調です。\n次の検証に進みます。",
        "進捗は順調です",
    ],
)
def test_literal_repost_after_delivery_is_blocked(body: str, tmp_path: pathlib.Path) -> None:
    result = _stop([_HUMAN, _SEND, _result(), _text(body)], tmp_path)
    assert result["decision"] == "block"
    assert "再掲した文を除き" in str(result["reason"])


_QUESTION = _call("AskUserQuestion", "q", {"questions": [{"question": "文体は？"}]})
_ANSWER = {
    "type": "user",
    "message": {"content": [{"type": "tool_result", "tool_use_id": "q", "content": ""}]},
    "toolUseResult": {"questions": [{"question": "文体は？"}], "answers": {"文体は？": "敬体"}, "annotations": {}},
}
_REJECTED = {
    **_result("q", error=True, content="The user doesn't want to proceed with this tool use."),
    "toolUseResult": "User rejected tool use",
}


@pytest.mark.parametrize("preface", [False, True])
@pytest.mark.parametrize("closing", ["", "…"])
@pytest.mark.parametrize(
    "after_question",
    [
        [],
        [{**_REJECTED, "toolDenialKind": "user-rejected"}],
        [{**_result("q", error=True, content="[Request interrupted by user for tool use]"), "toolDenialKind": "interrupted"}],
        [_REJECTED],
        [_result("other")],
    ],
    ids=["pending", "rejected", "interrupted", "rejected-without-kind", "unrelated-result"],
)
def test_stop_while_waiting_for_answer(preface: bool, closing: str, after_question: list[dict], tmp_path: pathlib.Path) -> None:
    entries = [_HUMAN]
    if preface:
        entries.extend([_SEND, _result()])
    entries.extend([_QUESTION, *after_question])
    if closing:
        entries.append(_text(closing))
    result = _stop(entries, tmp_path, last_assistant_message=closing)
    assert "decision" not in result
    assert "user_response_advisor" not in result.get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.mark.parametrize("preface", [False, True])
@pytest.mark.parametrize("answer", [_ANSWER, _result("q", content="敬体")])
def test_answer_requires_a_response_after_the_question(preface: bool, answer: dict, tmp_path: pathlib.Path) -> None:
    request = {**_HUMAN, "message": {"content": "文体を確認して読書会の案内を作成して"}}
    entries = [request, *([_SEND, _result()] if preface else []), _QUESTION, answer, _text("…")]
    result = _stop(entries, tmp_path, last_assistant_message="…")
    assert "decision" not in result
    assert "user_response_advisor" in result["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    "followup,closing",
    [
        ([], "読書会へぜひご参加ください。"),
        ([_call(_SEND_TO_USER, "s2", {"message": "読書会へぜひご参加ください。"}), _result("s2")], "…"),
        ([_call("AskUserQuestion", "q2", {"questions": [{"question": "何時に開始しますか？"}]})], ""),
    ],
    ids=["body", "delivery", "next-question"],
)
def test_visible_response_or_new_question_after_an_answer(followup: list[dict], closing: str, tmp_path: pathlib.Path) -> None:
    entries = [_HUMAN, _QUESTION, _ANSWER, *followup]
    if closing:
        entries.append(_text(closing))
    result = _stop(entries, tmp_path, last_assistant_message=closing)
    assert "decision" not in result
    assert "user_response_advisor" not in result.get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.mark.parametrize(
    "after_question",
    [
        [{**_result("q", error=True, content="PreToolUse:AskUserQuestion hook error"), "toolDenialKind": "permission-rule"}],
        [_result("q", error=True, content="<tool_use_error>Error: No such tool available: AskUserQuestion</tool_use_error>")],
        [_HUMAN],
        [_call("AskUserQuestion", "q2", {"questions": [{"question": "開催日は？"}]}), _result("q2")],
    ],
    ids=["hook-blocked", "tool-unavailable", "new-prompt", "latest-question-answered"],
)
def test_question_does_not_cover_unanswered_prompt(after_question: list[dict], tmp_path: pathlib.Path) -> None:
    result = _stop([_HUMAN, _QUESTION, *after_question, _text("…")], tmp_path, last_assistant_message="…")
    assert "decision" not in result
    assert "user_response_advisor" in result["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    "body", ["", "送信しました。", "受付番号S-38", "予算優先で進めます。", "担当の工程は完了しました。次に検証します。"]
)
def test_non_repost_is_not_blocked_after_delivery(body: str, tmp_path: pathlib.Path) -> None:
    entries = [_HUMAN, _SEND, _result(), _QUESTION, _ANSWER, _text(body)]
    assert _stop(entries, tmp_path, last_assistant_message=body).get("decision") != "block"


def test_without_successful_delivery_the_body_is_not_judged(tmp_path: pathlib.Path) -> None:
    """送達が無い、または送達が失敗したターンの本文は再掲に当たらない。"""
    two = "進捗は順調です。検証も通りました。"
    assert "decision" not in _stop([_HUMAN, _text(two)], tmp_path)
    assert "decision" not in _stop([_HUMAN, _SEND, _result(error=True), _text(two)], tmp_path)
    assert "decision" not in _stop([_HUMAN, _text(two), _SEND, _result()], tmp_path)


def test_failed_final_delivery_after_progress_can_fall_back_to_text(tmp_path: pathlib.Path) -> None:
    report = "検証が終わりました。失敗はありません。"
    send = _call(_SEND_TO_USER, "s2", {"message": report})
    failed = [_HUMAN, _SEND, _result(), send, _result("s2", error=True)]
    english = "The work is complete."
    reason = _stop([*failed, _text(english)], tmp_path, last_assistant_message=english)["reason"]
    assert 'source="response-language"' in reason and "delivered_repost_gate" not in reason
    result = _stop([*failed, _text(report)], tmp_path, last_assistant_message=report)
    assert "decision" not in result
    repeated = "進捗は順調です。" + report
    result = _stop([*failed, _text(repeated)], tmp_path, last_assistant_message=repeated)
    assert "delivered_repost_gate" in str(result.get("reason", ""))
    result = _stop([*failed, _HUMAN, _text(repeated)], tmp_path, last_assistant_message=repeated)
    assert "decision" not in result


@pytest.mark.parametrize("name", ["mcp__other__send_to_user", "mcp__agent-toolkit__send_to_user"])
def test_delivery_is_scoped_to_toolkit_including_resumed_calls(name: str, tmp_path: pathlib.Path) -> None:
    body = "案内です。"
    entries = [_HUMAN, _call(name, "s1", {"message": "案内です。"}), _result(), _text(body)]
    assert ("delivered_repost_gate" in _stop(entries, tmp_path, last_assistant_message=body).get("reason", "")) is (
        name == "mcp__agent-toolkit__send_to_user"
    )


def test_pending_async_work_and_subagent(tmp_path: pathlib.Path) -> None:
    """非同期の待機が残るStopにも適用し、サブエージェントは対象にしない。"""
    entries = [_HUMAN, _SEND, _result(), _text("進捗は順調です。")]
    background = [{"id": "b1", "type": "shell", "status": "running", "description": "待機"}]
    assert _stop(entries, tmp_path, background_tasks=background)["decision"] == "block"
    assert "decision" not in _stop(entries, tmp_path, agent_id="sub-1")


@pytest.mark.parametrize("command", ["goal", "restart"])
@pytest.mark.parametrize("sent_in_current_turn", [False, True])
def test_originless_command_starts_its_own_delivery_turn(
    command: str, sent_in_current_turn: bool, tmp_path: pathlib.Path
) -> None:
    """Claude Codeのコマンド入力は前ターンの送達を切り離し、今回の送達後の再掲だけを止める。"""
    command_input = {
        "type": "user",
        "promptId": "new-prompt",
        "message": {
            "content": f"<command-name>/{command}</command-name>\n"
            f"<command-message>{command}</command-message>\n<command-args>進捗を教えて</command-args>"
        },
    }
    entries = [_HUMAN, _SEND, _result(), command_input]
    if sent_in_current_turn:
        entries += [_SEND, _result()]
    result = _stop([*entries, _text("進捗は順調です。")], tmp_path)
    assert (result.get("decision") == "block") is sent_in_current_turn


@pytest.mark.parametrize(
    "extra",
    [
        {"isMeta": True},
        {"isSidechain": True},
        {"type": "system", "subtype": "local_command"},
        {"origin": {"kind": "machine"}},
        {"message": {"content": "機械側の通知"}},
    ],
)
def test_machine_command_record_does_not_reset_delivery(extra: dict, tmp_path: pathlib.Path) -> None:
    """機械側のコマンド記録やpromptIdの変化で、同じターンの送達を消さない。"""
    command = {
        "type": "user",
        "promptId": "machine-prompt",
        "message": {
            "content": "<command-name>/goal</command-name>\n"
            "<command-message>goal</command-message>\n<command-args>続けて</command-args>"
        },
        **extra,
    }
    result = _stop([_HUMAN, _SEND, _result(), command, _text("進捗は順調です。")], tmp_path)
    assert result["decision"] == "block"


@pytest.mark.parametrize(
    ("entries", "blocked"),
    [
        ([_HUMAN, _SEND_NOTICE, _text("…"), _TASK_NOTIFICATION], True),
        ([_HUMAN, _SEND_NOTICE, _SEND, _result(), _text("…"), *_COMPACT], True),
        ([_HUMAN, _text("回答です。"), *_COMPACT, _TASK_NOTIFICATION], False),
        # 前のターンの送達の成功・失敗は、完了通知で始まる現ターンの送達に数えない。
        ([_HUMAN, _SEND_NOTICE, _SEND, _result(), _text("…"), _TASK_NOTIFICATION], True),
        ([_HUMAN, _SEND_NOTICE, _SEND, _result(error=True), _text("失敗の報告。"), _TASK_NOTIFICATION], True),
    ],
    ids=["task-notification", "after-compaction", "no-notice", "earlier-turn-delivered", "earlier-turn-failed"],
)
def test_send_route_spans_turns_and_compaction(entries: list[dict], blocked: bool, tmp_path: pathlib.Path) -> None:
    body = "完了した処理を報告する。"
    result = _stop([*entries, _text(body)], tmp_path, last_assistant_message=body)
    assert (result.get("decision") == "block") is blocked
    if blocked:
        assert "を使える会話で" in str(result["reason"])


def test_repost_after_delivery_in_a_later_turn_is_blocked(tmp_path: pathlib.Path) -> None:
    body = "進捗は順調です。"
    entries = [_HUMAN, _SEND_NOTICE, _text("…"), _TASK_NOTIFICATION, _SEND, _result(), _text(body)]
    result = _stop(entries, tmp_path, last_assistant_message=body)
    assert result["decision"] == "block"
    assert "送信済みの文が通常の応答本文にある" in str(result["reason"])


def _response(identifier: str, *entries: dict) -> list[dict]:
    return [{**entry, "message": {**entry["message"], "id": identifier}} for entry in entries]


@pytest.mark.parametrize("body", ["発話の理解", "Now …"])
def test_pretooluse_notifies_once_per_response(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    monkeypatch.setattr(session_state, "state_path", lambda _: tmp_path / "state.json")
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(entry)
            for entry in [
                _SEND_NOTICE,
                _HUMAN,
                *_response("m1", _text(body), *(_call("Read", f"t{i}", {}) for i in range(5))),
                *_response("m2", _call("Read", "t5", {}), _text(body)),
            ]
        ),
        encoding="utf-8",
    )
    for tool_use_id, expected in [("t4", True), ("t0", False), ("t5", True)]:
        payload = {
            "session_id": "delivery-progress",
            "tool_name": "Read",
            "tool_use_id": tool_use_id,
            "transcript_path": str(path),
            "cwd": str(tmp_path),
        }
        assert dispatch.main(json.dumps(payload)) == 0
        stdout = capsys.readouterr().out
        output = json.loads(stdout)["hookSpecificOutput"] if stdout else {}
        assert "permissionDecision" not in output
        context = output.get("additionalContext", "")
        assert ("`send_to_user` を使ってください" in context) is expected
        assert body not in context


@pytest.mark.parametrize(
    ("prefix", "response", "extra", "expected"),
    [
        ([], _text("Now …"), {}, True),
        ([], _text("  "), {}, False),
        ([], _call(_SEND_TO_USER, "delivery", {"message": "Now …"}), {}, False),
        ([], {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Now …"}]}}, {}, False),
        ([], _text("Now …"), {"agent_id": "delegate"}, False),
        ([], _text("Now …"), {"turn_id": "codex"}, False),
        ([], _text("Now …"), {"runtime": "agy"}, False),
        ([_SEND, _result(error=True)], _text("Now …"), {}, False),
        ([_SEND, _result(error=True), _result("unrelated"), _SEND], _text("Now …"), {}, False),
        ([_SEND, _result(error=True), _SEND, _result()], _text("Now …"), {}, True),
        ([_SEND, _result(error=True), _HUMAN], _text("Now …"), {}, True),
        ([_SEND, _result(error=True), _TASK_NOTIFICATION], _text("Now …"), {}, True),
        ([_SEND, _result(error=True), *_COMPACT], _text("Now …"), {}, True),
        ([], _text("Now …"), {"tool_use_id": "missing"}, False),
        ([], _text("Now …"), {"tool_use_id": "repl_tool"}, False),
    ],
)
def test_pretooluse_delivery_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, prefix: list[dict], response: dict, extra: dict, expected: bool
) -> None:
    monkeypatch.setattr(session_state, "state_path", lambda _: tmp_path / "state.json")
    path = tmp_path / "transcript.jsonl"
    mixed = _call("Read", "tool", {"file_path": "not visible"})
    mixed["message"]["content"][:0] = response["message"]["content"]
    entries = [_SEND_NOTICE, _HUMAN, *prefix, *_response("m1", mixed)]
    for include_contract in (False, True):
        path.write_text(
            "\n".join(json.dumps(entry) for entry in (entries if include_contract else entries[1:])),
            encoding="utf-8",
        )
        payload = {"transcript_path": str(path), "tool_use_id": "tool", **extra}
        assert bool(delivered_repost_gate.pretooluse_notice(payload, "delivery-progress")) is (expected and include_contract)


@pytest.mark.parametrize("required", [False, True])
def test_pretooluse_reads_only_recent_records(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, required: bool) -> None:
    monkeypatch.setattr(session_state, "state_path", lambda _: tmp_path / "state.json")
    path = tmp_path / "transcript.jsonl"
    prefix = ([_SEND_NOTICE] if required else []) + [_HUMAN, _text("過去" * 500), *_COMPACT]
    path.write_text("".join(json.dumps(entry) + "\n" for entry in prefix), encoding="utf-8")
    payload = {"transcript_path": str(path), "tool_use_id": "tool"}
    assert delivered_repost_gate.pretooluse_notice(payload, "delivery-progress") is None

    with path.open("a", encoding="utf-8") as stream:
        for entry in _response("m1", _text("途中の応答"), _call("Read", "tool", {})):
            stream.write(json.dumps(entry) + "\n")

    offsets: list[int] = []
    read = delivered_repost_gate.transcript.read_transcript_bytes

    def record_offset(path: str, *, offset: int = 0) -> bytes | None:
        offsets.append(offset)
        return read(path, offset=offset)

    monkeypatch.setattr(delivered_repost_gate.transcript, "read_transcript_bytes", record_offset)
    assert bool(delivered_repost_gate.pretooluse_notice(payload, "delivery-progress")) is required
    assert delivered_repost_gate.pretooluse_notice(payload, "delivery-progress") is None
    assert len(offsets) == 2 and all(offset > 1000 for offset in offsets)
