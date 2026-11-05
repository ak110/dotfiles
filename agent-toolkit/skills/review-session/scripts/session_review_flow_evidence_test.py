"""公開run-script操作から通読・配送依頼・Codex詳細・改善点の保持を確認する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

from agent_toolkit._agents_server.launch_requests import wrap_delivery_body
from agent_toolkit._atk import run_script
from agent_toolkit._common import message_format
from agent_toolkit._testing import session_evidence_support as support


def _run(script: str, arguments: list[str]) -> int:
    return run_script.dispatch(argparse.Namespace(script_name=script, script_args=arguments))


@pytest.mark.parametrize("user_count", [0, 1])
def test_prepare_retains_each_main_utterance_and_input(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], user_count: int
) -> None:
    """少数のユーザー発話でも29発話・76呼出の途中の手順を全て通読できる。"""
    entries = [support.timestamped_entry("2026-11-01T00:00:00Z", "作業を実施する")] if user_count else []
    expected: list[tuple[int, str]] = []
    for index in range(76):
        if index < 29:
            entries.append(support.assistant_text(f"判断{index:03}を記録する"))
            expected.append((len(entries), f"判断{index:03}"))
        entries.append(support.claude_call("2026-11-01T00:01:00Z", f"c{index}", "Bash", {"command": f"操作{index:03}"}))
        expected.append((len(entries), f"操作{index:03}"))
        entries.append(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"c{index}",
                            "content": "結果本文を混ぜない",
                            "is_error": index == 30,
                        }
                    ],
                },
            }
        )
    entries.append(support.assistant_text("\n"))
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(transcript, entries)
    work = tmp_path / "work"
    work.mkdir()
    assert (
        _run("session-review-prepare", ["--transcript", str(transcript), "--work-dir", str(work), "--conversation-only"]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    document = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    rows = [line for line in document.splitlines() if line.startswith("- ") and "claude:main:" in line]
    assert len(rows) == 105 + user_count
    selected = rows[user_count:]
    for row, (line, text) in zip(selected, expected, strict=True):
        assert f"claude:main:{line}:" in row or f"claude:main:{line}（失敗1件）:" in row
        assert text in row
    assert "失敗1件" in document
    assert "結果本文を混ぜない" not in document
    assert len(document.encode("utf-8")) <= 100_000


def test_prepare_compacts_only_consecutive_calls_when_minimum_rows_overflow(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """最小行の総量が予算を超えたら、発話と区間の位置を保って連続呼出を集約する。"""
    entries = []
    ranges = []
    for turn in range(3):
        entries.append(support.timestamped_entry("2026-11-01T00:00:00Z", f"区間{turn}"))
        entries.append(support.assistant_text(f"判断{turn}"))
        first = len(entries) + 1
        for index in range(600):
            entries.append(
                support.claude_call("2026-11-01T00:00:01Z", f"c{turn}-{index}", "Read", {"file_path": f"/repo/{index}"})
            )
        ranges.append((first, len(entries)))
        entries.append(support.assistant_text(f"完了{turn}"))
    transcript = tmp_path / "large.jsonl"
    support.write_jsonl(transcript, entries)
    work = tmp_path / "work"
    work.mkdir()
    assert (
        _run("session-review-prepare", ["--transcript", str(transcript), "--work-dir", str(work), "--conversation-only"]) == 0
    )
    document = pathlib.Path(json.loads(capsys.readouterr().out)["conversation_path"]).read_text(encoding="utf-8")
    previous = -1
    for turn, (first, last) in enumerate(ranges):
        positions = [
            document.index(text)
            for text in (f"区間{turn}", f"判断{turn}", f"claude:large:{first}〜claude:large:{last}", f"完了{turn}")
        ]
        assert previous < positions[0] < positions[1] < positions[2] < positions[3]
        previous = positions[-1]
    assert document.count("呼出600件") == 3
    assert len(document.encode("utf-8")) <= 100_000


@pytest.mark.parametrize("wrapped", [False, True])
def test_prepare_shows_initial_delegate_request_and_user_query_keeps_human_origin(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, wrapped: bool
) -> None:
    """実配送生成を経た依頼の本文を通読へ載せ、追送と機械資料を依頼へ含めない。"""
    parent = tmp_path / "parent.jsonl"
    support.write_jsonl(parent, [support.timestamped_entry("2026-11-01T00:00:00Z", "メインへの依頼")])
    body = "最初の委譲依頼を実装する"
    if wrapped:
        nested = message_format.auto_message(
            "機械資料の先頭\n" + message_format.auto_message("入れ子の資料", source="agents-server", kind="work-contract"),
            source="read-delivery",
            kind="startup-document",
        )
        body = wrap_delivery_body(body + "\n" + nested)
    support.write_subagent(
        parent.with_suffix("") / "subagents",
        "agent-child",
        [
            support.timestamped_entry("2026-11-01T00:00:01Z", body),
            support.timestamped_entry("2026-11-01T00:00:02Z", wrap_delivery_body("追送だけの指示")),
            support.assistant_text("担当の返却"),
        ],
    )
    work = tmp_path / "work"
    work.mkdir()
    assert _run("session-review-prepare", ["--transcript", str(parent), "--work-dir", str(work), "--conversation-only"]) == 0
    document = pathlib.Path(json.loads(capsys.readouterr().out)["conversation_path"]).read_text(encoding="utf-8")
    assert "依頼=最初の委譲依頼を実装する" in document
    assert "機械資料の先頭" not in document and "入れ子の資料" not in document
    assert "追送だけの指示" not in document
    child = parent.with_suffix("") / "subagents" / "agent-child.jsonl"
    assert _run("session-review-evidence", [str(child), "--user-events"]) == 0
    users = [event for event in support.read_jsonl(capsys) if event["kind"] == "user"]
    assert [event["text"] for event in users] == ([] if wrapped else [body])


def test_codex_detail_exposes_inputs_outputs_and_execution_status(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """既存Codex記録形式の入力・出力・CommandExecutionをJSON再解析なしで取得する。"""
    transcript = tmp_path / "codex.jsonl"
    # response_itemとitem_completedの構造は既存のdetail・execution試験と共通である。
    payloads: list[dict[str, object]] = [
        {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "c1",
            "arguments": json.dumps({"cmd": "echo ok", "options": [1, True]}),
        },
        {"type": "function_call_output", "call_id": "c1", "output": "実行結果本文"},
        {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2", "input": "パッチの入力"},
        {"type": "custom_tool_call_output", "call_id": "c2", "output": "パッチの結果"},
        {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "id": "inner",
                "command": ["echo", "ok"],
                "status": "completed",
                "exit_code": 0,
                "stdout": "標準出力",
                "stderr": "標準エラー",
            },
        },
        {"type": "function_call", "name": "tool", "call_id": "long", "arguments": json.dumps({"body": "あ" * 9000})},
        {
            "type": "item_completed",
            "item": {
                "type": "McpToolCall",
                "id": "mcp",
                "server": "files",
                "tool": "read",
                "arguments": {"path": "/repo/file"},
                "status": "completed",
                "result": {"content": [{"type": "text", "text": "MCP本文"}]},
            },
        },
        {"type": "future_item", "value": "未知形式の保持"},
    ]
    support.write_jsonl(
        transcript,
        [
            {
                "type": "event_msg" if payload["type"] == "item_completed" else "response_item",
                "timestamp": "2026-11-01T00:00:00Z",
                "payload": payload,
            }
            for payload in payloads
        ],
    )
    arguments = [str(transcript)]
    for line in range(1, 9):
        arguments.extend(["--detail", f"codex:codex:{line}"])
    assert _run("session-review-evidence", arguments) == 0
    events = support.read_jsonl(capsys, raw=True)
    assert events[0]["input"] == {"cmd": "echo ok", "options": [1, True]}
    assert events[0]["call_id"] == events[1]["call_id"] == "c1"
    assert events[1]["text"] == "実行結果本文"
    assert events[2]["input"] == "パッチの入力" and events[3]["text"] == "パッチの結果"
    assert events[4]["command"] == ["echo", "ok"]
    assert events[4]["status"] == "completed" and events[4]["exit_code"] == 0
    assert events[4]["text"] == "標準出力" and events[4]["stderr"] == "標準エラー"
    assert events[5]["omitted"] is True and len(events[5]["input"]["body"]) <= 8000
    assert events[6]["input"] == {"path": "/repo/file"} and events[6]["text"] == "MCP本文"
    assert events[6]["call_id"] == "mcp" and events[6]["name"] == "mcp__files__read"
    assert json.loads(events[7]["text"])["payload"]["value"] == "未知形式の保持"
    assert [(event["record"], event["line"], event["timestamp"]) for event in events] == [
        ("codex:codex", line, "2026-11-01T00:00:00Z") for line in range(1, 9)
    ]


def test_codex_failed_execution_details_reach_bundle_evidence(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """失敗候補の個別証拠も、detailと同じコマンドと終了状態を保持する。"""
    transcript = tmp_path / "failed.jsonl"
    support.write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "timestamp": "2026-11-01T00:00:00Z",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "id": "failed-command",
                        "command": ["lint", "project.py"],
                        "status": "failed",
                        "exit_code": 2,
                        "stderr": "error: 検証を実行できない",
                    },
                },
            }
        ],
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert _run("session-review-evidence", [str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    index = [json.loads(line) for line in (bundle / "candidate-evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    details = [
        event
        for row in index
        for event in json.loads((bundle / row["path"]).read_text(encoding="utf-8"))["events"]
        if event.get("name") == "CommandExecution"
    ]
    assert details
    assert all(event["command"] == ["lint", "project.py"] and event["exit_code"] == 2 for event in details)
    assert all(event["status"] == "failed" and event["stderr"] == "error: 検証を実行できない" for event in details)


def test_reported_improvements_keep_duplicate_sources_lines_and_full_bodies(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """長文の改善点、同じ出所の同文行、別出所の同文を位置と行順付きで保持する。"""
    repeated = "気付いた改善点: 同じ内容"
    long = "気付いた改善点: " + "長い改善の本文" * 1200
    parent = tmp_path / "parent.jsonl"
    first = support.assistant_text(f"{repeated}\n{long}\n{repeated}\n```text\n気付いた改善点: コード例\n```")
    first["timestamp"] = "2026-11-01T00:00:01Z"
    later = support.assistant_text("気付いた改善点: 境界以後")
    later["timestamp"] = "2026-11-01T00:00:03Z"
    support.write_jsonl(parent, [first, later])
    child = support.assistant_text(repeated)
    child["timestamp"] = "2026-11-01T00:00:02Z"
    support.write_subagent(parent.with_suffix("") / "subagents", "agent-child", [child])
    assert (
        _run(
            "session-review-evidence",
            [str(parent), "--reported-improvements", "--observation-boundary", "2026-11-01T00:00:02Z"],
        )
        == 0
    )
    events = support.read_jsonl(capsys, raw=True)
    improvements = [event for event in events if event["kind"] == "reported-improvement"]
    assert [event["text"] for event in improvements] == [repeated, long, repeated, repeated]
    assert [event["record"] for event in improvements] == ["claude:parent"] * 3 + ["claude:parent/agent-child"]
    assert [event["line"] for event in improvements] == [1, 1, 1, 1]
    assert events[-1] == {"kind": "reported-improvement-summary", "count": 4}
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert _run("session-review-evidence", [str(parent), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    timeline = [json.loads(line) for line in (bundle / "timeline.jsonl").read_text(encoding="utf-8").splitlines()]
    assert next(
        event["reported_improvements"]
        for event in timeline
        if event.get("record") == "claude:parent" and event.get("line") == 1
    ) == [repeated, long, repeated]


def test_reported_improvements_distinguish_zero_and_unreadable_records(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """報告0件は要約、読込不能は非0と診断として区別する。"""
    transcript = tmp_path / "empty.jsonl"
    support.write_jsonl(transcript, [support.assistant_text("正常な返却")])
    assert _run("session-review-evidence", [str(transcript), "--reported-improvements"]) == 0
    assert support.read_jsonl(capsys) == [{"kind": "reported-improvement-summary", "count": 0}]
    assert _run("session-review-evidence", [str(tmp_path / "missing.jsonl"), "--reported-improvements"]) == 2
    assert any(event["kind"] == "error" for event in support.read_jsonl(capsys))
    missing_thread = "11111111-2222-4333-8444-555555555555"
    support.write_jsonl(
        transcript,
        [
            support.codex_tool_use_entry("2026-11-01T00:00:00Z", "missing", missing_thread),
            support.codex_tool_result_entry("2026-11-01T00:00:01Z", "missing", missing_thread),
        ],
    )
    assert _run("session-review-evidence", [str(transcript), "--reported-improvements"]) == 0
    events = support.read_jsonl(capsys, raw=True)
    assert {"kind": "reported-improvement-summary", "count": 0} in events
    assert any(event["kind"] == "unresolved-record" and event["record"] == missing_thread for event in events)


@pytest.mark.parametrize("other", [["--summary-only"], ["--bundle", "unused"], ["--catalog-claude-project", "unused"]])
def test_reported_improvements_reject_incompatible_query_modes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], other: list[str]
) -> None:
    """改善点の全量照会は、要約・bundle・カタログの既存の併用制約を守る。"""
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(transcript, [support.assistant_text("正常な返却")])
    assert _run("session-review-evidence", [str(transcript), "--reported-improvements", *other]) == 2
    assert any(event["kind"] == "error" for event in support.read_jsonl(capsys))
