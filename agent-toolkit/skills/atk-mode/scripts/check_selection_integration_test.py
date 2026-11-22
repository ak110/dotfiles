# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import json
import pathlib
import sys
import typing

import check_selection_loading
import pytest
import yaml
from agent_toolkit import atk
from agent_toolkit._plan import locations
from agent_toolkit._testing import git_repository, plan_fixture, wi_entry_files
from agent_toolkit._testing.lane_integration_support import (
    _adopt_argv,
    _integration_args,
    _integration_selection,
    _plan_operation,
    _record_wi_commit,
)
from agent_toolkit._testing.lane_support import _create, _invoke
from agent_toolkit._testing.selection_check_support import (
    _awi,
    _dispatch,
    _initial_allocation,
    _overlap_record,
    _track,
    _write_selection,
)
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


def test_require_integrated_checks_all_stages_without_current_wi(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["removed.py"], "統合状態": "統合済み"},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["removed.py"]},
            {"WI": "c.md", "レーン": "lane-03", "書込対象": ["removed.py"]},
        ],
        [
            {"レーン": "lane-01", "統合状態": "統合済み"},
            {"レーン": "lane-02", "段階": 2, "先行レーン": ["lane-01"]},
            {"レーン": "lane-03", "段階": 3, "先行レーン": ["lane-02"]},
        ],
    )
    before = path.read_bytes()
    assert _dispatch(str(path), "--require-integrated") == 1
    output = capsys.readouterr()
    assert all(value in output.err for value in ("lane-02", "lane-03", "b.md", "c.md", "2", "3"))
    assert path.read_bytes() == before
    assert _dispatch(str(path), "--require-integrated", "--publication-lane", "lane-01") == 0
    assert _dispatch(str(path), "--require-integrated", "--publication-lane", "lane-03") == 1
    assert _dispatch(str(path), "--require-integrated", "--publication-lane", "unknown") == 2
    assert path.read_bytes() == before
    data = yaml.safe_load(before)

    data["レーンの所要時間"][2]["統合状態"] = "統合済み"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--require-integrated", "--publication-lane", "lane-03") == 1
    for row in data["レーンの所要時間"]:
        row["統合状態"] = "統合済み"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--require-integrated") == 0


@pytest.mark.parametrize("state", ["未知", 1])
def test_require_integrated_rejects_invalid_state(
    tmp_path: pathlib.Path, state: object, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_selection(
        tmp_path / "selection.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": [], "統合状態": state}]
    )
    assert _dispatch(str(path), "--require-integrated") == 2
    assert "統合状態" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["--require-integrated", "--history"])
def test_read_only_modes_reject_new_plan_without_reopen(tmp_path: pathlib.Path, mode: str) -> None:
    path = _write_selection(tmp_path / "selection.yaml", [])
    before = path.read_bytes()
    assert _dispatch(str(path), mode, "--new-plan") == 2
    assert path.read_bytes() == before
    assert _dispatch(str(path), mode) == 0


@pytest.mark.parametrize("case", ["empty", "missing", "malformed", "unassigned-lane"])
def test_require_integrated_empty_and_unreadable_inputs(tmp_path: pathlib.Path, case: str) -> None:
    path = tmp_path / "selection.yaml"
    if case == "empty":
        _write_selection(path, [])
    elif case == "malformed":
        path.write_text("選定: [\n", encoding="utf-8")
    elif case == "unassigned-lane":
        _write_selection(path, [], costs=[{"レーン": "lane-01"}])
    before = path.read_bytes() if path.exists() else None
    assert _dispatch(str(path), "--require-integrated") == (0 if case == "empty" else 2)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("new_plan", [False, True])
def test_reopen_wi_retains_original_allocation_and_other_integrations(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, new_plan: bool
) -> None:
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更する。")
    _awi(notes, "b.md", "`README.md`を変更する。")
    _awi(notes, "c.md", "`src/model.py`を変更する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "b.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "c.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
        ],
        [{"レーン": "lane-01", "統合状態": "統合済み"}, {"レーン": "lane-02", "統合状態": "統合済み"}],
    )
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    old_plan = str(tmp_path / "removed-worktree" / "old-plan.md")
    for item in before["選定"][:2]:
        item["再開位置"] = old_plan
    before["初回配分"]["不可分成分"] = [
        {
            "WI": ["a.md", "b.md"],
            "結合条件": [{"WI1": "a.md", "WI2": "b.md", "種別": "再開計画", "根拠": {"計画1": old_plan, "計画2": old_plan}}],
        },
        {"WI": ["c.md"], "結合条件": []},
    ]
    path.write_text(yaml.safe_dump(before, allow_unicode=True), encoding="utf-8")
    options = ["--new-plan"] if new_plan else []
    assert _dispatch(str(path), "--reopen-wi", "a.md", *options, "--output", str(path), "--work-dir", str(repo)) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["初回配分"] == before["初回配分"]
    assert after["選定"][0].get("再開位置") == ("なし" if new_plan else before["選定"][0].get("再開位置"))
    assert after["選定"][1]["再開位置"] == old_plan
    assert [row["統合状態"] for row in after["レーンの所要時間"]] == ["未統合", "統合済み"]
    assert [row["統合状態"] for row in after["選定"]] == ["未統合", "統合済み", "統合済み"]
    saved = path.read_bytes()
    assert _dispatch(str(path), "--reopen-wi", "missing.md", "--output", str(path), "--work-dir", str(repo)) == 2
    assert path.read_bytes() == saved


@pytest.mark.parametrize(("state", "expected"), [("adopted", 0), ("rejected", 0), ("processing", 1)])
def test_reopen_wi_ignores_changed_dependency_of_finished_source(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    state: str,
    expected: int,
) -> None:
    repo, notes = env
    target = "20261120-000000-001.md"
    source = "20261120-000000-002.md"
    _awi(notes, target, "`README.md`を変更する。")
    _awi(notes, source, "`README.md`を変更する。")
    legacy_source = notes / "processing" / source
    dated_source = wi_entry_files.entry_path(notes, source)
    text = legacy_source.read_text(encoding="utf-8")
    dated_source.write_text(text.replace("type: awi\n", f"type: awi\nstate: {state}\n", 1), encoding="utf-8")
    legacy_source.unlink()
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": wi, "レーン": "lane-01", "書込対象": ["README.md"], "統合状態": "統合済み"} for wi in (target, source)],
        [{"レーン": "lane-01", "統合状態": "統合済み"}],
    )
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    before["初回配分"]["不可分成分"] = [
        {
            "WI": [target, source],
            "結合条件": [
                {
                    "WI1": target,
                    "WI2": source,
                    "種別": "依存",
                    "根拠": {
                        "リポジトリ1": "github.com/test/repo",
                        "リポジトリ2": "github.com/test/repo",
                        "依存元": source,
                        "depends_on": [target],
                    },
                }
            ],
        },
    ]
    path.write_text(yaml.safe_dump(before, allow_unicode=True), encoding="utf-8")
    written = path.read_bytes()

    assert _dispatch(str(path), "--reopen-wi", target, "--output", str(path), "--work-dir", str(repo)) == expected
    if expected != 0:
        assert "結合根拠が元入力と一致しない" in capsys.readouterr().err
        assert path.read_bytes() == written
        return
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["初回配分"] == before["初回配分"]
    assert [item["統合状態"] for item in after["選定"]] == ["未統合", "統合済み"]


def test_reopen_wi_keeps_explicit_pending_item_of_other_integrated_lane(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更する。")
    _awi(notes, "b.md", "`src/model.py`を変更する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"], "統合状態": "未統合"},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
        ],
        [{"レーン": "lane-01", "統合状態": "統合済み"}, {"レーン": "lane-02", "統合状態": "統合済み"}],
    )
    assert _dispatch(str(path), "--reopen-wi", "b.md", "--output", str(path), "--work-dir", str(repo)) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [item["統合状態"] for item in after["選定"]] == ["未統合", "未統合"]
    assert _dispatch(str(path), "--require-integrated", "--publication-lane", "lane-01") == 1


@pytest.mark.parametrize("state,expected", [(None, 1), ("未統合", 1), ("統合済み", 0)])
def test_public_check_overlap_uses_integration_state(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    state: str | None,
    expected: int,
) -> None:
    """同段階の2レーンが同じ定義を書く選定は、片方が統合済みの場合だけ競合として拒否しない。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "`README.md`を変える。")
    path = _write_selection(
        tmp_path / "state.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["README.md"]},
        ],
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["レーン間の重なり"] = [
        {
            "レーン1": "lane-01",
            "レーン2": "lane-02",
            "共通パス": "README.md",
            "レーン1の定義": ["f"],
            "レーン2の定義": ["f"],
            "判定": "交わる",
        }
    ]
    if state is not None:
        data["レーンの所要時間"][0]["統合状態"] = state
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == expected


def test_public_command_records_integrated_lane(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変える。")
    path = _write_selection(tmp_path / "state.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]}])
    for _ in range(2):
        assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["レーンの所要時間"][0]["統合状態"] == "統合済み"
    assert data["選定"][0]["WI"] == "a.md"
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    before = path.read_bytes()
    assert _dispatch(str(path), "--mark-integrated", "lane-99", "--output", str(path), "--work-dir", str(repo)) == 2
    assert path.read_bytes() == before


def test_marks_only_processed_wis_and_requires_every_wi(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    repo, notes = env
    names = ("done.md", "partial.md", "new.md")
    paths = ("README.md", "src/model.py", "README.md")
    for name, path_name in zip(names, paths, strict=True):
        _awi(notes, name, f"`{path_name}`を変える。")
    path = _write_selection(
        tmp_path / "state.yaml",
        [{"WI": name, "レーン": "lane-01", "書込対象": [path_name]} for name, path_name in zip(names, paths, strict=True)],
    )
    args = (str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo))
    good = path.read_bytes()
    invalid = yaml.safe_load(good)
    invalid["選定"][1]["書込対象"] = ["src/"]
    path.write_text(yaml.safe_dump(invalid, allow_unicode=True), encoding="utf-8")
    bad = path.read_bytes()
    assert _dispatch(*args, "--mark-integrated-wis", '["done.md"]') == 1
    assert path.read_bytes() == bad
    path.write_bytes(good)
    assert _dispatch(*args, "--mark-integrated-wis", '["done.md"]') == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [row.get("統合状態", "未統合") for row in data["選定"]] == ["統合済み", "未統合", "未統合"]
    assert _dispatch(str(path), "--require-integrated", "--work-dir", str(repo)) == 1
    before = path.read_bytes()
    assert _dispatch(*args, "--mark-integrated-wis", '["unknown.md"]') == 2
    assert path.read_bytes() == before
    assert _dispatch(*args, "--mark-integrated-wis", '["partial.md","new.md"]') == 0
    assert _dispatch(str(path), "--require-integrated", "--work-dir", str(repo)) == 0


def test_partial_integration_keeps_existing_precedence_readable(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    repo, notes = env
    cases = [
        ("a.md", "lane-01", "src/model.py"),
        ("b.md", "lane-01", "README.md"),
        ("c.md", "lane-02", "src/model.py"),
        ("d.md", "lane-03", "pytools/post_apply.py"),
    ]
    for name, _, target in cases:
        _awi(notes, name, f"`{target}`を変更する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": name, "レーン": lane, "書込対象": [target]} for name, lane, target in cases],
        [{"レーン": "lane-01"}, {"レーン": "lane-02", "段階": 2, "先行レーン": ["lane-01"]}, {"レーン": "lane-03"}],
    )
    source = yaml.safe_load(path.read_bytes())
    source["レーン間の重なり"] = [_overlap_record("src/model.py", "交わる")]
    path.write_text(yaml.safe_dump(source, allow_unicode=True), encoding="utf-8")
    args = (str(path), "--work-dir", str(repo))
    assert _dispatch(*args) == 0
    assert _dispatch(*args, "--mark-integrated", "lane-01", "--mark-integrated-wis", '["a.md"]', "--output", str(path)) == 0
    saved = yaml.safe_load(path.read_bytes())
    assert [item["WI"] for item in saved["選定"] if item.get("統合状態") == "統合済み"] == ["a.md"]
    assert saved["レーンの所要時間"][1]["先行レーン"] == ["lane-01"]
    assert _dispatch(*args) == 0
    assert _dispatch(*args, "--history") == 0
    assert _dispatch(*args, "--mark-integrated", "lane-03", "--output", str(path)) == 0
    updated = yaml.safe_load(path.read_bytes())
    assert [item["WI"] for item in updated["選定"] if item.get("統合状態") == "統合済み"] == ["a.md", "d.md"]
    assert _dispatch(*args, "--require-integrated") == 1


@pytest.mark.parametrize("problem", ["unknown-state", "state-type", "unknown-prior", "broad", "invalid-path", "conflict"])
def test_state_update_preserves_output_on_failure(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    problem: str,
) -> None:
    """不正な状態・完了履歴の未知参照・未統合の別レーンの違反・残る未統合競合は、既存の保存先を更新しない。

    記録するレーンの項目は作業ツリーの状態を前提とする判定から外れるため、本文の違反は未統合の別レーンの項目へ置く。
    """
    repo, notes = env
    decisions = []
    for number in (1, 2, 3):
        name = f"{number}.md"
        _awi(notes, name, "`README.md`を変える。")
        decisions.append({"WI": name, "レーン": f"lane-0{number}", "書込対象": ["README.md"]})
    if problem in ("broad", "invalid-path"):
        _awi(notes, "other.md", "`src/model.py`を変える。")
        decisions = [decisions[0], {"WI": "other.md", "レーン": "lane-02", "書込対象": ["src/model.py"]}]
    path = _write_selection(tmp_path / "state.yaml", decisions)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if problem not in ("conflict", "broad", "invalid-path"):
        data["選定"] = data["選定"][:1]
        data["レーンの所要時間"] = data["レーンの所要時間"][:1]
        data["初回配分"] = _initial_allocation(data["選定"])
    row = data["レーンの所要時間"][0]
    if problem in ("unknown-state", "state-type"):
        row["統合状態"] = "完了" if problem == "unknown-state" else []
    elif problem == "unknown-prior":
        row["統合状態"] = "統合済み"
        row["先行レーン"] = ["lane-99"]
    elif problem in ("broad", "invalid-path"):
        data["選定"][1]["書込対象"] = ["src/"] if problem == "broad" else ["docs/../src/model.py"]
    else:
        data["レーン間の重なり"] = [
            {
                "レーン1": left,
                "レーン2": right,
                "共通パス": "README.md",
                "レーン1の定義": ["f"],
                "レーン2の定義": ["f"],
                "判定": "交わる",
            }
            for left, right in (("lane-01", "lane-02"), ("lane-01", "lane-03"), ("lane-02", "lane-03"))
        ]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    before = path.read_bytes()
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) != 0
    assert path.read_bytes() == before


def _integrated_lane_selection(tmp_path: pathlib.Path, notes: pathlib.Path) -> pathlib.Path:
    _awi(notes, "a.md", "モデルの実装を見直す。")
    _awi(notes, "b.md", "`README.md`を変える。")
    _awi(notes, "c.md", "`docs/development/design.md`を変える。")
    return _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["README.md"]},
            {"WI": "c.md", "レーン": "lane-03", "書込対象": ["docs/development/design.md"]},
        ],
    )


def test_mark_integrated_accepts_renamed_write_target(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    (repo / "src/model.py").rename(repo / "src/model2.py")
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    result = yaml.safe_load(path.read_text(encoding="utf-8"))
    states = {row["レーン"]: row.get("統合状態", "未統合") for row in result["レーンの所要時間"]}
    assert states == {"lane-01": "統合済み", "lane-02": "未統合", "lane-03": "未統合"}
    items = {item["WI"]: item.get("統合状態", "未統合") for item in result["選定"]}
    assert items == {"a.md": "統合済み", "b.md": "未統合", "c.md": "未統合"}
    assert "src/model.py" not in capsys.readouterr().err


def test_integrated_items_do_not_block_later_operations(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    (repo / "src/model.py").rename(repo / "src/model2.py")
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert _dispatch(str(path), "--mark-integrated", "lane-02", "--output", str(path), "--work-dir", str(repo)) == 0
    addition = tmp_path / "extend.yaml"
    addition.write_text(
        yaml.safe_dump({"選定": [{"WI": "c.md", "書込対象": ["scripts/update_dotfiles.py"]}]}, allow_unicode=True),
        encoding="utf-8",
    )
    output = tmp_path / "extended.yaml"
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(output)) == 0
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert result["選定"][2]["書込対象"] == ["docs/development/design.md", "scripts/update_dotfiles.py"]
    assert "src/model.py" not in capsys.readouterr().err


@pytest.mark.parametrize("source", ["mark-integrated", "lane-row-only", "concurrent"])
def test_merged_lane_keeps_integrated_items_exempt(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    if source == "mark-integrated":
        assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    elif source == "lane-row-only":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["レーンの所要時間"][0]["統合状態"] = "統合済み"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    _awi(notes, "d.md", "`pytools/post_apply.py`を変える。")
    added = tmp_path / "added.yaml"
    added.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {
                        "WI": "d.md",
                        "レーン": "lane-09",
                        "鮮度": {"status": "current", "later_commit_count": 0},
                        "書込対象": ["pytools/post_apply.py"],
                        "統合状態": "統合済み",
                    }
                ],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [
                    {
                        "レーン": "lane-09",
                        "実装秒数": 10,
                        "統合秒数": 2,
                        "補正前の実装秒数": 10,
                        "補正前の統合秒数": 2,
                        "根拠": "追加の根拠",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-09": "lane-01"}), encoding="utf-8")
    args = ("--merge", str(added), "--lane-map", str(mapping), "--output", str(path), "--work-dir", str(repo))
    if source == "concurrent":
        # mergeが読んだ後でmark-integratedを完了させ、実時間の待機なしで書込の順を固定する。
        load = check_selection_loading.load_selection
        saved = None

        def load_and_mark(selection_file, **kwargs):
            nonlocal saved
            selection = load(selection_file, **kwargs)
            if selection_file == path:
                monkeypatch.setattr(check_selection_loading, "load_selection", load)
                assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
                saved = path.read_bytes()
            return selection

        monkeypatch.setattr(check_selection_loading, "load_selection", load_and_mark)
        assert _dispatch(str(path), *args) == 2
        error = capsys.readouterr().err
        assert "保存先を更新した" in error
        assert "再実行" in error
        assert saved is not None and path.read_bytes() == saved
    assert _dispatch(str(path), *args) == 0
    result = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert result["レーンの所要時間"][0]["統合状態"] == "未統合"
    items = {item["WI"]: item.get("統合状態", "未統合") for item in result["選定"]}
    assert items == {"a.md": "統合済み", "b.md": "未統合", "c.md": "未統合", "d.md": "未統合"}
    (repo / "src/model.py").rename(repo / "src/model2.py")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0

    (repo / "pytools/post_apply.py").unlink()
    (repo / "pytools").rmdir()
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    (repo / "pytools").write_text("新設先の途中にあるファイル", encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1


@pytest.mark.parametrize("state", ["未統合", "統合済み"])
@pytest.mark.parametrize("change", ["directory", "abbreviation", "norm-spec", "public-heading"])
def test_integrated_items_tolerate_worktree_changes(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    state: str,
    change: str,
) -> None:
    repo, notes = env
    item: dict[str, typing.Any] = {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}
    if change == "directory":
        _awi(notes, "a.md", "`src/`配下を整理する。")
        item["書込対象"] = ["src/"]
    elif change == "abbreviation":
        _track(repo)
        _awi(notes, "a.md", "`src/model.py`と`post_apply.py`を変更する。")
        item["書込対象"] = ["src/model.py", "pytools/post_apply.py"]
    elif change == "public-heading":
        _awi(notes, "a.md", "`README.md`を変更する。")
        item["書込対象"] = []
        item["公開工程の書込対象"] = ["README.md"]
    else:
        _awi(notes, "a.md", "`src/model.py`を変更する。")
    path = _write_selection(tmp_path / "selection.yaml", [item], [{"レーン": "lane-01", "統合状態": state}])
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    if change == "directory":
        (repo / "src").rename(repo / "src-new")
        expected, diagnostic = 1, "`書込対象`のパスの不正: src/"
    elif change == "abbreviation":
        _track(repo, "scripts/post_apply.py")
        expected, diagnostic = 2, "略記 `post_apply.py` の候補が複数ある"
    elif change == "norm-spec":
        (repo / "pyproject.toml").write_text(
            '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "仕様"\npaths = ["src/"]\nrequire-text = ["設計を読む"]\n',
            encoding="utf-8",
        )
        expected, diagnostic = 1, "プロジェクト規範の指定の不足"
    else:
        (repo / "TEST_RULES.md").write_text("# 検査用規範\n\n## 公開の手順\n", encoding="utf-8")
        expected, diagnostic = 1, "`公開工程の書込対象`の根拠不足"
    capsys.readouterr()
    if state == "統合済み":
        assert _dispatch(str(path), "--work-dir", str(repo)) == 0
        assert diagnostic not in capsys.readouterr().err
    else:
        assert _dispatch(str(path), "--work-dir", str(repo)) == expected
        assert diagnostic in capsys.readouterr().err


def test_unintegrated_missing_write_target_still_rejected(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["選定"][1]["書込対象"] = ["README.md", "src/missing.py"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    before = path.read_bytes()
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 1
    assert path.read_bytes() == before
    assert (
        "b.md: `書込対象`のパスの不正: src/missing.py: 未作成のパスで、本文の反映先にも`導出した新設先`にも無い"
        in capsys.readouterr().err
    )
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1


__all__ = ["fixture_env"]


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの新計画保存とWI採用まで接続し、旧計画/対象外WIを保持する"
        + "最小の2WIを確認する。同群Linux CI所有CPU最大0.413秒"
        + "。"
    ),
)
def test_changed_requirement_finalizes_new_plan_in_current_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    notes = tmp_path / "notes"
    git_repository.init_repository(notes, initial_branch="main", commit_message="隔離キュー")
    wi = "20261023-000000-001.md"
    other = "20261023-000000-002.md"
    source = wi_entry_files.entry_path(notes, wi, state="processing")
    other_source = wi_entry_files.entry_path(notes, other, state="processing")
    other_source.write_bytes(source.read_bytes())
    other_before = other_source.read_bytes()
    old = notes / "plans" / "2026" / "10" / "23-1000_old.md"
    old.parent.mkdir(parents=True)
    old.write_text(plan_fixture.eight_section_plan(repo=tmp_path / "reclaimed-worktree"), encoding="utf-8")
    old_before = old.read_bytes()
    document = yaml.safe_load(selected.read_text(encoding="utf-8"))
    document["選定"][0]["再開位置"] = str(old)
    document["選定"].append({**document["選定"][0], "WI": other, "統合状態": "統合済み"})
    document["初回配分"]["候補WI"].append(other)
    document["初回配分"]["レーン割当"][other] = "lane-01"
    document["初回配分"]["不可分成分"] = [
        {
            "WI": [wi, other],
            "結合条件": [{"WI1": wi, "WI2": other, "種別": "再開計画", "根拠": {"計画1": str(old), "計画2": str(old)}}],
        }
    ]
    selected.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(selected), "--reopen-wi", wi, "--new-plan", "--output", str(selected), "--work-dir", str(repo)) == 0
    capsys.readouterr()
    worktree = pathlib.Path(record["worktree"])
    plan = locations.working_plans_root(pathlib.Path.home()) / "15-1400_requirement-change.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(plan_fixture.eight_section_plan(repo=worktree), encoding="utf-8")
    _plan_operation(capsys, "plan-check", "--reject-migration-warnings", "--work-dir", str(worktree), str(plan))

    _plan_operation(capsys, "plan-check", "--work-dir", str(repo), str(plan), expected=1)
    (worktree / "changed.txt").write_text("要求変更の実装", encoding="utf-8")
    head = git_repository.commit_all(worktree, "要求変更を実装")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("新計画と実装HEADをレビュー受理済み。旧計画と対象外WIは保持する。", encoding="utf-8")
    _record_wi_commit(
        capsys,
        handoff,
        worktree,
        wi,
        head,
        git_repository.git_output(worktree, "rev-parse", "HEAD^"),
        step="要求変更",
        result="新計画の実装を記録",
    )
    manifest = tmp_path / "finalization.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [
                    {"name": old.name, "saved": str(old), "commands": []},
                    {
                        "name": plan.name,
                        "commands": [
                            [sys.executable, str(pathlib.Path(atk.__file__)), "plans", "commit", str(plan), "--skip-push"]
                        ],
                    },
                ],
                "wis": [
                    {"name": wi, "commit_record": str(handoff), "commands": [_adopt_argv(repo, wi, [head])]},
                    {"name": other, "commands": []},
                ],
                "handoff": str(handoff),
                "verification": [],
            }
        ),
        encoding="utf-8",
    )
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 0 and result["phase"] == "finalized", stderr
    assert old.read_bytes() == old_before and other_source.read_bytes() == other_before
    assert not (tmp_path / "reclaimed-worktree").exists()
    assert not plan.exists()
    assert list((notes / "plans").rglob(plan.name))
    assert "state: adopted" in source.read_text(encoding="utf-8")


@pytest.mark.parametrize("old_overlap", [False, True])
def test_merge_derives_order_after_applying_new_write_target(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, old_overlap: bool
) -> None:
    repo, notes = env
    shared, old = "src/model.py", "src/independent.py"
    for path in (shared, old):
        (repo / path).write_text("x", encoding="utf-8")
    chosen = shared if old_overlap else old
    for name, target in (("a.md", shared), ("b.md", chosen), ("c.md", shared)):
        _awi(notes, name, f"`{target}`を変更する。")
    source = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": [shared]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": [chosen]},
        ],
        [{"レーン": "lane-01", "段階": 2, "先行レーン": ["lane-02"]}, {"レーン": "lane-02", "段階": 1}]
        if old_overlap
        else None,
    )
    extra = _write_selection(tmp_path / "addition.yaml", [{"WI": "c.md", "レーン": "lane-99", "書込対象": [shared]}])
    raw = yaml.safe_load(extra.read_text(encoding="utf-8"))
    del raw["初回配分"]
    facts = tmp_path / "candidates.jsonl"
    staleness = raw["選定"][0].pop("鮮度")
    facts.write_text(
        json.dumps({"filename": "c.md", "state": "inbox", "ready": True, "staleness": staleness}) + "\n", encoding="utf-8"
    )
    del raw["レーンの所要時間"][0]["根拠"]
    raw["単一段階案の完了見込み秒数"] = 1320
    extra.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"lane-99": "lane-02"}), encoding="utf-8")
    assert (
        _dispatch(
            str(source),
            "--merge",
            str(extra),
            "--candidate-file",
            str(facts),
            "--lane-map",
            str(mapping),
            "--output",
            str(source),
            "--work-dir",
            str(repo),
        )
        == 0
    )
    saved = yaml.safe_load(source.read_text(encoding="utf-8"))
    predecessor, successor = ("lane-02", "lane-01") if old_overlap else ("lane-01", "lane-02")
    assert {row["レーン"]: (row["段階"], row["先行レーン"]) for row in saved["レーンの所要時間"]} == {
        predecessor: (1, []),
        successor: (2, [predecessor]),
    }
