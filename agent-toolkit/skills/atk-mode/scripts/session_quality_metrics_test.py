"""正式なrun/CI結果の収集と初回失敗の保持を確認する。"""

import datetime
import json
from pathlib import Path
from typing import Any

import pytest
from session_evidence_extract import _CollectedRecord, _Record
from session_quality_metrics import collect_quality_events, first_pass_metrics


def _record(line: int, entry: dict[str, Any]) -> _Record:
    return _Record(line, json.dumps(entry), entry)


def _family(outputs: list[tuple[str, str]]) -> list[_CollectedRecord]:
    records = []
    for index, (command, output) in enumerate(outputs):
        stamp = f"2026-11-01T00:{index:02}:00+00:00"
        records.extend(
            [
                _record(
                    index * 2 + 1,
                    {
                        "timestamp": stamp,
                        "cwd": "/repo",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "tool_use", "id": str(index), "name": "Bash", "input": {"command": command}}],
                        },
                    },
                ),
                _record(
                    index * 2 + 2,
                    {
                        "timestamp": stamp,
                        "message": {
                            "role": "user",
                            "content": [{"type": "tool_result", "tool_use_id": str(index), "content": output}],
                        },
                    },
                ),
            ]
        )
    return [_CollectedRecord("claude:lane", Path("lane.jsonl"), records, "claude", None, None, None, "main")]


def _metrics(outputs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return first_pass_metrics(
        collect_quality_events(_family(outputs), "process-wi"),
        datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC),
        datetime.datetime(2026, 12, 1, tzinfo=datetime.UTC),
    )


def test_final_run_failure_is_not_replaced_by_later_success() -> None:
    """正式show-runの失敗と次の成功を同じ作業の履歴として渡す。"""
    failed: dict[str, Any] = {
        "run_id": "first",
        "meta": {
            "argv": ["run", "--no-fix", "source.py"],
            "cwd": "/repo",
            "version": "1",
            "commands": ["pytest"],
            "exit_code": 1,
        },
        "commands": [{"command": "pytest", "status": "failed"}],
    }
    passed = {
        **failed,
        "meta": {**failed["meta"], "exit_code": 0},
        "run_id": "second",
        "commands": [{"command": "pytest", "status": "succeeded"}],
    }
    rows = _metrics(
        [
            ("atk review-table show /plans/one.exec-review.tsv", ""),
            ("pyfltr show-run first", json.dumps(failed)),
            ("pyfltr show-run second", json.dumps({"output": json.dumps(passed)})),
            ("pyfltr show-run second", json.dumps(passed)),
        ]
    )
    row = next(row for row in rows if row["stage"] == "final-verification")
    assert row["observed_count"] == 1
    assert row["first_success_count"] == 0
    assert row["unresolved_count"] == 0
    assert row["units"][0]["attempts"] == 2
    assert row["units"][0]["environment"]["cwd"] == "/repo"


def test_ci_polling_and_rerun_do_not_erase_first_failure() -> None:
    rows = _metrics(
        [
            ("atk review-table show /plans/one.exec-review.tsv", ""),
            ("atk wait-ci", "CI: completed/failure https://example.invalid/runs/1"),
            ("atk wait-ci", "CI: completed/failure https://example.invalid/runs/1"),
            ("atk wait-ci", "CI: completed/success https://example.invalid/runs/1"),
        ]
    )
    row = next(row for row in rows if row["stage"] == "ci")
    assert row["first_success_count"] == 0
    assert row["observed_count"] == 1
    assert row["unresolved_count"] == 0
    assert row["units"][0]["attempts"] == 1


def test_missing_verdict_and_focal_run_are_unknown() -> None:
    focal = {
        "run_id": "focal",
        "meta": {"argv": ["run", "--no-fix", "--no-always-targets"]},
        "commands": [{"command": "pytest", "status": "succeeded"}],
    }
    rows = _metrics([("pyfltr show-run focal", json.dumps(focal))])
    assert {row["stage"] for row in rows} == {"review", "final-verification", "ci"}
    assert all(row["observed_count"] == 0 and row["first_success_ratio"] is None and row["unknown_count"] == 1 for row in rows)


def test_formal_review_requires_initial_verdict_and_condition_evidence() -> None:
    header = (
        "状態: completed\nレビューHEAD: "
        + "a" * 40
        + "\n未解決の指摘数: 0\n読者別探索: {}\nレビュー開始時点: "
        + "b" * 40
        + "\n前回確認版の開始時点: なし\n前回確認版HEAD: なし\n"
    )
    good = next(
        row
        for row in _metrics([("atk agents wait", header + "完成条件1: 達成 — 根拠: 保存試験成功\n")])
        if row["stage"] == "review"
    )
    assert good["first_success_count"] == 1
    unknown = next(row for row in _metrics([("atk agents wait", header + "完成条件1: 根拠不足\n")]) if row["stage"] == "review")
    assert unknown["unknown_count"] == 1


def test_formal_original_and_quoted_status_are_not_confused(tmp_path: Path) -> None:
    header = (
        "状態: completed\nレビューHEAD: "
        + "a" * 40
        + "\n未解決の指摘数: 0\n読者別探索: {}\nレビュー開始時点: "
        + "b" * 40
        + "\n前回確認版の開始時点: なし\n前回確認版HEAD: なし\n"
    )
    original = tmp_path / "verdict.md"
    original.write_text(header + "work.md 原文要求1「未達と根拠不足を区別する」: 達成 — 根拠: 判定の試験\n", encoding="utf-8")
    row = next(
        row for row in _metrics([("atk agents wait", f"状態: completed\n正式原本: {original}\n")]) if row["stage"] == "review"
    )
    assert row["first_success_count"] == 1


def test_codex_yielded_command_keeps_review_result_identity() -> None:
    """Codexの開始/回収をまたぐ結果も、元の公開待機操作へ対応付ける。"""
    header = (
        "状態: completed\nレビューHEAD: "
        + "a" * 40
        + "\n未解決の指摘数: 0\n読者別探索: {}\nレビュー開始時点: "
        + "b" * 40
        + "\n前回確認版の開始時点: なし\n前回確認版HEAD: なし\n"
    )
    payloads = [
        {
            "type": "custom_tool_call",
            "name": "functions.exec",
            "call_id": "start",
            "input": 'text(await tools.exec_command({cmd:"atk agents wait"}))',
        },
        {"type": "custom_tool_call_output", "call_id": "start", "output": json.dumps({"session_id": 13, "output": ""})},
        {
            "type": "custom_tool_call",
            "name": "functions.exec",
            "call_id": "finish",
            "input": 'text(await tools.write_stdin({session_id:13,chars:""}))',
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "finish",
            "output": json.dumps({"exit_code": 0, "output": header + "完成条件1: 達成 — 根拠: 保存試験成功\n"}),
        },
    ]
    records = [
        _record(index + 1, {"timestamp": "2026-11-01T00:00:00+00:00", "payload": payload})
        for index, payload in enumerate(payloads)
    ]
    family = [_CollectedRecord("codex:lane", Path("rollout.jsonl"), records, "codex", None, None, None, "main")]
    row = next(
        row
        for row in first_pass_metrics(
            collect_quality_events(family, "process-wi"),
            datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC),
            datetime.datetime(2026, 12, 1, tzinfo=datetime.UTC),
        )
        if row["stage"] == "review"
    )
    assert row["observed_count"] == row["first_success_count"] == 1


def test_malformed_command_results_remain_unknown() -> None:
    shown = {
        "run_id": "broken",
        "meta": {"argv": ["run", "--no-fix"], "cwd": "/repo"},
        "commands": [{"command": "pytest", "status": "succeeded"}, "invalid"],
    }
    row = next(row for row in _metrics([("pyfltr show-run broken", json.dumps(shown))]) if row["stage"] == "final-verification")
    assert row["unknown_count"] == 1
    assert row["observed_count"] == 0


@pytest.mark.parametrize("status,exit_code", [("succeeded", 0), ("skipped", 0), ("succeeded", 1)])
def test_public_jsonl_run_preserves_completion_status(status: str, exit_code: int) -> None:
    meta = {
        "kind": "meta",
        "run_id": "jsonl",
        "argv": ["run", "--no-fix"],
        "cwd": "/repo",
        "exit_code": exit_code,
        "commands": ["pytest"],
    }
    command = {"kind": "command", "command": "pytest", "status": status}
    body = "\n".join(json.dumps(row) for row in (meta, command))
    row = next(
        row
        for row in _metrics(
            [
                ("atk review-table show /plans/one.exec-review.tsv", ""),
                ("pyfltr show-run jsonl", json.dumps({"output": body})),
            ]
        )
        if row["stage"] == "final-verification"
    )
    assert row["first_success_count"] == int(status == "succeeded" and exit_code == 0)
    assert row["not_performed_count"] == int(status == "skipped")


def test_filtered_run_output_cannot_prove_all_commands_succeeded() -> None:
    shown = {
        "run_id": "filtered",
        "meta": {"argv": ["run", "--no-fix"], "cwd": "/repo", "commands": ["pytest", "ruff-check"], "exit_code": 0},
        "commands": [{"command": "pytest", "status": "succeeded"}],
    }
    row = next(
        row
        for row in _metrics(
            [
                ("atk review-table show /plans/one.exec-review.tsv", ""),
                ("pyfltr show-run filtered --commands pytest", json.dumps(shown)),
            ]
        )
        if row["stage"] == "final-verification"
    )
    assert row["unknown_count"] == 1
    assert row["observed_count"] == 0


def test_independent_initial_reviews_and_revision_chain_are_separate_units() -> None:

    def verdict(head: str, findings: int, previous: str | None = None) -> str:
        return (
            f"状態: completed\nレビューHEAD: {head * 40}\n未解決の指摘数: {findings}\n"
            f"読者別探索: {{}}\nレビュー開始時点: {'d' * 40}\n"
            f"前回確認版の開始時点: {'d' * 40 if previous else 'なし'}\n"
            f"前回確認版HEAD: {previous * 40 if previous else 'なし'}\n"
            "work.md 完成条件1: 達成 — 根拠: test PASSED\n"
        )

    rows = _metrics(
        [("atk agents wait", verdict("a", 1)), ("atk agents wait", verdict("b", 0, "a")), ("atk agents wait", verdict("c", 0))]
    )
    row = next(row for row in rows if row["stage"] == "review")
    assert row["observed_count"] == 2
    assert row["first_success_count"] == 1
    assert row["first_success_ratio"] == 0.5
    assert row["unresolved_count"] == 0
    assert sorted(unit["attempts"] for unit in row["units"]) == [1, 2]
    assert sorted(unit["correction_count"] for unit in row["units"]) == [0, 1]
    assert all(unit["scope_change_count"] == 0 for unit in row["units"])


def test_explicit_tables_keep_independent_work_and_findings_separate() -> None:
    header = (
        "状態: completed\nレビューHEAD: "
        + "a" * 40
        + "\n未解決の指摘数: 0\n読者別探索: {}\nレビュー開始時点: "
        + "b" * 40
        + "\n前回確認版の開始時点: なし\n前回確認版HEAD: なし\n完成条件1: 達成 — 根拠: 試験\n"
    )
    rows = _metrics(
        [
            ("atk review-table show /plans/one.exec-review.tsv", ""),
            ("atk agents wait", header),
            ("atk review-table show /plans/two.exec-review.tsv", ""),
            ("atk agents wait", header.replace("未解決の指摘数: 0", "未解決の指摘数: 1")),
        ]
    )
    row = next(row for row in rows if row["stage"] == "review")
    assert row["observed_count"] == 2 and row["first_success_count"] == 1
    assert [unit["review_tables"] for unit in row["units"]] == [["one.exec-review.tsv"], ["two.exec-review.tsv"]]


def test_unlinked_runs_do_not_guess_logical_work_from_parent_session() -> None:
    shown = {
        "run_id": "unlinked",
        "meta": {"argv": ["run", "--no-fix"], "cwd": "/repo", "commands": ["pytest"], "exit_code": 0},
        "commands": [{"command": "pytest", "status": "succeeded"}],
    }
    row = next(
        row for row in _metrics([("pyfltr show-run unlinked", json.dumps(shown))]) if row["stage"] == "final-verification"
    )
    assert row["observed_count"] == 0 and row["unknown_count"] == 1


def test_added_requirement_is_a_new_verdict_without_erasing_initial_failure() -> None:
    """同じHEADでも契約追加後の正式再判定を数え、初回不足と修正版数を区別する。"""
    first = (
        f"状態: completed\nレビューHEAD: {'a' * 40}\n未解決の指摘数: 0\n読者別探索: {{}}\n"
        f"レビュー開始時点: {'d' * 40}\n前回確認版の開始時点: なし\n前回確認版HEAD: なし\n"
        "work.md 完成条件1: 未達 — 根拠: 初回不足\n"
    )
    revised = first.replace("前回確認版の開始時点: なし", f"前回確認版の開始時点: {'d' * 40}")
    revised = revised.replace("前回確認版HEAD: なし", f"前回確認版HEAD: {'a' * 40}")
    revised = revised.replace("未達 — 根拠: 初回不足", "達成 — 根拠: 修正確認") + "work.md 完成条件2: 達成 — 根拠: 範囲追加\n"
    row = next(
        row
        for row in _metrics([("atk agents wait", first), ("atk agents wait", revised), ("atk agents wait", revised)])
        if row["stage"] == "review"
    )
    assert row["observed_count"] == 1 and row["first_success_count"] == 0 and row["unresolved_count"] == 0
    assert row["units"][0]["attempts"] == 2
    assert row["units"][0]["scope_change_count"] == 1
    assert row["units"][0]["correction_count"] == 0


@pytest.mark.parametrize(
    "forms", [("original", "original"), ("inline", "original"), ("missing", "original"), ("original", "missing")]
)
@pytest.mark.parametrize("table_mapping", [False, True])
def test_wait_keeps_each_original_or_missing_review(tmp_path: Path, forms: tuple[str, str], table_mapping: bool) -> None:
    """同じ待機の正式原本・本文・取得不能を各作業へ保持し、成功だけの分母へ縮めない。"""
    bodies = []
    outputs = []
    for index, form in enumerate(forms):
        head = ("a" if table_mapping else "ab"[index]) * 40
        session = f"{index + 1:08d}-1111-1111-1111-111111111111"
        if table_mapping:
            outputs.append(
                (f"agents_server start extra_params=/plans/{index}.exec-review.tsv", json.dumps({"session_id": session}))
            )
        verdict = (
            f"状態: completed\nレビューHEAD: {head}\n未解決の指摘数: {index}\n読者別探索: {{}}\n"
            f"レビュー開始時点: {'d' * 40}\n前回確認版の開始時点: なし\n前回確認版HEAD: なし\n"
            "work.md 完成条件1: 達成 — 根拠: test PASSED\n"
        )
        path = tmp_path / f"review-{index}.md"
        if form == "original":
            path.write_text(verdict, encoding="utf-8")
        content = verdict if form == "inline" else f"状態: completed\n正式原本: {path}\n"
        bodies.append(f"本文開始: session_id={session}\n{content}本文終了: session_id={session}\n")
    outputs.append(("atk agents wait", "".join(bodies)))
    row = next(row for row in _metrics(outputs) if row["stage"] == "review")
    assert row["observed_count"] == sum(form != "missing" for form in forms)
    assert row["first_success_count"] == int(forms[0] != "missing")
    assert row["unknown_count"] == int("missing" in forms)
    assert row["unresolved_count"] == 1 + int(forms[0] == "missing")
