# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""検証コマンドと受入シナリオを検証する。"""

import pathlib

import check_plan_file
import pytest

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._plan.structure import verification
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import _check
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo


def test_rejects_missing_acceptance_scenario_for_adopted_plan(repo: tuple[pathlib.Path, str]) -> None:
    """採用行がある新書式計画は受入表を必須とする。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve())
    start = content.index("### 受入シナリオ\n")
    end = content.index("## 恒久化・リファクタリング\n", start)

    errors, _warnings = _check(work_dir, content[:start] + content[end:])

    assert any("### 受入シナリオ" in error for error in errors), errors


def test_accepts_none_acceptance_scenario_without_adopted_action(repo: tuple[pathlib.Path, str]) -> None:
    """採用行が無い計画は受入シナリオを`なし`として受理する。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve())
    content = content.replace(_plan_fixture.USER_ACTION_ROW, _plan_fixture.USER_ACTION_ROW.replace("| 採用 |", "| 不採用 |"))
    start = content.index("### 受入シナリオ\n")
    end = content.index("## 恒久化・リファクタリング\n", start)
    content = content[:start] + "### 受入シナリオ\n\nなし\n\n" + content[end:]

    errors, warnings = _check(work_dir, content)

    assert not errors, errors
    assert not warnings, warnings


def test_accepts_legacy_verification_without_acceptance_scenario(repo: tuple[pathlib.Path, str]) -> None:
    """進行中の旧2行計画は受入表なしでも読み取れる。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve())
    start = content.index("### 受入シナリオ\n")
    end = content.index("## 恒久化・リファクタリング\n", start)
    legacy = content[:start] + content[end:]
    legacy = legacy.replace("| 変更範囲の検証 | `pytest` |", "| 近接検証 | `pytest` |\n| 全体検証 | `make test` |", 1)

    errors, _warnings = _check(work_dir, legacy)

    assert not errors, errors


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("`pytest`<br>`make test`", 0), ("`pytest` 説明", 1), ("`pytest && make test`", 1), ("", 1)],
)
def test_public_plan_check_rejects_ambiguous_verification_cell(
    repo: tuple[pathlib.Path, str], capsys: pytest.CaptureFixture[str], cell: str, expected: int
) -> None:
    """CLIの`main`も、現行書式の検証表のセルに付いた説明・shell構文と空欄を拒否する。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(
        "| 変更範囲の検証 | `pytest` |", f"| 変更範囲の検証 | {cell} |", 1
    )
    path = work_dir / "plan.md"
    path.write_text(content, encoding="utf-8")
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == expected
    if expected:
        assert "次の操作:" in capsys.readouterr().err
    # 旧名「近接検証」の行を持つ進行中の計画はセルの説明とshell構文を拒否せず、従来どおり空欄だけを構造の違反とする。
    legacy = content.replace("| 変更範囲の検証 |", "| 近接検証 |", 1)
    path.write_text(legacy, encoding="utf-8")
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == (1 if not cell else 0)


@pytest.mark.parametrize("strict", [False, True])
def test_cd_verification_is_rejected_alike_at_drafting_and_integration(
    repo: tuple[pathlib.Path, str], capsys: pytest.CaptureFixture[str], strict: bool
) -> None:
    """`cd <dir> && <command>`の検証行は、起草時の計画検査と統合時の検証行取得が同じく拒否し、書き換え先を示す。

    統合時の検査（`--reject-migration-warnings`）でだけ拒否されると、統合の直列区間で計画の書換えが必要になる。
    """
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(
        "| 変更範囲の検証 | `pytest` |", "| 変更範囲の検証 | `cd agent-toolkit && uv run --frozen pytest -v` |", 1
    )
    path = work_dir / "plan.md"
    path.write_text(content, encoding="utf-8")
    options = ["--reject-migration-warnings"] if strict else []
    assert check_plan_file.main([*options, "--work-dir", str(work_dir), str(path)]) == 1
    drafting = capsys.readouterr().err
    assert "shellの再評価を要する検証コマンド" in drafting and "uv run --directory <dir>" in drafting
    with pytest.raises(ActionableError) as integration:
        verification.plan_commands(content)
    assert "shellの再評価を要する検証コマンド" in integration.value.message
    assert "uv run --directory <dir>" in integration.value.next_action


def test_legacy_verification_name_is_readable_but_rejected_for_revision(repo: tuple[pathlib.Path, str]) -> None:
    """進行中の旧名は読めるが、新規作成・改訂では移行を求める。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(
        "| 変更範囲の検証 | `pytest` |", "| 近接検証 | `pytest` |", 1
    )
    path = work_dir / "plan.md"
    path.write_text(content, encoding="utf-8")

    errors, warnings = check_plan_file.check(path, work_dir)
    strict_errors, _strict_warnings = check_plan_file.check(path, work_dir, reject_migration_warnings=True)

    assert not errors, errors
    assert any("検証名が旧形式" in warning for warning in warnings), warnings
    assert any("検証名が旧形式" in error for error in strict_errors), strict_errors


def test_current_plan_legacy_refactoring_table_is_migration_only(repo: tuple[pathlib.Path, str]) -> None:
    """旧リファクタリング表は読取時に警告し、新規作成・改訂では拒否する。"""
    work_dir, _base = repo
    legacy = """| 項目 | 内容 |
| --- | --- |
| 対象 | 判定処理 |
| 現状の問題 | 契約が旧い。 |
| 対応 | 更新する。 |
| 本計画に含めるか | 含める |"""
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(
        _plan_fixture.REFACTORING_TABLE,
        legacy,
        1,
    )
    path = work_dir / "legacy-refactoring.md"
    path.write_text(content, encoding="utf-8")

    read_errors, read_warnings = check_plan_file.check(path, work_dir)
    create_errors, create_warnings = check_plan_file.check(path, work_dir, reject_migration_warnings=True)
    message = "リファクタリング表が旧2列4行形式である。新規作成・改訂では`対象`、`現状の問題`、`対応`の3列表へ移行する"

    assert not read_errors, read_errors
    assert message in read_warnings
    assert message in create_errors
    assert message not in create_warnings


def _acceptance_header_row(header: tuple[str, ...]) -> str:
    """受入シナリオ表の見出し行を返す。"""
    return f"| {' | '.join(header)} |"


def _legacy_acceptance_plan(work_dir: pathlib.Path, legacy: tuple[str, ...]) -> str:
    """利用目的の列が無い保存済み計画を作成する。"""
    lines = _plan_fixture.current_plan(repo=work_dir.resolve()).splitlines()
    index = lines.index(_acceptance_header_row(_plan_format.PLAN_ACCEPTANCE_TABLE_HEADER))
    lines[index] = _acceptance_header_row(legacy)
    lines[index + 1] = _acceptance_header_row(tuple("---" for _ in legacy))
    cells = [cell.strip() for cell in lines[index + 2].strip("|").split("|")]
    del cells[3]
    lines[index + 2] = _acceptance_header_row(tuple(cells))
    return "\n".join(lines) + "\n"


def test_current_plan_acceptance_table_accepts_new_header(repo: tuple[pathlib.Path, str]) -> None:
    """現行の列名を持つ受入シナリオ表は新規作成・改訂として判定しても移行警告の対象にならない。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve())
    path = work_dir / "acceptance.md"
    path.write_text(content, encoding="utf-8")

    errors, warnings = check_plan_file.check(path, work_dir, reject_migration_warnings=True)

    assert _acceptance_header_row(_plan_format.PLAN_ACCEPTANCE_TABLE_HEADER) in content
    assert _plan_format.PLAN_ACCEPTANCE_TABLE_HEADER[-1] == "テスト"
    assert not errors, errors
    assert not any("受入シナリオ表" in warning for warning in warnings), warnings


@pytest.mark.parametrize("legacy", _plan_format.PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS)
def test_current_plan_legacy_acceptance_header_is_migration_only(
    repo: tuple[pathlib.Path, str], legacy: tuple[str, ...]
) -> None:
    """改名前の列名を持つ受入シナリオ表は読取時に警告して受理し、新規作成・改訂では拒否する。"""
    work_dir, _base = repo
    legacy_header = _acceptance_header_row(legacy)
    content = _legacy_acceptance_plan(work_dir, legacy)
    path = work_dir / "legacy-acceptance.md"
    path.write_text(content, encoding="utf-8")

    read_errors, read_warnings = check_plan_file.check(path, work_dir)
    create_errors, _create_warnings = check_plan_file.check(path, work_dir, reject_migration_warnings=True)

    assert legacy_header in content
    assert not read_errors, read_errors
    assert any("受入シナリオ表の列名が旧形式" in warning for warning in read_warnings), read_warnings
    assert any("受入シナリオ表の列名が旧形式" in error for error in create_errors), create_errors


@pytest.mark.parametrize("legacy", _plan_format.PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS)
def test_current_plan_legacy_acceptance_table_still_requires_filled_cells(
    repo: tuple[pathlib.Path, str], legacy: tuple[str, ...]
) -> None:
    """改名前の列名の表も現行の表と同じく空セルを拒否する。"""
    work_dir, _base = repo
    legacy_header = _acceptance_header_row(legacy)
    content = _legacy_acceptance_plan(work_dir, legacy)
    lines = content.splitlines(keepends=True)
    row_index = next(index for index, line in enumerate(lines) if line.startswith(legacy_header)) + 2
    cells = lines[row_index].rstrip("\n").split(" | ")
    cells[-1] = " |"
    lines[row_index] = " | ".join(cells) + "\n"
    content = "".join(lines)
    path = work_dir / "legacy-acceptance-empty.md"
    path.write_text(content, encoding="utf-8")

    errors, _warnings = check_plan_file.check(path, work_dir)

    assert any("空セルまたは列数不一致" in error for error in errors), errors


__all__ = ["fixture_repo"]
