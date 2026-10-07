"""証拠抽出のカタログ走査（`session_evidence_catalog.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.session_evidence_support import (
    TOOL_CALL_THREAD,
    agy_step,
    claude_call,
    codex_tool_result_entry,
    codex_tool_use_entry,
    local_time_transcript,
    read_jsonl,
    timestamped_entry,
    tool_call_session,
    write_agy_log,
    write_jsonl,
)


@pytest.mark.usefixtures("local_time_jst")
def test_observation_boundary_without_timezone_is_local_time(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """タイムゾーンを省いた`--observation-boundary`は、単一transcriptとカタログ走査の双方でローカル時刻として扱う。"""
    transcript = local_time_transcript(tmp_path)

    assert evidence.main([str(transcript), "--observation-boundary", "2026-10-07T00:00:00"]) == 0
    naive = read_jsonl(capsys)
    assert evidence.main([str(transcript), "--observation-boundary", "2026-10-07T00:00:00+09:00"]) == 0
    assert naive == read_jsonl(capsys)
    assert [event["text"] for event in naive] == ["JSTの10月6日23時59分の発話"]

    root = tmp_path / "project"
    write_jsonl(root / "later-session.jsonl", [timestamped_entry("2026-10-06T15:01:13Z", "境界後に始まるセッション")])
    catalog = ["--catalog-claude-project", str(root), "--since", "2026-10-06T00:00:00Z", "--observation-boundary"]
    assert evidence.main([*catalog, "2026-10-07T00:00:00"]) == 0
    naive_catalog = read_jsonl(capsys, raw=True)
    assert evidence.main([*catalog, "2026-10-07T00:00:00+09:00"]) == 0
    assert naive_catalog == read_jsonl(capsys, raw=True)
    assert [event["kind"] for event in naive_catalog] == ["catalog-summary"]


def test_catalog_claude_project_aggregates_traceable_descendants_without_leaving_root(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Claudeカタログはroot内のサブエージェント記録だけを集約し、root外参照を未解決として数える。"""
    root = tmp_path / "project"
    child_id = "child-session"
    missing_id = "outside-session"
    write_jsonl(
        root / "parent-session.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:00Z",
                "cwd": "/repo",
                "gitBranch": "develop",
                "message": {"role": "user", "content": "Base directory for this skill: /plugin/skills/process-wi"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-10T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "mcp__agents_server__start", "id": "child", "input": {}}],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:02Z",
                "toolUseResult": {"sessionId": child_id, "engine": "claude"},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "child"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-10T00:00:03Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "mcp__agents_server__start", "id": "missing", "input": {}}],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:04Z",
                "toolUseResult": {"sessionId": missing_id, "engine": "claude"},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "missing"}]},
            },
        ],
    )
    write_jsonl(
        root / f"{child_id}.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-09-10T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "wi", "input": {"command": "atk wi get"}}],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:02Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "wi", "content": "ok"}]},
            },
        ],
    )

    assert (
        evidence.main(
            [
                "--catalog-claude-project",
                str(root),
                "--since",
                "2026-09-09T00:00:00Z",
                "--observation-boundary",
                "2026-09-11T00:00:00Z",
            ]
        )
        == 0
    )
    events = read_jsonl(capsys, raw=True)
    parent, summary = events
    assert parent["session_id"] == "parent-session"
    assert parent["workflow"] == "process-wi"
    assert parent["descendant_count"] == 1
    assert parent["successful_wi_operation_count"] == 1
    assert parent["successful_wi_operations"][0]["operation"] == "get"
    assert summary["parent_record_count"] == 1
    assert summary["unresolved_record_count"] == 1


def test_catalog_counts_agy_children(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """走査rootの外にあるagyの委譲先ログを親の子孫数とトークンへ含め、未解決の参照に数えない。"""
    child_id = "e9244465-1577-44a9-a7b0-500b1f976b0e"
    root = tmp_path / "project"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_jsonl(
        root / "parent-session.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-10T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            codex_tool_use_entry(
                "2026-09-10T00:00:01Z",
                "call-agy",
                child_id,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_write",
            ),
            codex_tool_result_entry("2026-09-10T00:00:02Z", "call-agy", child_id, engine=None),
        ],
    )
    write_agy_log(
        tmp_path / "state",
        child_id,
        [
            agy_step(
                1,
                "agent_response",
                "DONE",
                usage={"input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 11, "total_tokens": 10},
            )
        ],
    )

    assert (
        evidence.main(
            [
                "--catalog-claude-project",
                str(root),
                "--since",
                "2026-09-09T00:00:00Z",
                "--observation-boundary",
                "2026-09-11T00:00:00Z",
            ]
        )
        == 0
    )
    parent, summary = read_jsonl(capsys, raw=True)
    assert parent["session_id"] == "parent-session"
    assert parent["descendant_count"] == 1
    assert parent["tokens"]["cache_read_input_tokens"] == 11
    assert summary["unresolved_record_count"] == 0


def test_catalog_codex_history_reports_unknown_fields_and_successful_wi_operation(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexカタログはメイン記録を期間で選び、未記録値をunknownとして返す。"""
    root = tmp_path / "codex-history"
    session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    write_jsonl(
        root / f"rollout-test-{session_id}.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-09-10T00:00:00Z",
                "payload": {"id": session_id, "cwd": "/repo"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-09-10T00:00:01Z",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "completed", "command": ["atk", "wi", "list"]},
                },
            },
        ],
    )

    assert (
        evidence.main(
            [
                "--catalog-codex-history",
                str(root),
                "--since",
                "2026-09-09T00:00:00Z",
                "--observation-boundary",
                "2026-09-11T00:00:00Z",
            ]
        )
        == 0
    )
    parent, summary = read_jsonl(capsys, raw=True)
    assert parent["session_id"] == session_id
    assert parent["branch"] == "unknown"
    assert parent["workflow"] == "unknown"
    assert parent["tokens"] == "unknown"
    assert parent["successful_wi_operations"][0]["operation"] == "list"
    assert summary["parent_record_count"] == 1


def test_catalog_rejects_an_unclassifiable_root_and_reversed_period(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """記録を判別できないrootと開始後に終わる観測期間を終了コード2で拒否する。"""
    root = tmp_path / "empty"
    root.mkdir()
    base = ["--catalog-claude-project", str(root), "--since", "2026-09-10T00:00:00Z"]

    assert evidence.main([*base, "--observation-boundary", "2026-09-11T00:00:00Z"]) == 2
    assert "判別できない" in read_jsonl(capsys)[0]["text"]

    assert evidence.main([*base, "--observation-boundary", "2026-09-09T00:00:00Z"]) == 2
    assert read_jsonl(capsys) == [{"kind": "error", "text": "観測境界は開始境界以後を指定する"}]


def test_tool_calls_with_catalog_include_delegates_outside_root_within_window(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """カタログ併用では窓の中の呼び出しを、走査rootの外の委譲先も含めて親セッションの`session_id`付きで返す。"""
    transcript = tool_call_session(tmp_path, monkeypatch, "echo done")
    root = transcript.parent
    write_jsonl(
        root / "outside-window.jsonl",
        [claude_call("2026-10-05T00:00:00Z", "early", "Bash", {"command": "echo early"})],
    )

    assert (
        evidence.main(
            [
                "--catalog-claude-project",
                str(root),
                "--since",
                "2026-10-06T00:00:02Z",
                "--observation-boundary",
                "2026-10-06T00:00:08Z",
                "--tool-calls",
            ]
        )
        == 0
    )

    *calls, summary = read_jsonl(capsys, raw=True)
    assert [(call["session_id"], call["record"], call["call_id"]) for call in calls] == [
        ("parent-session", "claude:parent-session", "bash-embed"),
        ("parent-session", "claude:parent-session/agent-child", "read-1"),
        ("parent-session", "claude:parent-session", "call-codex"),
        ("parent-session", f"codex:{TOOL_CALL_THREAD}", "c-exec"),
    ]
    assert summary["count"] == 4
    assert summary["by_session"] == {"parent-session": 4}
    assert summary["scan_root"] == str(root.resolve())
    assert summary["parent_record_count"] == 1
    assert summary["since"] == "2026-10-06T00:00:02+00:00"
    assert summary["observation_boundary"] == "2026-10-06T00:00:08+00:00"

    session_id = calls[-1]["session_id"]
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    projects = tmp_path / "home" / ".claude" / "projects"
    projects.mkdir(parents=True)
    (projects / "project").symlink_to(root, target_is_directory=True)
    assert evidence.main(["--claude-session-id", session_id, "--detail", f"{calls[-1]['record']}:{calls[-1]['line']}"]) == 0
    assert "rg needle" in json.dumps(read_jsonl(capsys, raw=True), ensure_ascii=False)


def test_catalog_still_rejects_other_query_modes(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--tool-calls`以外の照会modeとカタログ走査の併用は従来どおり拒否する。"""
    root = tmp_path / "project"
    write_jsonl(root / "session.jsonl", [timestamped_entry("2026-10-06T00:00:00Z", "記録")])

    assert (
        evidence.main(
            [
                "--catalog-claude-project",
                str(root),
                "--since",
                "2026-10-05T00:00:00Z",
                "--observation-boundary",
                "2026-10-07T00:00:00Z",
                "--grep",
                "記録",
            ]
        )
        == 2
    )
    (event,) = read_jsonl(capsys)
    assert "併用できない" in event["text"]
