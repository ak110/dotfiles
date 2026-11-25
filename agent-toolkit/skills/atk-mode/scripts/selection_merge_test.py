"""公開された選定の統合操作が保存する結果と、失敗時に原本を保持することを検証する。"""

import argparse
import io
import json
import pathlib

import pytest
import yaml
from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.selection_check_support import additional_active_items


def _item(wi: str, lane: str) -> dict[str, object]:
    return {"WI": wi, "レーン": lane, "鮮度": {"status": "current", "later_commit_count": 0}, "書込対象": ["README.md"]}


def _cost(lane: str, **extra: object) -> dict[str, object]:
    return {
        "レーン": lane,
        "実装秒数": 10,
        "統合秒数": 2,
        "補正前の実装秒数": 10,
        "補正前の統合秒数": 2,
        "根拠": lane + "の根拠",
        **extra,
    }


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
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# 共有文書\n", encoding="utf-8")
    git_repository.init_repository(repo, origin="https://github.com/test/repo.git")
    notes = tmp_path / "notes" / "processing"
    notes.mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes.parent))
    for wi in ("a.md", "b.md", "c.md", "d.md"):
        (notes / wi).write_text(
            "---\ntype: awi\n---\n# 変更\n\n## 反映内容と反映先\n\n`README.md`を変える。\n", encoding="utf-8"
        )
    active = additional_active_items(repo, notes.parent, "lane-01", 9)
    existing = tmp_path / "existing.yaml"
    added = tmp_path / "added.yaml"
    existing.write_text(
        yaml.safe_dump(
            {
                "選定": [_item("a.md", "lane-01"), _item("b.md", "lane-02"), *active],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [_cost("lane-01"), _cost("lane-02")],
                "レーン間の重なり": [_overlap("lane-01", "lane-02")],
                "初回配分": _allocation({"a.md": "lane-01", "b.md": "lane-02", **{row["WI"]: "lane-01" for row in active}}, 11),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    added.write_text(
        yaml.safe_dump(
            {
                "選定": [_item("c.md", "lane-03"), _item("d.md", "lane-04")],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
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


def test_public_merge_reads_judgments_and_mapping_from_stdin(inputs, tmp_path, capsys, monkeypatch):
    repo, existing, added = inputs
    old = yaml.safe_load(existing.read_bytes())
    old["選定"][1]["計画要否"] = "計画なし"
    existing.write_text(yaml.safe_dump(old, allow_unicode=True), encoding="utf-8")
    source = repo.parent / "notes/processing/a.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace("type: awi", "type: awi\nsource: add-awi-by-user"), encoding="utf-8"
    )
    data = yaml.safe_load(added.read_text(encoding="utf-8"))
    data.pop("見込みの補正")
    data["レーン対応"] = {"lane-03": "lane-01", "lane-04": "lane-02"}
    candidates = tmp_path / "existing-candidates.jsonl"
    data["除外候補"] = ["見送り.md"]
    records = [{"filename": item["WI"], "staleness": item.pop("鮮度"), "state": "processing"} for item in data["選定"]]
    records.append({"filename": "a.md", "staleness": {}, "state": "processing"})
    records.append({"filename": "見送り.md", "staleness": {}, "state": "processing"})
    candidates.write_text(
        "\n".join(map(json.dumps, records)),
        encoding="utf-8",
    )
    output = tmp_path / "merged.yaml"
    args = (
        str(existing),
        "--merge",
        "-",
        "--candidate-file",
        str(candidates),
        "--output",
        str(output),
        "--work-dir",
        str(repo),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(yaml.safe_dump(data, allow_unicode=True)))
    assert _dispatch(*args) == 0, capsys.readouterr().err
    assert "追加後の上限: N=13、上限B=2" in capsys.readouterr().out
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert {row["WI"]: row["レーン"] for row in result["選定"] if not row["WI"].startswith("active-")} == {
        "a.md": "lane-01",
        "b.md": "lane-02",
        "c.md": "lane-01",
        "d.md": "lane-02",
    }
    assert {row["WI"]: row["計画要否"] for row in result["選定"] if not row["WI"].startswith("active-")} == {
        "a.md": "計画なし",
        "b.md": "計画なし",
        "c.md": "計画あり",
        "d.md": "計画あり",
    }
    assert result["見込みの補正"] == yaml.safe_load(existing.read_bytes())["見込みの補正"]
    original = output.read_bytes()
    for record, diagnostic in (
        ({"filename": "a.md", "state": "inbox", "ready": True}, "処理可能な候補が選定にない: a.md"),
        ({"filename": "a.md", "state": "processing", "priority": "urgent"}, "a.md: urgentに優先の根拠が無い"),
        ({"filename": "別のWI.md", "state": "processing"}, "処理可能な候補が選定にない: 別のWI.md"),
    ):
        candidates.write_text(
            "\n".join(map(json.dumps, [*records[:2], records[-1], {"staleness": {}, **record}])),
            encoding="utf-8",
        )
        monkeypatch.setattr("sys.stdin", io.StringIO(yaml.safe_dump(data, allow_unicode=True)))
        assert _dispatch(*args) != 0
        assert diagnostic in capsys.readouterr().err
        assert output.read_bytes() == original
    candidates.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
    data["選定"][0]["WI"] = "供給されなかったWI.md"
    monkeypatch.setattr("sys.stdin", io.StringIO(yaml.safe_dump(data, allow_unicode=True)))
    assert _dispatch(*args) != 0
    assert output.read_bytes() == original


@pytest.mark.parametrize("existing_coefficient,coefficient", [(1, 1), (1, 2), (2, 2)])
def test_public_merge_correction_and_staged_comparison(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    existing_coefficient: int,
    coefficient: int,
) -> None:
    repo, existing, added = inputs
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    original["段階案の完了見込み秒数"] = 999
    original["見込みの補正"] = {"係数": existing_coefficient, "参照した記録": ["plans/previous.jsonl"], "除外した記録": []}
    for row in original["レーンの所要時間"]:
        row["実装秒数"] *= existing_coefficient
        row["統合秒数"] *= existing_coefficient
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    data = yaml.safe_load(added.read_text(encoding="utf-8"))
    data["見込みの補正"] = {"係数": coefficient, "参照した記録": ["plans/log.jsonl"], "除外した記録": []}
    for row in data["レーンの所要時間"]:
        row["補正前の実装秒数"] = 6001
        row["実装秒数"] = 6001 * coefficient
        row["統合秒数"] *= coefficient
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    output = tmp_path / "merged.yaml"
    output.write_text("保存済みの内容", encoding="utf-8")
    for comparison in (None, "成立する段階案なし"):
        if comparison is not None:
            data["段階案の完了見込み秒数"] = comparison
        added.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        before = output.read_bytes()
        result = _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        if coefficient != existing_coefficient:
            assert result != 0
            assert output.read_bytes() == before
            assert "係数が既存と異なる" in capsys.readouterr().err
        else:
            assert result == 0, capsys.readouterr().err
            merged = yaml.safe_load(output.read_text(encoding="utf-8"))
            assert merged["レーンの所要時間"][0]["補正前の実装秒数"] == 6011
            assert merged["レーンの所要時間"][0]["補正前の統合秒数"] == 4
            assert merged["レーンの所要時間"][0]["実装秒数"] == 6011 * coefficient
            assert merged.get("段階案の完了見込み秒数") == comparison
            assert _dispatch(str(output), "--history", "--work-dir", str(repo)) == 0


@pytest.mark.parametrize("limit_body", [False, True])
def test_public_merge_requires_processing_for_new_assignment(inputs, tmp_path, capsys, limit_body):
    repo, existing, added = inputs
    notes = tmp_path / "notes"
    inbox = notes / "inbox"
    inbox.mkdir()
    processing = notes / "processing" / "c.md"
    pending = inbox / processing.name
    processing.rename(pending)
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    before = existing.read_bytes()
    args = (
        str(existing),
        "--merge",
        str(added),
        "--lane-map",
        str(mapping),
        "--output",
        str(existing),
        "--work-dir",
        str(repo),
    )

    if limit_body:
        args += ("--body-wi", "d.md")
    assert _dispatch(*args) == 1
    diagnostic = capsys.readouterr().err
    assert "c.md: 追加割当の処理開始が未完了" in diagnostic
    assert "atk wi start-processing c.md" in diagnostic
    assert existing.read_bytes() == before and pending.is_file()
    pending.rename(processing)
    assert _dispatch(*args) == 0
    assert "c.md" in existing.read_text(encoding="utf-8")


def _allocation(assignments: dict[str, str], candidate_count: int = 1) -> dict[str, object]:
    return {
        "候補WI": [*assignments, *(f"unselected-{index}.md" for index in range(candidate_count - len(assignments)))],
        "レーン割当": assignments,
        "不可分成分": [{"WI": [name], "結合条件": []} for name in assignments],
    }


@pytest.mark.parametrize("case", ["room", "integrated", "empty", "empty-room", "body", "cost"])
def test_public_merge_accepts_new_final_lane_with_room(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    repo, existing, added = inputs
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    if case == "integrated":
        for row in original["レーンの所要時間"]:
            row["統合状態"] = "統合済み"
    if case in {"empty", "empty-room"}:
        original = {
            "選定": [],
            "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
            "レーンの所要時間": [],
            "初回配分": _allocation({}, 21 if case == "empty-room" else 0),
        }
        addition = yaml.safe_load(added.read_text(encoding="utf-8"))
        addition["レーン間の重なり"] = [_overlap("lane-03", "lane-04")]
        added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    if case != "empty":
        addition = yaml.safe_load(added.read_text(encoding="utf-8"))
        count = 9 if case in {"integrated", "empty-room"} else 8
        addition["選定"].extend(additional_active_items(repo, repo.parent / "notes", "lane-04", count))
        added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-99", "lane-04": "lane-01"}), encoding="utf-8")
    extra = ["--body-wi", "c.md", "--body-wi", "d.md"] if case == "body" else []
    if case == "cost":
        costs = tmp_path / "costs.json"
        costs.write_text(
            json.dumps(
                {
                    "lane-99": {
                        "実装秒数": 1,
                        "統合秒数": 1,
                        "補正前の実装秒数": 1,
                        "補正前の統合秒数": 1,
                        "根拠": "更新",
                    }
                }
            ),
            encoding="utf-8",
        )
        extra = ["--lane-cost-updates", str(costs)]
    before = {path: path.read_bytes() for path in (existing, added, mapping)}
    result = _merge_into_existing(repo, existing, added, mapping, *extra)
    if case == "empty":
        assert result == 1
        assert "上限" in capsys.readouterr().err
        assert all(path.read_bytes() == value for path, value in before.items())
    else:
        assert result == 0, capsys.readouterr().err
        merged = yaml.safe_load(existing.read_text(encoding="utf-8"))
        assert merged["初回配分"] == original["初回配分"]
        expected_lanes = {"lane-01", "lane-99"} if case == "empty-room" else {"lane-01", "lane-02", "lane-99"}
        assert {row["レーン"] for row in merged["レーンの所要時間"]} == expected_lanes
        assert merged["追加WI"] == [row["WI"] for row in yaml.safe_load(added.read_text(encoding="utf-8"))["選定"]]
        assert added.read_bytes() == before[added] and mapping.read_bytes() == before[mapping]
        assert _dispatch(str(existing), "--work-dir", str(repo)) == 0


@pytest.mark.parametrize("case", ["limit", "resume", "overlap"])
def test_new_final_lane_keeps_whole_selection_constraints(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    repo, existing, added = inputs
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    if case != "limit":
        addition["選定"].extend(additional_active_items(repo, repo.parent / "notes", "lane-04", 8))
    if case == "resume":
        original["選定"][0]["再開位置"] = "/plans/shared.md"
        addition["選定"][0]["再開位置"] = "/plans/shared.md"
    if case == "overlap":
        for record in addition["レーン間の重なり"]:
            if "lane-03" in (record["レーン1"], record["レーン2"]):
                record["判定"] = "交わる"
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-99", "lane-04": "lane-01"}), encoding="utf-8")
    before = {path: path.read_bytes() for path in (existing, added, mapping)}
    assert _merge_into_existing(repo, existing, added, mapping) == 1
    expected = {"limit": "上限", "resume": "同じ再開計画", "overlap": "同じ段階で定義が交わる"}[case]
    assert expected in capsys.readouterr().err
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize("reassign", [False, True])
def test_merge_resets_reassigned_lane_state(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    reassign: bool,
) -> None:
    repo, existing, added = inputs
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    for row in original["レーンの所要時間"]:
        row["統合状態"] = "統合済み"
    if reassign:
        for record in original["レーン間の重なり"]:
            record["判定"] = "交わる"
            record["レーン1の定義"] = record["レーン2の定義"] = ["共通の契約"]
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    data = yaml.safe_load(added.read_text(encoding="utf-8"))
    active = additional_active_items(repo, repo.parent / "notes", "lane-03", 9)
    data["選定"].extend(active)
    for record in data["レーン間の重なり"]:
        if reassign:
            record["判定"] = "交わる"
            record["レーン1の定義"] = record["レーン2の定義"] = ["共通の契約"]
    added.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    output = tmp_path / "whole.yaml"

    before = existing.read_bytes()
    expected = 1 if reassign else 0
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
        )
        == expected
    )
    assert existing.read_bytes() == before
    if not reassign:
        result = yaml.safe_load(output.read_text(encoding="utf-8"))
        assert result["初回配分"] == original["初回配分"]
        assert result["追加WI"] == ["c.md", "d.md", *(item["WI"] for item in active)]
        assert _dispatch(str(output), "--work-dir", str(repo)) == 0


def test_merge_preserves_initial_allocation_and_resets_only_destination(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    repo, existing, added = inputs
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    for row in original["レーンの所要時間"]:
        row["統合状態"] = "統合済み"
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-01"}), encoding="utf-8")
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(existing), "--work-dir", str(repo)
        )
        == 0
    )
    result = yaml.safe_load(existing.read_text(encoding="utf-8"))
    states = {row["レーン"]: row["統合状態"] for row in result["レーンの所要時間"]}
    assert states == {"lane-01": "未統合", "lane-02": "統合済み"}
    assert result["初回配分"] == original["初回配分"]
    assert _dispatch(str(existing), "--work-dir", str(repo)) == 0


def test_merge_cannot_hide_initial_limit_violation(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """追加後の件数やレーン名を初回の算定へ使わず、失敗時に保存先を保持する。"""
    repo, existing, added = inputs
    original = yaml.safe_load(existing.read_text(encoding="utf-8"))
    original["初回配分"]["候補WI"] = [row["WI"] for row in original["選定"]]
    original["初回配分"]["レーン割当"]["a.md"] = "lane-08"
    original["選定"][0]["レーン"] = "lane-08"
    original["レーンの所要時間"].append(_cost("lane-08"))
    original["レーン間の重なり"].append(_overlap("lane-08", "lane-02"))
    existing.write_text(yaml.safe_dump(original, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    addition["選定"].extend(additional_active_items(repo, repo.parent / "notes", "lane-04", 8))
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    before = existing.read_bytes()
    assert (
        _dispatch(
            str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(existing), "--work-dir", str(repo)
        )
        != 0
    )
    assert "初回配分: N=11、上限B=2、実レーン数=3" in capsys.readouterr().err
    assert existing.read_bytes() == before


def _merge_with_costs(
    repo: pathlib.Path,
    existing: pathlib.Path,
    added: pathlib.Path,
    mapping: pathlib.Path,
    costs: pathlib.Path,
    output: pathlib.Path,
) -> int:
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


def _merge_with_restricted_body(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    mapping: pathlib.Path,
    output: pathlib.Path,
    *wis: str,
) -> int:
    repo, existing, added = inputs
    return _dispatch(
        str(existing),
        "--merge",
        str(added),
        "--lane-map",
        str(mapping),
        "--output",
        str(output),
        "--work-dir",
        str(repo),
        *(part for wi in wis for part in ("--body-wi", wi)),
    )


def test_public_merge_combines_costs_and_both_definitions_once(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
) -> None:
    repo, existing, _added = inputs
    original = existing.read_bytes()
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    temporary = tmp_path / "whole.yaml"
    for output in (temporary, temporary, existing):
        assert _merge_with_restricted_body(inputs, mapping, output, "c.md", "d.md") == 0
        assert _dispatch(str(output), "--work-dir", str(repo)) == 0
        if output == temporary:
            assert existing.read_bytes() == original
    result = yaml.safe_load(existing.read_text(encoding="utf-8"))
    assert [item["WI"] for item in result["選定"] if not item["WI"].startswith("active-")] == ["a.md", "b.md", "c.md", "d.md"]
    assert [item["レーン"] for item in result["選定"] if not item["WI"].startswith("active-")] == [
        "lane-01",
        "lane-02",
        "lane-01",
        "lane-02",
    ]
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
    assert _dispatch(
        str(existing), "--merge", str(added), "--lane-map", str(mapping), "--output", str(output), "--work-dir", str(repo)
    ) == (1 if new_lanes else 0)
    if new_lanes:
        assert all(path.read_bytes() == data for path, data in before.items())
        assert not output.exists()
        return
    first = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert first["続行できない理由"] == ["なし"]
    assert [item["WI"] for item in first["選定"] if not item["WI"].startswith("active-")] == ["a.md", "b.md", "c.md", "d.md"]
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
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [_cost("lane-07")],
                "続行できない理由": ["なし"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    next_map = tmp_path / "next-map.json"
    next_map.write_text(json.dumps({"lane-07": "lane-01"}), encoding="utf-8")
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
    assert final["選定"][:-1] == first["選定"]
    assert [item["WI"] for item in final["選定"] if not item["WI"].startswith("active-")] == [
        "a.md",
        "b.md",
        "c.md",
        "d.md",
        "e.md",
    ]


def _merge_into_existing(
    repo: pathlib.Path, existing: pathlib.Path, added: pathlib.Path, mapping: pathlib.Path, *extra: str
) -> int:
    return _dispatch(
        str(existing),
        "--merge",
        str(added),
        "--lane-map",
        str(mapping),
        "--output",
        str(existing),
        "--work-dir",
        str(repo),
        *extra,
    )


@pytest.mark.parametrize("new_lane", [False, True])
def test_public_merge_recounts_unfinished_work_and_preserves_past_allocation(inputs, tmp_path, capsys, new_lane):
    repo, existing, added = inputs
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    addition["選定"].extend(additional_active_items(repo, repo.parent / "notes", "lane-04", 8))
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-03", "lane-04": "lane-02"}), encoding="utf-8")
    assert _merge_into_existing(repo, existing, added, mapping) == 0, capsys.readouterr().err
    assert "追加後の上限: N=21、上限B=3" in capsys.readouterr().out
    assert _dispatch(str(existing), "--mark-integrated", "lane-01", "--output", str(existing), "--work-dir", str(repo)) == 0
    before = existing.read_bytes()
    (first,) = yaml.safe_load(before)["追加時の配分"]
    assert len(first) == 21 and set(first.values()) == {"lane-01", "lane-02", "lane-03"}

    second = {
        "選定": additional_active_items(repo, repo.parent / "notes", "lane-05", 9),
        "レーンの所要時間": [_cost("lane-05", **({"段階": 2, "先行レーン": ["lane-03"]} if new_lane else {}))],
    }
    added.write_text(yaml.safe_dump(second, allow_unicode=True), encoding="utf-8")
    mapping.write_text(json.dumps({"lane-05": "lane-05" if new_lane else "lane-02"}), encoding="utf-8")
    code = _merge_into_existing(repo, existing, added, mapping)
    report = capsys.readouterr()
    if new_lane:
        assert code != 0
        assert "追加時の配分2: N=20、上限B=2、実レーン数=3" in report.err
        assert existing.read_bytes() == before
    else:
        assert code == 0, report.err
        assert "追加後の上限: N=20、上限B=2" in report.out
        past, current = yaml.safe_load(existing.read_bytes())["追加時の配分"]
        assert past == first
        assert len(current) == 20 and set(current.values()) == {"lane-02", "lane-03"}
