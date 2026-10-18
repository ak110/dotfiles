"""証拠抽出のカタログ走査（`session_evidence_catalog.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

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


def _operation_catalog(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], entries: list[dict[str, Any]], *, runtime: str = "codex"
) -> list[dict[str, Any]]:
    """ホスト記録を公開カタログCLIへ渡し、エンドユーザーが受け取る成功操作を返す。"""
    root = tmp_path / "records"
    timestamp = "2026-09-10T00:00:00Z"
    if runtime == "codex":
        records = [{"type": "session_meta", "timestamp": timestamp, "payload": {"id": "operation"}}]
        records.extend({"type": "response_item", "timestamp": timestamp, **entry} for entry in entries)
        path = root / "rollout-operation.jsonl"
    else:
        records = [
            {"type": "assistant" if index == 0 else "user", "timestamp": timestamp, **entry}
            for index, entry in enumerate(entries)
        ]
        path = root / "operation.jsonl"
    write_jsonl(path, records)
    option = "--catalog-codex-history" if runtime == "codex" else "--catalog-claude-project"
    assert (
        evidence.main([option, str(root), "--since", "2026-09-09T00:00:00Z", "--observation-boundary", "2026-09-11T00:00:00Z"])
        == 0
    )
    parent, _summary = read_jsonl(capsys, raw=True)
    return parent["successful_wi_operations"]


@pytest.mark.parametrize(
    "output",
    [{}, {"exit_code": 7}, {"exit_code": False}, {"exit_code": True}, {"exit_code": 0.0}, {"session_id": 12}, "未解析の結果"],
)
def test_wi_operation_requires_terminal_success(
    output: object, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """終了0を欠く応答を成功操作へ含めない。"""
    entries: list[dict[str, Any]] = [
        {"payload": {"type": "function_call", "call_id": "a", "arguments": json.dumps({"cmd": "atk wi get"})}},
        {"payload": {"type": "function_call_output", "call_id": "a", "output": output}},
    ]
    assert not _operation_catalog(tmp_path, capsys, entries)


def test_wi_operation_tracks_shell_wrapped_command_until_poll_completion(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """shell・exec包みの実行中応答を保持し、後続pollの終了0を元の操作へ対応させる。"""
    entries: list[dict[str, Any]] = [
        {
            "payload": {
                "type": "function_call",
                "call_id": "a",
                "arguments": json.dumps({"cmd": "/bin/bash -lc 'exec atk wi get'"}),
            }
        },
        {"payload": {"type": "function_call_output", "call_id": "a", "output": {"session_id": 12}}},
        {"payload": {"type": "function_call", "call_id": "b", "arguments": json.dumps({"session_id": 12})}},
        {"payload": {"type": "function_call_output", "call_id": "b", "output": {"session_id": 12}}},
        {"payload": {"type": "function_call", "call_id": "c", "arguments": json.dumps({"session_id": 12})}},
        {"payload": {"type": "function_call_output", "call_id": "c", "output": "Process exited with code 0"}},
    ]
    assert _operation_catalog(tmp_path, capsys, entries) == [
        {"operation": "get", "locator": {"record": "operation", "line": 2}}
    ]


@pytest.mark.parametrize(
    ("result", "expected"),
    [({"exit_code": 0}, 1), ({}, 0), ({"exit_code": 3}, 0), ({"exit_code": 0, "status": "running"}, 0)],
)
def test_completed_item_requires_exit_zero(
    result: dict, expected: int, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """完了というstatusだけで成功とせず、終了状態も検収する。"""
    entry = {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "command": ["atk", "wi", "list"],
                "status": "completed",
                **result,
            },
        },
    }
    assert len(_operation_catalog(tmp_path, capsys, [entry])) == expected


@pytest.mark.parametrize(
    ("output", "flag", "expected"),
    [
        ("ok", None, 0),
        ("ok", False, 1),
        ("Command running in background with ID: job1", False, 0),
        ("Process exited with code 0", True, 0),
    ],
)
def test_claude_success_is_explicit_and_terminal(
    output: str, flag: bool | None, expected: int, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claudeも不明な結果と背景実行を成功へ数えず、明示失敗を優先する。"""
    entries: list[dict[str, Any]] = [
        {"message": {"content": [{"type": "tool_use", "id": "a", "input": {"command": "atk wi get"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": output, "is_error": flag}]}},
    ]
    assert len(_operation_catalog(tmp_path, capsys, entries, runtime="claude")) == expected


def _catalog_fixture(tmp_path: pathlib.Path, runtime: str) -> list[str]:
    """既存のClaude・Codexテストコードと同じ記録形で、時刻順と条件が異なる3つの親セッションを作成する。"""
    root = tmp_path / "catalog"
    for prefix, second, workflow, cwd in (
        ("a", 2, True, "/repo/selected"),
        ("b", 1, True, "/repo/other"),
        ("c", 1, False, "/repo/selected"),
    ):
        session_id = f"{prefix * 8}-{prefix * 4}-4{prefix * 3}-8{prefix * 3}-{prefix * 12}"
        timestamp = f"2026-09-10T00:00:0{second}Z"
        text = "agent-toolkit:process-wiを実行する" if workflow else "通常の依頼"
        if runtime == "claude":
            records = [{"type": "user", "timestamp": timestamp, "cwd": cwd, "message": {"role": "user", "content": text}}]
            path = root / f"{session_id}.jsonl"
        else:
            records = [
                {"type": "session_meta", "timestamp": timestamp, "payload": {"id": session_id, "cwd": cwd}},
                {
                    "type": "response_item",
                    "timestamp": timestamp,
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
                },
            ]
            path = root / f"rollout-test-{session_id}.jsonl"
        write_jsonl(path, records)
    option = "--catalog-claude-project" if runtime == "claude" else "--catalog-codex-history"
    return [option, str(root), "--since", "2026-09-09T00:00:00Z", "--observation-boundary", "2026-09-11T00:00:00Z"]


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_catalog_selects_rows_and_fields(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    runtime: str,
) -> None:
    """公開指定だけで5項目・6項目・全項目と、workflow・cwdの積集合を取得する。"""
    argv = _catalog_fixture(tmp_path, runtime)
    fields = ["session_id", "workflow", "workflow_locator", "started_at", "tokens"]
    assert evidence.main([*argv, "--catalog-known-workflow", "--catalog-fields", ",".join(fields)]) == 0
    *parents, summary = read_jsonl(capsys, raw=True)
    assert len(parents) == 2
    assert all(list(parent) == ["kind", *fields] for parent in parents)
    assert parents[0]["session_id"].startswith("b") and parents[1]["session_id"].startswith("a")
    assert summary["parent_record_count"] == 3 and summary["selected_parent_count"] == 2
    for selected in ([*fields, "cwd"], None):
        options = ["--catalog-fields", ",".join(selected)] if selected else []
        assert evidence.main([*argv, "--catalog-workflow", "process-wi", "--catalog-cwd-contains", "selected", *options]) == 0
        parent, summary = read_jsonl(capsys, raw=True)
        assert parent["session_id"].startswith("a") and parent["cwd"] == "/repo/selected"
        if selected:
            assert list(parent) == ["kind", *selected]
        else:
            assert "successful_wi_operations" in parent and "branch" in parent
        assert summary["selected_parent_count"] == 1 and summary["parent_record_count"] == 3


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_catalog_empty_selection_preserves_scan_summary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    runtime: str,
) -> None:
    """親行0件でも、選択件数と走査・期間・未解決件数を区別できる。"""
    argv = _catalog_fixture(tmp_path, runtime)
    assert evidence.main([*argv, "--catalog-workflow", "absent", "--catalog-fields", "tokens,session_id"]) == 0
    (summary,) = read_jsonl(capsys, raw=True)
    assert summary["kind"] == "catalog-summary" and summary["runtime"] == runtime
    assert summary["scan_root"] == str((tmp_path / "catalog").resolve())
    assert summary["since"] == "2026-09-09T00:00:00+00:00"
    assert summary["observation_boundary"] == "2026-09-11T00:00:00+00:00"
    assert summary["parent_record_count"] == 3 and summary["selected_parent_count"] == 0
    assert summary["unresolved_record_count"] == 0


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_catalog_selection_validation_and_defaults(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    runtime: str,
) -> None:
    """無指定の全項目とunknownを保持し、不正な項目・非catalog・tool照会との併用を拒否する。"""
    argv = _catalog_fixture(tmp_path, runtime)
    assert evidence.main(argv) == 0
    *parents, summary = read_jsonl(capsys, raw=True)
    assert len(parents) == 3 and "selected_parent_count" not in summary
    assert any(parent["workflow"] == "unknown" for parent in parents)
    for value in ("no-such-field", "", "session_id,", "cwd,cwd"):
        assert evidence.main([*argv, "--catalog-fields", value]) == 2
        (error,) = read_jsonl(capsys, raw=True)
        assert error["kind"] == "error" and error["next_action"]
    for invalid in (["missing.jsonl", "--catalog-workflow", "process-wi"], [*argv, "--tool-calls", "--catalog-known-workflow"]):
        assert evidence.main(invalid) == 2
        assert read_jsonl(capsys, raw=True)[0]["next_action"]


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
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "wi", "content": "ok", "is_error": False}],
                },
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
    parent, summary = [event for event in events if event["kind"] != "unresolved-record"]
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
                    "item": {
                        "type": "CommandExecution",
                        "status": "completed",
                        "exit_code": 0,
                        "command": ["atk", "wi", "list"],
                    },
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

    *calls, summary = [
        event for event in read_jsonl(capsys, raw=True) if event["kind"] not in {"record-provenance", "unresolved-record"}
    ]
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
    """カタログと本文検索の併用を拒否し、利用できる照会を案内する。"""
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
    (event,) = read_jsonl(capsys, raw=True)
    assert event["kind"] == "error"
    assert "--tool-calls" in event["next_action"] and "--user-events" in event["next_action"]
