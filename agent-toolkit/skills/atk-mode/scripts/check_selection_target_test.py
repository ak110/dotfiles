"""全工程の算定と目標の受理を、生成・検査・mergeの公開操作で検証する。"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
from typing import Any

import pytest
import yaml

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository


def _dispatch(*args: str) -> int:
    return run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=["--", *args]))


def _wi(notes: pathlib.Path, name: str) -> None:
    header = yaml.safe_dump({"type": "awi", "state": "processing", "target_repo": "github.com/test/repo"})
    (notes / "2026" / "11" / name).write_text(
        f"---\n{header}---\n# 要求\n\n## 反映内容と反映先\n\n「src/model.py」を変更する\n",
        encoding="utf-8",
    )


@pytest.fixture(name="environment")
def _environment(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/model.py").write_text("pass\n", encoding="utf-8")
    git_repository.init_repository(repo, origin="https://github.com/test/repo.git")
    notes = tmp_path / "notes"
    (notes / "2026" / "11").mkdir(parents=True)
    _wi(notes, "first.md")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    return repo, notes


def _target(repo: pathlib.Path, seconds: object) -> None:
    (repo / "pyproject.toml").write_text(
        f"[tool.agent-toolkit.pick-wi-check]\ntarget-seconds = {json.dumps(seconds)}\n", encoding="utf-8"
    )


def _node(name: str, phase: str, wis: list[str], prior: list[str], seconds: float, resource: str) -> dict[str, Any]:
    # 合成した仕事量と速度から秒数を定義し、実機の測定済み性能と取り違えない。
    return {
        "ID": name,
        "工程": phase,
        "WI": wis,
        "先行工程": prior,
        "資源": resource,
        "秒数": seconds,
        "算定": "残量/速度",
        "残量": seconds,
        "速度": 1,
        "根拠": "合成仕様の仕事量と速度",
        "未確認前提": [],
    }


def _workflow(work: dict[str, float] | None = None, *, public: float = 30) -> dict[str, Any]:
    work = {"first.md": 100} if work is None else work
    nodes = [_node("select", "選定", [], [], 5, "直列")]
    integrations = []
    for index, (wi, seconds) in enumerate(work.items()):
        impl, review, verify, integrate = (f"{index}-{phase}" for phase in ("impl", "review", "verify", "integrate"))
        nodes.extend(
            [
                _node(impl, "実装", [wi], ["select"], seconds, "モデル"),
                _node(review, "レビュー", [wi], [impl], 10, "モデル"),
                _node(verify, "検証", [wi], [impl], 20, "検証"),
                _node(integrate, "統合", [wi], [review, verify], 10, "直列"),
            ]
        )
        integrations.append(integrate)
    nodes.extend(
        [
            _node("publish", "公開", [], integrations, public, "直列"),
            _node("cleanup", "後始末", [], ["publish"], 5, "直列"),
            _node("report", "報告", [], ["cleanup"], 5, "直列"),
        ]
    )
    return {
        "並列数": {
            "モデル同時数": 2,
            "資源上限": 4,
            "サービス上限": 4,
            "根拠": {"資源": "合成仕様の資源制約", "サービス": "合成仕様の利用枠", "費用": "同品質の1/2同時の合成比較"},
        },
        "工程": nodes,
    }


def _generate(
    tmp_path: pathlib.Path,
    repo: pathlib.Path,
    *,
    plan: dict[str, Any] | None = None,
    include_plan: bool = True,
    comparison: bool = False,
    names: list[str] | None = None,
    correction: dict[str, Any] | None = None,
) -> int:
    names = ["first.md"] if names is None else names
    facts = tmp_path / "candidates.jsonl"
    facts.write_text(
        "".join(
            json.dumps({"filename": name, "staleness": {"status": "current", "later_commit_count": 0}}) + "\n" for name in names
        ),
        encoding="utf-8",
    )
    document: dict[str, Any] = {
        "選定": [{"WI": name, "レーン": "lane-01", "書込対象": ["src/model.py"]} for name in names],
        "見込みの補正": {
            "実装係数": 1,
            "統合係数": 1,
            "共有待ち秒数": 0,
            "共有待ちの算定": "実測",
            "参照した記録": ["合成例の旧補正"],
            "除外した記録": [],
            **(correction or {}),
        },
        "レーンの所要時間": [
            {
                "レーン": "lane-01",
                "実装秒数": 100,
                "統合秒数": 10,
                "補正前の実装秒数": 100,
                "補正前の統合秒数": 10,
                "根拠": "合成例の1レーン配分",
            }
        ],
        "結合条件": [],
    }
    if include_plan:
        document["全工程の見込み"] = _workflow() if plan is None else plan
    if comparison:
        document["目標超過時の配分比較"] = "独立群の先行レビューと共有検証の集約を比較した"
    source = tmp_path / "judgments.yaml"
    source.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return _dispatch(
        str(source), "--candidate-file", str(facts), "--output", str(tmp_path / "selection.yaml"), "--work-dir", str(repo)
    )


@pytest.mark.parametrize("seconds", [10724, 10725, 10726, 92225])
def test_target_overrun_is_a_warning_and_does_not_stop_selection(environment, tmp_path, capsys, seconds):
    repo, _ = environment
    _target(repo, 10800)
    assert _generate(tmp_path, repo, plan=_workflow({"first.md": seconds}), comparison=True) == 0
    captured = capsys.readouterr()
    output = tmp_path / "selection.yaml"
    assert output.exists()
    assert f"全工程の壁時計見込み秒数: {seconds + 75}" in captured.out
    assert f"通常対象の完了見込み秒数: {seconds + 75}" in captured.out
    if seconds + 75 > 10800:
        assert "警告: 通常対象の完了見込み秒数" in captured.err and "目標所要秒数10800" in captured.err
        assert "項目を外さず" in captured.err and "品質工程の省略や次回送り" in captured.err
    else:
        assert "警告" not in captured.err
    saved = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert [item["WI"] for item in saved["選定"]] == ["first.md"]
    assert _dispatch(str(output), "--work-dir", str(repo)) == 0


def test_dependent_publication_cannot_be_hidden_by_implementation_budget(environment, tmp_path, capsys):
    repo, _ = environment
    _target(repo, 10800)
    assert _generate(tmp_path, repo, plan=_workflow({"first.md": 7200}, public=5400), comparison=True) == 0
    assert "12645" in capsys.readouterr().err
    assert (tmp_path / "selection.yaml").exists()


@pytest.mark.parametrize(("serial", "expected_seconds"), [(False, 55), (True, 45)])
def test_public_capacity_and_serial_definition_use_one_resource_path(environment, tmp_path, capsys, serial, expected_seconds):
    repo, notes = environment
    names = ["first.md", "second.md", "third.md"]
    for name in names[1:]:
        _wi(notes, name)
    plan = _workflow(dict.fromkeys(names, 10 if serial else 0), public=0)
    previous_impl = None
    for node in plan["工程"]:
        if node["工程"] in {"レビュー", "統合"}:
            node.update({"秒数": 0, "残量": 0})
        if node["工程"] == "実装" and serial:
            if previous_impl is not None:
                node["先行工程"].append(previous_impl)
            previous_impl = node["ID"]
        if node["工程"] == "検証" and serial:
            node.update({"秒数": 0, "残量": 0})
    _target(repo, expected_seconds)
    assert _generate(tmp_path, repo, plan=plan, names=names) == 0
    assert f"通常対象の完了見込み秒数: {expected_seconds}" in capsys.readouterr().out
    selected = tmp_path / "selection.yaml"
    before = selected.read_bytes()
    _target(repo, expected_seconds - 1)
    assert _dispatch(str(selected), "--work-dir", str(repo)) == 0
    assert "警告: 通常対象の完了見込み秒数" in capsys.readouterr().err
    assert selected.read_bytes() == before


@pytest.mark.parametrize("phase", ["実装", "レビュー"])
@pytest.mark.parametrize("resource", ["直列", "検証"])
def test_public_model_work_cannot_bypass_model_capacity(environment, tmp_path, phase, resource):
    repo, notes = environment
    _wi(notes, "second.md")
    _target(repo, 10800)
    plan = _workflow({"first.md": 8000, "second.md": 8000})
    plan["並列数"].update({"モデル同時数": 1, "資源上限": 1, "サービス上限": 1})
    next(node for node in plan["工程"] if node["工程"] == phase)["資源"] = resource
    assert _generate(tmp_path, repo, plan=plan, names=["first.md", "second.md"]) == 2
    assert not (tmp_path / "selection.yaml").exists()


@pytest.mark.parametrize(("capacity", "overrun_seconds"), [(1, "16075"), (2, None)])
def test_public_model_capacity_is_preserved_with_shared_definition_dependencies(
    environment, tmp_path, capsys, capacity, overrun_seconds
):
    repo, notes = environment
    _wi(notes, "second.md")
    _target(repo, 10800)
    plan = _workflow({"first.md": 8000, "second.md": 8000})
    plan["並列数"].update({"モデル同時数": capacity, "資源上限": capacity, "サービス上限": capacity})
    assert _generate(tmp_path, repo, plan=plan, names=["first.md", "second.md"]) == 0
    captured = capsys.readouterr()
    if overrun_seconds is None:
        assert "通常対象の完了見込み秒数: 8085" in captured.out
        (tmp_path / "selection.yaml").unlink()
        next(node for node in plan["工程"] if node["ID"] == "1-impl")["先行工程"].append("0-impl")
        assert _generate(tmp_path, repo, plan=plan, names=["first.md", "second.md"]) == 0
        overrun_seconds = "16075"
        captured = capsys.readouterr()
    assert overrun_seconds in captured.err and "警告" in captured.err
    assert (tmp_path / "selection.yaml").exists()


@pytest.mark.parametrize("missing", ["plan", "seconds", "conditional", "premise"])
def test_incomplete_evidence_is_a_warning_not_zero_or_target_success(environment, tmp_path, capsys, missing):
    repo, _ = environment
    _target(repo, 10800)
    plan = _workflow()
    if missing == "seconds":
        plan["工程"][-1].update({"秒数": None, "算定": "未確認"})
        plan["工程"][-1].pop("残量")
        plan["工程"][-1].pop("速度")
    elif missing == "conditional":
        plan["工程"][-1]["算定"] = "条件付き試算"
        plan["工程"][-1].pop("残量")
        plan["工程"][-1].pop("速度")
    elif missing == "premise":
        plan["工程"][-1]["未確認前提"] = ["公開後の観測未了"]
    assert _generate(tmp_path, repo, plan=plan, include_plan=missing != "plan", comparison=True) == 0
    assert "警告" in capsys.readouterr().err
    assert (tmp_path / "selection.yaml").exists()


def test_malformed_estimate_is_rejected_with_exit_code_2(environment, tmp_path, capsys):
    repo, _ = environment
    _target(repo, 10800)
    plan = _workflow()
    plan["並列数"]["根拠"]["費用"] = ""
    assert _generate(tmp_path, repo, plan=plan, comparison=True) == 2
    assert capsys.readouterr().err
    assert not (tmp_path / "selection.yaml").exists()


def test_estimate_with_broken_completion_path_is_rejected_with_exit_code_1(environment, tmp_path, capsys):
    repo, _ = environment
    _target(repo, 10800)
    plan = _workflow()
    # 工程の定義は形式として成立するが、選定が実装の先行工程に無く、完成経路が成立しない。入力を直せば解消できる。
    next(node for node in plan["工程"] if node["工程"] == "実装")["先行工程"] = []
    assert _generate(tmp_path, repo, plan=plan, comparison=True) == 1
    assert "全工程の見込みは未成立" in capsys.readouterr().err
    assert not (tmp_path / "selection.yaml").exists()


@pytest.mark.parametrize("approval", ["none", "rejected", "matched", "other"])
def test_giant_approval_keeps_parallel_normal_work(environment, tmp_path, capsys, approval):
    repo, notes = environment
    _target(repo, 10800)
    _wi(notes, "giant.md")
    plan = _workflow({"first.md": 4000, "giant.md": 40000})
    giant: dict[str, Any] = {"WI": "giant.md", "巨大判定根拠": "合成した仕事量と合理的な容量上限の比較"}
    if approval != "none":
        giant["承認元"] = {
            "対象WI": "first.md" if approval == "other" else "giant.md",
            "判断": "拒否" if approval == "rejected" else "承認",
            "出所": "合成した今回の名指し回答",
        }
    plan["巨大WI"] = [giant]
    # 承認の無い・拒否・未回答の巨大WIは通常対象のままで、目標超過は警告にとどまる。
    # 拒否するのは、入力を直せば解消できる承認元の対象WIの不一致（other）だけである。
    expected = 1 if approval == "other" else 0
    assert _generate(tmp_path, repo, plan=plan, names=["first.md", "giant.md"]) == expected
    captured = capsys.readouterr()
    if expected == 0:
        assert "全工程の壁時計見込み秒数: 40075" in captured.out
        if approval == "matched":
            assert "通常対象の完了見込み秒数: 4075" in captured.out
            assert '承認済み巨大WI: ["giant.md"]' in captured.out
        else:
            assert "通常対象の完了見込み秒数: 40075" in captured.out
        assert ("警告" in captured.err) == (approval != "matched")
    else:
        assert not (tmp_path / "selection.yaml").exists()


def test_legacy_history_is_readable_but_not_an_execution_allocation(environment, tmp_path, capsys):
    repo, _ = environment
    assert _generate(tmp_path, repo, include_plan=False) == 0
    selected = tmp_path / "selection.yaml"
    before = selected.read_bytes()
    _target(repo, 10800)
    assert _dispatch(str(selected), "--work-dir", str(repo)) == 0
    assert "警告: 全工程の見込みが無く" in capsys.readouterr().err
    assert selected.read_bytes() == before
    assert _dispatch(str(selected), "--history", "--work-dir", str(repo)) == 0
    assert "これから実行する配分の目標内成立は検収していない" in capsys.readouterr().out
    assert _dispatch(str(selected), "--history", "--output", str(selected), "--work-dir", str(repo)) == 2
    assert selected.read_bytes() == before
    assert _dispatch(str(selected), "--mark-integrated", "lane-01", "--output", str(selected), "--work-dir", str(repo)) == 0


@pytest.mark.parametrize("overrun", [False, True])
def test_public_merge_uses_recomputed_whole_plan_and_preserves_initial_allocation(environment, tmp_path, capsys, overrun):
    repo, notes = environment
    _target(repo, 10800)
    assert _generate(tmp_path, repo) == 0
    selected = tmp_path / "selection.yaml"
    before = selected.read_bytes()
    existing = yaml.safe_load(before)
    _wi(notes, "added.md")
    addition = copy.deepcopy(existing)
    del addition["初回配分"]
    addition["選定"] = [{**existing["選定"][0], "WI": "added.md"}]
    addition["全工程の見込み"] = _workflow({"first.md": 100, "added.md": 20000 if overrun else 100})
    addition["目標超過時の配分比較"] = "全対象を保持して代替を比較した"
    added = tmp_path / "addition.yaml"
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"lane-01": "lane-01"}), encoding="utf-8")
    merged = tmp_path / "merged.yaml"
    assert (
        _dispatch(
            str(selected), "--merge", str(added), "--lane-map", str(mapping), "--output", str(merged), "--work-dir", str(repo)
        )
        == 0
    )
    assert selected.read_bytes() == before and added.exists()
    captured = capsys.readouterr()
    result = yaml.safe_load(merged.read_text(encoding="utf-8"))
    assert result["初回配分"] == existing["初回配分"]
    assert [row["WI"] for row in result["選定"]] == ["first.md", "added.md"]
    if overrun:
        assert "警告: 通常対象の完了見込み秒数" in captured.err
    else:
        assert "警告" not in captured.err
        assert "通常対象の完了見込み秒数: 185" in captured.out


def test_public_merge_without_added_estimate_drops_stale_estimate_with_warning(environment, tmp_path, capsys):
    repo, notes = environment
    _target(repo, 10800)
    assert _generate(tmp_path, repo) == 0
    selected = tmp_path / "selection.yaml"
    existing = yaml.safe_load(selected.read_text(encoding="utf-8"))
    _wi(notes, "added.md")
    addition = copy.deepcopy(existing)
    del addition["初回配分"]
    del addition["全工程の見込み"]
    addition["選定"] = [{**existing["選定"][0], "WI": "added.md"}]
    added = tmp_path / "addition.yaml"
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"lane-01": "lane-01"}), encoding="utf-8")
    merged = tmp_path / "merged.yaml"
    assert (
        _dispatch(
            str(selected), "--merge", str(added), "--lane-map", str(mapping), "--output", str(merged), "--work-dir", str(repo)
        )
        == 0
    )
    assert "全工程の見込み" not in yaml.safe_load(merged.read_text(encoding="utf-8"))
    assert "警告: 全工程の見込みが無く" in capsys.readouterr().err


def test_lane_limit_stays_rejected_alongside_estimate_warnings(environment, tmp_path, capsys):
    repo, notes = environment
    _target(repo, 10800)
    assert _generate(tmp_path, repo, include_plan=False) == 0
    capsys.readouterr()
    saved = yaml.safe_load((tmp_path / "selection.yaml").read_text(encoding="utf-8"))
    # WI1件の上限は1レーンのため、別レーンのWIを足すとレーン数の上限だけが拒否の理由になる。
    _wi(notes, "second.md")
    (repo / "src/other.py").write_text("pass\n", encoding="utf-8")
    saved["選定"].append({**saved["選定"][0], "WI": "second.md", "レーン": "lane-02", "書込対象": ["src/other.py"]})
    saved["レーンの所要時間"].append({**saved["レーンの所要時間"][0], "レーン": "lane-02"})
    selection = tmp_path / "two-lanes.yaml"
    selection.write_text(yaml.safe_dump(saved, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(selection), "--work-dir", str(repo)) == 1
    err = capsys.readouterr().err
    assert "レーン数が上限を超える" in err


@pytest.mark.parametrize("value", [0, -1, True, "10800"])
def test_invalid_target_is_rejected(environment, tmp_path, value):
    repo, _ = environment
    _target(repo, value)
    assert _generate(tmp_path, repo) == 2


def test_measured_wait_still_requires_its_historical_record(environment, tmp_path, capsys):
    repo, _ = environment
    assert _generate(tmp_path, repo, correction={"参照した記録": []}) == 1
    assert "実測とした共有待ちに参照した記録がない" in capsys.readouterr().err
