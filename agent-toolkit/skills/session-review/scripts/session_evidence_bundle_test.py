"""証拠抽出の`--bundle`の集約実行（`session_evidence_bundle.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence
from session_review_evidence_test import (
    _bundle_delegate_return_texts,
)

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    agy_delegation_transcript,
    agy_step,
    assistant_text,
    claude_call,
    claude_result,
    codex_item,
    codex_tool_result_entry,
    codex_tool_use_entry,
    events_by_kind,
    hook_attachment,
    read_jsonl,
    timestamped_entry,
    write_agy_log,
    write_jsonl,
    write_subagent,
)


def test_observation_boundary_does_not_apply_to_delegate_records(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """境界前に始まった委譲先の終了記録を保持し、後発の委譲先を除く。"""
    transcript = _write_transcript(
        tmp_path,
        [timestamped_entry("2026-09-01T00:00:01Z", "メイン記録")],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [
            timestamped_entry("2026-09-01T00:00:01Z", "境界前の委譲先記録"),
            timestamped_entry("2026-09-01T00:00:03Z", "境界後の委譲先結果"),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-review",
        [
            timestamped_entry("2026-09-01T00:00:04Z", "後発の振り返り失敗"),
            hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "After",
                    "toolUseID": "review",
                    "content": "[auto-generated: test/review][warn] 後発の振り返り失敗",
                }
            )
            | {"timestamp": "2026-09-01T00:00:05Z"},
        ],
    )

    base = [str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z"]
    assert evidence.main(base) == 0

    events = read_jsonl(capsys, raw=True)
    assert [event["text"] for event in events if event["record"] == "claude:transcript/agent-child"] == [
        "境界前の委譲先記録",
        "境界後の委譲先結果",
    ]
    assert all(event["record"] != "agent-review" for event in events)

    assert evidence.main([*base, "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]
    assert evidence.main([*base, "--grep", "後発"]) == 0
    assert read_jsonl(capsys)[-1] == {"kind": "summary", "count": 0}
    assert evidence.main([*base, "--stats"]) == 0
    assert [event["agent"] for event in events_by_kind(read_jsonl(capsys), "stats-subagent")] == [
        "claude:transcript/agent-child"
    ]

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert evidence.main([*base, "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys)
    assert "後発の振り返り失敗" not in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("commands", "diagnostics", "expected_candidates"),
    (
        ([["rg", "-F", "one"], ["rg", "-F", "two"]], ["", ""], 0),
        ([["test", "-e", "/one"], ["test", "-e", "/two"]], ["", ""], 0),
        ([["rg", "-F", "one"], ["rg", "-F", "one"]], ["", ""], 0),
        ([["tool", "one"], ["tool", "two"]], ["same diagnostic", "same diagnostic"], 2),
        ([["tool", "one"], ["tool", "two"]], ["first diagnostic", "second diagnostic"], 2),
    ),
)
def test_codex_failed_commands_group_by_executable_exit_and_diagnostic(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    commands: list[list[str]],
    diagnostics: list[str],
    expected_candidates: int,
) -> None:
    """正常な偽判定は除外し、診断を伴う失敗を既存軸で集約する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "command": command,
                        "exit_code": 1,
                        "stderr": diagnostic,
                    },
                },
            }
            for command, diagnostic in zip(commands, diagnostics, strict=True)
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidates = [record for record in records if record["kind"] == "candidate"]

    assert len(candidates) == expected_candidates
    if expected_candidates:
        assert {candidate["candidate_kind"] for candidate in candidates} == {"command-failure"}
        assert sum(candidate["count"] for candidate in candidates) == 2
        assert sum(len(candidate["locators"]) for candidate in candidates) == 2


def test_bundle_excludes_transient_classifier_failure(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """auto mode classifierの一時的な判定不能は候補にせず専用区分へ数える。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "true"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "is_error": True,
                            "content": "The server-side auto mode classifier gave no verdict (error)",
                        }
                    ],
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert not [item for item in records if item["kind"] == "candidate"]
    assert records[-1]["excluded"]["runtime-transient"] == 1


def test_codex_failed_commands_without_diagnostic_or_command_use_record_position(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """診断とcommandを欠く失敗は異なる記録位置を同一候補へ集約しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "failed", "exit_code": 1},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "failed", "exit_code": 1},
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert len([record for record in records if record["kind"] == "candidate"]) == 2


def test_agy_delegate_failures_become_candidates_and_stats(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """agyの委譲先の失敗したツール、エラー報告および失敗終端が候補へ現れ、件数とトークン数が集計へ現れる。"""
    session_id = "a9244465-1577-44a9-a7b0-500b1f976b0e"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    run_command = {"CommandLine": "npx textlint README.md"}
    write_agy_log(
        tmp_path / "state",
        session_id,
        [
            {"event": "init", "conversation_id": session_id, "init": {"model": "gemini", "cwd": "/work"}},
            agy_step(
                1, "tool", "ACTIVE", tool_name="run_command", tool_info={"name": "run_command", "parameters": run_command}
            ),
            agy_step(
                1,
                "tool",
                "ERROR",
                tool_name="run_command",
                tool_info={
                    "name": "run_command",
                    "parameters": run_command,
                    "error": {"type": "TOOL_ERROR", "message": "sandbox server did not answer Run within 30s"},
                },
            ),
            agy_step(
                2,
                "tool",
                "DONE",
                tool_name="view_file",
                tool_info={"name": "view_file", "parameters": {"AbsolutePath": "/work/a.md"}, "output": "本文"},
            ),
            agy_step(3, "error_message", "DONE"),
            agy_step(
                4,
                "agent_response",
                "DONE",
                usage={"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 300, "total_tokens": 120},
            ),
            {
                "event": "result",
                "result": {
                    "conversation_id": session_id,
                    "status": "ERROR",
                    "error": "Individual quota reached.",
                    "response": "",
                },
            },
        ],
    )
    transcript = agy_delegation_transcript(tmp_path, session_id)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)
    candidates = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    failures = [
        item["locators"] for item in candidates if item["kind"] == "candidate" and item["candidate_kind"] == "tool-failure"
    ]
    record_id = f"agy:{session_id}"
    assert sorted(locator["line"] for locators in failures for locator in locators if locator["record"] == record_id) == [
        3,
        5,
        7,
    ]

    assert evidence.main([str(transcript), "--stats"]) == 0
    stats = read_jsonl(capsys, raw=True)
    thread = events_by_kind(stats, "stats-agent-thread")[0]
    assert (thread["engine"], thread["session_id"]) == ("agy", session_id)
    assert thread["tokens"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 300,
    }
    total = events_by_kind(stats, "stats-total")[0]
    assert total["agent_thread_counts"] == {"agy": 1}
    assert total["tokens"]["cache_read_input_tokens"] == 300
    assert not events_by_kind(stats, "unresolved-record")


def test_bundle_writes_every_scan_to_files_and_returns_summary_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """集約実行が全走査と一次選別候補を保存し、標準出力へ要約だけを返す。"""
    missing_thread = "99999999-9999-4999-8999-999999999999"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Base directory for this skill: /skills/demo"}],
                },
            },
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "作業中"}]}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {}},
                        {"type": "tool_use", "name": "Bash", "id": "call-2", "input": {}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": "失敗の詳細"}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-2", "content": "warning: 警告が出た"}],
                },
            },
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PostToolUse:Bash",
                    "toolUseID": "call-2",
                    "content": ["[auto-generated: agent-toolkit/posttooluse][notice] 通知本文"],
                }
            ),
            {
                "type": "user",
                "toolUseResult": {"status": "completed", "agentId": "agent-1", "summary": "完了報告"},
                "message": {"role": "user", "content": "<task-notification>内部通知</task-notification>"},
            },
            codex_tool_use_entry("2026-09-02T00:00:00Z", "call-3", missing_thread),
            codex_tool_result_entry("2026-09-02T00:00:00Z", "call-3", missing_thread),
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "最終結果"}]}},
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    bundle_events = read_jsonl(capsys, raw=True)

    stored: dict[str, list[dict]] = {}
    for filename, single_args in (
        ("timeline.jsonl", []),
        ("warnings.jsonl", ["--warn"]),
        ("stats.jsonl", ["--stats"]),
        ("hook-notices.jsonl", ["--hook-notices"]),
    ):
        path = bundle_dir / filename
        stored[filename] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert evidence.main([str(transcript), *single_args]) == 0
        single = [event for event in read_jsonl(capsys, raw=True) if event["kind"] != "unresolved-record"]
        assert stored[filename] == single
        assert {"kind": "bundle-file", "path": str(path.resolve()), "count": len(single)} in bundle_events

    candidates = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidate_items = [item for item in candidates if item["kind"] == "candidate"]
    assert [item["candidate_id"] for item in candidate_items] == ["c0001", "c0002"]
    assert [(item["locators"], item["candidate_kind"]) for item in candidate_items] == [
        ([{"record": "claude:transcript", "line": 5}], "tool-failure"),
        ([{"record": "claude:transcript", "line": 6}], "warning"),
    ]
    assert candidates[-1]["excluded"] == {"hook-notice-informational": 1, "initial-request": 1}
    assert candidates[-1]["included_locator_count"] == 2
    assert {"kind": "bundle-file", "path": str((bundle_dir / "candidates.jsonl").resolve()), "count": 3} in bundle_events
    evidence_index = [
        json.loads(line) for line in (bundle_dir / "candidate-evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["candidate_id"] for item in evidence_index] == ["c0001", "c0002"]
    assert [item["locators"] for item in evidence_index] == [item["locators"] for item in candidate_items]
    assert all(item["evidence_count"] > 0 and item["total_chars"] > 0 for item in evidence_index)
    candidate_evidence = [json.loads((bundle_dir / item["path"]).read_text(encoding="utf-8")) for item in evidence_index]
    assert [item["candidate_id"] for item in candidate_evidence] == ["c0001", "c0002"]
    assert all(item["events"] for item in candidate_evidence)
    # 失敗したツール結果の本文は切り詰めず、実行時警告など定型本文の種別だけに上限を残す。
    assert [item["text_limit"] for item in candidate_evidence] == [None, 2000]
    assert all(item["source_chars"] > 0 for item in candidate_evidence)
    assert {
        "kind": "bundle-file",
        "path": str((bundle_dir / "candidate-evidence.jsonl").resolve()),
        "count": 2,
    } in bundle_events
    metrics = next(item for item in bundle_events if item["kind"] == "bundle-evidence-metrics")
    assert metrics["candidate_evidence_lines"] == 2
    assert metrics["decision_count"] == 2
    assert metrics["analysis_group_count"] == 2
    assert metrics["full_scan_lines"] > metrics["candidate_evidence_lines"]
    assert metrics["full_scan_bytes"] > 0
    assert metrics["candidate_evidence_bytes"] > 0

    assert {event["event_kind"]: event["count"] for event in bundle_events if event["kind"] == "bundle-kind-count"} == {
        "user": 1,
        "skill-invocation": 1,
        "assistant": 1,
        "failed-tool": 1,
        "agent-completion": 1,
        "final-result": 1,
    }
    serialized = json.dumps(bundle_events, ensure_ascii=False)
    assert "作業中" not in serialized
    assert "Base directory for this skill" not in serialized
    locators = [event for event in bundle_events if event["kind"] == "bundle-locator"]
    assert [{key: value for key, value in event.items() if key != "timestamp"} for event in locators] == [
        {"kind": "bundle-locator", "event_kind": "user", "record": "claude:transcript", "line": 1},
        {
            "kind": "bundle-locator",
            "event_kind": "failed-tool",
            "record": "claude:transcript",
            "line": 5,
            "text": "失敗の詳細",
        },
        {
            "kind": "bundle-locator",
            "event_kind": "agent-completion",
            "record": "claude:transcript",
            "line": 8,
            "text": "agent-1: 完了報告",
        },
        {
            "kind": "bundle-locator",
            "event_kind": "final-result",
            "record": "claude:transcript",
            "line": 11,
            "text": "最終結果",
        },
    ]
    # 区間の境界の時刻を`--detail`の追加照会なしで確定できるよう、全イベントが`timestamp`を持つ。
    assert all("timestamp" in event for event in locators)
    assert [event for event in bundle_events if event["kind"] == "bundle-warning-group"] == [
        {
            "kind": "bundle-warning-group",
            "text": "warning: 警告が出た",
            "count": 1,
            "samples": [{"record": "claude:transcript", "line": 6}],
        }
    ]
    assert [event for event in bundle_events if str(event["kind"]).startswith("stats-")] == []
    assert bundle_events[-1] == {"kind": "unresolved-record", "record": missing_thread, "line": 10}
    assert [event for event in bundle_events if event["kind"] == "hook-notice"] == []


def test_bundle_clips_locator_body_and_groups_warnings_by_leading_text(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """位置イベントの本文を冒頭200文字へ切り詰め、冒頭120文字が同じ警告を1分類へまとめる。"""
    body = "あ" * 300
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": f"call-{index}", "input": {}} for index in range(1, 5)
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": body}],
                },
            },
            *(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": f"call-{index}", "content": f"warning: {'い' * 130}{index}"}
                        ],
                    },
                }
                for index in range(2, 5)
            ),
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    events = read_jsonl(capsys, raw=True)
    locator = next(event for event in events if event["kind"] == "bundle-locator")
    assert locator["text"] == "あ" * 200 + "…[省略]"
    assert [event for event in events if event["kind"] == "bundle-warning-group"] == [
        {
            "kind": "bundle-warning-group",
            "text": ("warning: " + "い" * 130)[:120],
            "count": 3,
            "samples": [
                {"record": "claude:transcript", "line": 3},
                {"record": "claude:transcript", "line": 4},
                {"record": "claude:transcript", "line": 5},
            ],
        }
    ]


def test_bundle_writes_one_evidence_file_per_candidate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """候補数が多い場合も、全候補IDへ対応する個別ファイルを索引から解決できる。"""
    entries: list[dict[str, object]] = [{"type": "user", "message": {"role": "user", "content": "最初の依頼"}}]
    for index in range(12):
        entries.append(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": f"call-{index}", "is_error": True, "content": f"失敗{index}"}
                    ],
                },
            }
        )
    transcript = _write_transcript(tmp_path, entries)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    capsys.readouterr()
    evidence_index = [
        json.loads(line) for line in (bundle_dir / "candidate-evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert evidence_index
    for item in evidence_index:
        body = json.loads((bundle_dir / item["path"]).read_text(encoding="utf-8"))
        assert body["candidate_id"] == item["candidate_id"]
        assert body["events"]


def test_candidate_evidence_file_holds_only_its_own_candidate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """1候補の証拠が大きい場合も、個別ファイルへ別候補の証拠を混ぜない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "最初の依頼"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": "あ" * 4000}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-2", "is_error": True, "content": "別の失敗"}],
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    capsys.readouterr()
    evidence_index = [
        json.loads(line) for line in (bundle_dir / "candidate-evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(evidence_index) >= 2
    for item in evidence_index:
        body = json.loads((bundle_dir / item["path"]).read_text(encoding="utf-8"))
        assert {locator["line"] for locator in body["locators"]} == {locator["line"] for locator in item["locators"]}
        assert all(event.get("record") is not None for event in body["events"])


def test_common_runtime_inserted_classifier(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """画面と共有する判定で新しい挿入本文を会話から除き、ユーザーの入力は残す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "最初の依頼"}},
            {"type": "user", "message": {"role": "user", "content": "<multi_agent_mode>自動配送"}},
            {"type": "user", "message": {"role": "user", "content": "<permissions instructions>権限設定"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        "You are `/root`, the primary agent in a team of agents collaborating to fulfill the user's goals."
                    ),
                },
            },
            {"type": "user", "message": {"role": "user", "content": "$agent-toolkit:process-wi"}},
            {"type": "user", "message": {"role": "user", "content": "後続のユーザー発話"}},
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()
    conversation = [json.loads(line) for line in (bundle_dir / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [item["text"] for item in conversation] == ["最初の依頼", "$agent-toolkit:process-wi", "後続のユーザー発話"]


def test_bundle_writes_conversation_of_main_utterances_with_full_text_detail(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話の流れはメイン記録の発話を全文で、ツール呼び出しを代表入力で、失敗したツール結果を診断の1行で時系列に載せる。

    振り返りは会話の流れからセッション全体の試行と遠回りを探すため、ツール呼び出しと失敗が流れに欠けると
    候補の観点に当たらない問題が分析の入力から欠ける。成功した結果の本文、書き込む本文、配送本文、実行環境の挿入、
    スキル展開を含めると量が膨らみ、ユーザーの発話と区別できなくなる。
    長い発話は会話の流れの表示で先頭と末尾だけになるため、記録位置の照会が全文を返す必要がある。
    """
    long_reply = "長い応答の先頭。" + "あ" * 1500 + "長い応答の末尾。"
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "振り返りを速くしたい"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": '<agent-toolkit-auto-inserted kind="notice">配送</agent-toolkit-auto-inserted>',
                },
            },
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "実行環境の注記"}},
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill: /x"}]},
            },
            {"type": "user", "message": {"role": "user", "content": "<system-reminder>注入</system-reminder>"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "調べます。"},
                        {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "ls"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "a.txt"}]},
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-2",
                            "name": "Write",
                            "input": {"file_path": "/x/a.md", "content": "書き込む本文"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-2",
                            "is_error": True,
                            "content": "Exit code 1\n書込失敗の診断",
                        }
                    ],
                },
            },
            {"type": "user", "message": {"role": "user", "content": "全文抽出すべきでは？"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": long_reply}]}},
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()

    conversation = [json.loads(line) for line in (bundle_dir / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(item["kind"], item.get("role") or item.get("tool"), item["line"], item["text"]) for item in conversation] == [
        ("utterance", "user", 1, "振り返りを速くしたい"),
        ("utterance", "assistant", 6, "調べます。"),
        ("tool-call", "Bash", 6, "ls"),
        ("tool-call", "Write", 8, "/x/a.md"),
        ("tool-failure", None, 9, "書込失敗の診断"),
        ("utterance", "user", 10, "全文抽出すべきでは？"),
        ("utterance", "assistant", 11, long_reply),
    ]
    assert conversation[4]["call_id"] == "call-2"
    serialized = (bundle_dir / "conversation.jsonl").read_text(encoding="utf-8")
    assert "a.txt" not in serialized and "書き込む本文" not in serialized

    assert evidence.main([str(transcript), "--detail", "main:11"]) == 0
    assert read_jsonl(capsys) == [{"kind": "detail", "line": 11, "timestamp": None, "role": "assistant", "text": long_reply}]


@pytest.mark.parametrize("existing", [False, True])
def test_bundle_rejects_output_that_is_not_an_existing_directory(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    existing: bool,
) -> None:
    """出力先が実在しない場合とディレクトリでない場合は、ファイルを生成せず終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    destination = tmp_path / ("regular.txt" if existing else "missing")
    if existing:
        destination.write_text("", encoding="utf-8")

    assert evidence.main([str(transcript), "--bundle", str(destination)]) == 2

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert str(destination) in events[0]["text"]
    assert list(tmp_path.rglob("*.jsonl")) == [transcript]


@pytest.mark.parametrize(
    "conflicting",
    [
        ["--warn"],
        ["--grep", "依頼"],
        ["--detail", "1"],
        ["--fixed-string", "依頼"],
        ["--record-schema", "1"],
        ["--stats"],
        ["--hook-notices"],
    ],
)
def test_bundle_is_exclusive_with_other_query_modes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    conflicting: list[str],
) -> None:
    """集約実行と他の照会モードの併用は引数誤用として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir), *conflicting]) == 2

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "--bundle" in events[0]["text"]
    assert not list(bundle_dir.iterdir())


@pytest.mark.parametrize(
    ("final_text", "expected"),
    [
        ("status: completed\noutput_file: /tmp/out.md", []),
        ("状態: completed\n未解決の指摘数: 0\n続行できない理由: なし", []),
        (
            "統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した",
            ["統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した"],
        ),
        *[
            (f"状態: completed\n未解決の指摘数: 0\n{role}: 指摘なし", [])
            for role in ("読者別探索", "一括置換後レビュー", "投稿前レビュー")
        ],
        *[
            (text, [text])
            for role in ("読者別探索", "一括置換後レビュー", "投稿前レビュー")
            for text in (
                f"状態: completed\n未解決の指摘数: 1\n{role}: 指摘あり",
                f"未解決の指摘数: 0\n続行できない理由: {role}の入力不足",
                f"状態: needs_escalation\n未解決の指摘数: 0\n続行できない理由: {role}の入力不足",
                f"状態: completed\n未解決の指摘数: 0\n{role}: 指摘なし\n想定外事象: 検証に失敗",
            )
        ],
    ],
)
def test_bundle_excludes_delegate_returns_that_only_report_success(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    final_text: str,
    expected: list[str],
) -> None:
    """`--bundle`の候補から成功の定型形式だけの返却を除き、想定外事象を持つ定型返却は残す。"""
    texts = _bundle_delegate_return_texts(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "委譲された依頼"}},
            assistant_text(final_text),
        ],
    )
    capsys.readouterr()

    assert texts == expected


def test_bundle_reports_hook_blocked_tool_call_as_hook_notice(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """hookが遮断したツール呼び出しの失敗を、`--bundle`の候補でhook通知として発生源と区分を付けて返す。"""
    blocked = (
        "PreToolUse:TaskStop hook error: [uv run hook.py pretooluse]: "
        '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="block">'
        "blocked: 所有記録の無いタスクの停止</agent-toolkit-auto-inserted>"
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call-1", "name": "TaskStop", "input": {"task_id": "t1"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": blocked}],
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()

    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidates = [record for record in records if record["kind"] == "candidate"]
    assert [(record["candidate_kind"], record["event_key"][0], record["event_key"][2]) for record in candidates] == [
        ("hook-notice", "pretooluse", "block")
    ]
    assert "所有記録の無いタスクの停止" in candidates[0]["text"]


_ADHOC_THREAD = "77777777-7777-4777-8777-777777777777"


_CODEX_EXEC_INPUT = (
    'const r=await tools.exec_command({cmd:"python3 -c \'print(open(\\"draft.md\\").read().count(\\"x\\"))\'"});'
)


def test_bundle_collects_adhoc_processing_per_record_including_delegates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """委譲先を含む記録ごとに、その場のコードによる加工を1件の候補へまとめ、読むだけの呼び出しを含めない。

    Codex形式の委譲先記録は、Codex CLIのrollout（2026年10月4日の記録）の`exec`ツールがJavaScript本文から
    `exec_command`を呼ぶ形を写した。
    """
    root = tmp_path / "project"
    transcript = root / "parent-session.jsonl"
    saved_output = "/home/u/.cache/agent-toolkit/managed-temp/atk-output-abc/output.txt"
    write_jsonl(
        transcript,
        [
            {"type": "user", "timestamp": "2026-10-06T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            claude_call("2026-10-06T00:00:01Z", "jq-1", "Bash", {"command": f"jq -r '.line' {saved_output}"}),
            claude_result("2026-10-06T00:00:02Z", "jq-1", "12"),
            claude_call("2026-10-06T00:00:03Z", "cat-1", "Bash", {"command": "cat agent-toolkit/README.md"}),
            claude_result("2026-10-06T00:00:04Z", "cat-1", "本文"),
            claude_call("2026-10-06T00:00:05Z", "rg-1", "Bash", {"command": "rg -n needle . | cut -c1-200"}),
            claude_result("2026-10-06T00:00:06Z", "rg-1", "a.py:1:needle"),
            claude_call("2026-10-06T00:00:07Z", "sed-1", "Bash", {"command": "sed -n 1,20p agent-toolkit/x.py"}),
            claude_result("2026-10-06T00:00:08Z", "sed-1", "本文"),
            codex_tool_use_entry("2026-10-06T00:00:09Z", "call-codex", _ADHOC_THREAD),
            codex_tool_result_entry("2026-10-06T00:00:10Z", "call-codex", _ADHOC_THREAD),
        ],
    )
    heredoc = "python3 - <<'EOF'\nimport json\nprint(json.dumps({'start': 1, 'end': 48}))\nEOF"
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [
            claude_call("2026-10-06T00:00:02Z", "heredoc-1", "Bash", {"command": heredoc}),
            claude_result("2026-10-06T00:00:03Z", "heredoc-1", '{"start": 1, "end": 48}'),
            claude_call("2026-10-06T00:00:04Z", "inline-1", "Bash", {"command": "uv run --frozen python -c 'print(1)'"}),
        ],
    )
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    write_jsonl(
        codex_home / "sessions" / "2026" / "10" / "06" / f"rollout-2026-10-06T00-00-09-{_ADHOC_THREAD}.jsonl",
        [
            {"timestamp": "2026-10-06T00:00:09Z", "type": "session_meta", "payload": {"id": _ADHOC_THREAD}},
            codex_item(
                "2026-10-06T00:00:11Z",
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "c-exec",
                    "input": _CODEX_EXEC_INPUT,
                },
            ),
            codex_item("2026-10-06T00:00:12Z", {"type": "custom_tool_call_output", "call_id": "c-exec", "output": "1"}),
            codex_item(
                "2026-10-06T00:00:13Z",
                {"type": "function_call", "name": "exec_command", "call_id": "c-rg", "arguments": '{"cmd": "rg needle"}'},
            ),
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)

    candidates = [
        json.loads(line)
        for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("candidate_kind") == "adhoc-processing"
    ]
    assert [(item["analysis_group_hint"], item["count"], item["calls"]) for item in candidates] == [
        (
            ["claude:parent-session"],
            1,
            [{"record": "claude:parent-session", "line": 2, "text": f"jq -r '.line' {saved_output}"}],
        ),
        (
            ["claude:parent-session/agent-child"],
            2,
            [
                {
                    "record": "claude:parent-session/agent-child",
                    "line": 1,
                    "text": "python3 - <<'EOF' import json print(json.dumps({'start': 1, 'end': 48})) EOF",
                },
                {
                    "record": "claude:parent-session/agent-child",
                    "line": 3,
                    "text": "uv run --frozen python -c 'print(1)'",
                },
            ],
        ),
        (
            [f"codex:{_ADHOC_THREAD}"],
            1,
            [
                {
                    "record": f"codex:{_ADHOC_THREAD}",
                    "line": 2,
                    "text": _CODEX_EXEC_INPUT,
                }
            ],
        ),
    ]
