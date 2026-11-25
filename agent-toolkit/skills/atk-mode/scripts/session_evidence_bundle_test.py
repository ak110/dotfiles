from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence
from agent_toolkit._common import session_review
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    codex_tool_result_entry,
    codex_tool_use_entry,
    hook_attachment,
    read_jsonl,
)
from session_evidence_extract import _CollectedRecord, _load_records


def test_bundle_excludes_registered_lane_and_inherits_processed_prefix(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def save(path: pathlib.Path, *messages: str) -> None:
        path.write_text(
            "".join(
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": text}}) + "\n" for text in messages
            ),
            encoding="utf-8",
        )

    parent, lane, descendant, unattended = (
        tmp_path / f"{name}.jsonl" for name in ("orchestrator", "transcript_full", "worker", "unattended")
    )
    for path, text in (
        (parent, "統括の記録"),
        (lane, "前回分析済み"),
        (descendant, "レーンの子"),
        (unattended, "未登録のレーン"),
    ):
        save(path, text)

    def collect() -> list[_CollectedRecord]:
        return [
            _CollectedRecord("main", parent, _load_records(str(parent)) or [], None, None, None, None, "main"),
            _CollectedRecord("lane", lane, _load_records(str(lane)) or [], None, "main", 1, None, "session"),
            _CollectedRecord("worker", descendant, _load_records(str(descendant)) or [], None, "lane", 1, None, "session"),
            _CollectedRecord("unattended", unattended, _load_records(str(unattended)) or [], None, "main", 1, None, "session"),
        ]

    before = session_review.directory() / "reviewed" / "claude-orchestrator.json"
    session_review.write(before, session_review.scope_records(collect()))
    session_review.register_lane("lane", str(tmp_path), "orchestrator", "claude", lane, None, "統合完了")
    save(lane, "前回分析済み", "登録までの続き")

    parent_bundle, lane_bundle = tmp_path / "for-parent", tmp_path / "for-lane"
    parent_bundle.mkdir()
    lane_bundle.mkdir()
    recorded = collect()
    monkeypatch.setattr(evidence, "_collect_records", lambda *_args, **_kwargs: (recorded, []))
    assert evidence.main([str(parent), "--bundle", str(parent_bundle), "--after", str(before)]) == 0
    scope = session_review.read(parent_bundle / "scope.json")
    assert set(scope["records"]) == {"main", "unattended"}
    assert set(scope["transferred"]) == {"lane", "worker"}
    timeline = (parent_bundle / "timeline.jsonl").read_text(encoding="utf-8")
    boundary = [row for line in timeline.splitlines() if (row := json.loads(line))["kind"] == "lane-review-boundary"]
    assert boundary == [{"kind": "lane-review-boundary", "session_id": "lane", "result_summary": "統合完了"}]
    session_review.write(before, scope)

    root = recorded[1]._replace(record_id="main", role="main", source_record=None)
    worker = recorded[2]._replace(source_record="main")
    monkeypatch.setattr(evidence, "_collect_records", lambda *_args, **_kwargs: ([root, worker], []))
    assert evidence.main([str(lane), "--bundle", str(lane_bundle)]) == 0
    scope = session_review.read(lane_bundle / "scope.json")
    assert (scope["records"]["main"]["after_line"], scope["records"]["main"]["through_line"]) == (1, 2)
    assert scope["records"]["worker"]["after_line"] == 1


def test_bundle_writes_every_scan_to_files_and_returns_summary_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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

        single = [
            event for event in read_jsonl(capsys, raw=True) if event["kind"] not in {"unresolved-record", "record-provenance"}
        ]
        assert [event for event in stored[filename] if event["kind"] != "record-provenance"] == single
        assert [event for event in stored[filename] if event["kind"] == "record-provenance"] == [
            event for event in stored["timeline.jsonl"] if event["kind"] == "record-provenance"
        ]
        assert {"kind": "bundle-file", "path": str(path.resolve()), "count": len(stored[filename])} in bundle_events

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
        "record-provenance": 1,
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
    unresolved = dict(bundle_events[-1])
    next_action = unresolved.pop("next_action")
    assert missing_thread in next_action and "記録保持者" in next_action
    assert unresolved == {
        "kind": "unresolved-record",
        "record": missing_thread,
        "line": 10,
        "reason": "record-location-unresolved",
        "source_record": "claude:transcript",
        "source_line": 10,
    }
    assert [event for event in bundle_events if event["kind"] == "hook-notice"] == []


def test_bundle_clips_locator_body_and_groups_warnings_by_leading_text(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
