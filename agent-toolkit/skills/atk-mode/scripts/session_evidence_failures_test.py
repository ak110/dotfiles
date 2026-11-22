"""公開照会で失敗検索と欠測伝播を確認する。"""

import json
import pathlib

import pytest
import session_review_evidence as evidence
import session_review_prepare as prepare


def _rows(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def _entries(error=True):
    return [
        {
            "type": "assistant",
            "timestamp": "2026-11-01T01:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "broken"}}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-11-01T01:00:01Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call", "is_error": error, "content": "Exit code 2\n失敗文言"}
                ],
            },
        },
    ]


def test_scan_failure_window(tmp_path: pathlib.Path, capsys):
    """Unicodeエスケープを含む複数projectから失敗呼出しだけを返す。"""
    for project, error in [("a", True), ("b", False)]:
        path = tmp_path / project / "session.jsonl"
        path.parent.mkdir()
        path.write_text("\n".join(json.dumps(entry) for entry in _entries(error)), encoding="utf-8")
    args = [
        "--scan-claude-projects",
        str(tmp_path),
        "--tool-calls",
        "--failure-fixed-string",
        "失敗文言",
        "--since",
        "2026-11-01T00:00:00Z",
        "--observation-boundary",
        "2026-11-01T02:00:00Z",
    ]
    assert evidence.main(args) == 0
    calls = [row for row in _rows(capsys) if row["kind"] == "tool-call"]
    assert [(pathlib.Path(row["record"]).parent.name, row["line"], row["result_line"]) for row in calls] == [("a", 1, 2)]
    call = calls[0]
    assert evidence.main(["--transcript", call["record"], "--detail", f"{call['record']}:{call['line']}"]) == 0
    assert any(row.get("input") == {"command": "broken"} for row in _rows(capsys))
    args[args.index("--since") + 1] = "2026-11-01T01:00:00Z"
    assert evidence.main(args) == 0
    assert not [row for row in _rows(capsys) if row["kind"] == "tool-call"]


def test_partial_main_and_prepare(tmp_path: pathlib.Path, capsys):
    path = tmp_path / "broken.jsonl"
    rows = _entries()
    original = json.dumps(rows[0]).encode() + b"\n{broken\n\xff\n[]\n\x00\n" + json.dumps(rows[1]).encode() + b"\n"
    path.write_bytes(original)
    assert evidence.main([str(path), "--tool-calls"]) == 2
    capsys.readouterr()
    assert evidence.main([str(path), "--tool-calls", "--allow-partial-records"]) == 0
    result = _rows(capsys)
    assert [row["line"] for row in result if row["kind"] == "record-gap"] == [2, 3, 4, 5]
    call = next(row for row in result if row["kind"] == "tool-call")
    assert call["result_line"] is None
    assert all(row["partial"] for row in result)
    assert evidence.main([str(path), "--context-at", "6", "--phrase", "broken", "--allow-partial-records"]) == 2
    context = _rows(capsys)
    assert not any(row["kind"] == "context-verdict" for row in context)
    assert [row["line"] for row in context if row["kind"] == "record-gap"] == [2, 3, 4, 5]
    work = tmp_path / "work"
    work.mkdir()
    assert (
        prepare.main(["--transcript", str(path), "--work-dir", str(work), "--allow-partial-records", "--conversation-only"])
        == 0
    )
    result = _rows(capsys)[0]
    assert result["partial"]
    assert len(result["unconfirmed_scope"]) == 4
    assert path.read_bytes() == original


def test_scan_codex_and_persisted(tmp_path: pathlib.Path, capsys):
    """Codexの実行結果とClaudeの退避結果を公開走査から照会する。"""
    codex = tmp_path / "codex"
    codex.mkdir()
    records = [
        {
            "type": "response_item",
            "timestamp": "2026-11-01T01:00:00Z",
            "payload": {"type": "function_call", "name": "exec_command", "call_id": "c", "arguments": '{"cmd":"broken"}'},
        },
        {
            "type": "response_item",
            "timestamp": "2026-11-01T01:00:01Z",
            "payload": {"type": "function_call_output", "call_id": "c", "output": "Process exited with code 2\n失敗文言"},
        },
    ]
    (codex / "rollout.jsonl").write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    window = [
        "--tool-calls",
        "--failure-fixed-string",
        "失敗文言",
        "--since",
        "2026-11-01T00:00:00Z",
        "--observation-boundary",
        "2026-11-01T02:00:00Z",
    ]
    assert evidence.main(["--scan-codex-history", str(codex), *window]) == 0
    calls = [row for row in _rows(capsys) if row["kind"] == "tool-call"]
    assert [(row["line"], row["result_line"]) for row in calls] == [(1, 2)]
    claude = tmp_path / "claude"
    claude.mkdir()
    saved = tmp_path / "saved.txt"
    saved.write_text("Exit code 2\n失敗文言", encoding="utf-8")
    records = _entries()
    records[1]["message"]["content"][0]["content"] = f"<persisted-output>\nFull output saved to: {saved}\n</persisted-output>"
    records[1]["toolUseResult"] = {"stdout": "Exit code 2\n失敗文言", "persistedOutputPath": str(saved)}
    (claude / "session.jsonl").write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    assert evidence.main(["--scan-claude-projects", str(claude), *window]) == 0
    assert len([row for row in _rows(capsys) if row["kind"] == "tool-call"]) == 1


def test_failure_query_invalid_combinations(tmp_path: pathlib.Path, capsys):
    """単一・横断・カタログの意味を混ぜた指定と空の失敗文言は拒否する。"""
    for args in [
        ["--failure-fixed-string", "x"],
        ["--tool-calls", "--failure-fixed-string="],
        ["--scan-claude-projects", str(tmp_path), "--tool-calls", "--failure-fixed-string", "x"],
        ["--catalog-claude-project", str(tmp_path), "--tool-calls", "--failure-fixed-string", "x"],
    ]:
        assert evidence.main(args) == 2
        assert _rows(capsys)[0]["kind"] == "error"


def test_partial_elapsed_keeps_gaps(tmp_path: pathlib.Path, capsys):
    path = tmp_path / "elapsed.jsonl"
    path.write_bytes(
        b'{"type":"user","timestamp":"2026-11-01T00:00:00Z","message":{"role":"user","content":"a"}}\n'
        b"{broken\n"
        b'{"type":"user","timestamp":"2026-11-01T00:01:00Z","message":{"role":"user","content":"b"}}\n'
    )
    assert evidence.main([str(path), "--allow-partial-records", "--elapsed-until", "2026-11-01T00:01:00Z"]) == 0
    rows = _rows(capsys)
    assert next(row for row in rows if row["kind"] == "session-elapsed")["elapsed_seconds"] == 60
    assert [row["line"] for row in rows if row["kind"] == "record-gap"] == [2]
    assert all(row["partial"] for row in rows)


@pytest.mark.parametrize("scan", [False, True])
def test_failure_filter_preserves_negative_classification(tmp_path: pathlib.Path, capsys, scan: bool):
    """空の検索結果と引用を失敗へ変えず、実エラーの入力と結果だけを返す。"""
    path = tmp_path / "rollout.jsonl"
    entries = []
    for call_id, command, output, exit_code in [
        ("empty", "rg -n -F needle /data/file", "", 1),
        ("error", "rg -n -F needle /data/file", "error: file missing", 1),
        ("file", "cat /data/file", "引用 exit_code error: file missing", 0),
    ]:
        entries.extend(
            [
                {
                    "type": "response_item",
                    "timestamp": "2026-11-01T01:00:00Z",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": call_id,
                        "arguments": json.dumps({"cmd": command}),
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-11-01T01:00:01Z",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({"output": output, "exit_code": exit_code}),
                    },
                },
            ]
        )
    entries.append({"type": "event_msg", "payload": {"type": "agent_message", "message": "引用 exit_code error: file missing"}})
    path.write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")
    target = (
        ["--scan-codex-history", str(tmp_path), "--since", "2026-11-01", "--observation-boundary", "2026-11-02"]
        if scan
        else [str(path)]
    )
    assert evidence.main([*target, "--tool-calls", "--failure-fixed-string", "exit_code"]) == 0
    rows = _rows(capsys)
    assert [row["call_id"] for row in rows if row["kind"] == "tool-call"] == ["error"]
    summary = next(row for row in rows if row["kind"] == "tool-call-summary")
    assert summary["count"] == 1 and summary["by_tool"] == {"exec_command": 1}


def test_failure_filter_keeps_unresolved_and_since_summary(tmp_path: pathlib.Path, capsys):
    """失敗だけの選択でも未取得診断と時刻欠測・内訳を保持する。"""
    path = tmp_path / "parent.jsonl"
    entries = _entries()
    entries[0].pop("timestamp")
    path.write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")
    child = path.with_suffix("") / "subagents" / "agent-broken.jsonl"
    child.parent.mkdir(parents=True)
    child.write_text("{broken", encoding="utf-8")
    args = [str(path), "--tool-calls", "--failure-fixed-string", "失敗文言", "--since", "2026-11-01"]
    assert evidence.main(args) == 0
    rows = _rows(capsys)
    assert next(row for row in rows if row["kind"] == "unresolved-record")["next_action"]
    summary = next(row for row in rows if row["kind"] == "tool-call-summary")
    assert summary["count"] == 0 and summary["missing_timestamps"] == 1
    assert summary["by_tool"] == {} and summary["by_record"] == {} and summary["by_role"] == {}
    assert (
        evidence.main(
            [
                "--scan-claude-projects",
                str(tmp_path),
                "--tool-calls",
                "--failure-fixed-string",
                "broken",
                "--since",
                "2026-11-01",
                "--observation-boundary",
                "2026-11-02",
            ]
        )
        == 2
    )
    assert next(row for row in _rows(capsys) if row["kind"] == "unresolved-record")["next_action"]
