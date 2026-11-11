"""選定の見込みを目標所要秒数と比べ、未計測の共有待ちと超過時の配分比較を公開操作で確かめる。"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import pytest
import yaml

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository

# 2026年11月11日の巡回の開始時の見込み（実装の合計28,000秒、統合の合計10,500秒、共有待ち0秒）を1レーンへ縮めた値。
_IMPLEMENTATION = 28000
_INTEGRATION = 10500


def _dispatch(*args: str) -> int:
    return run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=["--", *args]))


@pytest.fixture(name="environment")
def _environment(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/model.py").write_text("pass\n", encoding="utf-8")
    git_repository.init_repository(repo, origin="https://github.com/test/repo.git")
    notes = tmp_path / "notes"
    (notes / "2026" / "11").mkdir(parents=True)
    header = yaml.safe_dump({"type": "awi", "state": "processing", "target_repo": "github.com/test/repo"})
    (notes / "2026" / "11" / "first.md").write_text(
        f"---\n{header}---\n# 要求\n\n## 反映内容と反映先\n\n`src/model.py`を変更する\n", encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    return repo, notes


def _target(repo: pathlib.Path, seconds: object) -> None:
    (repo / "pyproject.toml").write_text(
        f"[tool.agent-toolkit.pick-wi-check]\ntarget-seconds = {json.dumps(seconds)}\n", encoding="utf-8"
    )


def _generate(tmp_path: pathlib.Path, repo: pathlib.Path, correction: dict[str, Any], top: dict[str, Any] | None = None) -> int:
    """1件・1レーンの判断入力から選定を生成し、生成時の検査の終了コードを返す。"""
    facts = tmp_path / "candidates.jsonl"
    facts.write_text(
        json.dumps({"filename": "first.md", "staleness": {"status": "current", "later_commit_count": 0}}) + "\n",
        encoding="utf-8",
    )
    judgments = tmp_path / "judgments.yaml"
    document: dict[str, Any] = {
        "選定": [{"WI": "first.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}],
        "見込みの補正": {"実装係数": 1, "統合係数": 1, "参照した記録": [], "除外した記録": [], **correction},
        "レーンの所要時間": [
            {
                "レーン": "lane-01",
                "実装秒数": _IMPLEMENTATION,
                "統合秒数": _INTEGRATION,
                "補正前の実装秒数": _IMPLEMENTATION,
                "補正前の統合秒数": _INTEGRATION,
                "根拠": "1件を1レーンで処理する",
            }
        ],
        "段階案の完了見込み秒数": "成立する段階案なし",
        "結合条件": [],
        **(top or {}),
    }
    judgments.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    output = tmp_path / "selection.yaml"
    return _dispatch(str(judgments), "--candidate-file", str(facts), "--output", str(output), "--work-dir", str(repo))


def test_unmeasured_wait_and_overrun_are_reported_against_target(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """今回の巡回の見込みと目標10,800秒では、差を示し、0秒の推定の待ちと比較記録の欠落で止める。"""
    repo, _ = environment
    _target(repo, 10800)
    assert _generate(tmp_path, repo, {"共有待ち秒数": 0, "共有待ちの算定": "推定"}) == 1
    error = capsys.readouterr().err
    assert "未計測の共有待ちを0秒と推定している" in error
    assert "全体の完了見込み秒数38500が目標所要秒数10800を超える（差 27700）のに`目標超過時の配分比較`がない" in error
    assert "項目を外さず" in error
    assert not (tmp_path / "selection.yaml").exists()


def test_comparison_record_and_estimated_wait_pass_and_show_difference(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """待ちを推定して比較記録を書いた選定は保存し、要約に目標との差を出す。"""
    repo, _ = environment
    _target(repo, 10800)
    comparison = "後段の独立準備を先行し、検証を集約する案で見込み31,000秒。段階の組み直しは見込み34,000秒で不採用"
    correction = {"共有待ち秒数": 1800, "共有待ちの算定": "推定"}
    assert _generate(tmp_path, repo, correction, {"目標超過時の配分比較": comparison}) == 0
    out = capsys.readouterr().out
    assert "全体の完了見込み秒数: 40300（共有待ち秒数 1800を1回含む）" in out
    assert "目標所要秒数: 10800（差 29500）" in out
    saved = yaml.safe_load((tmp_path / "selection.yaml").read_text(encoding="utf-8"))
    assert saved["目標超過時の配分比較"] == comparison
    assert [item["WI"] for item in saved["選定"]] == ["first.md"]


def test_without_target_no_target_line_or_overrun_violation(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """目標を設定しないプロジェクトでは、目標の行も超過の違反も出さない。"""
    repo, _ = environment
    assert _generate(tmp_path, repo, {"共有待ち秒数": 600, "共有待ちの算定": "推定"}) == 0
    captured = capsys.readouterr()
    assert "目標所要秒数" not in captured.out
    assert "目標超過時の配分比較" not in captured.err


def test_measured_wait_requires_referenced_records(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録を参照しない実測の待ちは拒否する。"""
    repo, _ = environment
    assert _generate(tmp_path, repo, {"共有待ち秒数": 0, "共有待ちの算定": "実測"}) == 1
    assert "実測とした共有待ちに参照した記録がない" in capsys.readouterr().err


def test_generation_requires_wait_basis(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """新しく生成する選定は、共有待ちの算定の無い補正を受け付けない。"""
    repo, _ = environment
    assert _generate(tmp_path, repo, {"共有待ち秒数": 0}) == 2
    assert "`共有待ちの算定`" in capsys.readouterr().err


def test_saved_selection_without_basis_is_read_as_before(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """算定を持たない保存済みの選定は、目標の設定があっても従来どおり検査を通す。"""
    repo, _ = environment
    assert _generate(tmp_path, repo, {"共有待ち秒数": 600, "共有待ちの算定": "推定"}) == 0
    output = tmp_path / "selection.yaml"
    saved = yaml.safe_load(output.read_text(encoding="utf-8"))
    del saved["見込みの補正"]["共有待ちの算定"]
    saved["見込みの補正"]["共有待ち秒数"] = 0
    output.write_text(yaml.safe_dump(saved, allow_unicode=True), encoding="utf-8")
    _target(repo, 10800)
    assert _dispatch(str(output), "--work-dir", str(repo)) == 0


@pytest.mark.parametrize("value", [0, -1, True, "10800"])
def test_invalid_target_is_rejected(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, value: object
) -> None:
    """目標所要秒数が正の有限数でなければ入力の誤りとして止める。"""
    repo, _ = environment
    _target(repo, value)
    assert _generate(tmp_path, repo, {"共有待ち秒数": 600, "共有待ちの算定": "推定"}) == 2


def test_merge_rejects_addition_without_shared_wait_basis(
    environment: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """追加の合流入力が算定を欠くと、既存側の`実測`を引き継がずに拒否し、統合結果を保存しない。"""
    repo, notes = environment
    header = yaml.safe_dump({"type": "awi", "state": "processing", "target_repo": "github.com/test/repo"})
    (notes / "2026" / "11" / "added.md").write_text(
        f"---\n{header}---\n# 追加\n\n## 反映内容と反映先\n\n`src/model.py`を変更する\n", encoding="utf-8"
    )
    measured = {"共有待ち秒数": 20, "共有待ちの算定": "実測", "参照した記録": ["previous"]}
    assert _generate(tmp_path, repo, measured) == 0
    selected = tmp_path / "selection.yaml"
    addition = yaml.safe_load(selected.read_text(encoding="utf-8"))
    del addition["初回配分"]
    addition["選定"][0]["WI"] = "added.md"
    del addition["見込みの補正"]["共有待ちの算定"]
    addition["見込みの補正"].update({"共有待ち秒数": 0, "参照した記録": []})
    added = tmp_path / "addition.yaml"
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"lane-01": "lane-01"}), encoding="utf-8")
    merged = tmp_path / "merged.yaml"
    capsys.readouterr()
    rc = _dispatch(
        str(selected),
        "--merge",
        str(added),
        "--lane-map",
        str(mapping),
        "--output",
        str(merged),
        "--work-dir",
        str(repo),
        "--body-wi",
        "added.md",
    )
    assert rc == 2
    assert "`共有待ちの算定`" in capsys.readouterr().err
    assert not merged.exists()
