"""公開選定統合の保存結果と、失敗時の原本保持を検証する。"""

import argparse
import json
import pathlib
import typing

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


def _merge_with_costs(
    repo: pathlib.Path,
    existing: pathlib.Path,
    added: pathlib.Path,
    mapping: pathlib.Path,
    costs: pathlib.Path,
    output: pathlib.Path,
) -> int:
    """確定費用を渡す公開統合を実行し、保存対象は呼び出し側で選ぶ。"""
    return _dispatch(
        str(existing),
        "--merge",
        str(added),
        "--lane-map",
        str(mapping),
        "--lane-cost-updates",
        str(costs),
        "--output",
        str(output),
        "--work-dir",
        str(repo),
    )


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


@pytest.mark.parametrize(
    "case",
    [
        "duplicate-wi",
        "missing-map",
        "extra-map",
        "bad-map",
        "inconsistent-stage",
        "blocker",
        "blocker-existing",
        "mixed-existing",
        "mixed-added",
    ],
)
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
    elif case in ("blocker-existing", "mixed-existing"):
        original = yaml.safe_load(existing.read_text(encoding="utf-8"))
        original["続行できない理由"] = ["割当未確定"] if case == "blocker-existing" else ["なし", "割当未確定"]
        existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    elif case == "mixed-added":
        value["続行できない理由"] = ["なし", "割当未確定"]
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


@pytest.mark.parametrize("marker_side", ["existing", "added", "both", "empty"])
@pytest.mark.parametrize("new_lanes", [False, True])
def test_public_merge_accepts_completion_marker_and_reuses_result(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    marker_side: str,
    new_lanes: bool,
) -> None:
    """完了書式を両側と合流/新規追加で受理し、保存結果を次の既存入力へ渡す。"""
    repo, existing, added = inputs
    for side, path in (("existing", existing), ("added", added)):
        if marker_side in (side, "both", "empty"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["続行できない理由"] = [] if marker_side == "empty" else ["なし"]
            path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps({"lane-03": "lane-05", "lane-04": "lane-06"} if new_lanes else {"lane-03": "lane-01", "lane-04": "lane-02"}),
        encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in (existing, added, mapping)}
    output = tmp_path / "whole.yaml"
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        == 0
    )
    first = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert first["続行できない理由"] == ["なし"]
    assert [item["WI"] for item in first["選定"]] == ["a.md", "b.md", "c.md", "d.md"]
    assert all(path.read_bytes() == data for path, data in before.items())

    (repo / "EXTRA.md").write_text("# 独立した反映先\n", encoding="utf-8")
    (repo.parent / "notes/processing/e.md").write_text(
        "---\ntype: awi\n---\n\n## 反映内容と反映先\n\n`EXTRA.md`を変える。\n", encoding="utf-8"
    )
    next_added = tmp_path / "next.yaml"
    next_added.write_text(
        yaml.safe_dump(
            {
                "選定": [{**_item("e.md", "lane-07"), "書込対象": ["EXTRA.md"]}],
                "レーンの所要時間": [_cost("lane-07")],
                "続行できない理由": ["なし"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    next_map = tmp_path / "next-map.json"
    next_map.write_text(json.dumps({"lane-07": "lane-07"}), encoding="utf-8")
    assert (
        _dispatch(
            str(output),
            "--merge",
            str(next_added),
            "--lane-map",
            str(next_map),
            "--output",
            str(output),
            "--work-dir",
            str(repo),
        )
        == 0
    )
    final = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert final["続行できない理由"] == ["なし"]
    assert final["選定"][:4] == first["選定"]
    assert [item["WI"] for item in final["選定"]] == ["a.md", "b.md", "c.md", "d.md", "e.md"]


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
                "単一段階案の完了見込み秒数": 30,
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


def test_public_merge_applies_final_cost_updates_and_preserves_metadata(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """確定した再見積りを合流後へ反映し、元項目と任意メタ情報を構造上の値で保持する。"""
    repo, existing, added = inputs
    metadata = {
        "プロジェクト規範の指定": "対象規範の指定",
        "再開位置": "計画なし",
        "上流投入": "なし",
        "上流投入先": ["/upstream"],
        "上流要求": "契約",
        "プロジェクト固有の公開後の操作の順序": "公開後に反映",
    }
    originals = []
    for path in (existing, added):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for item in data["選定"]:
            item.update(metadata)
            item["公開工程の書込対象"] = []
        originals.extend(data["選定"])
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    updates = {"lane-01": {"実装秒数": 15.5, "統合秒数": 0, "根拠": "再見積りを確定"}}
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps(updates, ensure_ascii=False), encoding="utf-8")
    before = {path: path.read_bytes() for path in (existing, added, mapping, costs)}
    output = tmp_path / "output.yaml"
    assert _merge_with_costs(repo, existing, added, mapping, costs, output) == 0
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert result["選定"][:2] == originals[:2]
    for item, original in zip(result["選定"][2:], originals[2:], strict=True):
        assert item == {**original, "レーン": {"lane-03": "lane-01", "lane-04": "lane-02"}[original["レーン"]]}
    assert result["レーンの所要時間"][0] == {"レーン": "lane-01", "先行レーン": [], **updates["lane-01"]}
    assert result["レーンの所要時間"][1]["実装秒数"] == 20
    assert all(path.read_bytes() == value for path, value in before.items())

    (repo / "EXTRA.md").write_text("# 別の反映先\n", encoding="utf-8")
    (repo.parent / "notes/processing/e.md").write_text(
        "---\ntype: awi\n---\n\n## 反映内容と反映先\n\n`EXTRA.md`を変える。\n", encoding="utf-8"
    )
    additional = tmp_path / "additional.yaml"
    additional.write_text(
        yaml.safe_dump(
            {"選定": [{**_item("e.md", "lane-03"), "書込対象": ["EXTRA.md"]}], "レーンの所要時間": [_cost("lane-03")]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    second_map = tmp_path / "second-map.json"
    second_map.write_text(json.dumps({"lane-03": "lane-05"}), encoding="utf-8")
    second_costs = tmp_path / "second-costs.json"
    final_cost = {"実装秒数": 11, "統合秒数": 1, "根拠": "新規レーンの確定値"}
    second_costs.write_text(json.dumps({"lane-05": final_cost}, ensure_ascii=False), encoding="utf-8")
    assert _merge_with_costs(repo, output, additional, second_map, second_costs, output) == 0
    final = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert final["選定"][:4] == result["選定"]
    assert [item["WI"] for item in final["選定"]] == ["a.md", "b.md", "c.md", "d.md", "e.md"]
    assert final["選定"][4]["レーン"] == "lane-05"
    assert final["レーンの所要時間"][:2] == result["レーンの所要時間"]
    assert final["レーンの所要時間"][2] == {"レーン": "lane-05", "先行レーン": [], **final_cost}
    assert final["レーン間の重なり"] == result["レーン間の重なり"]
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize(
    "updates",
    [
        None,
        [],
        {"lane-09": {"実装秒数": 1, "統合秒数": 1, "根拠": "外部"}},
        {"lane-01": {"実装秒数": True, "統合秒数": 1, "根拠": "型"}},
        {"lane-01": {"実装秒数": 1, "統合秒数": -1, "根拠": "型"}},
        {"lane-01": {"実装秒数": 1, "根拠": "欠落"}},
        {"lane-01": {"実装秒数": 1, "統合秒数": 1, "根拠": " "}},
        {"lane-01": {"実装秒数": 1, "統合秒数": 1, "根拠": "値", "段階": 2}},
    ],
)
def test_public_merge_rejects_invalid_cost_updates(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    updates: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対象外・型・必須欄の不正は、入力と保存先を変更せず次の操作を返す。"""
    repo, existing, added = inputs
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps(updates, ensure_ascii=False), encoding="utf-8")
    before = {path: path.read_bytes() for path in (existing, added, mapping, costs)}
    assert _merge_with_costs(repo, existing, added, mapping, costs, existing) == 2
    assert "次の操作:" in capsys.readouterr().err
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize("existing_comparison", [None, 99])
def test_public_merge_requires_whole_selection_comparison(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    existing_comparison: int | None,
) -> None:
    """旧比較値は流用せず、追加時に確定した全体値だけを保存する。本文限定でも拒否する。"""
    repo, existing, added = inputs
    original: dict[str, object] = {"選定": [_item("a.md", "lane-01")], "レーンの所要時間": [_cost("lane-01")]}
    if existing_comparison is not None:
        original["単一段階案の完了見込み秒数"] = existing_comparison
    existing.write_text(yaml.safe_dump(original), encoding="utf-8")
    overlap = _overlap("lane-03", "lane-04", "交わる")
    overlap["レーン1の定義"] = overlap["レーン2の定義"] = ["共有契約"]
    data: dict[str, object] = {
        "選定": [_item("c.md", "lane-03"), _item("d.md", "lane-04")],
        "レーンの所要時間": [_cost("lane-03"), _cost("lane-04", **{"段階": 2, "先行レーン": ["lane-03"]})],
        "レーン間の重なり": [overlap],
    }
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-05"}), encoding="utf-8")
    for comparison in (None, 31):
        if comparison is not None:
            data["単一段階案の完了見込み秒数"] = comparison
        added.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        before = existing.read_bytes()
        assert _dispatch(
            str(existing),
            "--merge",
            str(added),
            "--lane-map",
            str(mapping),
            "--output",
            str(existing),
            "--work-dir",
            str(repo),
            "--body-wi",
            "c.md",
        ) == (1 if comparison is None else 0)
        if comparison is None:
            assert existing.read_bytes() == before
            assert "単一段階案の完了見込み秒数" in capsys.readouterr().err
        else:
            assert yaml.safe_load(existing.read_text(encoding="utf-8"))["単一段階案の完了見込み秒数"] == comparison


def test_public_merge_preserves_derived_new_paths(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """新設先の要求・配置根拠を保持し、再取得した出力でも同じ被覆判定を受ける。"""
    repo, existing, added = inputs
    data = yaml.safe_load(added.read_text(encoding="utf-8"))
    (repo / "src").mkdir()
    for index, item in enumerate(data["選定"]):
        new = f"src/save{index}.py"
        item["書込対象"] = [new]
        item["導出した新設先"] = [
            {"パス": new, "反映範囲": "src/", "要求": "結果を保存する", "配置根拠": "共有入口を読んだ結果から保存責務を分ける"}
        ]
        (repo.parent / "notes/processing" / item["WI"]).write_text(
            "---\ntype: awi\n---\n\n## 反映内容と反映先\n\n`src/`に保存入口を新設する。\n", encoding="utf-8"
        )
    data["レーン間の重なり"] = []
    added.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-03", "lane-04": "lane-04"}), encoding="utf-8")
    output = tmp_path / "whole.yaml"
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        == 0
    )
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert typing.cast(list[dict[str, object]], result["選定"])[2:] == [
        {**item, "公開工程の書込対象": []} for item in data["選定"]
    ]
    assert _dispatch(str(output), "--work-dir", str(repo)) == 0
