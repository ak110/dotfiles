# pylint: disable=useless-import-alias,unused-import  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import pathlib
import typing

import check_plan_file
import pytest
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo

_UNMARKED = "20261101-000000-001.md"
_MARKED = "20261101-000000-002.md"
_EXEMPT = "20261101-000000-003.md"
_UWI = "20261101-000000-004.md"
_ABSENT = "20261101-000000-009.md"


def _notes(tmp_path: pathlib.Path) -> pathlib.Path:
    notes = tmp_path / "notes"
    processing = notes / "processing"
    processing.mkdir(parents=True)
    entries = {
        _UNMARKED: "type: awi\nsource: process-wi",
        _MARKED: "type: awi\nsource: process-wi\ndetailed: true",
        _EXEMPT: "type: awi\nsource: add-awi-by-user",
        _UWI: "type: uwi\nsource: process-wi",
    }
    for name, metadata in entries.items():
        body = (
            "# 要求\n\n## 調査結果と判断根拠\n\n`agent-toolkit/skills/atk-mode/playbooks/write-plan.md`の並行する更新を扱う。\n"
        )
        (processing / name).write_text(f"---\n{metadata}\n---\n{body}", encoding="utf-8")
    return notes


def _check(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    related: tuple[str, ...],
    **kwargs: typing.Any,
) -> tuple[list[str], list[str]]:
    work_dir, _base = repo
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.wi_list_plan(repo=work_dir.resolve(), related_wi=tuple((name, "") for name in related)),
        encoding="utf-8",
    )
    return check_plan_file.check(plan_path, work_dir, private_notes=_notes(tmp_path), **kwargs)


def test_marked_exempt_and_uwi_entries_pass(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    errors, _warnings = _check(repo, tmp_path, (_MARKED, _EXEMPT, _UWI))
    assert not errors, errors


def test_unmarked_detail_target_is_rejected(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    errors, _warnings = _check(repo, tmp_path, (_MARKED, _UNMARKED))
    assert any("詳細化済みの印が無い" in error and _UNMARKED in error for error in errors), errors
    assert not any(_MARKED in error for error in errors), errors


def test_absent_entry_is_reported(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    errors, _warnings = _check(repo, tmp_path, (_ABSENT,))
    assert any("計画の対象WIを取得できない" in error and _ABSENT in error for error in errors), errors


@pytest.mark.parametrize(
    ("path", "accepted"),
    [("agent-toolkit/skills/atk-mode/playbooks/write-plan.md", True), ("agent-toolkit/skills/atk-mode/SKILL.md", False)],
)
def test_agent_rule_paths_are_matched_against_wi_bodies(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path, path: str, accepted: bool
) -> None:
    errors, _warnings = _check(repo, tmp_path, (_MARKED,), agent_rule_paths=(path,))
    assert (not any(path in error for error in errors)) is accepted, errors


def test_agent_rule_path_of_undetailed_wi_is_accepted_from_handoff(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    # 詳細化しないWIは本文を置き換えられないため、編集するパスは引き継ぎ記録で受け付ける。  # pyfltr: allow-comment
    path = "agent-toolkit/skills/atk-mode/SKILL.md"
    errors, _warnings = _check(repo, tmp_path, (_EXEMPT,), agent_rule_paths=(path,), handoff_texts=(f"- 編集: `{path}`\n",))
    assert not any(path in error for error in errors), errors


@pytest.mark.parametrize(
    ("handoff", "warned"),
    [((), True), (("- 形式検証の判定: 当てない（既存の排他で直列化している）\n",), False)],
    ids=["missing", "handoff"],
)
def test_formal_verification_judgment_reads_wi_bodies_and_handoff(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path, handoff: tuple[str, ...], warned: bool
) -> None:
    errors, warnings = _check(repo, tmp_path, (_EXEMPT,), handoff_texts=handoff)
    assert not errors, errors
    assert any("形式検証の判定" in warning for warning in warnings) is warned, warnings


def test_wi_list_plan_rejects_sections(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    work_dir, _base = repo
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.wi_list_plan(repo=work_dir.resolve(), related_wi=((_MARKED, ""),)) + "\n## 反映内容と反映先\n\n内容\n",
        encoding="utf-8",
    )
    errors, _warnings = check_plan_file.check(plan_path, work_dir, private_notes=_notes(tmp_path))
    assert any("見出しをH1だけにする" in error for error in errors), errors
