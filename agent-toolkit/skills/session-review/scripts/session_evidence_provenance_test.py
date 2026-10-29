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


_MAIN = "claude:parent-session"
_SUBAGENT = "claude:parent-session/agent-child"
_TASK = f"codex:{support.TOOL_CALL_THREAD}"
_SHELL = "codex:77777777-7777-4777-8777-777777777777"
_LAUNCHER = {_SUBAGENT: _MAIN, _TASK: _MAIN, _SHELL: _TASK}
"""`_family`が書く記録ごとの起動元。"""


def _provenance(events: list[dict]) -> dict[str, dict]:
    return {event["record"]: event for event in events if event["kind"] == "record-provenance"}


def _referenced_with_launchers(events: list[dict]) -> set[str]:
    """結果のイベントが`record`欄か配列要素の`record`欄で参照する記録と、その起動元を根まで集める。"""
    records: set[str] = set()
    for event in events:
        if event["kind"] in {"record-provenance", "unresolved-record", "unresolved-delegation"}:
            continue
        values = [event.get("record")]
        for value in event.values():
            if isinstance(value, list):
                values.extend(item.get("record") for item in value if isinstance(item, dict))
        records.update(value for value in values if isinstance(value, str))
    for record in list(records):
        while record in _LAUNCHER:
            record = _LAUNCHER[record]
            records.add(record)
    return records


def test_public_default_and_bundle_files_keep_all_record_provenance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """通常表示と`--bundle`が書くファイルは4記録全ての由来を持ち、欠けた役割文書はnullとして区別する。"""
    parent = _family(tmp_path, monkeypatch)
    assert evidence.main([str(parent)]) == 0
    provenance = _provenance(support.read_jsonl(capsys, raw=True))
    assert set(provenance) == {_MAIN, _SUBAGENT, _TASK, _SHELL}
    main = provenance[_MAIN]
    assert main["role"] == "main" and main["source_record"] is None and main["mode"] is None
    subagent = provenance[_SUBAGENT]
    assert subagent["mode"] == "Explore" and subagent["role_document"] is None
    task = provenance[_TASK]
    assert task["source_record"] == _MAIN and task["source_line"] == 7
    assert task["mode"] == "task" and task["role_document"] == "exec"
    shell = provenance[_SHELL]
    assert shell["source_record"] == _TASK and shell["mode"] == "shell" and shell["role_document"] is None
    output = tmp_path / "bundle"
    output.mkdir()
    assert evidence.main([str(parent), "--bundle", str(output)]) == 0
    capsys.readouterr()
    for filename in ("timeline.jsonl", "warnings.jsonl", "stats.jsonl", "hook-notices.jsonl"):
        lines = (output / filename).read_text(encoding="utf-8").splitlines()
        assert set(_provenance([json.loads(line) for line in lines])) == set(provenance), filename


@pytest.mark.parametrize(
    "mode",
    [
        ["--warn"],
        ["--grep", "needle"],
        ["--fixed-string", "needle"],
        ["--tool-calls"],
        ["--hook-notices"],
        ["--stats"],
        ["--bundle"],
    ],
)
def test_public_query_modes_limit_record_provenance_to_referenced_records(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: list[str]
) -> None:
    """照会の標準出力は、結果が参照する記録とその起動元の由来だけを返す。"""
    parent = _family(tmp_path, monkeypatch)
    arguments = list(mode)
    if arguments == ["--bundle"]:
        output = tmp_path / "bundle"
        output.mkdir()
        arguments.append(str(output))
    assert evidence.main([str(parent), *arguments]) == 0
    events = support.read_jsonl(capsys, raw=True)
    assert set(_provenance(events)) == _referenced_with_launchers(events)


@pytest.mark.parametrize(
    ("phrase", "matched", "expected"),
    [
        # needleはメインのBashの入力とtaskの委譲先のコマンドにだけ現れる。
        ("needle", {_MAIN, _TASK}, {_MAIN, _TASK}),
        # shellの委譲先の発話にだけ現れ、起動元のtaskとメインを根までたどる。
        ("記録を読む", {_SHELL}, {_SHELL, _TASK, _MAIN}),
    ],
)
def test_public_fixed_string_omits_unmatched_delegate_provenance(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phrase: str,
    matched: set[str],
    expected: set[str],
) -> None:
    """一致しない委譲先の由来を出力せず、一致した記録とその起動元の連鎖だけを返す。"""
    parent = _family(tmp_path, monkeypatch)
    assert evidence.main([str(parent), "--fixed-string", phrase]) == 0
    events = support.read_jsonl(capsys, raw=True)
    summary = next(event for event in events if event["kind"] == "fixed-string-summary")
    assert {locator["record"] for locator in summary["locators"]} == matched
    assert set(_provenance(events)) == expected


def test_public_catalog_tool_calls_group_by_role_document(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """root外のexecの起動件数とmodeを一回のカタログ照会から取得し、由来は呼び出しを持つ記録とその起動元に限る。"""
    parent = _family(tmp_path, monkeypatch)
    window = ["--since", "2026-10-06T00:00:00Z", "--observation-boundary", "2026-10-06T00:00:30Z"]
    assert evidence.main(["--catalog-claude-project", str(parent.parent), *window, "--tool-calls"]) == 0
    events = support.read_jsonl(capsys, raw=True)
    summary = next(event for event in events if event["kind"] == "tool-call-summary")
    role = summary["by_role"]["document:exec"]
    assert role["by_tool"]["mcp__agents_server__start"] == 1
    assert role["by_mode"] == {"shell": 1}
    # shellの委譲先はツールを呼ばないため、その由来は出力しない。
    assert set(_provenance(events)) == _referenced_with_launchers(events) == {_MAIN, _SUBAGENT, _TASK}
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
    assert evidence.main([str(parent)]) == 0
    events = support.read_jsonl(capsys, raw=True)
    role = next(event for event in events if event["kind"] == "record-provenance")
    assert role["role_document"] == initial_role


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        # agents_serverが起動前に拒否した応答。2026年10月29日の記録の形を使う。
        ({"content": [{"type": "text", "text": "Error executing tool start: 委譲先は起動していない"}], "isError": True}, 0),
        ({"content": [{"type": "text", "text": "応答"}]}, 1),
    ],
)
def test_public_rejected_start_is_not_unresolved_delegation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: dict,
    expected: int,
) -> None:
    """拒否された起動は委譲先が無いため未解決に数えず、識別子を欠く成功応答は未解決として残す。"""
    parent = support.tool_call_session(tmp_path, monkeypatch, "needle")
    codex = next((tmp_path / "codex").rglob("*.jsonl"))
    entries = [json.loads(line) for line in codex.read_text(encoding="utf-8").splitlines()]
    entries.append(
        {
            "type": "event_msg",
            "timestamp": "2026-10-06T00:00:12Z",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "McpToolCall",
                    "id": "rejected-start",
                    "server": "agents_server",
                    "tool": "start",
                    "arguments": {"cwd": "/repo", "subagent_md_path": "reader-fit-review"},
                    "status": "failed" if result.get("isError") else "completed",
                    "result": result,
                },
            },
        }
    )
    support.write_jsonl(codex, entries)
    assert evidence.main([str(parent)]) == 0
    events = support.read_jsonl(capsys, raw=True)
    assert sum(event["kind"] == "unresolved-delegation" for event in events) == expected
