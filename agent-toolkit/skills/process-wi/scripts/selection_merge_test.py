"""公開選定統合の保存結果と、失敗時の原本保持を検証する。"""

import argparse
import json
import pathlib

import pytest
import yaml

from agent_toolkit._atk import run_script


def _item(wi: str, lane: str) -> dict[str, object]:
    return {"WI": wi, "レーン": lane, "鮮度": {"status": "current", "later_commit_count": 0}, "書込対象": ["README.md"]}


def _cost(lane: str, **extra: object) -> dict[str, object]:
    return {"レーン": lane, "実装秒数": 10, "統合秒数": 2, "根拠": lane + "の根拠", **extra}


def _overlap(left: str, right: str, judgment: str = "交わらない") -> dict[str, object]:
    return {
        "レーン1": left,
        "レーン2": right,
        "共通パス": "README.md",
        "レーン1の定義": [left + "の節"],
        "レーン2の定義": [right + "の節"],
        "判定": judgment,
    }


@pytest.fixture(name="inputs")
def fixture_inputs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """本文と反映先を実在させ、2レーンずつの選定を保存する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# 共有文書\n", encoding="utf-8")
    notes = tmp_path / "notes" / "processing"
    notes.mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes.parent))
    for wi in ("a.md", "b.md", "c.md", "d.md"):
        (notes / wi).write_text(
            "---\ntype: awi\n---\n# 変更\n\n## 反映内容と反映先\n\n`README.md`を変える。\n", encoding="utf-8"
        )
    existing = tmp_path / "existing.yaml"
    added = tmp_path / "added.yaml"
    existing.write_text(
        yaml.safe_dump(
            {
                "選定": [_item("a.md", "lane-01"), _item("b.md", "lane-02")],
                "レーンの所要時間": [_cost("lane-01"), _cost("lane-02")],
                "レーン間の重なり": [_overlap("lane-01", "lane-02")],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    added.write_text(
        yaml.safe_dump(
            {
                "選定": [_item("c.md", "lane-03"), _item("d.md", "lane-04")],
                "レーンの所要時間": [_cost("lane-03"), _cost("lane-04")],
                "レーン間の重なり": [
                    _overlap(left, right)
                    for left, right in (
                        ("lane-03", "lane-04"),
                        ("lane-01", "lane-03"),
                        ("lane-01", "lane-04"),
                        ("lane-02", "lane-03"),
                        ("lane-02", "lane-04"),
                    )
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return repo, existing, added


def _dispatch(*args: str) -> int:
    return run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=["--", *args]))


def test_public_merge_combines_costs_and_both_definitions_once(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """返却前・受領時は一時全体、最終割当後は原本へ同じ公開操作で保存できる。"""
    repo, existing, added = inputs
    original = existing.read_bytes()
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    temporary = tmp_path / "whole.yaml"
    for output in (temporary, temporary, existing):
        assert (
            _dispatch(
                str(existing),
                "--merge",
                str(added),
                "--lane-map",
                str(mapping),
                "--output",
                str(output),
                "--work-dir",
                str(repo),
                "--body-wi",
                "c.md",
                "--body-wi",
                "d.md",
            )
            == 0
        )
        assert _dispatch(str(output), "--work-dir", str(repo)) == 0
        if output == temporary:
            assert existing.read_bytes() == original
    result = yaml.safe_load(existing.read_text(encoding="utf-8"))
    assert [item["WI"] for item in result["選定"]] == ["a.md", "b.md", "c.md", "d.md"]
    assert [item["レーン"] for item in result["選定"]] == ["lane-01", "lane-02", "lane-01", "lane-02"]
    assert [(cost["実装秒数"], cost["統合秒数"]) for cost in result["レーンの所要時間"]] == [(20, 4), (20, 4)]
    assert all("根拠" in cost["根拠"] for cost in result["レーンの所要時間"])
    (overlap,) = result["レーン間の重なり"]
    assert set(overlap["レーン1の定義"]) == {"lane-01の節", "lane-03の節"}
    assert set(overlap["レーン2の定義"]) == {"lane-02の節", "lane-04の節"}


@pytest.mark.parametrize("case", ["duplicate-wi", "missing-map", "extra-map", "bad-map", "inconsistent-stage", "blocker"])
def test_public_merge_failure_preserves_all_files(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    case: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """入力誤りと全体不整合では保存先を含む全ての原本を保持する。"""
    repo, existing, added = inputs
    value = yaml.safe_load(added.read_text(encoding="utf-8"))
    lane_map: object = {"lane-03": "lane-01", "lane-04": "lane-02"}
    if case == "duplicate-wi":
        value["選定"][0]["WI"] = "a.md"
    elif case == "missing-map":
        lane_map = {"lane-03": "lane-01"}
    elif case == "extra-map":
        lane_map = {"lane-03": "lane-01", "lane-04": "lane-02", "lane-05": "lane-05"}
    elif case == "bad-map":
        lane_map = []
    elif case == "inconsistent-stage":
        value["レーンの所要時間"][0]["段階"] = 2
    else:
        value["続行できない理由"] = ["割当未確定"]
    added.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps(lane_map), encoding="utf-8")
    before = {path: path.read_bytes() for path in (existing, added, mapping)}
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(existing), "--work-dir", str(repo)
        )
        != 0
    )
    assert "次の操作:" in capsys.readouterr().err
    assert all(path.read_bytes() == data for path, data in before.items())


def test_public_merge_adds_new_lane_and_transforms_predecessor(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    """後段の新レーンと先行参照を変換し、合流先の既存レーンを保持する。"""
    repo, existing, added = inputs
    existing.write_text(
        yaml.safe_dump({"選定": [_item("a.md", "lane-01")], "レーンの所要時間": [_cost("lane-01")]}), encoding="utf-8"
    )
    overlap = _overlap("lane-03", "lane-04", "交わる")
    overlap["レーン1の定義"] = overlap["レーン2の定義"] = ["共通の契約"]
    added.write_text(
        yaml.safe_dump(
            {
                "選定": [_item("c.md", "lane-03"), _item("d.md", "lane-04")],
                "レーンの所要時間": [_cost("lane-03"), _cost("lane-04", **{"段階": 2, "先行レーン": ["lane-03"]})],
                "レーン間の重なり": [overlap],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-05"}), encoding="utf-8")
    output = tmp_path / "result.yaml"
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        == 0
    )
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert result["レーンの所要時間"][1]["先行レーン"] == ["lane-01"]
    assert result["選定"][2]["レーン"] == "lane-05"
