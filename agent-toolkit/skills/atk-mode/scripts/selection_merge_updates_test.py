import json
import pathlib
import typing

import pytest
import yaml
from selection_merge_test import _allocation, _cost, _dispatch, _item, _merge_into_existing, _merge_with_costs, _overlap
from selection_merge_test import fixture_inputs as fixture_inputs  # pylint: disable=useless-import-alias


def test_public_merge_transforms_existing_predecessor(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    repo, existing, added = inputs
    existing.write_text(
        yaml.safe_dump(
            {
                "選定": [_item("a.md", "lane-01"), _item("b.md", "lane-02")],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [_cost("lane-01"), _cost("lane-02", **{"段階": 2, "先行レーン": ["lane-01"]})],
                "初回配分": _allocation({"a.md": "lane-01", "b.md": "lane-02"}, 11),
            }
        ),
        encoding="utf-8",
    )
    old = yaml.safe_load(existing.read_text(encoding="utf-8"))
    record = _overlap("lane-01", "lane-02", "交わる")
    record["レーン1の定義"] = record["レーン2の定義"] = ["共通の契約"]
    old["レーン間の重なり"] = [record]
    existing.write_text(yaml.safe_dump(old, allow_unicode=True), encoding="utf-8")
    overlap = _overlap("lane-03", "lane-04", "交わる")
    overlap["レーン1の定義"] = overlap["レーン2の定義"] = ["共通の契約"]
    added.write_text(
        yaml.safe_dump(
            {
                "選定": [_item("c.md", "lane-03"), _item("d.md", "lane-04")],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [_cost("lane-03"), _cost("lane-04", **{"段階": 2, "先行レーン": ["lane-03"]})],
                "レーン間の重なり": [
                    overlap,
                    *[
                        {**record, "レーン1": left, "レーン2": right}
                        for left, right in (("lane-01", "lane-04"), ("lane-03", "lane-02"))
                    ],
                ],
                "単一段階案の完了見込み秒数": 30,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    output = tmp_path / "result.yaml"
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        == 0
    )
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert result["レーンの所要時間"][1]["先行レーン"] == ["lane-01"]
    assert result["選定"][3]["レーン"] == "lane-02"


def test_public_merge_applies_final_cost_updates_and_preserves_metadata(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
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
    updates = {
        "lane-01": {"実装秒数": 15.5, "統合秒数": 0, "補正前の実装秒数": 15.5, "補正前の統合秒数": 0, "根拠": "再見積りを確定"}
    }
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps(updates, ensure_ascii=False), encoding="utf-8")
    before = {path: path.read_bytes() for path in (existing, added, mapping, costs)}
    output = tmp_path / "output.yaml"
    assert _merge_with_costs(repo, existing, added, mapping, costs, output) == 0
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert result["選定"][:2] == originals[:2]
    for item, original in zip(result["選定"][2:], originals[2:], strict=True):
        assert item == {**original, "レーン": {"lane-03": "lane-01", "lane-04": "lane-02"}[original["レーン"]]}
    assert result["レーンの所要時間"][0] == {"レーン": "lane-01", "先行レーン": [], "統合状態": "未統合", **updates["lane-01"]}
    assert result["レーンの所要時間"][1]["実装秒数"] == 20
    assert all(path.read_bytes() == value for path, value in before.items())

    (repo / "EXTRA.md").write_text("# 別の反映先\n", encoding="utf-8")
    (repo.parent / "notes/processing/e.md").write_text(
        "---\ntype: awi\n---\n\n## 反映内容と反映先\n\n`EXTRA.md`を変える。\n", encoding="utf-8"
    )
    additional = tmp_path / "additional.yaml"
    additional.write_text(
        yaml.safe_dump(
            {
                "選定": [{**_item("e.md", "lane-03"), "書込対象": ["EXTRA.md"]}],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [_cost("lane-03")],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    second_map = tmp_path / "second-map.json"
    second_map.write_text(json.dumps({"lane-03": "lane-01"}), encoding="utf-8")
    second_costs = tmp_path / "second-costs.json"
    final_cost = {"実装秒数": 11, "統合秒数": 1, "補正前の実装秒数": 11, "補正前の統合秒数": 1, "根拠": "既存レーンの確定値"}
    second_costs.write_text(json.dumps({"lane-01": final_cost}, ensure_ascii=False), encoding="utf-8")
    assert _merge_with_costs(repo, output, additional, second_map, second_costs, output) == 0
    final = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert final["選定"][:4] == result["選定"]
    assert [item["WI"] for item in final["選定"]] == ["a.md", "b.md", "c.md", "d.md", "e.md"]
    assert final["選定"][4]["レーン"] == "lane-01"
    assert final["レーンの所要時間"][1] == result["レーンの所要時間"][1]
    assert final["レーンの所要時間"][0] == {"レーン": "lane-01", "先行レーン": [], "統合状態": "未統合", **final_cost}
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
    repo, existing, added = inputs
    original: dict[str, object] = {
        "選定": [_item("a.md", "lane-01"), _item("b.md", "lane-02")],
        "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
        "レーンの所要時間": [_cost("lane-01"), _cost("lane-02", **{"段階": 2, "先行レーン": ["lane-01"]})],
    }
    original["初回配分"] = _allocation({"a.md": "lane-01", "b.md": "lane-02"}, 11)
    if existing_comparison is not None:
        original["単一段階案の完了見込み秒数"] = existing_comparison
    existing.write_text(yaml.safe_dump(original), encoding="utf-8")
    old = yaml.safe_load(existing.read_text(encoding="utf-8"))
    record = _overlap("lane-01", "lane-02", "交わる")
    record["レーン1の定義"] = record["レーン2の定義"] = ["共通の契約"]
    old["レーン間の重なり"] = [record]
    existing.write_text(yaml.safe_dump(old, allow_unicode=True), encoding="utf-8")
    overlap = _overlap("lane-03", "lane-04", "交わる")
    overlap["レーン1の定義"] = overlap["レーン2の定義"] = ["共通の契約"]
    data: dict[str, object] = {
        "選定": [_item("c.md", "lane-03"), _item("d.md", "lane-04")],
        "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
        "レーンの所要時間": [_cost("lane-03"), _cost("lane-04", **{"段階": 2, "先行レーン": ["lane-03"]})],
        "レーン間の重なり": [
            overlap,
            *[
                {**record, "レーン1": left, "レーン2": right}
                for left, right in (("lane-01", "lane-04"), ("lane-03", "lane-02"))
            ],
        ],
    }
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
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


@pytest.mark.parametrize("reassign", [False, True])
@pytest.mark.parametrize("limited_body", [False, True])
def test_public_merge_preserves_created_derived_paths(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    reassign: bool,
    limited_body: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, existing, added = inputs
    (repo / "src").mkdir()
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    for index, item in enumerate(original["選定"]):
        name = f"src/save{index}.py"
        section = "書込対象" if index == 0 else "公開工程の書込対象"
        item["書込対象"] = []
        item[section] = [name]
        if section == "公開工程の書込対象":
            (repo / "RULES.md").write_text("# 規範\n\n## 公開\n", encoding="utf-8")
            item["公開工程の規範根拠"] = [{"パス": name, "規範ファイル": "RULES.md", "節": "公開"}]
        item["導出した新設先"] = [{"パス": name, "反映範囲": "src/", "要求": "結果を保存する", "配置根拠": "保存用入口の配置"}]
        (repo / name).write_text("# 保存入口\n", encoding="utf-8")
        (repo.parent / "notes/processing" / item["WI"]).write_text(
            "---\ntype: awi\n---\n\n## 反映内容と反映先\n\n`src/`へ保存用入口を新設する。\n", encoding="utf-8"
        )
    for cost in original["レーンの所要時間"]:
        cost["統合状態"] = "統合済み"
        cost["根拠"] = "対象リポジトリの規範AGENTS.mdの公開の節でsrc/save1.pyを所有する"
    original["レーン間の重なり"] = []
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    addition["レーン間の重なり"] = [_overlap("lane-03", "lane-04")]
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(
        json.dumps({"lane-03": "lane-01" if reassign else "lane-02", "lane-04": "lane-01" if reassign else "lane-02"}),
        encoding="utf-8",
    )
    extra = ["--body-wi", "c.md", "--body-wi", "d.md"] if limited_body else []
    assert _merge_into_existing(repo, existing, added, mapping, *extra) == 0
    result = yaml.safe_load(existing.read_text(encoding="utf-8"))
    for item, old in zip(result["選定"][:2], original["選定"], strict=True):
        assert item["導出した新設先"] == old["導出した新設先"]
        assert item["書込対象"] == old["書込対象"]
        assert item["公開工程の書込対象"] == old.get("公開工程の書込対象", [])
    states = {cost["レーン"]: cost["統合状態"] for cost in result["レーンの所要時間"]}
    assert states["lane-01"] == ("未統合" if reassign else "統合済み")
    assert states["lane-02"] == ("統合済み" if reassign else "未統合")
    target = "lane-01" if reassign else "lane-02"
    assert states[target] == "未統合"
    assert _dispatch(str(existing), "--work-dir", str(repo)) == 0
    assert _dispatch(str(existing), "--mark-integrated", target, "--output", str(existing), "--work-dir", str(repo)) == 0
    final = yaml.safe_load(existing.read_text(encoding="utf-8"))
    assert next(cost for cost in final["レーンの所要時間"] if cost["レーン"] == target)["統合状態"] == "統合済み"

    assert all(item["統合状態"] == "統合済み" for item in final["選定"] if item["レーン"] == target)
    assert [{key: value for key, value in item.items() if key != "統合状態"} for item in final["選定"]] == [
        {key: value for key, value in item.items() if key != "統合状態"} for item in result["選定"]
    ]
    assert not capsys.readouterr().err


def test_public_merge_rejects_derived_directory_without_saving(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, existing, added = inputs
    (repo / "src/save.py").mkdir(parents=True)
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    original["選定"][0].update(
        {
            "書込対象": ["src/save.py"],
            "導出した新設先": [{"パス": "src/save.py", "反映範囲": "src/", "要求": "保存入口", "配置根拠": "保存責務"}],
        }
    )
    (repo.parent / "notes/processing/a.md").write_text(
        "---\ntype: awi\n---\n\n## 反映内容と反映先\n\n`src/`へ保存用入口を新設する。\n", encoding="utf-8"
    )
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    before = existing.read_bytes()
    assert _merge_into_existing(repo, existing, added, mapping) == 1
    assert "導出した新設先" in capsys.readouterr().err
    assert existing.read_bytes() == before


def test_public_merge_preserves_derived_new_paths(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
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
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    output = tmp_path / "whole.yaml"
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        == 0
    )
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert typing.cast(list[dict[str, object]], result["選定"])[2:] == [
        {**item, "レーン": {"lane-03": "lane-01", "lane-04": "lane-02"}[item["レーン"]], "公開工程の書込対象": []}
        for item in data["選定"]
    ]
    assert _dispatch(str(output), "--work-dir", str(repo)) == 0


def test_merge_rejudges_new_pair_under_existing_record(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, existing, added = inputs
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    addition["選定"] = [_item("c.md", "lane-03")]
    addition["レーンの所要時間"] = [_cost("lane-03")]
    addition["レーン間の重なり"] = []
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01"}), encoding="utf-8")
    before = existing.read_bytes()
    capsys.readouterr()
    assert _merge_into_existing(repo, existing, added, mapping) == 1
    err = capsys.readouterr().err
    assert "lane-01とlane-02: 重複パスの記録不足: README.md（未判定の組: b.md（lane-02）とc.md（lane-03））" in err
    assert "表示のレーン名は合流後の名前" in err and "追加WIの側を追加YAMLのレーン名（仮名）" in err
    assert existing.read_bytes() == before
    addition["レーン間の重なり"] = [_overlap("lane-02", "lane-03")]
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    assert _merge_into_existing(repo, existing, added, mapping) == 0
    assert yaml.safe_load(existing.read_text(encoding="utf-8"))["レーン間の重なり"] == [
        {
            **_overlap("lane-01", "lane-02"),
            "レーン1の定義": ["lane-01の節", "lane-03の節"],
            "判定済みWIの組": [["a.md", "b.md"], ["b.md", "c.md"]],
        }
    ]


def test_merge_with_same_name_temporary_lane_judges_existing_members(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    repo, existing, added = inputs
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    addition["選定"] = [_item("c.md", "lane-01"), _item("d.md", "lane-02")]
    addition["レーンの所要時間"] = [_cost("lane-01"), _cost("lane-02")]
    addition["レーン間の重なり"] = [_overlap("lane-01", "lane-02")]
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-01": "lane-01", "lane-02": "lane-02"}), encoding="utf-8")
    assert _merge_into_existing(repo, existing, added, mapping) == 0
    assert yaml.safe_load(existing.read_text(encoding="utf-8"))["レーン間の重なり"] == [
        {
            **_overlap("lane-01", "lane-02"),
            "判定済みWIの組": [["a.md", "b.md"], ["a.md", "d.md"], ["b.md", "c.md"], ["c.md", "d.md"]],
        }
    ]


def test_merge_preserves_item_integration_state(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    repo, existing, added = inputs
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    original["レーンの所要時間"][0]["統合状態"] = "統合済み"
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    addition["選定"][0]["統合状態"] = "統合済み"
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    output = tmp_path / "whole.yaml"
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        == 0
    )
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert {row["レーン"]: row["統合状態"] for row in result["レーンの所要時間"]} == {"lane-01": "未統合", "lane-02": "未統合"}
    items = {item["WI"]: item.get("統合状態", "未統合") for item in result["選定"]}
    assert items == {"a.md": "統合済み", "b.md": "未統合", "c.md": "未統合", "d.md": "未統合"}
