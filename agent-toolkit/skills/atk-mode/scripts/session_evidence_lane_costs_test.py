"""保存済み計画とrolloutを指定する公開統計の回帰試験。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import session_review_evidence as evidence
from agent_toolkit._testing.session_evidence_support import read_jsonl


def _rollout(path: Path, cwd: str, *, output: str | None = "日本語", incomplete: bool = False) -> None:
    entries = [
        ("turn_context", "00:00:00", {"cwd": cwd, "model": "test-model", "effort": "medium"}),
        ("response_item", "00:00:03", {"type": "function_call", "arguments": '{"cmd":"cat a.md"}', "call_id": "a"}),
        ("response_item", "00:00:05", {"type": "function_call_output", "output": output, "call_id": "a"}),
        ("response_item", "00:00:12", {"type": "custom_tool_call", "input": "*** Begin Patch", "call_id": "b"}),
        ("response_item", "00:00:15", {"type": "custom_tool_call_output", "output": "", "call_id": "b"}),
        ("response_item", "00:10:15", {"type": "function_call", "arguments": '{"cmd":"git status"}', "call_id": "c"}),
    ]
    if not incomplete:
        entries.append(("response_item", "00:10:15", {"type": "function_call_output", "output": "", "call_id": "c"}))
    path.write_text(
        "\n".join(
            json.dumps({"timestamp": f"2026-11-15T{time}Z", "type": kind, "payload": body}) for kind, time, body in entries
        ),
        encoding="utf-8",
    )


def test_saved_lane_cohort(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = tmp_path / "lane.md"
    plan.write_text(
        "- 作業種別: 通常変更\n  - 20261115-000000-001.md\n  - 20261115-000000-002.md\n  - 20261115-000000-001.md\n"
        "| 2026-11-15 00:00 | 開始: 実装 | |\n| 2026-11-15 03:00 | 実装終了 | |\n| 2026-11-15 03:06 | 検証終了 | |",
        encoding="utf-8",
    )
    _rollout(tmp_path / "lane.jsonl", "/work/lane-01")
    _rollout(tmp_path / "main.jsonl", "/work/main")
    assert (
        evidence.main(
            ["--stats", "--saved-plan", str(plan), "--saved-plan", str(plan), "--saved-rollout", str(tmp_path / "*.jsonl")]
        )
        == 0
    )
    (event,) = read_jsonl(capsys)
    assert event["kind"] == "stats-lane-costs"
    assert event["estimate_basis"] == "selected_plans"
    assert event["plans"]["by_kind"] == {"通常変更": {"plans": 1, "wis": 2, "median_minutes_per_wi": 63}}
    assert event["plans"]["phase_minutes_per_wi"] == {"実装": 60, "検証": 3}
    assert event["plans"]["capped_intervals"] == 1
    assert (event["rollout_files"], event["lane_files"], event["estimate_plan_wis"]) == (2, 1, 2)
    assert (event["matched_calls"]["samples"], event["matched_calls"]["total_seconds"]) == (3, 5)
    assert (event["estimated_calls_per_wi"], event["estimated_tool_seconds_per_wi"]) == (1.5, 2.5)
    assert (event["read_calls"], event["read_output_chars"], event["read_output_utf8_bytes"]) == (1, 3, 9)
    (gap,) = event["thinking_proxy"]
    assert (gap["model"], gap["effort"], gap["samples"], gap["median_seconds"]) == ("test-model", "medium", 1, 7)
    assert event["gaps"] == {"excluded_thinking_intervals": 1}


def test_saved_lane_missing_inputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "lane.jsonl"
    _rollout(path, "/process-wi-lane", output=None, incomplete=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n{")
    missing = str(tmp_path / "*.md")
    assert evidence.main(["--stats", "--saved-plan", missing, "--saved-rollout", str(path)]) == 0
    (event,) = read_jsonl(capsys)
    assert event["unmatched_patterns"] == [missing]
    assert event["matched_calls"]["samples"] == 2
    assert event["read_output_chars"] is event["read_output_utf8_bytes"] is event["estimated_calls_per_wi"] is None
    assert event["gaps"] == {
        "invalid_rows": 1,
        "missing_read_outputs": 1,
        "unmatched_calls": 1,
        "excluded_thinking_intervals": 1,
    }
    assert evidence.main(["--stats", "--saved-rollout", str(tmp_path / "absent")]) == 0
    (absent,) = read_jsonl(capsys)
    assert absent["matched_calls"]["total_seconds"] is absent["read_calls"] is None


@pytest.mark.parametrize(
    ("cwd", "expected"),
    [
        ("/work/process-wi-lane-01", 1),
        ("/work/process-wi-develop-lane-01", 1),
        (r"C:\work\process-wi-release-1.0-deadbeef-lane-99\src", 1),
        ("/work/lane-10", 1),
        ("/work/session-old/lane-11-fsas5qk6/wt", 1),
        ("/work/not-process-wi-lane-01", 0),
        ("/work/lane-012", 0),
    ],
)
def test_saved_lane_names(tmp_path: Path, capsys: pytest.CaptureFixture[str], cwd: str, expected: int) -> None:
    path = tmp_path / "rollout.jsonl"
    _rollout(path, cwd)
    assert evidence.main(["--stats", "--saved-rollout", str(path)]) == 0
    (event,) = read_jsonl(capsys)
    assert (event["lane_files"], event["matched_calls"]["samples"]) == (expected, 3 * expected)


@pytest.mark.parametrize("second_wi", [True, False])
def test_saved_lane_plan_without_interval(tmp_path: Path, capsys: pytest.CaptureFixture[str], *, second_wi: bool) -> None:
    start = "| 2026-09-24 00:00 | 開始 | |\n"
    end = "| 2026-09-24 00:10 | 終了 | |\n"
    (tmp_path / "first.md").write_text(
        "- 対象リポジトリ: `/work/lane-01`\n  - 20260924-000000-001.md\n" + start + end, encoding="utf-8"
    )
    (tmp_path / "second.md").write_text(
        "- 対象リポジトリ: `/work/lane-02`\n" + ("  - 20260924-000000-002.md\n" if second_wi else "") + start, encoding="utf-8"
    )
    for lane in ("lane-01", "lane-02"):
        _rollout(tmp_path / f"{lane}.jsonl", f"/work/{lane}")
    assert evidence.main(["--stats", "--saved-plan", str(tmp_path / "*.md"), "--saved-rollout", str(tmp_path / "*.jsonl")]) == 0
    (event,) = read_jsonl(capsys)
    assert event["matched_calls"]["samples"] == 6
    assert event["plans"]["plans_without_wis"] == (0 if second_wi else 1)
    assert event["plans"]["related_wi_count"] == (2 if second_wi else None)
    assert event["plans"]["plan_wi_count"] == 1
    assert event["estimate_plan_wis"] == (2 if second_wi else None)
    assert event["estimated_calls_per_wi"] == (3 if second_wi else None)
    assert event["estimated_reads_per_wi"] == (1 if second_wi else None)


def test_saved_lane_unpaired_worktrees(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("- 対象リポジトリ: `/work/process-wi-main-lane-01`\n  - 20261115-000000-001.md\n", encoding="utf-8")
    path = tmp_path / "rollout.jsonl"
    args = ["--stats", "--saved-plan", str(plan), "--saved-rollout", str(path)]
    _rollout(path, "/work/process-wi-release-lane-01/src")
    assert evidence.main(args) == 0
    (event,) = read_jsonl(capsys)
    assert event["unpaired_plans"] == [str(plan)]
    assert event["gaps"]["rollout_worktrees_without_plan"] == 1
    assert event["matched_calls"]["samples"] == 3
    assert event["estimated_calls_per_wi"] is None

    _rollout(path, "/work/process-wi-main-lane-01/src")
    assert evidence.main(args) == 0
    (event,) = read_jsonl(capsys)
    assert event["unpaired_plans"] == []
    assert event["estimated_calls_per_wi"] == 3
    assert event["estimate_basis"] == "worktrees"


@pytest.mark.parametrize("others", [[], ["--stats", "--owner-session"], ["--stats", "--since", "2026-09-24"]])
def test_saved_lane_rejects_ignored_options(others: list[str]) -> None:
    assert evidence.main(["--saved-rollout", "absent", *others]) == 2
