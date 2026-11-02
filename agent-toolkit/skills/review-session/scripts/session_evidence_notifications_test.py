"""実ホストの通知形式を公開の証拠収集から候補まで確認する。"""

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript


def _codex_call(identity: str, name: str, inputs: dict[str, object] | str) -> dict[str, object]:
    """公開ツールのJSON入力とexecのコード入力を記録形式にする。"""
    payload: dict[str, object] = {"name": name, "call_id": identity}
    if isinstance(inputs, str):
        payload.update(type="custom_tool_call", input=inputs)
    else:
        payload.update(type="function_call", arguments=json.dumps(inputs))
    return {"type": "response_item", "payload": payload}


def _codex_result(identity: str, output: str, *, custom: bool = False) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output" if custom else "function_call_output",
            "call_id": identity,
            "output": output,
        },
    }


def _codex_notice(message_id: str, *, provenance: bool = True) -> dict[str, object]:
    """Codexの2026-11-01のdeveloper記録265行で観測したメタデータ形式。"""
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "id": message_id,
            "role": "developer",
            "content": [{"type": "input_text", "text": '<atk-auto source="pretooluse" kind="warn">資料を確認せよ</atk-auto>'}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": ["hooks.additional_context"],
                "content_item_metadata": [{"provenance": {"type": "harness", "hook": provenance}}],
            },
        },
    }


def test_codex_hook_messages_keep_occurrences_and_exclude_quotes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同文の別通知を保持し、同一メッセージの重複と由来のない引用を除く。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_notice("msg-first"),
            _codex_notice("msg-first"),
            _codex_notice("msg-second"),
            _codex_notice("quote", provenance=False),
        ],
    )
    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1] == {"kind": "summary", "count": 2}
    assert evidence.main([str(transcript), "--warn"]) == 0
    warnings = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len([row for row in warnings if row.get("kind") == "warning"]) == 2
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text().splitlines()]
    notice = next(row for row in candidates if row.get("candidate_kind") == "hook-notice")
    assert notice["occurrence_count"] == 2
    assert notice["locators"][0]["line"] == 1


@pytest.mark.parametrize("summary", [False, True])
def test_wait_notifications_are_delegate_candidates(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], summary: bool
) -> None:
    """CLIのJSONとエージェント向け要約の受信通知を原記録へ対応付ける。"""
    body = "気付いた改善点: 解析が入力を欠落させる\n気付いた改善点: 別の入力も欠落する"
    output = (
        f"通知: 1件（session_id: child）\n本文開始: session_id=child\n{body}\n本文終了: session_id=child\n"
        if summary
        else json.dumps({"session_id": "child", "status": "running", "notices": [{"body": body}]}, ensure_ascii=False)
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "wait", "name": "Bash", "input": {"command": "atk agents wait"}}],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "wait", "content": output}]},
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "quote", "name": "Bash", "input": {"command": "cat example.json"}}],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "quote", "content": output}]},
            },
        ],
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text().splitlines()]
    notice = next(row for row in candidates if row.get("candidate_kind") == "delegate-return")
    assert notice["text"] == body
    assert [locator["line"] for locator in notice["locators"]] == [2]


@pytest.mark.parametrize("route", ["stdin", "cell", "nested", "unrelated", "quoted"])
def test_wait_notifications_follow_execution_and_cell_handles(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    """2026-11-02のCodex標準ツールの二層回収形式で、別コマンドとコード中の引用を除く。"""
    body = "気付いた改善点: 後続回収の通知"
    notice = json.dumps({"session_id": "child", "status": "running", "notices": [{"body": body}]}, ensure_ascii=False)
    command = "cat example.json" if route == "unrelated" else "atk agents wait"
    rows = [
        _codex_call("start", "functions.exec_command", {"cmd": command}),
        _codex_result("start", json.dumps({"session_id": 123, "output": ""})),
    ]
    if route == "cell":
        rows = [
            _codex_call("start", "functions.exec", 'text(await tools.exec_command({cmd:"atk agents wait"}));'),
            _codex_result("start", "Script running with cell ID cell-one\nWall time: 1 seconds", custom=True),
            _codex_call("resume", "functions.wait", {"cell_id": "cell-one"}),
        ]
    elif route == "nested":
        rows.extend(
            [
                _codex_call("outer", "functions.exec", 'text(await tools.write_stdin({session_id:123,chars:""}));'),
                _codex_result("outer", "Script running with cell ID cell-two\nWall time: 1 seconds", custom=True),
                _codex_call("resume", "functions.wait", {"cell_id": "cell-two"}),
            ]
        )
    elif route == "quoted":
        rows.append(_codex_call("resume", "functions.exec", 'text("tools.write_stdin({session_id:123})");'))
    else:
        rows.append(_codex_call("resume", "functions.write_stdin", {"session_id": 123, "chars": ""}))
    output = "Script completed\nOutput:\n" + json.dumps({"output": notice, "exit_code": 0}) if route == "nested" else notice
    rows.append(_codex_result("resume", output, custom=route == "quoted"))
    transcript = _write_transcript(tmp_path, rows)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text().splitlines()]
    notices = [row for row in candidates if row.get("candidate_kind") == "delegate-return"]
    if route in {"unrelated", "quoted"}:
        assert not notices
    else:
        assert len(notices) == 1
        assert notices[0]["text"] == body
        assert notices[0]["locators"][0]["line"] == len(rows)
