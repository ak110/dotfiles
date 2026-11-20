"""保存済み計画とrolloutを指定する公開統計の回帰試験。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import session_review_evidence as evidence
from agent_toolkit._testing.session_evidence_support import read_jsonl


def _plan(root: Path, name: str, worktree: str, wis: list[str], progress: str = "") -> Path:
    path = root / "plans" / "2026" / "11" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# 計画\n\n### 計画メタ情報\n\n- 作業種別: 通常変更\n- 対象リポジトリ: `{worktree}`\n- 関連WI:\n"
        + "".join(f"  - {wi}.md: 作業\n" for wi in wis)
        + "\n## 進捗ログ\n\n| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
        + progress,
        encoding="utf-8",
    )
    return path


def _task(plan: Path | str) -> str:
    document = Path.cwd() / "exec-review.subagent.md"
    return (
        '<atk-auto source="agents-server" kind="delivery">\n'
        f"次の文書の手順を実行せよ（出所: {document}）。\n役割: 実行レビュータスク\n入力:\n計画: {plan}\n"
        "## 手順・参照資料\nレビューの手順\n</atk-auto>"
    )


def _rollout(
    path: Path,
    cwd: str,
    *,
    task: str = "",
    branch: str = "",
    output: str | None = "日本語",
    incomplete: bool = False,
    day: str = "15",
) -> None:
    entries = [
        ("session_meta", "00:00:00", {"cwd": cwd, "git": {"branch": branch}}),
        ("response_item", "00:00:00", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": task}]}),
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
            json.dumps({"timestamp": f"2026-11-{day}T{time}Z", "type": kind, "payload": body}) for kind, time, body in entries
        ),
        encoding="utf-8",
    )


def test_saved_lane_cohort(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = _plan(
        tmp_path,
        "15-0000_lane.md",
        "/work/lane-01",
        ["20261115-000000-001", "20261115-000000-002", "20261115-000000-001"],
        "| 2026-11-15 00:00 | 開始: 実装 | |\n| 2026-11-15 03:00 | 実装終了 | |\n| 2026-11-15 03:06 | 検証終了 | |",
    )
    with plan.open("a", encoding="utf-8") as stream:
        stream.write("\n| 2026-11-15 | 日付だけの結果 | 完了 |\n\n## 対象外\n\n  - 20261115-111111-001.md: 別レーンが実装\n")
    task = _task(plan).replace(str(Path.cwd() / "exec-review.subagent.md"), r"C:\plugins\roles\exec-review.subagent.md")
    _rollout(tmp_path / "lane.jsonl", "/work/lane-01", task=task)
    _rollout(tmp_path / "main.jsonl", "/work/main")
    assert (
        evidence.main(
            ["--stats", "--saved-plan", str(plan), "--saved-plan", str(plan), "--saved-rollout", str(tmp_path / "*.jsonl")]
        )
        == 0
    )
    (event,) = read_jsonl(capsys)
    assert event["kind"] == "stats-lane-costs"
    assert event["estimate_basis"] == "assigned_plans"
    assert event["plans"]["by_kind"] == {"通常変更": {"plans": 1, "wis": 2, "median_minutes_per_wi": 63}}
    assert event["plans"]["phase_minutes_per_wi"] == {"実装": 60, "検証": 3}
    assert event["plans"]["capped_intervals"] == 1
    assert event["plans"]["progress_rows_without_minute"] == 1
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
        "rollouts_without_plan_assignment": 1,
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
    first = _plan(tmp_path, "15-0000_first.md", "/work/lane-01", ["20260924-000000-001"], start + end)
    second = _plan(tmp_path, "17-0000_second.md", "/work/lane-02", ["20260924-000000-002"] if second_wi else [], start)
    for lane, plan in (("lane-01", first), ("lane-02", second)):
        _rollout(tmp_path / f"{lane}.jsonl", f"/work/{lane}", task=_task(plan))
    assert (
        evidence.main(["--stats", "--saved-plan", str(first.parent / "*.md"), "--saved-rollout", str(tmp_path / "*.jsonl")])
        == 0
    )
    (event,) = read_jsonl(capsys)
    assert event["matched_calls"]["samples"] == 6
    assert event["plans"]["plans_without_wis"] == (0 if second_wi else 1)
    assert event["plans"]["related_wi_count"] == (2 if second_wi else None)
    assert event["plans"]["plan_wi_count"] == 1
    assert event["estimate_plan_wis"] == (2 if second_wi else None)
    assert event["estimated_calls_per_wi"] == (3 if second_wi else None)
    assert event["estimated_reads_per_wi"] == (1 if second_wi else None)


def test_saved_lane_unpaired_worktrees(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = _plan(tmp_path, "15-0000_lane.md", "/work/process-wi-main-lane-01", ["20261115-000000-001"])
    path = tmp_path / "rollout.jsonl"
    args = ["--stats", "--saved-plan", str(plan), "--saved-rollout", str(path)]
    _rollout(path, "/work/process-wi-release-lane-01/src", task=_task(plan))
    assert evidence.main(args) == 0
    (event,) = read_jsonl(capsys)
    assert event["unpaired_plans"] == [str(plan)]
    assert event["gaps"]["rollouts_without_matching_plan"] == 1
    assert event["matched_calls"]["samples"] == 3
    assert event["estimated_calls_per_wi"] is None

    _rollout(path, "/work/process-wi-main-lane-01/src", task=_task(plan))
    assert evidence.main(args) == 0
    (event,) = read_jsonl(capsys)
    assert event["unpaired_plans"] == []
    assert event["estimated_calls_per_wi"] == 3
    assert event["estimate_basis"] == "assigned_plans"


@pytest.mark.parametrize("others", [[], ["--stats", "--owner-session"], ["--stats", "--since", "2026-09-24"]])
def test_saved_lane_rejects_ignored_options(others: list[str]) -> None:
    assert evidence.main(["--saved-rollout", "absent", *others]) == 2


def test_saved_lane_reused_across_rounds(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cwd = "/repo/process-wi-lane-02"
    rounds = {}
    for day, suffix in (("15", "1"), ("17", "2")):
        branch = f"process-wi-024abbfe-5000-4000-8000-00000000000{suffix}-lane-02"
        plan = _plan(
            tmp_path,
            f"{day}-0410_process-wi_レーン02.md",
            cwd,
            [f"202611{day}-040000-001"],
            f"| 2026-11-{day} 05:00 | 実装commit | worktree={cwd}、branch={branch}。 |\n",
        )
        record = tmp_path / f"{day}.jsonl"
        _rollout(record, cwd, task=_task("/user/.claude/plans/" + plan.name), branch=branch, day=day)
        rounds[day] = plan, record, branch
    first, second = rounds["15"], rounds["17"]
    args = ["--stats", "--saved-plan", str(second[0]), "--saved-rollout", str(second[1])]
    assert evidence.main([*args, "--saved-plan", str(first[0]), "--saved-rollout", str(first[1])]) == 0
    (both,) = read_jsonl(capsys)
    assert both["estimated_calls_per_wi"] == 3
    assert evidence.main([*args, "--saved-rollout", str(first[1])]) == 0
    (missing,) = read_jsonl(capsys)
    assert missing["matched_calls"]["samples"] == both["matched_calls"]["samples"] == 6
    assert missing["gaps"]["rollouts_without_matching_plan"] == 1
    assert missing["paired_rollouts"] == {str(second[0]): [str(second[1])]}
    assert all(value is None for key, value in missing.items() if key.startswith("estimate"))
    assert evidence.main(args) == 0
    (single,) = read_jsonl(capsys)
    assert (single["matched_calls"]["samples"], single["estimated_calls_per_wi"]) == (3, 3)

    _rollout(second[1], cwd, task=_task("/user/.claude/plans/" + second[0].name), branch=first[2])
    assert evidence.main(args) == 0
    (wrong_round,) = read_jsonl(capsys)
    assert wrong_round["gaps"]["rollouts_without_matching_plan"] == 1
    assert wrong_round["estimated_calls_per_wi"] is None


@pytest.mark.parametrize(
    "source",
    [
        "no-task",
        "mention",
        "tool-result",
        "another-month",
        "another-task",
        "later",
        "wrong-wi",
        "manual-change",
        "foreign-archive",
    ],
)
def test_saved_lane_unproven_assignment(tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str) -> None:
    cwd = "/repo/lane-01"
    plan = _plan(tmp_path, "15-0000_lane.md", cwd, ["20261115-000000-001"])
    record = tmp_path / "rollout.jsonl"
    task = {
        "mention": f'<atk-auto source="agents-server" kind="delivery">\n比較用の資料を読む。\n計画: {plan}\n</atk-auto>',
        "another-month": _task(plan.as_posix().replace("/2026/11/", "/2026/09/")),
        "another-task": _task(plan),
        "manual-change": _task(plan),
        "wrong-wi": _task(plan).replace("入力:\n", "入力:\n担当WI: 20261115-000000-999.md\n"),
        "foreign-archive": _task("/unrelated/plans/2026/11/" + plan.name),
    }.get(source, "")
    _rollout(record, cwd, task=task, output=_task(plan) if source == "tool-result" else "結果")
    if source in {"later", "another-task", "manual-change"}:
        with record.open("a", encoding="utf-8") as stream:
            field = plan if source == "later" else plan.with_name("17-0000_other.md")
            message = "別の計画を実装する。" if source == "manual-change" else _task(field)
            stream.write(
                "\n" + json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": message}})
            )
    assert evidence.main(["--stats", "--saved-plan", str(plan), "--saved-rollout", str(record)]) == 0
    (event,) = read_jsonl(capsys)
    assert event["matched_calls"]["samples"] == 3
    assert event["unpaired_plans"] == [str(plan)]
    assert event["estimated_calls_per_wi"] is None
