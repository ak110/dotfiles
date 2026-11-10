"""`atk run-script review-adoption`の公開入口を通した採否集計の試験。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.session_evidence_support import write_jsonl

_ROLE_PROMPT = "出所: /plugin/agent-toolkit/share/exec-review.subagent.md"


def _row(round_value: str, issue: str, level: str, response: str, reason: str, *, location: str = "file.py") -> str:
    cells = [round_value, "exec-review", location, issue, level, response, reason]
    return "\t".join(json.dumps(cell, ensure_ascii=False) for cell in cells) + "\n"


def _commit(repo: Path, time: str) -> None:
    git_repository.commit_all(repo, "試験データ", env={**os.environ, "GIT_AUTHOR_DATE": time, "GIT_COMMITTER_DATE": time})


def _claude_review(path: Path, table: str, model: str) -> None:
    write_jsonl(
        path,
        [
            {
                "type": "user",
                "timestamp": "2026-11-03T00:00:00Z",
                "message": {"role": "user", "content": f"{_ROLE_PROMPT}\nレビュー指摘管理表: /home/u/.claude/plans/{table}"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-03T00:00:01Z",
                "effort": "high",
                "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": "完了"}]},
            },
        ],
    )


@pytest.fixture(name="dataset")
def _dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    notes = git_repository.init_repository(tmp_path / "notes")
    plans = notes / "plans/2026/10"
    plans.mkdir(parents=True)
    (plans / "28-0000_old.exec-review.tsv").write_text(_row("1", "古い指摘", "詳細", "修正した", ""), encoding="utf-8")
    _commit(notes, "2026-10-28T12:00:00+00:00")
    plans = notes / "plans/2026/11"
    plans.mkdir(parents=True)
    (plans / "03-0000_known.exec-review.tsv").write_text(
        _row("9", "実装時の解釈: 入力境界", "要件", "", "判断を記録")
        + _row("9", "実装時の判断: 共通読込", "実装", "", "判断を記録")
        + _row("9", "実装中の判断: 検証範囲", "詳細", "", "判断を記録")
        + _row("9", "記録", "仕様", "", "実装中の判断の引き継ぎであり、指摘への拒否ではない")
        + _row("9", "記録", "実装", "", "", location="実装時の技術判断: 呼出側")
        + _row("1", "別track", "詳細", "", "").replace('"exec-review"', '"plan-review"')
        + _row("1", "実装時の判断を確認する指摘", "仕様", "修正した", "")
        + _row("1", "見送り", "仕様", "", "理由: 対象外\n根拠の所在: メインの技術判断。計画の実施内容")
        + _row("2", "撤回", "詳細", "", "理由: 前提が誤り\n根拠の所在: レビュー担当が再確認で撤回"),
        encoding="utf-8",
    )
    (plans / "03-0001_unknown.exec-review.tsv").write_text(_row("1", "未応答", "実装", "", ""), encoding="utf-8")
    (plans / "03-0002_judgments.exec-review.tsv").write_text(
        "".join(
            _row(
                "9",
                f"記録{number}",
                "要件",
                "",
                "実装時の判断: 記録" if number == 0 else "",
                location="file.py" if number == 0 else "実装時の技術判断: 記録",
            )
            for number in range(3)
        ),
        encoding="utf-8",
    )
    _commit(notes, "2026-11-03T12:00:00+00:00")
    claude = tmp_path / "claude"
    _claude_review(claude / "p/review.jsonl", "03-0000_known.exec-review.tsv", "claude-opus-5-5")
    _claude_review(claude / "p/judgments.jsonl", "03-0002_judgments.exec-review.tsv", "claude-opus-5-5")
    # 委譲元の記録にも同じ表の名前が現れるが、役割文書の配送が無いため帰属させない。
    write_jsonl(
        claude / "p/main.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-11-03T00:00:00Z",
                "message": {"role": "user", "content": "03-0001_unknown.exec-review.tsv を確認する"},
            }
        ],
    )
    return notes, claude, tmp_path / "codex"


def _run(dataset: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    notes, claude, codex = dataset
    arguments = [
        "--",
        "--since",
        "2026-11-01T00:00:00+00:00",
        "--until",
        "2026-11-07T00:00:00+00:00",
        "--private-notes",
        str(notes),
        "--claude-projects-dir",
        str(claude),
        "--codex-sessions-dir",
        str(codex),
    ]
    assert run_script.dispatch(argparse.Namespace(script_name="review-adoption", script_args=arguments)) == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_review_adoption_counts_periods_levels_reasons_and_models(dataset, capsys) -> None:
    """今回と直前の期間ごとに採否・level・不採用理由・モデル別の件数を返し、特定できない表はモデル不明に数える。"""
    events = _run(dataset, capsys)
    periods = {event["period"]: event for event in events if event["kind"] == "review-adoption-period"}
    current = periods["current"]
    assert (current["table_count"], current["adopted"], current["rejected"], current["unanswered"]) == (3, 1, 2, 1)
    assert current["by_level"]["仕様"] == {"adopted": 1, "rejected": 1, "unanswered": 0}
    assert current["by_reason"] == {"判断による見送り": 1, "レビュー担当の前提の誤り・撤回": 1}
    assert current["by_model"]["claude:claude-opus-5-5/high"] == {"adopted": 1, "rejected": 2, "unanswered": 0, "tables": 2}
    assert current["by_model"]["モデル不明"] == {"adopted": 0, "rejected": 0, "unanswered": 1, "tables": 1}
    assert periods["previous"]["table_count"] == 1
    assert periods["previous"]["adopted"] == 1
    rejections = [event for event in events if event["kind"] == "review-adoption-rejection"]
    assert {(event["row"], event["reason_class"]) for event in rejections} == {
        (8, "判断による見送り"),
        (9, "レビュー担当の前提の誤り・撤回"),
    }
    notes, _, _ = dataset
    assert current["excluded_judgment_count"] == 8
    assert current["excluded_judgments"] == [
        {"table": str(notes / "plans/2026/11" / name), "row": number}
        for name, numbers in (
            ("03-0000_known.exec-review.tsv", range(1, 6)),
            ("03-0002_judgments.exec-review.tsv", range(1, 4)),
        )
        for number in numbers
    ]
    assert "要件" not in current["by_level"]
    assert events[-1]["kind"] == "review-adoption-coverage"
