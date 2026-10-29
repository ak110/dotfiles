"""標準native読取の一回実行と全量配送を公開hook経由で確かめる。"""

import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._hooks import posttooluse, progress_self_check
from agent_toolkit._hooks.pretooluse import codex_read_capture as subject
from agent_toolkit._hooks.pretooluse import dispatch
from agent_toolkit._hooks.pretooluse.decision import Decision

_ACTUAL_CAPACITY = subject._capacity  # pylint: disable=protected-access


@pytest.fixture(autouse=True)
def _context_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    """hook単体ではモデルの実記録を容量だけの固定入力へ置き換える。"""
    monkeypatch.setattr(subject, "_capacity", lambda _payload: ("response", 258400))


@pytest.mark.parametrize("kind", ["cat", "chain", "range", "json", "failure"])
def test_public_hooks_capture_native_result_once(
    kind: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    source = tmp_path / "日本語 空白.txt"
    body = "資料の全文\n" * 2000
    source.write_text(body, encoding="utf-8")
    json_source = tmp_path / "data.json"
    json_source.write_text(json.dumps({"body": body}), encoding="utf-8")
    commands = {
        "cat": f"cat -- '{source}'",
        "chain": f"cat -- '{source}'; cat -- '{source}'",
        "range": f"sed -n '2,$p' -- '{source}'",
        "json": f"jq -r '.body' '{json_source}'",
        "failure": f"cat -- '{tmp_path / 'missing'}'",
    }
    original = {"command": commands[kind], "timeout_ms": 30000}
    payload = {
        "tool_name": "Bash",
        "tool_input": original,
        "session_id": "capture-test",
        "turn_id": "turn",
        "cwd": str(tmp_path),
    }
    monkeypatch.setattr(dispatch, "read_delivery", lambda *_args, **_kwargs: Decision())
    assert dispatch.main(json.dumps(payload)) == 0
    before = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    rewritten = before["updatedInput"]
    assert before["permissionDecision"] == "allow"
    assert rewritten["timeout_ms"] == original["timeout_ms"]
    result = subprocess.run(
        ["bash", "-c", rewritten["command"]], capture_output=True, encoding="utf-8", check=False, timeout=30
    )
    observed = []

    def observe(*args: Any, **_kwargs: Any) -> list[str]:
        observed.append(args)
        return []

    monkeypatch.setattr(posttooluse, "_observe_tool", observe)
    monkeypatch.setattr(posttooluse.termination_evidence, "observe_tool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(progress_self_check, "claim_notice", lambda *_args, **_kwargs: None)
    after_payload = {
        **payload,
        "tool_input": rewritten,
        "hook_event_name": "PostToolUse",
        "tool_response": "出力は切り詰め済み",
    }
    assert posttooluse.main(json.dumps(after_payload)) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    delivered = json.loads(context.split("\n", 1)[1])
    assert delivered["command"] == commands[kind]
    assert delivered["exit_code"] == result.returncode
    assert observed[0][0]["tool_response"]["exit_code"] == result.returncode
    assert observed[0][3]["command"] == commands[kind]
    if kind == "failure":
        assert result.returncode != 0
        assert "missing" in delivered["stderr"]
        assert not delivered["stdout"]
    else:
        expected = (
            body * 2
            if kind == "chain"
            else body.removeprefix("資料の全文\n")
            if kind == "range"
            else body + "\n"
            if kind == "json"
            else body
        )
        assert delivered["stdout"] == expected
        assert not delivered["stderr"]
    root = subject.managed_temp.resolve_session_temp("capture-test")
    assert not [path for path in root.glob("codex-read-*") if path.is_dir()]


@pytest.mark.parametrize(
    "command",
    ["cat /x; rm /x", "cat /x | sh", "cat $(touch /x)", "sed -i p /x", "jq --run-tests /x", "/untrusted/cat /x", "cat /x > /y"],
)
def test_side_effecting_commands_keep_native_permission_flow(command: str) -> None:
    assert not subject.standard_read(command)


def test_claude_is_not_rewritten() -> None:
    payload = {"tool_name": "Bash", "session_id": "claude"}
    assert subject.prepare(payload, {"command": "cat /x"}) is None


def test_capacity_and_unfinished_execution_are_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"tool_name": "Bash", "session_id": "capacity", "turn_id": "turn"}
    rewritten = subject.prepare(payload, {"command": "cat /x"})
    assert rewritten is not None
    root = subject.managed_temp.resolve_session_temp("capacity")
    capture = next(root.glob("codex-read-*"))
    pending = subject.collect(payload, rewritten)
    assert pending is not None and pending.response is None and capture.exists()
    (capture / "stdout").write_text("容量超過", encoding="utf-8")
    (capture / "stderr").write_text("", encoding="utf-8")
    (capture / "status").write_text("0", encoding="utf-8")
    monkeypatch.setattr(subject, "_capacity", lambda _payload: ("response", 1))
    too_large = subject.collect(payload, rewritten)
    assert too_large is not None and too_large.response is None
    assert "文脈の配送容量" in too_large.context
    assert not capture.exists()


def test_model_context_and_same_response_total(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """実記録の文脈枠から使用量を引き、同じ応答の複数配送を合算する。"""
    transcript = tmp_path / "rollout.jsonl"
    events = [
        {"payload": {"type": "task_started", "model_context_window": 10000}},
        {"type": "token_usage_record", "payload": {"usage": {"input_tokens": 100, "output_tokens": 200}}},
        {"type": "response_item", "payload": {"text": "後続のイベント" * 10000}},
    ]
    transcript.write_text("\n".join(map(json.dumps, events)) + "\n", encoding="utf-8")
    payload = {"transcript_path": str(transcript)}
    capacity = _ACTUAL_CAPACITY(payload)
    assert capacity is not None and capacity[1] == 8700
    monkeypatch.setattr(subject, "_capacity", _ACTUAL_CAPACITY)
    assert subject._reserve(payload, tmp_path, 5000)  # pylint: disable=protected-access
    assert not subject._reserve(payload, tmp_path, 5000)  # pylint: disable=protected-access
    assert subject._reserve(payload, tmp_path, 3700)  # pylint: disable=protected-access


def test_existing_denial_does_not_prepare_capture(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """既存の操作拒否は補正によるallowへ進まない。"""
    monkeypatch.setattr(dispatch, "_decide", lambda *_args, **_kwargs: Decision(block="元の拒否"))
    monkeypatch.setattr(subject, "prepare", lambda *_args: pytest.fail("拒否された操作を補正した"))
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat /x"}, "session_id": "denial", "turn_id": "turn"}
    assert dispatch.main(json.dumps(payload)) == 2
    assert "元の拒否" in capsys.readouterr().err
