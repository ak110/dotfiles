# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定のレーンの段階・モデル・要約を検証する。"""

import json
import pathlib
import re
import typing

import check_selection
import pytest
import yaml

from agent_toolkit._testing.selection_check_support import (
    _DOTFILES_PYPROJECT,
    _awi,
    _dispatch,
    _initial_allocation,
    _overlap_record,
    _write_files,
    _write_selection,
)
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


@pytest.mark.parametrize("same", [True, False])
def test_public_command_spaced_paths_lane_overlap(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    same: bool,
) -> None:
    """空白入りの共有ファイルだけに双方の定義記録を要求する。"""
    repo, notes = env
    decisions = []
    for index, name in enumerate(("a.md", "b.md")):
        target = f"docs/file {0 if same else index}.md"
        (repo / target).write_text("x", encoding="utf-8")
        _awi(notes, name, f"`{target}`を変更する。")
        decisions.append({"WI": name, "レーン": f"lane-0{index + 1}", "書込対象": [target]})
    path = _write_selection(tmp_path / "spaced-lanes.yaml", decisions)
    assert _dispatch(str(path), "--work-dir", str(repo)) == (1 if same else 0)


@pytest.mark.parametrize("target", ["docs/file name.md", "assets dir/data.md", "日本語/別 ファイル.txt"])
@pytest.mark.parametrize("quote", ["plain", "single", "double"])
@pytest.mark.parametrize("field", ["書込対象", "公開工程の書込対象", "書き込まない反映先"])
def test_public_command_accepts_spaced_paths(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    target: str,
    quote: str,
    field: str,
) -> None:
    """明示文字列の境界を維持し、YAMLの表記と書込区分によらず個別パスを受理する。"""
    repo, notes = env
    (repo / target).parent.mkdir(parents=True, exist_ok=True)
    (repo / target).write_text("x", encoding="utf-8")
    _awi(notes, "a.md", f"`{target}`を扱う。")
    decision = {"WI": "a.md", "レーン": "lane-01", "書込対象": [], field: [target]}
    path = _write_selection(tmp_path / "space.yaml", [decision])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["レーンの所要時間"][0]["根拠"] = f"対象リポジトリの規範AGENTS.mdの公開工程の節がpath {target}を所有する"
    text = yaml.safe_dump(data, allow_unicode=True)
    literal = target if quote == "plain" else (f"'{target}'" if quote == "single" else json.dumps(target, ensure_ascii=False))
    text = text.replace(f"- {target}\n", f"- {literal}\n")
    path.write_text(text, encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0


@pytest.mark.parametrize("case,expected", [("directory", 0), ("new", 0), ("uncovered", 0), ("broad", 1), ("duplicate", 1)])
def test_public_command_checks_spaced_path_coverage_and_overlap(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    case: str,
    expected: int,
) -> None:
    """空白入りの新設・範囲・分類にも同じ実在・書込予定・過大範囲・重複条件を使う。"""
    repo, notes = env
    directory = "docs dir/"
    (repo / directory).mkdir()
    target = directory if case == "directory" else directory + "file name.md"
    if case != "new":
        (repo / directory / "file name.md").write_text("x", encoding="utf-8")
    _awi(notes, "a.md", f"`{target}`を扱う。")
    writes = [] if case == "uncovered" else [directory if case == "broad" else target]
    decision = {"WI": "a.md", "レーン": "lane-01", "書込対象": writes}
    if case == "duplicate":
        decision["書き込まない反映先"] = [target]
    path = _write_selection(tmp_path / "space.yaml", [decision])
    assert _dispatch(str(path), "--work-dir", str(repo)) == expected


@pytest.mark.parametrize("option", ["--merge", "--lane-map", "--output"])
def test_merge_options_must_be_provided_together(tmp_path: pathlib.Path, option: str) -> None:
    """統合モードの不完全な呼出は、ファイルを読む前に入力誤りとして拒否する。"""
    with pytest.raises(SystemExit) as error:
        check_selection.main([str(tmp_path / "selection.yaml"), option, str(tmp_path / "other")])
    assert error.value.code == 2


def test_cost_updates_require_merge(tmp_path: pathlib.Path) -> None:
    """費用更新単独の呼出を、ファイル読込前に拒否する。"""
    assert _dispatch(str(tmp_path / "selection.yaml"), "--lane-cost-updates", str(tmp_path / "updates.json")) == 2


def test_public_check_requires_single_stage_estimate(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ段階案の比較値だけを変え、欠落拒否・0の受理・成功要約を観測する。"""
    repo, notes = env
    for wi in ("a.md", "b.md"):
        _awi(notes, wi, "`src/model.py`を変える。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": wi, "レーン": lane, "書込対象": ["src/model.py"]} for wi, lane in (("a.md", "lane-01"), ("b.md", "lane-02"))],
        [{"レーン": "lane-01"}, {"レーン": "lane-02", "段階": 2, "先行レーン": ["lane-01"]}],
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["レーン間の重なり"] = [_overlap_record("src/model.py", "交わる")]
    field = "単一段階案の完了見込み秒数"
    for value, code in ((None, 1), (0, 0), (1320, 0)):
        data.pop(field, None)
        if value is not None:
            data[field] = value
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        assert _dispatch(str(path), "--work-dir", str(repo), "--body-wi", "a.md") == code
        result = capsys.readouterr()
        if code:
            assert field in result.err and "次の操作:" in result.err
            assert "配分の説明" in result.err
        else:
            assert f"{field}: {value}" in result.out and not result.err
    path = _write_selection(tmp_path / "single.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}])
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert field not in capsys.readouterr().out


@pytest.mark.parametrize("value", ["100", True, -1, [], None])
def test_public_check_rejects_invalid_single_stage_estimate(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], value: object
) -> None:
    """比較値の不正型は段階の有無を問わず入力違反とする。"""
    repo, _notes = env
    path = _write_selection(tmp_path / "selection.yaml", [])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["単一段階案の完了見込み秒数"] = value
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 2
    message = capsys.readouterr().err
    assert "単一段階案の完了見込み秒数" in message and "型" in message and "次の操作:" in message


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    ("paths", "same_lane", "rationale", "expected"),
    [
        (("src/model.py", "src/model.py"), False, "src/model.pyの異なる定義", 1),
        (("src/model.py", "src/model.py"), False, "別の対象", 1),
        (("src/", "src/model.py"), False, "src/model.pyの異なる定義", 1),
        (("src/", "src/model.py"), False, "src/の異なる定義", 1),
        (("src/", "src/models/"), False, "src/models/の異なる定義", 1),
        (("src/", "src/models/"), False, "src/models-old/の定義", 1),
        (("src/", "src-old/model.py"), False, "別対象", 0),
        (("src/model.py", "src/model.py"), True, "同じレーンで直列化", 0),
    ],
)
def test_public_check_rejects_rationale_only_overlaps(
    legacy: bool,
    paths: tuple[str, str],
    same_lane: bool,
    rationale: str,
    expected: int,
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """新旧欄名とも自由文だけでは共有を受理せず、似た名前と同じレーンは共有から外す。"""
    repo, notes = env
    (repo / "src/models").mkdir()
    for name, path in zip(("a.md", "b.md"), paths, strict=True):
        _awi(notes, name, f"`{path}`を書き込む。")
    decisions_key, wi_key, lane_key, files_key, costs_key, rationale_key = (
        ("decisions", "awi", "lane", "write_files", "lane_costs", "rationale")
        if legacy
        else ("選定", "WI", "レーン", "書込対象", "レーンの所要時間", "根拠")
    )
    staleness_key, seconds = ("staleness", "implementation_seconds") if legacy else ("鮮度", "実装秒数")
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                decisions_key: [
                    {wi_key: "a.md", lane_key: "lane-01", files_key: [paths[0]], staleness_key: {"status": "current"}},
                    {
                        wi_key: "b.md",
                        lane_key: "lane-01" if same_lane else "lane-02",
                        files_key: [paths[1]],
                        staleness_key: {"status": "current"},
                    },
                ],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                costs_key: [
                    {
                        lane_key: lane,
                        rationale_key: rationale,
                        seconds: 600,
                        "統合秒数": 60,
                        "補正前の実装秒数": 600,
                        "補正前の統合秒数": 60,
                    }
                    for lane in (("lane-01",) if same_lane else ("lane-01", "lane-02"))
                ],
                "初回配分": _initial_allocation(
                    [
                        {"WI": "a.md", "レーン": "lane-01"},
                        {"WI": "b.md", "レーン": "lane-01" if same_lane else "lane-02"},
                    ]
                ),
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == expected

    err = capsys.readouterr().err
    if expected:
        assert "lane-01とlane-02" in err
        assert "重複パスの記録不足" in err
        assert paths[1] in err
        assert "次の操作:" in err
        assert "双方の定義と判定" in err
    else:
        assert not err


@pytest.mark.parametrize("missing_lane", ["lane-01", "lane-02"])
def test_overlap_rejects_rationale_missing_on_either_side(
    missing_lane: str,
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """片方の自由文だけが共通パスを持っても、構造化記録の不足を示す。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "`src/model.py`を書き込む。")
    selection = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
        ],
        [
            {"レーン": lane, "根拠": "別対象" if lane == missing_lane else "src/model.pyの異なる定義"}
            for lane in ("lane-01", "lane-02")
        ],
    )

    assert check_selection.main(["--work-dir", str(repo), str(selection)]) == 1
    assert "重複パスの記録不足: src/model.py" in capsys.readouterr().err


def test_public_check_accepts_zero_candidate_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """候補0件の選定結果は空の初回配分で受理する。

    候補0件のprocess-wiの1回の実行はアラート監査だけを行うため、受領側が選定結果を拒否すると監査へ進めない。
    """
    notes = tmp_path / "notes"
    (notes / "processing").mkdir(parents=True)
    path = tmp_path / "selection.yaml"
    _write_selection(path, [])
    monkeypatch.setattr(check_selection._plan_file, "private_notes_root", lambda: notes)  # pylint: disable=protected-access
    assert check_selection.main([str(path), "--work-dir", str(tmp_path)]) == 0, capsys.readouterr().err


def test_public_check_model_and_stage_selection_contract(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """担当別モデルと後段の先行統合条件を、公開コマンドから同じ選定で確かめる。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "`src/model.py`を書き込む。")

    def run(second_lane: str, second_models: dict[str, str], prior: list[str]) -> tuple[int, str]:
        selection = _write_selection(
            tmp_path / "selection.yaml",
            [
                {
                    "WI": "a.md",
                    "レーン": "lane-01",
                    "書込対象": ["src/model.py"],
                    "担当モデル": {"実装担当": "claude:opus/high"},
                },
                {"WI": "b.md", "レーン": second_lane, "書込対象": ["src/model.py"], "担当モデル": second_models},
            ],
            [
                {"レーン": "lane-01", "段階": 1, "根拠": "先行"},
                {"レーン": "lane-02", "段階": 2, "先行レーン": prior, "根拠": "後段"},
            ],
        )
        if second_lane == "lane-02":
            data = yaml.safe_load(selection.read_text(encoding="utf-8"))
            data["レーン間の重なり"] = [_overlap_record("src/model.py", "交わる")]
            selection.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        code = _dispatch("--work-dir", str(repo), str(selection))
        return code, capsys.readouterr().err

    assert run("lane-02", {"実行レビュー担当": "codex:gpt-6-sol/medium"}, ["lane-01"]) == (0, "")

    code, err = run("lane-02", {"実行レビュー担当": "codex:gpt-6-sol/medium"}, [])
    assert code == 1
    assert "lane-02: 段階2の後段に先行レーンがない" in err
    assert "依存がない" in err

    code, err = run("lane-01", {"実装担当": "codex:gpt-6-sol/medium"}, ["lane-01"])
    assert code == 1
    assert "lane-01: 実装担当のモデル指定が衝突" in err


def _parse_summary(output: str) -> dict[str, object]:
    """成功時の要約行を、WI総数・通常レーン数・レーンごとの値・`レーン: なし`の一覧へ読み直す。"""
    parsed: dict[str, object] = {"lanes": {}}
    lanes = typing.cast(dict[str, dict[str, object]], parsed["lanes"])
    for line in output.splitlines():
        if line.startswith("WI総数: "):
            parsed["total"] = int(line.removeprefix("WI総数: "))
        elif line.startswith("通常レーン数: "):
            parsed["lane_count"] = int(line.removeprefix("通常レーン数: "))
        elif line.startswith("レーン: なし: "):
            parsed["none"] = json.loads(line.removeprefix("レーン: なし: "))
        elif found := re.fullmatch(
            r"(\S+): WI (\d+)件、段階 (\d+)、先行レーン (\[.*?\])、実装秒数 (\S+)、統合秒数 (\S+)、WI (\[.*\])", line
        ):
            lanes[found[1]] = {
                "count": int(found[2]),
                "stage": int(found[3]),
                "prior": json.loads(found[4]),
                "seconds": (float(found[5]), float(found[6])),
                "wis": json.loads(found[7]),
            }
    return parsed


def _expected_summary(selection: dict[str, typing.Any]) -> dict[str, object]:
    """選定結果のYAMLの値から、読み取り契約の省略時の値と旧欄名を適用して要約の期待値を求める。"""
    items = selection.get("選定", selection.get("decisions", []))
    costs = selection.get("レーンの所要時間", selection.get("lane_costs", []))
    lane_of = [(item.get("WI", item.get("awi")), item.get("レーン", item.get("lane"))) for item in items]
    lanes: dict[str, dict[str, object]] = {}
    for awi, lane in lane_of:
        if lane == "なし":
            continue
        row = next(row for row in costs if row.get("レーン", row.get("lane")) == lane)
        entry = lanes.setdefault(
            lane,
            {
                "count": 0,
                "stage": row.get("段階", row.get("stage", 1)),
                "prior": row.get("先行レーン", row.get("prior_lanes", [])),
                "seconds": (
                    float(row.get("実装秒数", row.get("implementation_seconds"))),
                    float(row.get("統合秒数", row.get("integration_seconds"))),
                ),
                "wis": [],
            },
        )
        entry["count"] = typing.cast(int, entry["count"]) + 1
        typing.cast(list[str], entry["wis"]).append(awi)
    return {
        "lanes": lanes,
        "total": len(items),
        "lane_count": len(lanes),
        "none": [awi for awi, lane in lane_of if lane == "なし"],
    }


@pytest.mark.parametrize("layout", ["staged", "legacy", "empty"])
def test_public_command_prints_lane_summary_on_success(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], layout: str
) -> None:
    """成功時の標準出力だけから、WI総数・通常レーン数・各レーンの件数・全WI名・段階・先行レーン・秒数を読める。

    旧欄名の秒数を読まない、`レーン: なし`の項目が欠ける、WI名を切り詰めるのいずれかがあると、受領側はレーン構成を
    出力ファイルから読み直す必要が残る。期待値は入力のYAMLから求める。
    """
    repo, notes = env
    for name in ("a.md", "b.md", "c.md"):
        _awi(notes, name, "`src/model.py`を変える。")
    _awi(notes, "d.md", "`docs/development/design.md`を変える。")
    selection: dict[str, typing.Any]
    if layout == "staged":
        _awi(notes, "d.md", "`src/model.py`を変える。")
        selection = {
            "選定": [
                {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
                {"WI": "b.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
                {"WI": "d.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
                {"WI": "c.md", "レーン": "なし", "書込対象": []},
            ],
            "レーンの所要時間": [
                {"レーン": "lane-01", "実装秒数": 900, "統合秒数": 120, "根拠": "先行"},
                {"レーン": "lane-02", "段階": 2, "先行レーン": ["lane-01"], "実装秒数": 300.5, "統合秒数": 30, "根拠": "後段"},
            ],
            "レーン間の重なり": [_overlap_record("src/model.py", "交わる")],
            "単一段階案の完了見込み秒数": 1500,
        }
    elif layout == "legacy":
        selection = {
            "decisions": [
                {"awi": "a.md", "lane": "lane-01", "write_files": ["src/model.py"]},
                {"awi": "c.md", "lane": "なし", "write_files": []},
            ],
            "lane_costs": [
                {"lane": "lane-01", "implementation_seconds": 450, "integration_seconds": 45, "rationale": "旧形式"}
            ],
        }
    else:
        selection = {"選定": [], "レーンの所要時間": []}
    selection["見込みの補正"] = {"係数": 1, "参照した記録": [], "除外した記録": []}
    for row in selection.get("レーンの所要時間", selection.get("lane_costs", [])):
        row["補正前の実装秒数"] = row.get("実装秒数", row.get("implementation_seconds"))
        row["補正前の統合秒数"] = row.get("統合秒数", row.get("integration_seconds"))
    for item in selection.get("選定", selection.get("decisions", [])):
        item.setdefault("鮮度" if "WI" in item else "staleness", {"status": "current", "later_commit_count": 0})
    selection["初回配分"] = _initial_allocation(selection.get("選定", selection.get("decisions", [])))
    path = tmp_path / "selection.yaml"
    path.write_text(yaml.safe_dump(selection, allow_unicode=True), encoding="utf-8")

    assert _dispatch("--work-dir", str(repo), str(path)) == 0
    result = capsys.readouterr()
    assert result.err == ""
    assert _parse_summary(result.out) == _expected_summary(selection)


@pytest.mark.parametrize("failure", ["content", "input", "norm-spec"])
def test_public_command_prints_no_summary_on_failure(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    """内容違反（規範指定の不足を含む）と入力不備では、終了コード1・2と次の操作を示し、標準出力へ要約を書かない。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`と`AGENTS.md`を変える。")
    _write_files(repo, "AGENTS.md")
    decisions: list[dict[str, typing.Any]] = [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py", "AGENTS.md"]}]
    if failure == "content":
        decisions[0]["書込対象"] = ["src/"]
    if failure == "norm-spec":
        (repo / "pyproject.toml").write_text(_DOTFILES_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    path = _write_selection(tmp_path / "selection.yaml", decisions)
    if failure == "input":
        path.write_text("選定: [\n", encoding="utf-8")
    assert _dispatch("--work-dir", str(repo), str(path)) == (2 if failure == "input" else 1)
    result = capsys.readouterr()
    assert "次の操作: " in result.err
    assert "WI総数" not in result.out and "通常レーン数" not in result.out


__all__ = ["fixture_env"]
