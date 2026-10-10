"""公開選定操作から定型欄を生成保存し、不正入力の拒否と追加時の履歴保持を確かめる。"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import pytest
import yaml

from agent_toolkit._atk import run_script


def _dispatch(*args: str) -> int:
    return run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=["--", *args]))


@pytest.fixture(name="environment")
def _environment(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/model.py").write_text("pass\n", encoding="utf-8")
    notes = tmp_path / "notes"
    (notes / "processing").mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    return repo, notes


def _wi(notes: pathlib.Path, name: str, dependencies: list[str] | None = None) -> None:
    header = yaml.safe_dump({"type": "awi", "target_repo": "github.com/test/repo", "depends_on": dependencies or []})
    (notes / "processing" / name).write_text(
        f"---\n{header}---\n# 要求\n\n## 反映内容と反映先\n\n`src/model.py`を変更する\n",
        encoding="utf-8",
    )


def _inputs(
    tmp_path: pathlib.Path, names: list[str], *, edges: list[dict[str, Any]] | None = None
) -> tuple[pathlib.Path, pathlib.Path]:
    facts = tmp_path / "candidates.jsonl"
    # wi listの公開JSON Linesが持つfilenameとstalenessの形を使う。
    facts.write_text(
        "".join(
            json.dumps({"filename": name, "staleness": {"status": "current", "later_commit_count": index}}) + "\n"
            for index, name in enumerate(names)
        ),
        encoding="utf-8",
    )
    judgments = tmp_path / "judgments.yaml"
    document: dict[str, Any] = {
        "選定": [{"WI": name, "レーン": "lane-01", "書込対象": ["src/model.py"]} for name in names],
        "レーンの所要時間": [
            {"レーン": "lane-01", "実装秒数": 100, "統合秒数": 10, "根拠": "同じ変更定義を同じレーンで処理する"}
        ]
        if names
        else [],
        "結合条件": edges or [],
    }
    judgments.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return facts, judgments


@pytest.mark.parametrize("kind", ["書込重複", "依存", "再開計画"])
def test_public_generation_uses_candidate_facts_and_typed_edges_once(
    environment: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    kind: str,
) -> None:
    repo, notes = environment
    names = ["first.md", "second.md"]
    _wi(notes, names[0])
    _wi(notes, names[1], [names[0]])
    evidence = (
        {"定義1": ["modelの変更"], "定義2": ["modelの変更"], "段階比較": "同段階で同じ定義を処理する"}
        if kind == "書込重複"
        else {"依存元": names[1]}
        if kind == "依存"
        else {}
    )
    facts, judgments = _inputs(tmp_path, names, edges=[{"WI1": names[0], "WI2": names[1], "種別": kind, "根拠": evidence}])
    if kind == "再開計画":
        value = yaml.safe_load(judgments.read_text(encoding="utf-8"))
        for index, item in enumerate(value["選定"]):
            item["再開位置"] = f"/absolute/plans/shared.md 工程{index}から再開"
        judgments.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    output = tmp_path / "selection.yaml"
    assert _dispatch(str(judgments), "--candidate-file", str(facts), "--output", str(output), "--work-dir", str(repo)) == 0
    saved = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert [item["鮮度"]["later_commit_count"] for item in saved["選定"]] == [0, 1]
    assert all("担当モデル" not in item for item in saved["選定"])
    initial = saved["初回配分"]
    assert initial["候補WI"] == names and initial["レーン割当"] == dict.fromkeys(names, "lane-01")
    assert initial["不可分成分"][0]["WI"] == names
    generated = initial["不可分成分"][0]["結合条件"][0]["根拠"]
    if kind == "書込重複":
        assert generated["パス1"] == ["src/model.py"] and generated["段階2"] == 1
    elif kind == "依存":
        assert generated["depends_on"] == [names[0]] and generated["リポジトリ1"] == "github.com/test/repo"
    else:
        assert generated["計画1"] == generated["計画2"] == "shared.md"


def test_public_generation_empty_candidates_and_failure_preserve_destination(
    environment: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    repo, _notes = environment
    facts, judgments = _inputs(tmp_path, [])
    output = tmp_path / "selection.yaml"
    assert _dispatch(str(judgments), "--candidate-file", str(facts), "--output", str(output), "--work-dir", str(repo)) == 0
    empty = output.read_bytes()
    saved = yaml.safe_load(empty)
    assert saved["初回配分"] == {"候補WI": [], "レーン割当": {}, "不可分成分": []}
    # 意味判断の欠落は推測で埋めず、成功済み保存先を変更しない。
    judgments.write_text("選定: [{}]\nレーンの所要時間: []\n", encoding="utf-8")
    assert _dispatch(str(judgments), "--candidate-file", str(facts), "--output", str(output), "--work-dir", str(repo)) == 2
    assert output.read_bytes() == empty


def test_generated_addition_saves_once_and_preserves_initial_allocation(
    environment: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    repo, notes = environment
    _wi(notes, "first.md")
    facts, judgments = _inputs(tmp_path, ["first.md"])
    output = tmp_path / "selection.yaml"
    assert _dispatch(str(judgments), "--candidate-file", str(facts), "--output", str(output), "--work-dir", str(repo)) == 0
    initial = yaml.safe_load(output.read_text(encoding="utf-8"))["初回配分"]
    _wi(notes, "second.md")
    facts, addition = _inputs(tmp_path, ["second.md"])
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"lane-01": "lane-01"}), encoding="utf-8")
    assert (
        _dispatch(
            str(output),
            "--merge",
            str(addition),
            "--candidate-file",
            str(facts),
            "--lane-map",
            str(mapping),
            "--output",
            str(output),
            "--work-dir",
            str(repo),
            "--body-wi",
            "second.md",
        )
        == 0
    )
    saved = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert saved["初回配分"] == initial and saved["追加WI"] == ["second.md"]
    assert [item["WI"] for item in saved["選定"]] == ["first.md", "second.md"]
