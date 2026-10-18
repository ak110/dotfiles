"""週次費用指標を登録済みCLIから検証する。記録の形はsession_evidence_supportと同じホスト形式。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest
import session_review_evidence

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.session_evidence_support import agy_step, write_agy_log, write_jsonl

_CHILD = "11111111-1111-4111-8111-111111111111"


def _claude(time: str, *, child: str | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = [
        {"timestamp": time, "type": "user", "message": {"role": "user", "content": "/process-wi"}},
        {
            "timestamp": time,
            "type": "assistant",
            "message": {
                "id": "message",
                "role": "assistant",
                "content": [],
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "output_tokens": 40,
                },
            },
        },
    ]
    entries.append(entries[-1])
    if child:
        entries.extend(
            [
                {
                    "timestamp": time,
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "start", "name": "mcp__agents_server__start", "input": {}}],
                    },
                },
                {
                    "timestamp": time,
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "start", "content": "完了"}],
                    },
                    "toolUseResult": {"session_id": child},
                },
            ]
        )
    return entries


def _codex(time: str, identity: str) -> list[dict[str, Any]]:
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 25,
        "output_tokens": 50,
        "reasoning_output_tokens": 30,
        "total_tokens": 150,
    }
    return [
        {"timestamp": time, "type": "session_meta", "payload": {"id": identity}},
        {
            "timestamp": time,
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"last_token_usage": usage, "total_token_usage": usage}},
        },
    ]


def _commit(repo: Path, time: str) -> None:
    git_repository.commit_all(repo, "試験データ", env={**os.environ, "GIT_AUTHOR_DATE": time, "GIT_COMMITTER_DATE": time})


@pytest.fixture(name="dataset")
def _dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    claude, codex = tmp_path / "任意のClaude記録", tmp_path / "任意のCodex記録"
    time = "2026-10-06T12:00:00+00:00"
    write_jsonl(claude / "project-a" / "parent.jsonl", _claude(time, child=_CHILD))
    write_jsonl(claude / "project-a" / "parent" / "subagents" / "agent-sub.jsonl", _claude(time))
    write_jsonl(codex / f"2026/10/06/rollout-child-{_CHILD}.jsonl", _codex(time, _CHILD))
    write_jsonl(codex / "2026/10/06/rollout-other.jsonl", _codex(time, "other"))
    repo = git_repository.init_repository(tmp_path / "notes")
    for target, kind in (
        ("one/adopted/same.md", "awi"),
        ("two/adopted/same.md", "awi"),
        ("one/adopted/question.md", "uwi"),
        ("one/rejected/no.md", "awi"),
    ):
        path = repo / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntype: '{kind}'\n---\n# 試験\n", encoding="utf-8")
    table = repo / "plans/ci/check.exec-review.tsv"
    table.parent.mkdir(parents=True)
    rows = [
        ["1", "exec-review", "場所", "指摘1", "要件", "修正", ""],
        ["5", "implementation-review", "場所", "指摘2", "詳細", "", "根拠あり"],
        ["9", "plan-conformance", "場所", "除外", "実装", "", "根拠あり"],
        ["8", "plan-review", "場所", "除外", "仕様", "", "根拠あり"],
        ["7", "independent", "場所", "除外", "実装", "", "根拠あり"],
    ]
    table.write_text(
        "".join("\t".join(json.dumps(cell, ensure_ascii=False) for cell in row) + "\n" for row in rows), encoding="utf-8"
    )
    (repo / "plans/empty.exec-review.tsv").write_text("", encoding="utf-8")
    _commit(repo, time)
    return claude, codex, repo


def _run(dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str], *period: str) -> list[dict[str, Any]]:
    claude, codex, repo = dataset
    arguments = [
        "--",
        "--claude-projects-dir",
        str(claude),
        "--codex-sessions-dir",
        str(codex),
        "--private-notes",
        str(repo),
        *period,
    ]
    assert run_script.dispatch(argparse.Namespace(script_name="cost-metrics", script_args=arguments)) == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_weekly_metrics(dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    """任意の所在、週境界、開始だけの指定を登録済みCLIが受理する。"""
    events = _run(dataset, capsys, "--since", "2026-10-05", "--until", "2026-10-13")
    first, second, coverage = events
    assert [first["week"], second["week"]] == ["2026-10-05", "2026-10-12"]
    assert first["adopted_awi_count"] == 1
    assert first["root_session_count"] == {"all": 2, "process-wi": 1}
    assert first["tokens"]["all"]["total"] == {
        "input_tokens": 170,
        "cache_creation_input_tokens": 40,
        "cache_read_input_tokens": 110,
        "output_tokens": 180,
    }
    assert first["tokens_per_awi"]["process-wi"]["output_tokens"] == 130
    assert second["adopted_awi_count"] == 0
    assert second["tokens_per_awi"] == {"all": None, "process-wi": None}
    assert coverage["unresolved_delegate_count"] == 0
    assert _run(dataset, capsys, "--since", "2026-10-05")[0] == first


def test_tokens_match_stats_total(
    dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """委譲先と親を二重計上せず、既存stats-totalの4成分と一致する。"""
    claude, codex, _ = dataset
    events = _run(dataset, capsys, "--since", "2026-10-05", "--until", "2026-10-12")
    # 既存CLIは標準のsessions配置を使うため、同じrolloutをその配置へ移す。
    codex_home = codex.parent / "codex-home"
    codex.rename(codex_home)
    sessions = codex_home / "sessions"
    sessions.mkdir()
    (codex_home / "2026").rename(sessions / "2026")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert session_review_evidence.main([str(claude / "project-a/parent.jsonl"), "--stats"]) == 0
    total = next(
        json.loads(line)["tokens"] for line in capsys.readouterr().out.splitlines() if json.loads(line)["kind"] == "stats-total"
    )
    assert events[0]["tokens"]["process-wi"]["total"] == total


def test_review_metrics_exec_review_only(dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    """読み取り互換trackを含め、実行レビューの行だけで件数と率を計算する。"""
    first = _run(dataset, capsys, "--since", "2026-10-05", "--until", "2026-10-12")[0]
    assert first["review"] == {
        "table_count": 2,
        "rounds": {"0": 1, "1": 0, "2": 0, "3": 0, "4": 0, "5": 1, "6+": 0},
        "levels": {"要件": 1, "仕様": 0, "詳細": 1, "実装": 0, "未記録": 0},
        "finding_count": 2,
        "without_modification_count": 1,
        "five_or_more_ratio": 0.5,
        "without_modification_ratio": 0.5,
    }


def test_coverage_reports_missing_weeks(dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    """欠けた週、読取不能記録と表、未保存表、未解決委譲を明示する。"""
    claude, _, repo = dataset
    write_jsonl(claude / "project-b/missing.jsonl", _claude("2026-10-06T13:00:00Z", child="missing-child"))
    (claude / "project-b/broken.jsonl").write_bytes(b"\xff")
    (repo / "plans/broken.exec-review.tsv").write_text("不正な表", encoding="utf-8")
    _commit(repo, "2026-10-06T13:00:00Z")
    unsaved = Path.home() / ".claude/plans/current.exec-review.tsv"
    unsaved.parent.mkdir(parents=True, exist_ok=True)
    unsaved.write_text("", encoding="utf-8")
    old = claude / "project-b/old.jsonl"
    old.write_text("不正な旧記録", encoding="utf-8")
    os.utime(old, (0, 0))
    events = _run(dataset, capsys, "--since", "2026-09-28", "--until", "2026-10-12")
    assert events[0]["missing_token_records"] == {"claude": True, "codex": True}
    coverage = events[-1]
    assert coverage["by_runtime"]["claude"]["unreadable_record_count"] == 1
    assert coverage["unreadable_table_count"] == 1
    assert coverage["unsaved_table_count"] == 1
    assert coverage["unresolved_delegate_count"] == 1


def test_agy_delegate_keeps_runtime_coverage(
    dataset: tuple[Path, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """探索rootの外にあるAntigravityの委譲先も、トークンと被覆範囲へ加える。"""
    claude, _, _ = dataset
    identity = "22222222-2222-4222-8222-222222222222"
    time = "2026-10-06T13:00:00Z"
    write_jsonl(claude / "project-b/agy-parent.jsonl", _claude(time, child=identity))
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    write_agy_log(
        state,
        identity,
        [
            {
                "timestamp": time,
                **agy_step(1, "agent_response", "DONE", usage={"input_tokens": 3, "output_tokens": 4, "cache_read_tokens": 5}),
            }
        ],
    )
    events = _run(dataset, capsys, "--since", "2026-10-05", "--until", "2026-10-12")
    assert events[0]["tokens"]["all"]["by_runtime"]["agy"] == {
        "input_tokens": 3,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 5,
        "output_tokens": 4,
    }
    assert events[-1]["by_runtime"]["agy"]["oldest_record_at"] == "2026-10-06T13:00:00+00:00"
    log = next(state.rglob(f"{identity}.jsonl"))
    log.write_bytes(b"\xff")
    failed = _run(dataset, capsys, "--since", "2026-10-05", "--until", "2026-10-12")[-1]
    assert failed["by_runtime"]["agy"]["unreadable_record_count"] == 1
    assert failed["unresolved_delegate_count"] == 1


def test_adoption_counts_rename_into_adopted(dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    """Gitが改名と判定するキューからの移動も、その週の採用AWIへ数える。"""
    _, _, repo = dataset
    source = repo / "one/pending/moved.md"
    source.parent.mkdir(parents=True)
    source.write_text("---\ntype: awi\n---\n# 採用の移動\n", encoding="utf-8")
    _commit(repo, "2026-10-06T13:00:00Z")
    source.rename(repo / "one/adopted/moved.md")
    _commit(repo, "2026-10-07T13:00:00Z")
    week = _run(dataset, capsys, "--since", "2026-10-05", "--until", "2026-10-12")[0]
    assert week["adopted_awi_count"] == 2


@pytest.mark.parametrize("period", [("--since", "不正"), ("--since", "2026-10-13", "--until", "2026-10-05")])
def test_invalid_period_has_next_action(period: tuple[str, ...], capsys: pytest.CaptureFixture[str]) -> None:
    assert run_script.dispatch(argparse.Namespace(script_name="cost-metrics", script_args=["--", *period])) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["kind"] == "error"
    assert "次の操作" in error["next_action"]
