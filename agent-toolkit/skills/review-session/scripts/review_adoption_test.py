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
        + _row("1", "見送り", "仕様", "", "根拠の所在: レビュー担当の指摘をレーン担当が対象外と判断\n判断主体: レーン担当")
        + _row("2", "撤回", "詳細", "", "判断主体: レビュー担当\n根拠の所在: 再確認で撤回")
        + _row("2", "移管", "仕様", "", "根拠の所在: 後続へ移す\n判断主体: ユーザー\n移管先: 20261104-183257-001.md")
        + _row(
            "2",
            "旧形式",
            "詳細",
            "",
            "根拠の所在: レビュー担当が後続AWIへ移せると区分し、メインが次回へ回した。AWI 20261104-183257-001.mdへ登録した",
        ),
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
    assert (current["table_count"], current["adopted"], current["rejected"], current["transferred"], current["unanswered"]) == (
        3,
        1,
        3,
        1,
        1,
    )
    assert current["by_level"]["仕様"] == {"adopted": 1, "rejected": 1, "transferred": 1, "unanswered": 0}
    assert current["by_reason"] == {"判断による見送り": 1, "レビュー担当の前提の誤り・撤回": 1, "判断主体不明": 1}
    assert current["by_model"]["claude:claude-opus-5-5/high"] == {
        "adopted": 1,
        "rejected": 3,
        "transferred": 1,
        "unanswered": 0,
        "tables": 2,
    }
    assert current["by_model"]["モデル不明"] == {
        "adopted": 0,
        "rejected": 0,
        "transferred": 0,
        "unanswered": 1,
        "tables": 1,
    }
    assert periods["previous"]["table_count"] == 1
    assert periods["previous"]["adopted"] == 1
    rejections = [event for event in events if event["kind"] == "review-adoption-rejection"]
    assert {(event["row"], event["reason_class"]) for event in rejections} == {
        (8, "判断による見送り"),
        (9, "レビュー担当の前提の誤り・撤回"),
        (11, "判断主体不明"),
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


def _codex_review(path: Path, table: str, *, preamble_bytes: int) -> None:
    """実記録と同じ並び（道具の定義、developerの本文、標識の無い規範の配送、環境の状態、依頼文）のCodexの記録を書く。"""
    filler = "規" * (preamble_bytes // 3)
    time = "2026-11-03T00:00:00Z"
    write_jsonl(
        path,
        [
            {"type": "session_meta", "timestamp": time, "payload": {"id": "review"}},
            {"type": "response_item", "timestamp": time, "payload": {"type": "additional_tools", "tools": []}},
            {
                "type": "response_item",
                "timestamp": time,
                "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "方針"}]},
            },
            {
                "type": "response_item",
                "timestamp": time,
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": filler}]},
            },
            {"type": "world_state", "timestamp": time, "payload": {"state": "x"}},
            {
                "type": "response_item",
                "timestamp": time,
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"{_ROLE_PROMPT}\nレビュー指摘管理表: /p/{table}"}],
                },
            },
            {
                "type": "turn_context",
                "timestamp": time,
                "payload": {"model": "gpt-6.1-sol", "effort": "medium"},
            },
            {"type": "response_item", "timestamp": time, "payload": {"type": "function_call", "call_id": "c", "name": "shell"}},
        ],
    )


def test_review_adoption_finds_request_after_large_preamble_and_ignores_quotes(dataset, capsys) -> None:
    """依頼文より前の内容が256KiBを超えるCodexの記録へ帰属させ、標識を引用しただけの記録へは帰属させない。"""
    _notes, claude, codex = dataset
    _codex_review(codex / "2026/11/03/rollout-review.jsonl", "03-0001_unknown.exec-review.tsv", preamble_bytes=300 * 1024)
    # 振り返りの会話など、最初のモデル出力より後ろで標識と表の名前を引用した記録。
    write_jsonl(
        claude / "p/quote.jsonl",
        [
            {"type": "user", "timestamp": "2026-11-03T00:00:00Z", "message": {"role": "user", "content": "振り返りを行う"}},
            {
                "type": "assistant",
                "timestamp": "2026-11-03T00:00:01Z",
                "effort": "low",
                "message": {"role": "assistant", "model": "claude-quote-1", "content": [{"type": "text", "text": "読む"}]},
            },
            {
                "type": "user",
                "timestamp": "2026-11-03T00:00:02Z",
                "message": {"role": "user", "content": f"{_ROLE_PROMPT} 03-0000_known.exec-review.tsv"},
            },
        ],
    )
    events = _run(dataset, capsys)
    current = next(event for event in events if event.get("period") == "current")
    assert "モデル不明" not in current["by_model"]
    assert [model for model in current["by_model"] if model.startswith("codex:")] == ["codex:gpt-6.1-sol/medium"]
    assert not any("claude-quote-1" in model for model in current["by_model"])
