# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""計画の旧書式の移行を検証する。"""

import collections.abc
import pathlib
import subprocess
import sys

import check_plan_file
import pytest

from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._testing import git_repository
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import (
    _TWO_FILE_MIGRATION,
    _check,
    _check_new,
    _new_format_plan,
    human_new_format_plan,
)
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo

_REAL_LEGACY_TWO_FILE_PLAN = pathlib.Path("/home/aki/.claude/plans/fb-hooks-45ab5132.md")


_REAL_LEGACY_TWO_FILE_DETAIL = _REAL_LEGACY_TWO_FILE_PLAN.with_name(f"{_REAL_LEGACY_TWO_FILE_PLAN.stem}.detail.md")


type _MigrationInputFactory = collections.abc.Callable[[pathlib.Path], tuple[str, str]]


def _migration_legacy_action_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧3列表だけを加えた現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    new_table = "\n".join(
        [
            f"| {' | '.join(_plan_format.PLAN_LEGACY_ACTION_TABLE_HEADER)} |",
            "| --- | --- | --- |",
            f"| {_plan_fixture.USER_ACTION_SUBJECT} | 指示どおり | - |",
        ]
    )
    return main.replace(_plan_fixture.human_action_table(wi=False), new_table, 1), detail


def _migration_legacy_bug_table_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の本文内バグ調査表だけを加えた現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    main = main.replace("- 作業種別: 通常変更", "- 作業種別: バグ対応", 1)
    return main, _plan_fixture.inline_bug_section(variant=_plan_fixture.BUG_VARIANT_LEGACY_STANDALONE) + detail


def _migration_legacy_history_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の変更履歴見出しだけを持つ現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    return (
        main.replace(f"## {_plan_format.PLAN_H2_HISTORY}", f"## {_plan_format.PLAN_H2_LEGACY_HISTORY}", 1),
        detail,
    )


def _migration_legacy_progress_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の進捗ログ見出しだけを持つ現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    return (
        main.replace(f"## {_plan_format.PLAN_H2_PROGRESS}", f"## {_plan_format.PLAN_H2_LEGACY_PROGRESS}", 1),
        detail,
    )


def _migration_legacy_agent_judgment_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式のエージェント提案詳細の見出しだけを持つ現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    return (
        main.replace(
            f"## {_plan_format.PLAN_H2_AGENT_JUDGMENT}",
            f"## {_plan_format.PLAN_H2_LEGACY_AGENT_JUDGMENT}",
            1,
        ),
        detail,
    )


def _migration_legacy_user_event_heading_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式のユーザー発言見出しだけを持つ現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    return main.replace(_plan_fixture.USER_EVENT_HEADING, _plan_fixture.LEGACY_USER_EVENT_HEADING, 1), detail


def _migration_legacy_metadata_name_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の計画メタ情報項目名だけを加えた現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    return main.replace("- 作業種別:", "- 実装詳細: `legacy.detail.md`\n- 作業種別:", 1), detail


def _migration_legacy_detail_reference_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の計画ファイル（詳細）参照だけを加えた現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    return main.replace("- 作業種別:", "- 計画ファイル（詳細）: `legacy.detail.md`\n- 作業種別:", 1), detail


def _migration_legacy_materials_heading_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の提示素材見出しだけを加えた現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    history = f"## {_plan_format.PLAN_H2_HISTORY}"
    return main.replace(history, f"## {_plan_format.PLAN_H2_MATERIALS}\n\n旧形式の素材。\n\n{history}", 1), detail


def _migration_legacy_bug_reference_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式のバグ調査ファイル参照だけを持つ現行形式の計画を返す。"""
    main, detail = human_new_format_plan(repo)
    main = main.replace("- 作業種別: 通常変更", "- 作業種別: バグ対応", 1)
    bug_path = (repo / "migration.bugs.md").resolve()
    legacy_reference = _plan_fixture.bug_reference_section(bug_path).replace(
        _plan_format.PLAN_BUG_FILE_REFERENCE_PREFIX,
        _plan_format.PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX,
        1,
    )
    return main, legacy_reference + detail


def _migration_legacy_two_file_id_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧ID形式だけを残した二ファイル計画を返す。"""
    main, detail = _new_format_plan(repo, git_repository.git_output(repo, "rev-parse", "HEAD"))
    start = main.index(f"## {_plan_format.PLAN_H2_MATERIALS}")
    end = main.index(f"## {_plan_format.PLAN_H2_LEGACY_HISTORY}", start)
    main = main[:start] + main[end:]
    detail_field = f"- {_plan_format.PLAN_METADATA_DETAIL_FIELD}: `plan.detail.md`"
    related_field = f"- {_plan_format.PLAN_METADATA_RELATED_WI_FIELD}: なし"
    return main.replace(detail_field, related_field, 1), detail


def _migration_legacy_materials_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の提示素材表を持つ二ファイル計画を返す。"""
    main, detail = _new_format_plan(repo, git_repository.git_output(repo, "rev-parse", "HEAD"))
    main = main.replace(
        _plan_fixture.TWO_FILE_ACTION_TABLE,
        _plan_fixture.human_action_table(wi=False),
        1,
    )
    start = main.index(f"## {_plan_format.PLAN_H2_MATERIALS}")
    end = main.index(f"## {_plan_format.PLAN_H2_LEGACY_HISTORY}", start)
    return main[:start] + _plan_fixture.LEGACY_MATERIALS_SECTION + main[end:], detail


def test_new_format_accepts_legacy_detail_metadata_field_with_warning(
    repo: tuple[pathlib.Path, str],
) -> None:
    """旧形式の詳細参照項目を読み取り互換で受理し、移行警告を返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 計画ファイル（詳細）:", "- 実装詳細:")

    errors, warnings = _check_new(work_dir, main_content, detail_content)

    assert not errors, errors
    assert "計画メタ情報の項目名が旧形式である。新規作成・改訂では`計画ファイル（詳細）`へ移行する" in warnings


def test_new_format_accepts_legacy_bug_file_reference_with_warning(
    repo: tuple[pathlib.Path, str],
) -> None:
    """旧形式の計画ファイル（バグ）参照を読み取り互換で受理し、移行警告を返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    detail_content = detail_content.replace("- 計画ファイル（バグ）:", "- バグ調査ファイル:")

    errors, warnings = _check_new(work_dir, main_content, detail_content)

    assert not errors, errors
    assert "バグ調査ファイル参照が旧形式である。新規作成・改訂では`- 計画ファイル（バグ）:`へ移行する" in warnings


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("| 変更範囲の検証 |", "| レーン内検証 |", "固定行"),
        ("### リファクタリング", "### 自由見出し", "固定見出し"),
        ("対象の公開契約を更新する。", "#### 深い見出し", "H4以深"),
    ],
)
def test_rejects_current_single_file_structure_violations(
    repo: tuple[pathlib.Path, str], old: str, new: str, message: str
) -> None:
    """現行書式の固定節、固定表、見出し深度の違反を拒否する。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(old, new, 1)

    errors, _warnings = _check(work_dir, content)

    assert any(message in error for error in errors), errors


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            _migration_legacy_action_input,
            "実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する",
        ),
        (
            _migration_legacy_bug_table_input,
            "バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する",
        ),
        (
            _migration_legacy_history_input,
            "変更履歴の見出しが旧形式である。新規作成・改訂では`## 変更履歴（計画時）`へ移行する",
        ),
        (
            _migration_legacy_progress_input,
            "進捗ログの見出しが旧形式である。新規作成・改訂では`## 進捗ログ（実行時）`へ移行する",
        ),
        (
            _migration_legacy_agent_judgment_input,
            "エージェント提案の詳細の見出しが旧形式である。新規作成・改訂では`## エージェント提案詳細`へ移行する",
        ),
        (
            _migration_legacy_user_event_heading_input,
            "変更履歴のユーザー発言見出しが旧形式である。新規作成・改訂では`### ユーザー発言<1から始まる連番>`へ移行する",
        ),
        (
            _migration_legacy_metadata_name_input,
            "計画メタ情報の項目名が旧形式である。新規作成・改訂では`計画ファイル（詳細）`へ移行する",
        ),
        (
            _migration_legacy_detail_reference_input,
            "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
        ),
        (
            _migration_legacy_materials_heading_input,
            "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する",
        ),
        (
            _migration_legacy_bug_reference_input,
            "バグ調査ファイル参照が旧形式である。新規作成・改訂では`- 計画ファイル（バグ）:`へ移行する",
        ),
        (
            _migration_legacy_two_file_id_input,
            "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する",
        ),
        (
            _migration_legacy_materials_input,
            "提示素材が旧形式である。新規作成・改訂では素材表と要求表へ移行する",
        ),
    ],
)
def test_rejects_each_migration_warning_for_new_creation(
    repo: tuple[pathlib.Path, str],
    factory: _MigrationInputFactory,
    message: str,
) -> None:
    """移行警告を個別に新規作成用のエラーへ移す。"""
    work_dir, _base = repo
    main_content, detail_content = factory(work_dir)

    errors, warnings = _check_new(
        work_dir,
        main_content,
        detail_content,
        plan_name="migration.md",
        reject_migration_warnings=True,
    )

    assert message in errors
    assert message not in warnings


def test_rejects_progress_log_rows_only_for_new_creation(repo: tuple[pathlib.Path, str]) -> None:
    """進捗ログの内容行を持つ本文は新規作成で失敗し、既存計画の読み取りでは成功する。"""
    work_dir, _base = repo
    main_content, detail_content = human_new_format_plan(work_dir)
    main_content = main_content.replace(
        _plan_fixture.PROGRESS_TABLE, _plan_fixture.PROGRESS_TABLE + _plan_fixture.PROGRESS_ROW, 1
    )

    read_errors, read_warnings = _check_new(work_dir, main_content, detail_content, plan_name="progress-read.md")
    create_errors, _create_warnings = _check_new(
        work_dir,
        main_content,
        detail_content,
        plan_name="progress-create.md",
        reject_migration_warnings=True,
        reject_progress_log_rows=True,
    )

    assert not read_errors, read_errors
    assert read_warnings == [_TWO_FILE_MIGRATION]
    assert _TWO_FILE_MIGRATION in create_errors
    assert any("起草時に内容行を置かない" in error for error in create_errors), create_errors


def test_keeps_plan_size_advisory_when_rejecting_migration_warnings(repo: tuple[pathlib.Path, str]) -> None:
    """旧形式を拒否する場合も行数の助言を警告に残す。"""
    work_dir, _base = repo
    main_content, detail_content = human_new_format_plan(work_dir)
    padding = 1201 - len(detail_content.splitlines())
    detail_content = detail_content.rstrip("\n") + "\n" + "\n".join(["行数の助言を検証する。"] * padding) + "\n"
    assert len(detail_content.splitlines()) == 1201

    errors, warnings = _check_new(
        work_dir,
        main_content,
        detail_content,
        plan_name="advisory.md",
        reject_migration_warnings=True,
    )

    assert errors == [_TWO_FILE_MIGRATION]
    assert len(warnings) == 1
    assert warnings[0].startswith("計画の行数が閾値を超えている")
    # 撤去済みの節と付属素材の規定を案内すると、実行主体が実在しない規定を探す。
    assert "実装資料と完了条件" not in warnings[0]
    assert "付属素材" not in warnings[0]


def test_rejects_all_migration_warnings_in_legacy_two_file_plan(repo: tuple[pathlib.Path, str]) -> None:
    """旧二ファイル形式で同時に発生する移行警告を全てエラーへ移す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    expected = [
        "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
        "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する",
        "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する",
    ]

    errors, warnings = _check_new(
        work_dir,
        main_content,
        detail_content,
        reject_migration_warnings=True,
    )

    assert errors == expected
    assert not warnings


@pytest.mark.parametrize("variant", [_plan_fixture.BUG_VARIANT_PREVIOUS_ROWS, _plan_fixture.BUG_VARIANT_LEGACY_ROWS])
def test_new_format_accepts_legacy_bug_table_rows_with_warning(repo: tuple[pathlib.Path, str], variant: str) -> None:
    """6区分化前と統廃合前の行構成を持つ調査表を読み取りで受理し、移行warningを返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    legacy_bug_file = _plan_fixture.bug_file(variant=variant)
    errors, warnings = _check_new(work_dir, main_content, detail_content, bug_file_content=legacy_bug_file)
    assert not errors, errors
    assert any("現行より前の行構成" in warning for warning in warnings), warnings


def _run_rejecting_migration_warnings(work_dir: pathlib.Path, path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """改訂用の`--reject-migration-warnings`を付けてCLIで計画を確かめる。"""
    return subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(check_plan_file.__file__)),
            "--reject-migration-warnings",
            "--work-dir",
            str(work_dir),
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_cli_rejects_migration_warnings_on_revision(repo: tuple[pathlib.Path, str]) -> None:
    """改訂用CLI入力では読み取り互換の移行警告をエラーとして返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, detail_name="revision-plan.detail.md")
    path = work_dir / "revision-plan.md"
    path.write_text(main_content, encoding="utf-8")
    (work_dir / "revision-plan.detail.md").write_text(detail_content, encoding="utf-8")

    result = _run_rejecting_migration_warnings(work_dir, path)

    assert result.returncode == 1
    assert "[warn]" not in result.stderr
    assert "二ファイル計画が旧ID形式である" in result.stderr


def test_cli_allows_progress_rows_when_rejecting_migration_warnings(repo: tuple[pathlib.Path, str]) -> None:
    """改訂用CLI入力は移行警告を拒否しても実行工程の進捗行を保持する。"""
    work_dir, _base = repo
    main_content = _plan_fixture.current_plan(repo=work_dir.resolve())
    main_content = main_content.replace(
        _plan_fixture.PROGRESS_TABLE,
        _plan_fixture.PROGRESS_TABLE + _plan_fixture.PROGRESS_ROW,
        1,
    )
    path = work_dir / "progress-revision.md"
    path.write_text(main_content, encoding="utf-8")

    result = _run_rejecting_migration_warnings(work_dir, path)

    assert result.returncode == 0, result.stderr
    assert not result.stderr


@pytest.mark.skipif(
    not (_REAL_LEGACY_TWO_FILE_PLAN.is_file() and _REAL_LEGACY_TWO_FILE_DETAIL.is_file()),
    reason="実在する旧二ファイル計画がこの環境に無い",
)
def test_cli_accepts_review_ids_in_real_legacy_two_file_plan() -> None:
    """実在する旧二ファイル計画を公式CLIで基準を満たすか確かめ、旧IDをエラーにしない。"""
    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(check_plan_file.__file__)),
            "--work-dir",
            "/home/aki/dotfiles",
            str(_REAL_LEGACY_TWO_FILE_PLAN),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert not any("レビュー指摘行の`ID`" in line for line in result.stderr.splitlines()), result.stderr
    assert "[warn] 二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する" in result.stderr


def test_new_canonical_headings_reject_legacy_id_tables(repo: tuple[pathlib.Path, str]) -> None:
    """新しい固定H2と旧ID表を混在させたcanonical形式を拒否する。"""
    work_dir, base = repo
    legacy_main_content, detail_content = _new_format_plan(work_dir, base, detail_name="canonical-plan.detail.md")
    main_content = _plan_fixture.to_canonical_main(legacy_main_content)
    errors, warnings = _check_new(work_dir, main_content, detail_content, plan_name="canonical-plan.md")
    assert any("canonical形式の`## 実施内容`" in error for error in errors), errors
    assert "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する" not in warnings

    mixed = legacy_main_content.replace("canonical-plan.detail.md", "mixed-plan.detail.md", 1)
    errors, warnings = _check_new(work_dir, mixed, detail_content, plan_name="mixed-plan.md")
    assert not errors, errors
    assert "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する" in warnings, warnings


def test_legacy_wi_names_are_read_compatible_and_warned(repo: tuple[pathlib.Path, str]) -> None:
    """改名前の項目名は読み取りで受理し、移行warningを返す。"""
    work_dir, _base = repo
    main_content, detail_content = human_new_format_plan(work_dir)
    main_content = _plan_fixture.legacy_wi_names(main_content)
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    assert (
        f"計画メタ情報の項目名が旧形式である。新規作成・改訂では`{_plan_format.PLAN_METADATA_RELATED_WI_FIELD}`へ移行する"
        in warnings
    ), warnings


def test_legacy_wi_names_are_rejected_on_creation(repo: tuple[pathlib.Path, str]) -> None:
    """新規作成・改訂を行う場合は改名前の項目名を拒否する。"""
    work_dir, _base = repo
    main_content, detail_content = human_new_format_plan(work_dir)
    main_content = _plan_fixture.legacy_wi_names(main_content)
    errors, _warnings = _check_new(work_dir, main_content, detail_content, reject_migration_warnings=True)
    assert any(_plan_format.PLAN_METADATA_RELATED_WI_FIELD in error for error in errors), errors


__all__ = ["fixture_repo"]
