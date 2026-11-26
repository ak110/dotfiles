# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import pathlib

import check_plan_file
import pytest
import yaml
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import _OBSERVATION_RESUME, _RESUMED_AWI, _RESUMED_DECISION, _run_plan_cli
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo
from agent_toolkit._testing.selection_check_support import _dispatch as _dispatch_selection
from agent_toolkit._testing.selection_check_support import _write_selection


@pytest.mark.parametrize(
    ("selection", "expected_fragment"),
    [
        ("match", None),
        ("replace", "欠落=['20260831-000000-002.md']"),
        ("extend", "欠落=['20260831-000000-002.md']"),
    ],
)
def test_lane_selection_checks_related_wi_set(
    repo: tuple[pathlib.Path, str], selection: str, expected_fragment: str | None
) -> None:
    work_dir, _base = repo
    filename = _plan_fixture.WI_FILES[0][0]
    other = "20260831-000000-002.md"
    selected = {"match": (filename,), "replace": (other,), "extend": (filename, other)}[selection]
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),)), encoding="utf-8"
    )
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump({"decisions": [{"awi": name, "lane": "lane-01", "詳細化要否": "詳細化あり"} for name in selected]}),
        encoding="utf-8",
    )

    errors, _warnings = check_plan_file.check(plan_path, work_dir, selection_file=selection_path, lane="lane-01")

    if expected_fragment is None:
        assert not errors, errors
    else:
        assert any(expected_fragment in error for error in errors), errors
        if selection == "replace":
            assert any(f"余剰=['{filename}']" in error for error in errors), errors


@pytest.mark.parametrize(
    ("prior_count", "selected_extra", "expected_fragment"),
    (
        (1, (), None),
        (2, (), None),
        (1, ("20260831-000000-004.md",), "欠落=['20260831-000000-004.md']"),
    ),
)
def test_lane_selection_combines_prior_plans(
    repo: tuple[pathlib.Path, str], prior_count: int, selected_extra: tuple[str, ...], expected_fragment: str | None
) -> None:
    work_dir, _base = repo
    names = ("20260831-000000-001.md", "20260831-000000-002.md")
    current_name = _plan_fixture.WI_FILES[0][0]
    prior_paths = tuple(work_dir / f"prior-{index}.md" for index in range(prior_count))
    for index, prior_path in enumerate(prior_paths):
        prior_path.write_text(
            _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((names[index], "先行要求"),)),
            encoding="utf-8",
        )
    plan_path = work_dir / "additional.md"
    plan_path.write_text(
        _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((current_name, "追加要求"),)),
        encoding="utf-8",
    )
    selection_path = work_dir / "selection.yaml"
    selected = (*names[:prior_count], current_name, *selected_extra)
    selection_path.write_text(
        yaml.safe_dump({"decisions": [{"awi": name, "lane": "lane-01", "詳細化要否": "詳細化あり"} for name in selected]}),
        encoding="utf-8",
    )

    errors, _warnings = check_plan_file.check(
        plan_path, work_dir, selection_file=selection_path, lane="lane-01", prior_plans=prior_paths
    )

    if expected_fragment is None:
        assert not errors, errors
    else:
        assert any(expected_fragment in error for error in errors), errors


def test_lane_selection_reads_current_field_names(repo: tuple[pathlib.Path, str]) -> None:
    work_dir, _base = repo
    filename = _plan_fixture.WI_FILES[0][0]
    other = "20260831-000000-002.md"
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),)), encoding="utf-8"
    )
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {"WI": filename, "レーン": "lane-01", "詳細化要否": "詳細化あり"},
                    {"WI": other, "レーン": "lane-01", "再開位置": "/tmp/plan.md 実装"},
                    {"WI": "20260831-000000-003.md", "レーン": "lane-02"},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    errors, _warnings = check_plan_file.check(plan_path, work_dir, selection_file=selection_path, lane="lane-01")

    assert not errors, errors


def _run_lane_check_with_resumed(
    work_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    related: tuple[str, ...],
    decisions: list[dict[str, str]],
) -> tuple[int, str]:
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=tuple((name, "要求") for name in related)),
        encoding="utf-8",
    )
    return _run_plan_cli(work_dir, monkeypatch, [plan_path], decisions, (_plan_fixture.WI_FILES[0][0],))


_NEW_AWI = _plan_fixture.WI_FILES[0][0]


_UNPLANNED_RESUMED_DECISION = {**_RESUMED_DECISION, "再開位置": _OBSERVATION_RESUME.format(plan="計画なし")}


def test_lane_selection_requires_all_lane_wis_regardless_of_detail_requirement(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir, _base = repo
    other = "20260831-000000-009.md"
    code, stderr = _run_lane_check_with_resumed(
        work_dir,
        monkeypatch,
        (_NEW_AWI,),
        [{"WI": _NEW_AWI, "レーン": "lane-01", "詳細化要否": "詳細化あり"}, {"WI": other, "レーン": "lane-01"}],
    )
    assert code != 0
    assert f"欠落=['{other}']" in stderr


@pytest.mark.parametrize("resumed", [_RESUMED_DECISION, _UNPLANNED_RESUMED_DECISION], ids=["planned", "unplanned"])
@pytest.mark.parametrize("new_resume_value", [None, "なし"], ids=["omitted", "explicit-none"])
def test_lane_selection_excludes_resumed_decisions(
    repo: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    new_resume_value: str | None,
    resumed: dict[str, str],
) -> None:
    work_dir, _base = repo
    new_decision = {"awi": _NEW_AWI, "lane": "lane-01", "詳細化要否": "詳細化あり"}
    if new_resume_value is not None:
        new_decision["再開位置"] = new_resume_value
    code, stderr = _run_lane_check_with_resumed(work_dir, monkeypatch, (_NEW_AWI,), [new_decision, resumed])
    assert code == 0, stderr
    assert not stderr


def test_lane_selection_rejects_resumed_awi_in_new_plan(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir, _base = repo
    code, stderr = _run_lane_check_with_resumed(
        work_dir,
        monkeypatch,
        (_NEW_AWI, _RESUMED_AWI),
        [{"awi": _NEW_AWI, "lane": "lane-01", "詳細化要否": "詳細化あり"}, _RESUMED_DECISION],
    )
    assert code == 1
    assert f"余剰=['{_RESUMED_AWI}']" in stderr


def test_lane_selection_rejects_lane_with_only_resumed_decisions(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir, _base = repo
    code, stderr = _run_lane_check_with_resumed(work_dir, monkeypatch, (_RESUMED_AWI,), [_RESUMED_DECISION])
    assert code == 1
    assert "新しい計画の対象となるWIが無い" in stderr


def test_lane_selection_rejects_duplicate_wi_across_plans(repo: tuple[pathlib.Path, str]) -> None:
    work_dir, _base = repo
    filename = _plan_fixture.WI_FILES[0][0]
    content = _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),))
    plan_path = work_dir / "additional.md"
    prior_path = work_dir / "prior.md"
    plan_path.write_text(content, encoding="utf-8")
    prior_path.write_text(content, encoding="utf-8")
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump({"decisions": [{"awi": filename, "lane": "lane-01", "詳細化要否": "詳細化あり"}]}), encoding="utf-8"
    )

    errors, _warnings = check_plan_file.check(
        plan_path, work_dir, selection_file=selection_path, lane="lane-01", prior_plans=(prior_path,)
    )

    assert any("計画間で関連WIが重複する" in error for error in errors), errors


def test_cli_accepts_prior_plan_for_added_lane_wi(repo: tuple[pathlib.Path, str]) -> None:
    work_dir, _base = repo
    prior_name = "20260831-000000-002.md"
    current_name = _plan_fixture.WI_FILES[0][0]
    prior_path = work_dir / "prior.md"
    plan_path = work_dir / "additional.md"
    prior_path.write_text(
        _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((prior_name, "先行要求"),)), encoding="utf-8"
    )
    plan_path.write_text(
        _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((current_name, "追加要求"),)), encoding="utf-8"
    )
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump(
            {"decisions": [{"awi": name, "lane": "lane-01", "詳細化要否": "詳細化あり"} for name in (prior_name, current_name)]}
        ),
        encoding="utf-8",
    )

    assert (
        check_plan_file.main(
            [
                "--selection-file",
                str(selection_path),
                "--lane",
                "lane-01",
                "--prior-plan",
                str(prior_path),
                "--work-dir",
                str(work_dir),
                str(plan_path),
            ]
        )
        == 0
    )


@pytest.mark.parametrize("root", ("", "-"))
def test_lane_selection_rejects_missing_human_reason(repo: tuple[pathlib.Path, str], root: str) -> None:
    """人間由来の実施行に根拠がない計画は、選定レーンを指定すると基準を満たさず失敗する。"""
    work_dir, _base = repo
    filename = _plan_fixture.WI_FILES[0][0]
    content = _plan_fixture.eight_section_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),))
    content = content.replace(_plan_fixture.USER_ACTION_REASON, root, 1)
    plan_path = work_dir / "plan.md"
    plan_path.write_text(content, encoding="utf-8")
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump({"decisions": [{"awi": filename, "lane": "lane-01", "詳細化要否": "詳細化あり"}]}), encoding="utf-8"
    )

    errors, _warnings = check_plan_file.check(plan_path, work_dir, selection_file=selection_path, lane="lane-01")

    assert any("人間由来行の根拠がない" in error for error in errors), errors


__all__ = ["fixture_repo"]


def test_lane_with_only_undetailed_wis_matches_plan(repo: tuple[pathlib.Path, str]) -> None:
    work_dir, _base = repo
    wi = _plan_fixture.WI_FILES[0]
    plan_path = work_dir / "plan.md"
    plan_path.write_text(_plan_fixture.awi_plan(repo=work_dir, related_wi=(wi,)), encoding="utf-8")
    selection = work_dir / "selection.yaml"
    selection.write_text(
        yaml.safe_dump({"選定": [{"WI": wi[0], "レーン": "lane-01", "詳細化要否": "詳細化なし"}]}, allow_unicode=True),
        encoding="utf-8",
    )

    errors, _warnings = check_plan_file.check(plan_path, work_dir, selection_file=selection, lane="lane-01")

    assert not any("一致しない" in error or "既存計画で続ける" in error for error in errors), errors


@pytest.mark.parametrize("include_optional", [False, True])
def test_plan_must_list_wis_without_detail_requirement(repo: tuple[pathlib.Path, str], include_optional: bool) -> None:
    work_dir, _base = repo
    required, optional = _plan_fixture.WI_FILES[:2]
    plan_path = work_dir / "plan.md"
    related = (required, optional) if include_optional else (required,)
    plan_path.write_text(_plan_fixture.awi_plan(repo=work_dir, related_wi=related), encoding="utf-8")
    selection = work_dir / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {"WI": required[0], "レーン": "lane-01", "詳細化要否": "詳細化あり"},
                    {"WI": optional[0], "レーン": "lane-01", "詳細化要否": "詳細化なし"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    errors, _warnings = check_plan_file.check(plan_path, work_dir, selection_file=selection, lane="lane-01")

    if include_optional:
        assert not any("一致しない" in error for error in errors), errors
    else:
        assert any("欠落" in error and optional[0] in error for error in errors), errors


@pytest.mark.parametrize("include_required", [False, True])
def test_revalidated_legacy_selection_drives_plan_check(repo, tmp_path, monkeypatch, capsys, include_required):
    work_dir, _base = repo
    required, optional = _plan_fixture.WI_FILES[:2]
    notes = tmp_path / "isolated-notes"
    processing = notes / "processing"
    processing.mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    (processing / optional[0]).write_text("---\ntype: awi\nsource: add-awi-by-user\n---\n# 統合済みの要求\n", encoding="utf-8")
    paths = ["plan-check-required.py", "plan-check-optional.py"]
    (processing / required[0]).write_text(
        f"---\ntype: awi\nsource: process-wi\n---\n# 要求\n\n## 反映内容と反映先\n\n`{paths[0]}`を変える。\n",
        encoding="utf-8",
    )
    for path in paths:
        (work_dir / path).write_text("pass\n", encoding="utf-8")
    selection = _write_selection(
        tmp_path / "legacy-selection.yaml",
        [
            {
                "WI": required[0],
                "レーン": "lane-01",
                "詳細化要否": "詳細化あり",
                "書込対象": paths[:1],
                "公開工程の書込対象": [],
            },
            {
                "WI": optional[0],
                "レーン": "lane-01",
                "統合状態": "統合済み",
                "書込対象": paths[1:],
                "公開工程の書込対象": [],
            },
        ],
    )
    before = yaml.safe_load(selection.read_bytes())
    assert (
        _dispatch_selection(str(selection), "--output", str(selection), "--work-dir", str(work_dir), "--body-wi", required[0])
        == 0
    )
    saved = yaml.safe_load(selection.read_bytes())
    assert saved["初回配分"] == before["初回配分"]
    assert saved["選定"] == [before["選定"][0], {**before["選定"][1], "詳細化要否": "詳細化なし"}]

    plan_path = work_dir / "plan.md"
    related = (required,) if include_required else (optional,)
    plan_path.write_text(_plan_fixture.awi_plan(repo=work_dir, related_wi=related), encoding="utf-8")
    code = check_plan_file.main(
        [str(plan_path), "--selection-file", str(selection), "--lane", "lane-01", "--work-dir", str(work_dir)]
    )
    diagnostic = capsys.readouterr().err
    if include_required:
        assert code == 0, diagnostic
    else:
        assert code != 0 and f"欠落=['{required[0]}']" in diagnostic
        assert f"欠落=['{optional[0]}']" not in diagnostic
