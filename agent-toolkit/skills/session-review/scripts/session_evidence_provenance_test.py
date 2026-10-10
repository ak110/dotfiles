"""全照会が同じ親子関係と起動時の役割を返すことを公開CLIで検証する。

CodexのMcpToolCallの形は2026年10月10日の記録のargumentsとstructuredContentを使う。
"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing import session_evidence_support as support


def _family(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    parent = support.tool_call_session(tmp_path, monkeypatch, "needle")
    entries = [json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines()]
    entries[5]["message"]["content"][0]["input"]["mode"] = "task"
    support.write_jsonl(parent, entries)
    support.write_subagent(
        parent.with_suffix("") / "subagents",
        "agent-child",
        [support.claude_call("2026-10-06T00:00:03.500Z", "read-1", "Read", {"file_path": "/repo/a.py"})],
        {"agentType": "Explore"},
    )
    codex = next((tmp_path / "codex").rglob("*.jsonl"))
    entries = [json.loads(line) for line in codex.read_text(encoding="utf-8").splitlines()]
    entries.insert(
        1,
        support.codex_item(
            "2026-10-06T00:00:05Z",
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "次の文書の手順を実行せよ（出所: /plugin/share/exec.subagent.md）。\n作業"}
                ],
            },
        ),
    )
    child = "77777777-7777-4777-8777-777777777777"
    entries.append(
        {
            "type": "event_msg",
            "timestamp": "2026-10-06T00:00:12Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "McpToolCall",
                    "id": "native-start",
                    "server": "agents_server",
                    "tool": "start",
                    "arguments": {"mode": "shell", "command": "検査"},
                    "result": {"structuredContent": {"session_id": child}},
                },
            },
        }
    )
    support.write_jsonl(codex, entries)
    support.write_jsonl(
        codex.with_name(f"rollout-child-{child}.jsonl"),
        [
            {"type": "session_meta", "timestamp": "2026-10-06T00:00:12Z", "payload": {"id": child}},
            support.codex_item(
                "2026-10-06T00:00:13Z",
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "記録を読む"}]},
            ),
        ],
    )
    return parent


@pytest.mark.parametrize(
    "mode",
    [
        [],
        ["--warn"],
        ["--grep", "needle"],
        ["--fixed-string", "needle"],
        ["--tool-calls"],
        ["--hook-notices"],
        ["--stats"],
        ["--bundle"],
    ],
)
def test_public_query_modes_include_record_provenance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: list[str]
) -> None:
    """同じ4記録の由来を全収集照会から読み、欠けた役割文書はnullとして区別する。"""
    parent = _family(tmp_path, monkeypatch)
    arguments = list(mode)
    if arguments == ["--bundle"]:
        output = tmp_path / "bundle"
        output.mkdir()
        arguments.append(str(output))
    assert evidence.main([str(parent), *arguments]) == 0
    events = support.read_jsonl(capsys, raw=True)
    provenance = {event["record"]: event for event in events if event["kind"] == "record-provenance"}
    assert len(provenance) == 4
    main = provenance["claude:parent-session"]
    assert main["role"] == "main" and main["source_record"] is None and main["mode"] is None
    subagent = provenance["claude:parent-session/agent-child"]
    assert subagent["mode"] == "Explore" and subagent["role_document"] is None
    task = provenance[f"codex:{support.TOOL_CALL_THREAD}"]
    assert task["source_record"] == "claude:parent-session" and task["source_line"] == 7
    assert task["mode"] == "task" and task["role_document"] == "exec"
    shell = provenance["codex:77777777-7777-4777-8777-777777777777"]
    assert shell["source_record"] == task["record"] and shell["mode"] == "shell" and shell["role_document"] is None


def test_public_catalog_tool_calls_group_by_role_document(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """root外のexecの起動件数とmodeを一回のカタログ照会から取得する。"""
    parent = _family(tmp_path, monkeypatch)
    window = ["--since", "2026-10-06T00:00:00Z", "--observation-boundary", "2026-10-06T00:00:30Z"]
    assert evidence.main(["--catalog-claude-project", str(parent.parent), *window, "--tool-calls"]) == 0
    events = support.read_jsonl(capsys, raw=True)
    summary = next(event for event in events if event["kind"] == "tool-call-summary")
    role = summary["by_role"]["document:exec"]
    assert role["by_tool"]["mcp__agents_server__start"] == 1
    assert role["by_mode"] == {"shell": 1}
    assert evidence.main(["--catalog-codex-history", str(tmp_path / "codex" / "sessions"), *window]) == 0
    events = support.read_jsonl(capsys, raw=True)
    root = next(event for event in events if event["kind"] == "catalog-parent")
    assert root["role_document"] == "exec"


@pytest.mark.parametrize("initial_role", [None, "exec"])
@pytest.mark.parametrize("separator", ["/", "\\"])
def test_public_provenance_keeps_initial_delivery_role(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], initial_role: str | None, separator: str
) -> None:
    """起動前の挿入を除き、役割無しの初回と役割有りの初回を後続配送で上書きしない。"""
    parent = tmp_path / "role.jsonl"
    initial = "指定した範囲を読む"
    if initial_role:
        source = f"/plugin/share/{initial_role}.subagent.md".replace("/", separator)
        initial = f"次の文書の手順を実行せよ（出所: {source}）。"
    support.write_jsonl(
        parent,
        [
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "実行環境の挿入"}},
            support.timestamped_entry("2026-10-10T00:00:00Z", "# AGENTS.md instructions\n規範"),
            support.timestamped_entry("2026-10-10T00:00:01Z", initial),
            support.timestamped_entry(
                "2026-10-10T00:01:00Z", "次の文書の手順を実行せよ（出所: /plugin/share/review.subagent.md）。"
            ),
        ],
    )
    assert evidence.main([str(parent), "--tool-calls"]) == 0
    events = support.read_jsonl(capsys, raw=True)
    role = next(event for event in events if event["kind"] == "record-provenance")
    assert role["role_document"] == initial_role
