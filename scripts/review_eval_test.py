"""review_eval.pyの公開コマンドを通した準備と採点の試験。"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
import review_eval
from agent_toolkit._atk import review_table


def _write_cases(path: pathlib.Path) -> pathlib.Path:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "missed",
                        "kind": "見逃し",
                        "role": "exec-review",
                        "repo": "target",
                        "plan": "plans/2026/10/01-0000_case",
                        "description": "見逃し事例",
                        "expect": {
                            "detect": [{"id": "policy", "description": "方針違反", "patterns": ["本文.{0,10}配送", "方針"]}],
                            "absent": [{"id": "noise", "description": "誤指摘", "patterns": ["バックスラッシュ"]}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _table(path: pathlib.Path, issues: list[str]) -> pathlib.Path:
    review_table.init(path)
    for index, issue in enumerate(issues):
        review_table.add(path, "1", "exec-review", f"file{index}.py", issue)
    return path


def _prepared_table(path: pathlib.Path, issues: list[str], capsys: pytest.CaptureFixture[str]) -> pathlib.Path:
    """管理表を作り、作成時の成功行を採点結果の出力から除く。"""
    _table(path, issues)
    capsys.readouterr()
    return path


def _results(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_score_counts_detected_false_and_unclassified(tmp_path, monkeypatch, capsys) -> None:
    """全ての照合語句が同じ行に一致した期待だけを検出とし、出すべきでない指摘を誤指摘、残りを未分類として数える。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    table = _prepared_table(
        tmp_path / "case.exec-review.tsv",
        ["hookが本文を追加コンテキストへ配送しており方針に反する", "バックスラッシュの戻り先", "無関係な指摘"],
        capsys,
    )

    assert review_eval.main(["score", "--result", f"missed={table}"]) == 0
    result, total = _results(capsys)
    assert result["detected"] == ["policy"]
    assert result["missed"] == []
    assert result["false_findings"] == ["noise"]
    assert result["unclassified"] == [{"row": 3, "location": "file2.py"}]
    assert total == {"total": {"detected": 1, "missed": 0, "false_findings": 1, "unclassified": 1}}


def test_score_reports_missed_when_patterns_match_different_rows(tmp_path, monkeypatch, capsys) -> None:
    """照合語句が別々の行にだけ一致する場合は検出とせず見逃しとして返す。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    table = _prepared_table(tmp_path / "case.exec-review.tsv", ["本文を配送する", "方針の確認"], capsys)

    assert review_eval.main(["score", "--result", f"missed={table}"]) == 0
    result, total = _results(capsys)
    assert result["missed"] == ["policy"]
    assert total["total"]["unclassified"] == 2


def test_score_rejects_unknown_case(tmp_path, monkeypatch, capsys) -> None:
    """評価セットに無い事例IDは失敗として終了し、理由を標準エラーへ出す。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    table = _prepared_table(tmp_path / "case.exec-review.tsv", [], capsys)

    assert review_eval.main(["score", "--result", f"other={table}"]) == 1
    assert "評価セットに無い事例ID: other" in capsys.readouterr().err


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_prepare_rebuilds_worktree_and_launch_values(tmp_path, monkeypatch, capsys) -> None:
    """対応記録の書換え後のOIDから範囲を求め、レビュー対象の作業ツリーと計画の複製、起動値を用意する。"""
    monkeypatch.setattr(review_eval, "CASES_PATH", _write_cases(tmp_path / "cases.json"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "old")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "new")
    new = _git(repo, "rev-parse", "HEAD")
    notes = tmp_path / "notes"
    plan_dir = notes / "plans/2026/10"
    plan_dir.mkdir(parents=True)
    (plan_dir / "01-0000_case.md").write_text("# 計画\n", encoding="utf-8")
    (plan_dir / "01-0000_case.wi-commits.jsonl").write_text(
        f'{{"commits":["{old}"],"awi":["x.md"]}}\n{{"rewrite":{{"{old}":"{new}"}}}}\n', encoding="utf-8"
    )
    out = tmp_path / "out"

    assert review_eval.main(["prepare", "--out", str(out), "--repo", f"target={repo}", "--private-notes", str(notes)]) == 0
    (prepared,) = _results(capsys)
    worktree = pathlib.Path(prepared["start"]["cwd"])
    assert _git(worktree, "rev-parse", "HEAD") == new
    assert prepared["start"]["extra_params"]["開始時のHEAD"] == _git(repo, "rev-parse", f"{new}^")
    assert prepared["start"]["extra_params"]["開始時のHEAD"] == old
    assert pathlib.Path(prepared["start"]["extra_params"]["計画"]).read_text(encoding="utf-8") == "# 計画\n"
    assert prepared["table"].endswith("01-0000_case.exec-review.tsv")
    assert prepared["start"]["subagent_md_path"] == "exec-review"
    assert base != old
    subprocess.run(prepared["cleanup"], check=True)
    assert not worktree.exists()


def test_bundled_cases_load_and_compile() -> None:
    """同梱の評価セットが読み込め、事例IDが重複せず、照合語句が正規表現として成立する。"""
    cases = review_eval.load_cases()
    assert {case.kind for case in cases} >= {"見逃し", "正しい指摘", "誤指摘"}
    assert all(case.detect or case.absent for case in cases)
