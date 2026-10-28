"""照会の要約選択と横断発話検索を、証拠抽出の公開関数から検証する。

記録の形はsession_evidence_user_events_test.pyのClaude Code 2.1.281〜2.1.295と
2026年8月〜10月のCodex rollout、および共有のsession_evidence_supportから写した。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.session_evidence_support import (
    assistant_text,
    claude_call,
    codex_item,
    read_jsonl,
    timestamped_entry,
    write_jsonl,
    write_subagent,
)


def _query(capsys: pytest.CaptureFixture[str], arguments: list[str], code: int = 0) -> list[dict[str, Any]]:
    assert evidence.main(arguments) == code
    return read_jsonl(capsys, raw=True)


def _record(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "parent.jsonl"
    write_jsonl(
        path,
        [
            timestamped_entry("2026-10-01T00:00:01Z", "needle\nneedle"),
            {**assistant_text("needle"), "timestamp": "2026-10-01T00:00:02Z"},
            claude_call("2026-10-01T00:00:03Z", "call", "Bash", {"command": "echo needle"}),
            timestamped_entry("2026-10-01T00:00:04Z", "終わり"),
        ],
    )
    return path


@pytest.mark.parametrize("mode", ["grep", "fixed-string", "context-at", "tool-calls", "warn"])
def test_summary_only_query_modes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], mode: str) -> None:
    """詳細と要約の単位を保ち、0件も公開表示から取得する。"""
    transcript = _record(tmp_path)
    arguments = {
        "grep": ["--grep", "needle"],
        "fixed-string": ["--fixed-string", "needle", "--fixed-string", "missing"],
        "context-at": ["--context-at", "4", "--phrase", "needle", "--phrase", "missing"],
        "tool-calls": ["--tool-calls"],
        "warn": ["--warn"],
    }[mode]
    normal = _query(capsys, [str(transcript), *arguments])
    summary = _query(capsys, [str(transcript), *arguments, "--summary-only"])
    assert not {event["kind"] for event in summary} & {"match", "context-match", "tool-call", "warning"}
    if mode == "grep":
        assert [event for event in summary if event["kind"] == "summary"] == [
            event for event in normal if event["kind"] == "summary"
        ]
        assert summary[-1]["count"] == 3
    elif mode == "fixed-string":
        assert [(event["query"], event["count"]) for event in summary if event["kind"] == "fixed-string-summary"] == [
            ("needle", 3),
            ("missing", 0),
        ]
        assert all("locators" not in event for event in summary)
    elif mode == "context-at":
        assert [event for event in summary if event["kind"] == "context-verdict"] == [
            event for event in normal if event["kind"] == "context-verdict"
        ]
    elif mode == "tool-calls":
        assert [event for event in summary if event["kind"] == "tool-call-summary"] == [
            event for event in normal if event["kind"] == "tool-call-summary"
        ]
        assert summary[-1]["count"] == 1
    else:
        assert summary[-1] == {"kind": "warning-summary", "count": 0}
    if mode in {"grep", "fixed-string", "tool-calls"}:
        assert any(event["kind"] == "record-provenance" for event in summary)


def test_summary_only_bundle_keeps_saved_files(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """bundleの全量保存と無指定表示を保ちながら、位置と本文の表示だけを省く。"""
    transcript = _record(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    normal = _query(capsys, [str(transcript), "--bundle", str(bundle)])
    contents = {str(path.relative_to(bundle)): path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    summary = _query(capsys, [str(transcript), "--bundle", str(bundle), "--summary-only"])
    assert {str(path.relative_to(bundle)): path.read_bytes() for path in bundle.rglob("*") if path.is_file()} == contents
    assert not {event["kind"] for event in summary} & {"bundle-locator", "bundle-warning-group"}
    kinds = {"bundle-file", "bundle-evidence-metrics", "bundle-kind-count"}
    assert [event for event in summary if event["kind"] in kinds] == [event for event in normal if event["kind"] in kinds]


@pytest.mark.parametrize("summary_only", [False, True])
def test_bundle_scope_tracks_appended_records(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], summary_only: bool
) -> None:
    """表示の要約指定によらず、元記録の追記を新しい完全行とdigestへ反映する。"""
    transcript = _record(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    arguments = [str(transcript), "--bundle", str(bundle)]
    if summary_only:
        arguments.append("--summary-only")
    _query(capsys, arguments)
    before = json.loads((bundle / "scope.json").read_text(encoding="utf-8"))["records"]["claude:parent"]
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(timestamped_entry("2026-10-01T00:00:05Z", "追記した要求"), ensure_ascii=False) + "\n")
    _query(capsys, arguments)
    after = json.loads((bundle / "scope.json").read_text(encoding="utf-8"))["records"]["claude:parent"]
    assert before["through_line"] == 4
    assert after["through_line"] == 5
    assert after["digest"] != before["digest"]


def test_summary_only_warning_deduplication(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """hookの成功記録と追加文脈にある同じ警告を1件と数える。"""
    path = tmp_path / "warnings.jsonl"
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 警告本文"
    write_jsonl(
        path,
        [
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": notice}}),
                    "stderr": "",
                    "exitCode": 0,
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            },
        ],
    )
    normal = _query(capsys, [str(path), "--warn"])
    assert len([event for event in normal if event["kind"] == "warning"]) == 1
    summary = _query(capsys, [str(path), "--warn", "--summary-only"])
    assert summary[-1] == {"kind": "warning-summary", "count": 1}


def test_user_events_search_invalid_catalog_options(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """発話の検索と親の表示項目の組、および対象外の要約を修正方法付きで拒否する。"""
    _path, catalog = _history(tmp_path, "claude")
    for arguments in (["--user-events", "--catalog-fields", "cwd"], ["--summary-only"]):
        event = _query(capsys, [*catalog, *arguments], 2)[0]
        assert event["kind"] == "error" and event["next_action"]


def test_summary_only_keeps_diagnostics(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """未解決委譲と読取不能を要約時にも保持し、カタログの呼出し件数も保持する。"""
    transcript = _record(tmp_path)
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(claude_call("2026-10-01T00:00:05Z", "unresolved", "mcp__agents_server__start", {"engine": "codex"}))
            + "\n"
        )
    write_subagent(transcript.with_suffix("") / "subagents", "agent-broken", [])
    broken = transcript.with_suffix("") / "subagents" / "agent-broken.jsonl"
    broken.write_text("broken", encoding="utf-8")
    for source in (
        [str(transcript)],
        ["--catalog-claude-project", str(tmp_path), "--since", "2026-10-01", "--observation-boundary", "2026-10-02"],
    ):
        normal = _query(capsys, [*source, "--tool-calls"])
        summary = _query(capsys, [*source, "--tool-calls", "--summary-only"])
        diagnostics = {"unresolved-record", "unresolved-delegation", "error"}
        assert [event for event in summary if event["kind"] in diagnostics] == [
            event for event in normal if event["kind"] in diagnostics
        ]
        assert any(event["kind"] in diagnostics for event in summary)
        assert [event for event in summary if event["kind"] == "tool-call-summary"] == [
            event for event in normal if event["kind"] == "tool-call-summary"
        ]
    errors = _query(capsys, [str(tmp_path / "missing.jsonl"), "--grep", "x", "--summary-only"], 2)
    assert errors[0]["kind"] == "error" and errors[0]["next_action"]


@pytest.mark.parametrize(
    "mode",
    [[], ["--user-events"], ["--detail", "1"], ["--record-schema", "1"], ["--elapsed-until", "2026-10-02"], ["--hook-notices"]],
)
def test_summary_only_rejects_unsupported_modes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], mode: list[str]
) -> None:
    result = _query(capsys, [str(_record(tmp_path)), *mode, "--summary-only"], 2)
    assert result[0]["kind"] == "error" and result[0]["next_action"]


def _history(tmp_path: pathlib.Path, runtime: str) -> tuple[pathlib.Path, list[str]]:
    root = tmp_path / runtime
    root.mkdir()
    for name, cwd in (("parent", "/repo/target"), ("other", "/elsewhere")):
        if runtime == "claude":
            path = root / f"{name}.jsonl"
            entries = [
                {**timestamped_entry("2026-10-01T00:00:00Z", "/process-wi"), "cwd": cwd},
                {**assistant_text("前の発話 needle"), "timestamp": "2026-10-01T00:00:01Z"},
                timestamped_entry("2026-10-01T00:00:02Z", "needle 原文\n" + "長文" * 2000),
                timestamped_entry(None, "needle 時刻なし"),
                {**timestamped_entry("2026-10-01T00:00:03Z", "needle 通知"), "origin": {"kind": "plugin"}},
                timestamped_entry("2026-10-01T00:00:04Z", "needle 観測後"),
            ]
        else:
            path = root / f"rollout-{name}.jsonl"

            def message(role: str, text: str, timestamp: str | None) -> dict:
                return {
                    **codex_item(
                        timestamp or "",
                        {
                            "type": "message",
                            "role": role,
                            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
                        },
                    ),
                    "timestamp": timestamp,
                }

            entries = [
                {"type": "session_meta", "timestamp": "2026-10-01T00:00:00Z", "payload": {"id": name, "cwd": cwd}},
                message("user", "/process-wi", "2026-10-01T00:00:00Z"),
                message("assistant", "前の発話 needle", "2026-10-01T00:00:01Z"),
                message("user", "needle 原文\n" + "長文" * 2000, "2026-10-01T00:00:02Z"),
                message("user", "needle 時刻なし", None),
                message("user", '<atk-auto source="test" kind="notice">needle 通知</atk-auto>', "2026-10-01T00:00:03Z"),
                message("user", "needle 観測後", "2026-10-01T00:00:04Z"),
            ]
        write_jsonl(path, entries)
    return path if name == "parent" else root / ("parent.jsonl" if runtime == "claude" else "rollout-parent.jsonl"), [
        "--catalog-claude-project" if runtime == "claude" else "--catalog-codex-history",
        str(root),
        "--since",
        "2026-10-01T00:00:01Z",
        "--observation-boundary",
        "2026-10-01T00:00:03Z",
    ]


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("search", [["--user-match-fixed", "needle"], ["--user-match-regex", "n.e+dl[e]"]])
def test_catalog_user_events_search(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str, search: list[str]
) -> None:
    _path, catalog = _history(tmp_path, runtime)
    events = _query(
        capsys,
        [
            *catalog,
            "--user-events",
            *search,
            "--catalog-known-workflow",
            "--catalog-workflow",
            "process-wi",
            "--catalog-cwd-contains",
            "target",
            "--with-preceding-assistant",
        ],
    )
    users = [event for event in events if event["kind"] == "user"]
    assert len(users) == 1
    assert users[0]["text"] == "needle 原文\n" + "長文" * 2000
    assert users[0]["timestamp"] == "2026-10-01T00:00:02Z" and users[0]["session_id"] == "parent"
    assert users[0]["line"] == (3 if runtime == "claude" else 4) and users[0]["sequence"] > 0
    assert users[0]["preceding_assistant"]["text"] == "前の発話 needle"
    assert events[-1]["count"] == 1 and events[-1]["parent_record_count"] == 2 and events[-1]["selected_parent_count"] == 1
    assert events[-1]["undated_event_count"] == 1
    empty = _query(capsys, [*catalog, "--user-events", "--user-match-fixed", "不一致"])
    assert empty[-1]["count"] == 0 and empty[-1]["selected_parent_count"] == 2


def test_user_events_search_excludes_generated_and_context(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "answer.jsonl"
    question = claude_call(
        "2026-10-01T00:00:01Z",
        "ask",
        "AskUserQuestion",
        {"questions": [{"question": "質問だけの語", "options": [{"label": "選択肢だけの語"}]}]},
    )
    answer = {
        "type": "user",
        "timestamp": "2026-10-01T00:00:02Z",
        "toolUseResult": {"answers": {"質問だけの語": "実回答"}},
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "ask", "content": "質問だけの語"}]},
    }
    write_jsonl(path, [question, answer, timestamped_entry(None, '<atk-auto source="test" kind="notice">実回答</atk-auto>')])
    for text, count in (("質問だけの語", 0), ("選択肢だけの語", 0), ("実回答", 1)):
        events = _query(capsys, [str(path), "--user-events", "--user-match-fixed", text])
        assert events[-1]["count"] == count
        if count:
            user = events[0]
            assert user["line"] == 2 and user["timestamp"] == "2026-10-01T00:00:02Z"
            assert user["assistant_context"] and user["user_response"]


def test_user_events_preceding_assistant(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _record(tmp_path)
    events = _query(capsys, [str(path), "--user-events", "--with-preceding-assistant"])
    users = [event for event in events if event["kind"] == "user"]
    assert users[0]["preceding_assistant"] is None
    assert users[1]["preceding_assistant"] == {
        "kind": "utterance",
        "role": "assistant",
        "record": "claude:parent",
        "line": 2,
        "timestamp": "2026-10-01T00:00:02Z",
        "text": "needle",
    }
    assert events[-1]["count"] == 2


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_user_events_search_single_matches_catalog(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    path, catalog = _history(tmp_path, runtime)
    search = ["--user-events", "--user-match-fixed", "needle", "--with-preceding-assistant"]
    single = _query(capsys, [str(path), *catalog[2:], *search])
    multiple = _query(capsys, [*catalog, *search, "--catalog-cwd-contains", "target"])

    assert [event for event in single if event["kind"] in {"user", "user-event-undated"}] == [
        event for event in multiple if event["kind"] in {"user", "user-event-undated"}
    ]
    broken = path.parent / ("broken.jsonl" if runtime == "claude" else "rollout-broken.jsonl")
    broken.write_bytes(b"not-json")
    diagnosed = _query(capsys, [*catalog, *search])
    assert diagnosed[-1]["unresolved_record_count"] == 1
    assert any(event["kind"] == "unresolved-record" and event["next_action"] for event in diagnosed)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--user-match-fixed", "x"],
        ["--user-match-regex", "x"],
        ["--with-preceding-assistant"],
        ["--user-events", "--user-match-regex", "["],
        ["--user-events", "--user-match-fixed", "x", "--user-match-regex", "x"],
    ],
)
def test_user_events_search_invalid_options(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    events = _query(capsys, [str(_record(tmp_path)), *arguments], 2)
    assert events[0]["kind"] == "error" and events[0]["next_action"]
