"""OpenCodeの保存記録を、ログ表示と親からの証拠抽出・統計で読む。"""

import json
from pathlib import Path

import pytest
import session_review_evidence as evidence

from agent_toolkit import atk
from agent_toolkit._agents_server import shared_layout
from agent_toolkit._testing.session_evidence_support import claude_call, claude_result, events_by_kind, read_jsonl, write_jsonl


def test_opencode_log_and_parent_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """親の起動結果からOpenCode記録を発見し、失敗・返却・使用量を同じ位置で取得する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    session_id = "ses_example"
    log = shared_layout.session_log_path("root-example", session_id, engine="opencode")
    write_jsonl(
        log,
        [
            {
                "type": "agent_toolkit_input",
                "timestamp": "2026-10-20T00:00:00Z",
                "prompt": "調査して",
                "model": "local/qwen",
                "cwd": "/work",
            },
            {"type": "step_start", "timestamp": 1792454400000, "sessionID": session_id, "part": {"type": "step-start"}},
            {
                "type": "tool_use",
                "timestamp": 1792454401000,
                "sessionID": session_id,
                "part": {
                    "type": "tool",
                    "callID": "call-one",
                    "tool": "bash",
                    "state": {"status": "error", "input": {"command": "pytest"}, "error": "test failed"},
                },
            },
            {
                "type": "text",
                "timestamp": 1792454402000,
                "sessionID": session_id,
                "part": {"type": "text", "text": "調査結果を返す"},
            },
            {
                "type": "step_finish",
                "timestamp": 1792454403000,
                "sessionID": session_id,
                "part": {
                    "type": "step-finish",
                    "reason": "stop",
                    "cost": 0.01,
                    "tokens": {"input": 20, "output": 10, "total": 35, "cache": {"read": 5, "write": 0}},
                },
            },
        ],
    )
    parent = tmp_path / "parent.jsonl"
    result = claude_result("2026-10-20T00:00:01Z", "launch", json.dumps({"session_id": session_id, "engine": "opencode"}))
    result["toolUseResult"] = {"session_id": session_id, "engine": "opencode"}
    write_jsonl(
        parent,
        [
            claude_call(
                "2026-10-20T00:00:00Z",
                "launch",
                "mcp__agents_server__start",
                {"mode": "explore", "prompt": "調査して", "model_type": "opencode:local/qwen/high"},
            ),
            result,
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        atk.main(["agents", "logs", session_id, "--format", "markdown"])
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "調査結果を返す" in output
    assert "test failed" in output
    assert evidence.main([str(parent), "--stats"]) == 0
    stats = read_jsonl(capsys, raw=True)
    thread = events_by_kind(stats, "stats-agent-thread")[0]
    assert (thread["engine"], thread["session_id"]) == ("opencode", session_id)
    assert thread["tokens"] == {
        "input_tokens": 20,
        "output_tokens": 10,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 0,
    }
    assert not events_by_kind(stats, "unresolved-record")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(parent), "--bundle", str(bundle)]) == 0
    read_jsonl(capsys, raw=True)
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(
        item.get("candidate_kind") == "tool-failure"
        and any(locator["record"] == f"opencode:{session_id}" and locator["line"] == 3 for locator in item["locators"])
        for item in candidates
    )
