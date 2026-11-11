# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""計画の関連WIと選定WIの一致の判定を検証する。"""

import pathlib

import check_plan_file
import pytest
import yaml

from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import _OBSERVATION_RESUME, _RESUMED_AWI, _RESUMED_DECISION, _run_plan_cli
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo


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
    """選定レーンのWI集合を計画メタ情報と比べ、欠落と余剰を示す。"""
    work_dir, _base = repo
    filename = _plan_fixture.WI_FILES[0][0]
    other = "20260831-000000-002.md"
    selected = {"match": (filename,), "replace": (other,), "extend": (filename, other)}[selection]
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),)), encoding="utf-8"
    )
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump({"decisions": [{"awi": name, "lane": "lane-01"} for name in selected]}),
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
    """凍結済み計画を再び判定せず、追加計画と先行計画の和集合が基準を満たすか確かめる。"""
    work_dir, _base = repo
    names = ("20260831-000000-001.md", "20260831-000000-002.md")
    current_name = _plan_fixture.WI_FILES[0][0]
    prior_paths = tuple(work_dir / f"prior-{index}.md" for index in range(prior_count))
    for index, prior_path in enumerate(prior_paths):
        prior_path.write_text(
            _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((names[index], "先行要求"),)),
            encoding="utf-8",
        )
    plan_path = work_dir / "additional.md"
    plan_path.write_text(
        _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((current_name, "追加要求"),)),
        encoding="utf-8",
    )
    selection_path = work_dir / "selection.yaml"
    selected = (*names[:prior_count], current_name, *selected_extra)
    selection_path.write_text(
        yaml.safe_dump({"decisions": [{"awi": name, "lane": "lane-01"} for name in selected]}), encoding="utf-8"
    )

    errors, _warnings = check_plan_file.check(
        plan_path, work_dir, selection_file=selection_path, lane="lane-01", prior_plans=prior_paths
    )

    if expected_fragment is None:
        assert not errors, errors
    else:
        assert any(expected_fragment in error for error in errors), errors


def test_lane_selection_reads_current_field_names(repo: tuple[pathlib.Path, str]) -> None:
    """日本語の欄名（`選定`、`WI`、`レーン`、`再開位置`）で書いた選定結果からも、旧欄名と同じくレーンの割当を読み取る。"""
    work_dir, _base = repo
    filename = _plan_fixture.WI_FILES[0][0]
    other = "20260831-000000-002.md"
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),)), encoding="utf-8"
    )
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {"WI": filename, "レーン": "lane-01"},
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
    """再開位置を含む選定結果をそのまま`plan-check`のCLIに渡し、終了コードと標準エラーを返す。"""
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=tuple((name, "要求") for name in related)),
        encoding="utf-8",
    )
    return _run_plan_cli(work_dir, monkeypatch, [plan_path], decisions, (_plan_fixture.WI_FILES[0][0],))


_NEW_AWI = _plan_fixture.WI_FILES[0][0]


_UNPLANNED_RESUMED_DECISION = {**_RESUMED_DECISION, "再開位置": _OBSERVATION_RESUME.format(plan="計画なし")}


@pytest.mark.parametrize("resumed", [_RESUMED_DECISION, _UNPLANNED_RESUMED_DECISION], ids=["planned", "unplanned"])
@pytest.mark.parametrize("new_resume_value", [None, "なし"], ids=["omitted", "explicit-none"])
def test_lane_selection_excludes_resumed_decisions(
    repo: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    new_resume_value: str | None,
    resumed: dict[str, str],
) -> None:
    """観測のみの再開位置を持つ項目は、計画の有無によらず新しい計画の対象から外れる。

    再開位置なしの集合と一致する新規計画を受理する。再開項目を新しい計画の対象に含めると、
    実装済みの項目を新しい計画で再実装することになる。
    """
    work_dir, _base = repo
    new_decision = {"awi": _NEW_AWI, "lane": "lane-01"}
    if new_resume_value is not None:
        new_decision["再開位置"] = new_resume_value
    code, stderr = _run_lane_check_with_resumed(work_dir, monkeypatch, (_NEW_AWI,), [new_decision, resumed])
    assert code == 0, stderr
    assert not stderr


def test_lane_selection_rejects_resumed_awi_in_new_plan(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """再開位置が指す既存計画で続ける項目を新規計画へ重ねると`余剰`で拒否する。"""
    work_dir, _base = repo
    code, stderr = _run_lane_check_with_resumed(
        work_dir, monkeypatch, (_NEW_AWI, _RESUMED_AWI), [{"awi": _NEW_AWI, "lane": "lane-01"}, _RESUMED_DECISION]
    )
    assert code == 1
    assert f"余剰=['{_RESUMED_AWI}']" in stderr


def test_lane_selection_rejects_lane_with_only_resumed_decisions(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """全件が再開位置を持つレーンは、新しい計画の対象が無いことを示して拒否する。"""
    work_dir, _base = repo
    code, stderr = _run_lane_check_with_resumed(work_dir, monkeypatch, (_RESUMED_AWI,), [_RESUMED_DECISION])
    assert code == 1
    assert "新しい計画の対象となるWIが無い" in stderr


def test_lane_selection_rejects_duplicate_wi_across_plans(repo: tuple[pathlib.Path, str]) -> None:
    """先行計画と追加計画の重複は、和集合が一致しても拒否する。"""
    work_dir, _base = repo
    filename = _plan_fixture.WI_FILES[0][0]
    content = _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),))
    plan_path = work_dir / "additional.md"
    prior_path = work_dir / "prior.md"
    plan_path.write_text(content, encoding="utf-8")
    prior_path.write_text(content, encoding="utf-8")
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(yaml.safe_dump({"decisions": [{"awi": filename, "lane": "lane-01"}]}), encoding="utf-8")

    errors, _warnings = check_plan_file.check(
        plan_path, work_dir, selection_file=selection_path, lane="lane-01", prior_plans=(prior_path,)
    )

    assert any("計画間で関連WIが重複する" in error for error in errors), errors


def test_cli_accepts_prior_plan_for_added_lane_wi(repo: tuple[pathlib.Path, str]) -> None:
    """公開CLIから先行計画を併記して追加レーンの全WIを確認できる。"""
    work_dir, _base = repo
    prior_name = "20260831-000000-002.md"
    current_name = _plan_fixture.WI_FILES[0][0]
    prior_path = work_dir / "prior.md"
    plan_path = work_dir / "additional.md"
    prior_path.write_text(
        _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((prior_name, "先行要求"),)), encoding="utf-8"
    )
    plan_path.write_text(
        _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((current_name, "追加要求"),)), encoding="utf-8"
    )
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(
        yaml.safe_dump({"decisions": [{"awi": name, "lane": "lane-01"} for name in (prior_name, current_name)]}),
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
    content = _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=((filename, "要求"),))
    content = content.replace(_plan_fixture.USER_ACTION_REASON, root, 1)
    plan_path = work_dir / "plan.md"
    plan_path.write_text(content, encoding="utf-8")
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(yaml.safe_dump({"decisions": [{"awi": filename, "lane": "lane-01"}]}), encoding="utf-8")

    errors, _warnings = check_plan_file.check(plan_path, work_dir, selection_file=selection_path, lane="lane-01")

    assert any("人間由来行の根拠がない" in error for error in errors), errors


__all__ = ["fixture_repo"]
