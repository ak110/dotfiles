"""警告のhook通知に対応行の無い終了を、Stop入力の記録で一度だけ止めることを確かめる。 pyfltr: allow-comment"""

import json
import pathlib

import pytest

from agent_toolkit._common import send_misdetection, session_record_paths
from agent_toolkit._hooks import hook_notice_response_gate as gate
from agent_toolkit._hooks.user_prompt_submit_test import _run_subcommand
from agent_toolkit._testing.helpers import repl_send_to_user_entries

_HUMAN = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "調べて"}}
_SEND_TO_USER = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
_WARN = (
    '<atk-auto source="pretooluse" kind="warn">\n'
    "セッション原記録の直接参照である。\n次の操作: 照会コマンドを使ってください。\n</atk-auto>"
)
_NOTICE = '<atk-auto source="pretooluse" kind="notice">\n検索の前に、スキルを起動してください。\n</atk-auto>'


def _attachment(
    body: str, *, event: str = "PreToolUse", record_type: str = "hook_additional_context", uuid: str = "warn-1"
) -> dict:
    content: object = [body] if record_type == "hook_additional_context" else body
    return {
        "type": "attachment",
        "uuid": uuid,
        "attachment": {
            "type": record_type,
            "content": content,
            "hookName": f"{event}:Bash",
            "toolUseID": "call-1",
            "hookEvent": event,
        },
    }


def _text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _call(name: str, identifier: str, inputs: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": identifier, "name": name, "input": inputs}]},
    }


def _result(identifier: str, content: str) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": identifier, "content": content}]}}


def _evaluate(entries: list[dict], tmp_path: pathlib.Path, **extra: object) -> tuple[str, str]:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    payload = {"session_id": "hook-notice-response", "transcript_path": str(path), **extra}
    return gate.evaluate(json.dumps(payload))


def _send_receipt(identifier: str, *, ok: bool = True) -> dict:
    return _result(identifier, json.dumps({"ok": ok}))


@pytest.mark.parametrize(
    ("entries", "blocked"),
    [
        ([_HUMAN, _attachment(_WARN), _text("調査を終えた。")], True),
        ([_HUMAN, _attachment(_WARN), _text("hook通知への対応: 照会コマンドへ切り替えた。\n調査を終えた。")], False),
        ([_HUMAN, _attachment(_WARN), _text("- hook通知への対応: 誤検出（引用だけを読んだ）。")], False),
        (
            [
                _HUMAN,
                _attachment(_WARN),
                _call(_SEND_TO_USER, "s1", {"message": "報告\nhook通知への対応: 処置した。"}),
                _send_receipt("s1"),
            ],
            False,
        ),
        (
            [
                _HUMAN,
                _attachment(_WARN),
                *repl_send_to_user_entries("repl_warn", {"message": "hook通知への対応: 処置した。"}, ok=True),
            ],
            False,
        ),
        ([_HUMAN, _text("hook通知への対応: 先に書いた。"), _attachment(_WARN), _text("終えた。")], True),
        ([_HUMAN, _attachment(_NOTICE), _text("終えた。")], False),
        ([_attachment(_WARN), _HUMAN, _text("終えた。")], False),
        ([_HUMAN, _attachment(_WARN, record_type="hook_system_message"), _text("終えた。")], True),
        ([_HUMAN, _call(_SEND_TO_USER, "s1", {"message": "報告"}), _result("s1", "送信済み。" + _WARN), _text("…")], False),
        (
            [
                _HUMAN,
                _call("mcp__plugin_agent-toolkit_agents_server__send_message", "m1", {"message": "続けて"}),
                _result("m1", json.dumps({"result": "委譲先の報告: " + _WARN}, ensure_ascii=False)),
                _text("終えた。"),
            ],
            False,
        ),
        ([_HUMAN, _call("Bash", "b1", {"command": "cat log"}), _result("b1", _WARN), _text("終えた。")], False),
    ],
    ids=[
        "warn-without-response",
        "response-in-text",
        "response-as-list-item",
        "response-in-send-to-user",
        "response-in-repl-code",
        "response-before-warn",
        "notice-only",
        "warn-in-previous-turn",
        "system-message-warn",
        "quoted-warn-in-toolkit-result",
        "quoted-warn-in-delegate-report",
        "quoted-warn-in-command-output",
    ],
)
def test_blocks_only_unanswered_warn(entries: list[dict], blocked: bool, tmp_path: pathlib.Path) -> None:
    decision, body = _evaluate(entries, tmp_path)
    assert (decision == "block") is blocked
    if blocked:
        assert gate.RESPONSE_PREFIX in body
        assert "pretooluse: " in body or "send-to-user" in body or "pretooluse" in body


@pytest.mark.parametrize("event", ["PreToolUse", "Stop", "SubagentStop"])
def test_blocks_once_per_warning_set(tmp_path: pathlib.Path, event: str) -> None:
    entries = [_HUMAN, _attachment(_WARN, event=event), _text("終えた。")]
    decision, body = _evaluate(entries, tmp_path)
    assert decision == "block"
    assert _evaluate([*entries, _attachment(body, event="Stop", uuid="block-1")], tmp_path)[0] == "approve"
    other = _WARN.replace("直接参照", "別の参照")
    assert _evaluate([*entries, _attachment(other), _text("終えた。")], tmp_path)[0] == "block"


@pytest.mark.parametrize("extra", [{"agent_id": "child"}, {"hook_event_name": "Stop", "turn_id": "t", "model": "gpt"}])
def test_skips_subagents_and_other_hosts(extra: dict, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if "turn_id" in extra:
        monkeypatch.setattr(gate, "is_codex_payload", lambda payload: True)
    decision, _ = _evaluate([_HUMAN, _attachment(_WARN), _text("終えた。")], tmp_path, **extra)
    assert decision == "approve"


def _send_block(entries: list[dict], tmp_path: pathlib.Path, tool_name: str, tool_input: dict) -> str | None:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    payload = {"session_id": "hook-notice-send", "transcript_path": str(path)}
    return gate.send_block(payload, "hook-notice-send", tool_name, tool_input)


def test_send_block_stops_report_once_until_response(tmp_path: pathlib.Path) -> None:
    """警告の後に対応行の無い報告を送ろうとすると、送る前に1回止める。対応行を含む本文は通す。 pyfltr: allow-comment"""
    entries = [_HUMAN, _attachment(_WARN), _text("作業中。")]
    blocked = _send_block(entries, tmp_path, _SEND_TO_USER, {"message": "途中報告"})
    assert blocked is not None and "未対応の警告" in blocked and "pretooluse" in blocked
    assert _send_block(entries, tmp_path, _SEND_TO_USER, {"message": "途中報告"}) is None
    assert _send_block(entries, tmp_path, _SEND_TO_USER, {"message": "hook通知への対応: 処置した。\n途中報告"}) is None


def test_send_block_asks_question_after_response_report(tmp_path: pathlib.Path) -> None:
    entries = [_HUMAN, _attachment(_WARN)]
    blocked = _send_block(entries, tmp_path, "AskUserQuestion", {"questions": []})
    assert blocked is not None and "send_to_user" in blocked
    answered = [*entries, _call(_SEND_TO_USER, "s1", {"message": "hook通知への対応: 誤検出。"}), _send_receipt("s1")]
    assert _send_block(answered, tmp_path, "AskUserQuestion", {"questions": []}) is None


@pytest.mark.parametrize("tool_name", [_SEND_TO_USER, "AskUserQuestion"])
@pytest.mark.parametrize("event", ["PreToolUse", "Stop", "SubagentStop"])
def test_pretooluse_denies_user_facing_call_with_unanswered_warning(tmp_path: pathlib.Path, tool_name: str, event: str) -> None:
    """PreToolUseのhookの入口から、未対応の警告を残した報告・質問を止める。 pyfltr: allow-comment"""
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in [_HUMAN, _attachment(_WARN, event=event)]),
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "hook-notice-dispatch",
        "transcript_path": str(path),
        "tool_name": tool_name,
        "tool_input": {"message": "報告"}
        if tool_name == _SEND_TO_USER
        else {
            "questions": [
                {
                    "question": "進めますか？",
                    "header": "確認",
                    "multiSelect": False,
                    "options": [{"label": "はい", "description": "進める"}, {"label": "いいえ", "description": "止める"}],
                }
            ]
        },
    }
    result = _run_subcommand("pretooluse", payload, state_dir=tmp_path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "未対応の警告" in output["permissionDecisionReason"]


def test_send_block_ignores_notice_and_previous_turn(tmp_path: pathlib.Path) -> None:
    assert _send_block([_HUMAN, _attachment(_NOTICE)], tmp_path, _SEND_TO_USER, {"message": "報告"}) is None
    assert _send_block([_attachment(_WARN), _HUMAN], tmp_path, _SEND_TO_USER, {"message": "報告"}) is None


def test_send_block_and_stop_share_one_block_per_set(tmp_path: pathlib.Path) -> None:
    """送達前に一度止めた組は、対応しないまま終える時のStopで再び止めない。 pyfltr: allow-comment"""
    entries = [_HUMAN, _attachment(_WARN), _text("終えた。")]
    assert _send_block(entries, tmp_path, _SEND_TO_USER, {"message": "報告"}) is not None
    path = tmp_path / "transcript.jsonl"
    payload = {"session_id": "hook-notice-send", "transcript_path": str(path)}
    assert gate.evaluate(json.dumps(payload))[0] == "approve"


def test_same_text_warning_after_next_human_input_is_a_new_set(tmp_path: pathlib.Path) -> None:
    """別の発話の後に同じ文面で新しく届いた警告は、新しい組として1回止める。 pyfltr: allow-comment"""
    first = [_HUMAN, _attachment(_WARN), _text("終えた。")]
    assert _evaluate(first, tmp_path)[0] == "block"
    second = [*first, _HUMAN, _attachment(_WARN, uuid="warn-2"), _text("終えた。")]
    assert _evaluate(second, tmp_path)[0] == "block"


def test_warning_before_completion_notice_is_kept(tmp_path: pathlib.Path) -> None:
    """人間入力の後に届いた未対応の警告は、その後の完了通知で捨てない。 pyfltr: allow-comment"""
    notification = {
        "type": "user",
        "message": {"content": "<task-notification>\n<status>completed</status>\n</task-notification>"},
    }
    entries = [_HUMAN, _attachment(_WARN), _text("待つ。"), notification, _text("終えた。")]
    assert _evaluate(entries, tmp_path)[0] == "block"


def test_misdetection_resolves_only_matching_delivery(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(session_record_paths, "stop_log_path", lambda sid: tmp_path / f"{sid}.log")
    first, second = "1" * 32, "2" * 32
    sid = "misdetection"

    def warning(identifier):
        return (
            f'<atk-auto source="send-to-user" kind="warn" check="missing-disposition" delivery_id="{identifier}">\n'
            f"警告 {identifier}\n</atk-auto>"
        )

    warnings = [warning(first), warning(second)]
    send_misdetection.remember_warnings(sid, warnings)
    args = {"check": "missing-disposition", "reason": "類型を説明しただけである。", "delivery_id": first}
    claims, error = send_misdetection.parse([args])
    assert error is None
    assert send_misdetection.validate_displayed(sid, claims) is None
    invalid, _ = send_misdetection.parse([{**args, "delivery_id": "3" * 32}])
    assert send_misdetection.validate_displayed(sid, invalid) is not None
    assert send_misdetection.resolved(sid) == set()
    assert send_misdetection.record(sid, claims, None)
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(entry) for entry in [_HUMAN, *(_attachment(body, uuid=str(i)) for i, body in enumerate(warnings))]
        ),
        encoding="utf-8",
    )
    reason = gate.send_block({"session_id": sid, "transcript_path": str(path)}, sid, "AskUserQuestion", {})
    assert reason is not None
    assert second in reason
    assert first not in reason
    summary = send_misdetection.summary(sid)
    assert "missing-disposition: 1件" in summary
    assert first in summary
    assert args["reason"] in summary


@pytest.mark.parametrize("repl", [False, True])
def test_failed_send_does_not_resolve_warning(tmp_path: pathlib.Path, repl: bool) -> None:
    body = {"message": "hook通知への対応: 処置した。"}
    sent = (
        repl_send_to_user_entries("repl_warn", body, ok=False)
        if repl
        else [_call(_SEND_TO_USER, "s1", body), _send_receipt("s1", ok=False)]
    )
    assert _evaluate([_HUMAN, _attachment(_WARN), *sent], tmp_path)[0] == "block"
