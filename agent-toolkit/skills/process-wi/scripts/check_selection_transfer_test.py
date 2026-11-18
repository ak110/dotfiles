# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""追加済みで未着手のWIを別レーンへ移す操作を検証する。"""

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
    """移管で移管元に残るWIと新たに重なる組は、同じ呼び出しの--extend-write-targetsで記録して保存できる。"""
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
    assert after["レーン間の重なり"] == [record]


@pytest.mark.parametrize("case", ["emptied", "partner", "kept"])
def test_transfer_retargets_overlap_records(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, case: str) -> None:
    """移したWIの重なりの記録を移管先へ付け替え、同じレーン同士・重ならなくなった組の記録を除く。"""
    repo, notes = env
    sharing, existing, wi, lane, updated = {
        "emptied": ({"b.md", "d.md"}, _overlap("lane-02", "lane-03", ["bの節"], ["dの節"]), "d.md", "lane-02", ["lane-02"]),
        "partner": (
            {"a.md", "c.md"},
            _overlap("lane-01", "lane-02", ["aの節"], ["cの節"]),
            "c.md",
            "lane-03",
            ["lane-02", "lane-03"],
        ),
        "kept": (
            {"a.md", "b.md", "c.md"},
            _overlap("lane-01", "lane-02", ["aの節"], ["bの節", "cの節"]),
            "c.md",
            "lane-03",
            ["lane-02", "lane-03"],
        ),
    }[case]

    def edit(data: dict[str, typing.Any]) -> None:
        _share_readme(notes, data, sharing)
        data["レーン間の重なり"] = [existing]

    path = _selection(tmp_path, repo, notes, edit=edit)
    extra: list[str] = []
    if case == "kept":
        addition = tmp_path / "overlaps.yaml"
        new = _overlap("lane-02", "lane-03", ["bの節"], ["cの節"])
        addition.write_text(yaml.safe_dump({"レーン間の重なり": [new]}, allow_unicode=True), encoding="utf-8")
        extra = ["--extend-write-targets", str(addition)]
    assert _transfer(path, repo, tmp_path, wi, lane, dict.fromkeys(updated, _COST), *extra) == 0
    records = yaml.safe_load(path.read_text(encoding="utf-8"))["レーン間の重なり"]
    if case == "emptied":
        assert records == []
    elif case == "partner":
        assert records == [_overlap("lane-01", "lane-03", ["aの節"], ["cの節"])]
    else:
        assert existing in records
        assert sorted((record["レーン1"], record["レーン2"]) for record in records) == [
            ("lane-01", "lane-02"),
            ("lane-01", "lane-03"),
            ("lane-02", "lane-03"),
        ]


def test_transfer_moves_added_wi_and_updates_costs(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    """追加済みで未着手のWIを移し、所要時間・未統合状態を更新し、初回配分を保つ。"""
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


def test_transfer_drops_emptied_source_lane(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    """移管元が空になるときはそのレーン行を除き、移管先の費用更新だけを求める。"""
    repo, notes = env
    path = _selection(tmp_path, repo, notes)
    assert _transfer(path, repo, tmp_path, "d.md", "lane-02", {"lane-03": _COST}) == 2
    assert _transfer(path, repo, tmp_path, "d.md", "lane-02", {"lane-02": _COST}) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [row["レーン"] for row in after["レーンの所要時間"]] == ["lane-01", "lane-02"]


@pytest.mark.parametrize(
    "case", ["initial", "integrated", "frozen-plan", "unknown-wi", "same-lane", "unknown-lane", "no-update"]
)
def test_transfer_rejects_ineligible_and_keeps_file(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, case: str
) -> None:
    """対象外のWI・不正な移管先・費用更新の欠落は終了コード2で拒否し、保存済み選定を変えない。"""
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
    """再mergeでの二重追加の拒否は、移管操作の追加後も保たれる。"""
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
    """移管は他の更新操作・--body-wiと併用できない。"""
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
