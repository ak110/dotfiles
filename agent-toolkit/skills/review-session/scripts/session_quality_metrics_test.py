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
    failed = {
        "run_id": "first",
        "meta": {"argv": ["run", "--no-fix", "source.py"], "cwd": "/repo", "version": "1"},
        "commands": [{"command": "pytest", "status": "failed"}],
    }
    passed = {**failed, "run_id": "second", "commands": [{"command": "pytest", "status": "succeeded"}]}
    rows = _metrics(
        [
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
    """同じCI URLの再表示を重複計上せず、後続成功は未解決だけを更新する。"""
    rows = _metrics(
        [
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
    """空の表や焦点runを正式な初回成功へ昇格しない。"""
    focal = {
        "run_id": "focal",
        "meta": {"argv": ["run", "--no-fix", "--no-always-targets"]},
        "commands": [{"command": "pytest", "status": "succeeded"}],
    }
    rows = _metrics([("pyfltr show-run focal", json.dumps(focal))])
    assert {row["stage"] for row in rows} == {"review", "final-verification", "ci"}
    assert all(row["observed_count"] == 0 and row["first_success_ratio"] is None and row["unknown_count"] == 1 for row in rows)


def test_formal_review_requires_initial_verdict_and_condition_evidence() -> None:
    """0件の再レビューと根拠不足を初回合格へ変換しない。"""
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
    """正式原本を読み、要求の引用に含まれる未達という語を判定値にしない。"""
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
    """既知のpytest成功へ不正な別コマンドが混ざる入力を正常なrunにしない。"""
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
    """公開JSONLを包絡から読み、未実行や非0終了を初回成功としない。"""
    meta = {"kind": "meta", "run_id": "jsonl", "argv": ["run", "--no-fix"], "cwd": "/repo", "exit_code": exit_code}
    command = {"kind": "command", "command": "pytest", "status": status}
    body = "\n".join(json.dumps(row) for row in (meta, command))
    row = next(
        row
        for row in _metrics([("pyfltr show-run jsonl", json.dumps({"output": body}))])
        if row["stage"] == "final-verification"
    )
    assert row["first_success_count"] == int(status == "succeeded" and exit_code == 0)
