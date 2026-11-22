# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import json
import pathlib
import typing

import pytest
import yaml
from agent_toolkit._testing.selection_check_support import (
    _awi,
    _dispatch,
    _initial_allocation,
    _write_selection,
)
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env

_COST = {
    "実装秒数": 300,
    "統合秒数": 30,
    "補正前の実装秒数": 300,
    "補正前の統合秒数": 30,
    "根拠": "移管後の再見積もり",
}


def _selection(
    tmp_path: pathlib.Path,
    repo: pathlib.Path,
    notes: pathlib.Path,
    *,
    edit: typing.Callable[[dict[str, typing.Any]], None] | None = None,
) -> pathlib.Path:
    """初回a.md（lane-01）と、追加b.md・c.md（lane-02）、追加d.md（lane-03）を持つ選定を保存する。"""
    decisions = []
    for index, (name, lane) in enumerate((("a.md", "lane-01"), ("b.md", "lane-02"), ("c.md", "lane-02"), ("d.md", "lane-03"))):
        target = f"target-{index}.md"
        (repo / target).write_text("x", encoding="utf-8")
        _awi(notes, name, f"`{target}`を変える。")
        decisions.append({"WI": name, "レーン": lane, "書込対象": [target]})
    path = _write_selection(tmp_path / "selection.yaml", decisions)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["初回配分"] = _initial_allocation(decisions[:1])
    data["初回配分"]["候補WI"] += [f"candidate-{i}.md" for i in range(20)]
    data["追加WI"] = ["b.md", "c.md", "d.md"]
    if edit is not None:
        edit(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _transfer(
    path: pathlib.Path, repo: pathlib.Path, tmp_path: pathlib.Path, wi: str, lane: str, updates: object, *extra: str
) -> int:
    cost_file = tmp_path / "updates.json"
    cost_file.write_text(json.dumps(updates), encoding="utf-8")
    return _dispatch(
        str(path), "--transfer-wi", wi, "--to-lane", lane, "--lane-cost-updates", str(cost_file), "--output", str(path),
        "--work-dir", str(repo), *extra,
    )  # fmt: skip


def _overlap(left: str, right: str, left_definitions: list[str], right_definitions: list[str]) -> dict[str, object]:
    return {
        "レーン1": left,
        "レーン2": right,
        "共通パス": "README.md",
        "レーン1の定義": left_definitions,
        "レーン2の定義": right_definitions,
        "判定": "交わらない",
    }


def _share_readme(notes: pathlib.Path, data: dict[str, typing.Any], names: set[str]) -> None:
    """指定WIの書込対象と本文へ、共通パス`README.md`を加える。"""
    for index, item in enumerate(data["選定"]):
        if item["WI"] in names:
            item["書込対象"].append("README.md")
            _awi(notes, item["WI"], f"`target-{index}.md`と`README.md`を変える。")


def test_transfer_records_new_overlap_with_extend_write_targets(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    repo, notes = env
    path = _selection(tmp_path, repo, notes, edit=lambda data: _share_readme(notes, data, {"b.md", "c.md"}))
    before = path.read_bytes()
    updates = {"lane-02": _COST, "lane-03": _COST}
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", updates) == 1
    assert path.read_bytes() == before
    addition = tmp_path / "overlaps.yaml"
    record = _overlap("lane-02", "lane-03", ["bの節"], ["cの節"])
    addition.write_text(yaml.safe_dump({"レーン間の重なり": [record]}, allow_unicode=True), encoding="utf-8")
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", updates, "--extend-write-targets", str(addition)) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert next(item["レーン"] for item in after["選定"] if item["WI"] == "c.md") == "lane-03"
    assert after["レーン間の重なり"] == [{**record, "判定済みWIの組": [["b.md", "c.md"]]}]


@pytest.mark.parametrize("case", ["emptied", "partner", "merged"])
def test_transfer_retargets_overlap_records(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, case: str) -> None:
    repo, notes = env
    sharing, existing, wi, lane, updated, expected = {
        "emptied": (
            {"b.md", "d.md"},
            [_overlap("lane-02", "lane-03", ["bの節"], ["dの節"])],
            "d.md",
            "lane-02",
            ["lane-02"],
            [],
        ),
        "partner": (
            {"a.md", "c.md"},
            [_overlap("lane-01", "lane-02", ["aの節"], ["cの節"])],
            "c.md",
            "lane-03",
            ["lane-02", "lane-03"],
            [_overlap("lane-01", "lane-03", ["aの節"], ["cの節"])],
        ),
        "merged": (
            {"a.md", "c.md", "d.md"},
            [
                _overlap("lane-01", "lane-02", ["aの節"], ["cの節"]),
                _overlap("lane-01", "lane-03", ["aの節"], ["dの節"]),
                _overlap("lane-02", "lane-03", ["cの節"], ["dの節"]),
            ],
            "c.md",
            "lane-03",
            ["lane-02", "lane-03"],
            [_overlap("lane-01", "lane-03", ["aの節"], ["cの節", "dの節"])],
        ),
    }[case]

    def edit(data: dict[str, typing.Any]) -> None:
        _share_readme(notes, data, sharing)
        data["レーン間の重なり"] = existing

    path = _selection(tmp_path, repo, notes, edit=edit)
    assert _transfer(path, repo, tmp_path, wi, lane, dict.fromkeys(updated, _COST)) == 0
    if expected:
        expected[0]["判定済みWIの組"] = [["a.md", "c.md"]] + ([["a.md", "d.md"]] if case == "merged" else [])
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["レーン間の重なり"] == expected


@pytest.mark.parametrize("case", ["new-pair-under-existing-record", "split-record"])
def test_transfer_drops_records_whose_wi_pairs_change(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    repo, notes = env
    sharing, existing, rejudged = {
        "new-pair-under-existing-record": (
            {"b.md", "c.md", "d.md"},
            [_overlap("lane-02", "lane-03", ["節X"], ["dの節"])],
            [_overlap("lane-02", "lane-03", ["bの節"], ["cの節", "dの節"])],
        ),
        "split-record": (
            {"a.md", "b.md", "c.md"},
            [_overlap("lane-01", "lane-02", ["aの節"], ["bの節", "cの節"])],
            [
                _overlap("lane-01", "lane-02", ["aの節"], ["bの節"]),
                _overlap("lane-01", "lane-03", ["aの節"], ["cの節"]),
                _overlap("lane-02", "lane-03", ["bの節"], ["cの節"]),
            ],
        ),
    }[case]

    def edit(data: dict[str, typing.Any]) -> None:
        _share_readme(notes, data, sharing)
        data["レーン間の重なり"] = existing

    path = _selection(tmp_path, repo, notes, edit=edit)
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    before = path.read_bytes()
    updates = {"lane-02": _COST, "lane-03": _COST}
    capsys.readouterr()
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", updates) == 1
    err = capsys.readouterr().err
    assert all(f"{r['レーン1']}と{r['レーン2']}: 重複パスの記録不足: README.md（未判定の組: " in err for r in rejudged)
    assert "--extend-write-targets" in err and "移管後のレーン名" in err
    assert {
        "new-pair-under-existing-record": "（未判定の組: b.md（lane-02）とc.md（lane-03）、b.md（lane-02）とd.md（lane-03））",
        "split-record": "lane-01とlane-02: 重複パスの記録不足: README.md（未判定の組: a.md（lane-01）とb.md（lane-02））",
    }[case] in err
    assert path.read_bytes() == before
    addition = tmp_path / "overlaps.yaml"
    addition.write_text(yaml.safe_dump({"レーン間の重なり": rejudged}, allow_unicode=True), encoding="utf-8")
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", updates, "--extend-write-targets", str(addition)) == 0
    pairs = (
        [[["b.md", "c.md"], ["b.md", "d.md"]]]
        if case == "new-pair-under-existing-record"
        else [[["a.md", "b.md"]], [["a.md", "c.md"]], [["b.md", "c.md"]]]
    )
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["レーン間の重なり"] == [
        {**row, "判定済みWIの組": judged} for row, judged in zip(rejudged, pairs, strict=True)
    ]


def test_transfer_moves_added_wi_and_updates_costs(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    repo, notes = env
    path = _selection(tmp_path, repo, notes)
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", {"lane-02": _COST, "lane-03": _COST}) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert {item["WI"]: item["レーン"] for item in after["選定"]} == {
        "a.md": "lane-01",
        "b.md": "lane-02",
        "c.md": "lane-03",
        "d.md": "lane-03",
    }
    assert after["初回配分"] == before["初回配分"]
    assert after["追加WI"] == before["追加WI"]
    rows = {row["レーン"]: row for row in after["レーンの所要時間"]}
    assert rows["lane-03"]["実装秒数"] == 300 and rows["lane-03"]["統合状態"] == "未統合"


def test_transfer_into_integrated_lane_keeps_existing_items_integrated(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    repo, notes = env

    def edit(data: dict[str, typing.Any]) -> None:
        next(row for row in data["レーンの所要時間"] if row["レーン"] == "lane-03")["統合状態"] = "統合済み"

    path = _selection(tmp_path, repo, notes, edit=edit)
    updates = {"lane-02": _COST, "lane-03": _COST}
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", updates) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert {item["WI"]: item.get("統合状態", "未統合") for item in after["選定"]} == {
        "a.md": "未統合",
        "b.md": "未統合",
        "c.md": "未統合",
        "d.md": "統合済み",
    }
    assert next(row for row in after["レーンの所要時間"] if row["レーン"] == "lane-03")["統合状態"] == "未統合"
    before = path.read_bytes()
    assert _transfer(path, repo, tmp_path, "d.md", "lane-02", updates) == 2
    assert path.read_bytes() == before


def test_transfer_into_integrated_lane_carries_record_beside_unrecorded_integrated_pair(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """移管先の統合済み項目との組は全体検査が記録を求めないため、判定済みの組の記録の付け替えを妨げない。

    統合済みのlane-03のdはaと`README.md`を共有するが、a-dの記録は無い。a-cの記録はcの移管先へ付け替わる。
    """
    repo, notes = env

    def edit(data: dict[str, typing.Any]) -> None:
        _share_readme(notes, data, {"a.md", "c.md", "d.md"})
        next(row for row in data["レーンの所要時間"] if row["レーン"] == "lane-03")["統合状態"] = "統合済み"
        data["レーン間の重なり"] = [_overlap("lane-01", "lane-02", ["aの節"], ["cの節"])]

    path = _selection(tmp_path, repo, notes, edit=edit)
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", {"lane-02": _COST, "lane-03": _COST}) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["レーン間の重なり"] == [
        {**_overlap("lane-01", "lane-03", ["aの節"], ["cの節"]), "判定済みWIの組": [["a.md", "c.md"]]}
    ]

    before = path.read_bytes()
    assert _dispatch(str(path), "--work-dir", str(repo), "--reopen-wi", "d.md", "--output", str(path)) == 1
    assert path.read_bytes() == before

    record = after["レーン間の重なり"][0]
    record["レーン2の定義"].append("dの節")
    record["判定済みWIの組"].append(["a.md", "d.md"])
    addition = tmp_path / "decide-before-reopen.yaml"
    addition.write_text(yaml.safe_dump({"レーン間の重なり": [record]}, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path)) == 0
    decided = path.read_bytes()
    del record["判定済みWIの組"]
    addition.write_text(yaml.safe_dump({"レーン間の重なり": [record]}, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path)) == 0
    assert path.read_bytes() == decided
    assert _dispatch(str(path), "--work-dir", str(repo), "--reopen-wi", "d.md", "--output", str(path)) == 0


def test_transfer_drops_emptied_source_lane(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    repo, notes = env
    path = _selection(tmp_path, repo, notes)
    assert _transfer(path, repo, tmp_path, "d.md", "lane-02", {"lane-03": _COST}) == 2
    assert _transfer(path, repo, tmp_path, "d.md", "lane-02", {"lane-02": _COST}) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [row["レーン"] for row in after["レーンの所要時間"]] == ["lane-01", "lane-02"]


@pytest.mark.parametrize(("final_lane", "code"), [("lane-04", 1), ("lane-03", 0)])
def test_emptied_source_lane_still_counts_toward_cumulative_bound(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    final_lane: str,
    code: int,
) -> None:
    repo, notes = env
    path = _selection(tmp_path, repo, notes)
    assert _transfer(path, repo, tmp_path, "d.md", "lane-02", {"lane-02": _COST}) == 0
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert next(item for item in saved["選定"] if item["WI"] == "d.md")["移管元レーン"] == ["lane-03"]
    (repo / "target-4.md").write_text("x", encoding="utf-8")
    _awi(notes, "e.md", "`target-4.md`を変える。")
    addition = tmp_path / "addition.yaml"
    addition.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {
                        "WI": "e.md",
                        "レーン": "lane-new",
                        "鮮度": {"status": "current", "later_commit_count": 0},
                        "書込対象": ["target-4.md"],
                    }
                ],
                "レーンの所要時間": [{"レーン": "lane-new", **_COST}],
                "見込みの補正": saved["見込みの補正"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    lane_map = tmp_path / "lane-map.json"
    lane_map.write_text(json.dumps({"lane-new": final_lane}), encoding="utf-8")
    before = path.read_bytes()
    capsys.readouterr()
    assert (
        _dispatch(
            str(path), "--merge", str(addition), "--lane-map", str(lane_map), "--output", str(path), "--work-dir", str(repo)
        )
        == code
    )
    if code:
        assert "上限B=3、実レーン数=4" in capsys.readouterr().err
        assert path.read_bytes() == before
    else:
        lanes = [row["レーン"] for row in yaml.safe_load(path.read_text(encoding="utf-8"))["レーンの所要時間"]]
        assert lanes == ["lane-01", "lane-02", "lane-03"]


@pytest.mark.parametrize(
    "case", ["initial", "integrated", "frozen-plan", "unknown-wi", "same-lane", "unknown-lane", "no-update"]
)
def test_transfer_rejects_ineligible_and_keeps_file(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, case: str
) -> None:
    repo, notes = env

    def edit(data: dict[str, typing.Any]) -> None:
        if case == "integrated":
            data["選定"][2]["統合状態"] = "統合済み"
        if case == "frozen-plan":
            data["選定"][2]["再開位置"] = str(tmp_path / "plans" / "old-plan.md")

    path = _selection(tmp_path, repo, notes, edit=edit)
    before = path.read_bytes()
    wi, lane = {"initial": ("a.md", "lane-02"), "unknown-wi": ("zzz.md", "lane-02"), "same-lane": ("c.md", "lane-02")}.get(
        case, ("c.md", "lane-03")
    )
    if case == "unknown-lane":
        lane = "lane-99"
    updates = {} if case == "no-update" else {"lane-02": _COST, lane: _COST}
    assert _transfer(path, repo, tmp_path, wi, lane, updates) == 2
    assert path.read_bytes() == before


def test_transfer_keeps_whole_check(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    """移管後の全体検査（書込対象の競合）に違反する移管は拒否し、保存しない。"""
    repo, notes = env

    def edit(data: dict[str, typing.Any]) -> None:
        data["選定"][2]["書込対象"] = ["target-3.md"]
        data["レーンの所要時間"][2].update({"段階": 2, "先行レーン": ["lane-01"]})
        data["単一段階案の完了見込み秒数"] = 1320

    path = _selection(tmp_path, repo, notes, edit=edit)
    before = path.read_bytes()
    assert _transfer(path, repo, tmp_path, "c.md", "lane-03", {"lane-02": _COST, "lane-03": _COST}) != 0
    assert path.read_bytes() == before


def test_merge_still_rejects_duplicate_wi(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    repo, notes = env
    path = _selection(tmp_path, repo, notes)
    addition = tmp_path / "addition.yaml"
    addition.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    lane_map = tmp_path / "lane-map.json"
    lane_map.write_text(json.dumps({"lane-01": "lane-01", "lane-02": "lane-02", "lane-03": "lane-03"}), encoding="utf-8")
    before = path.read_bytes()
    code = _dispatch(
        str(path), "--merge", str(addition), "--lane-map", str(lane_map), "--output", str(path), "--work-dir", str(repo)
    )
    assert code == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("extra", [["--reopen-wi", "b.md"], ["--body-wi", "c.md"], ["--mark-integrated", "lane-01"]])
def test_transfer_rejects_combined_operations(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, extra: list[str]
) -> None:
    repo, notes = env
    path = _selection(tmp_path, repo, notes)
    cost_file = tmp_path / "updates.json"
    cost_file.write_text(json.dumps({"lane-02": _COST, "lane-03": _COST}), encoding="utf-8")
    before = path.read_bytes()
    assert _dispatch(
            str(path), "--transfer-wi", "c.md", "--to-lane", "lane-03", "--lane-cost-updates", str(cost_file),
            "--output", str(path), "--work-dir", str(repo), *extra,
    ) == 2  # fmt: skip
    assert path.read_bytes() == before


__all__ = ["fixture_env"]
