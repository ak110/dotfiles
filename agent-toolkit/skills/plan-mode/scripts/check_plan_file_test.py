"""意味契約中心の計画検査を検証する。"""

import collections.abc
import contextlib
import io
import pathlib
import subprocess
import sys
import typing

import check_plan_file
import pytest
import yaml

from agent_toolkit._plan import fixture as _plan_fixture  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._plan import locations as _plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._plan import structure as _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_REAL_LEGACY_TWO_FILE_PLAN = pathlib.Path("/home/aki/.claude/plans/fb-hooks-45ab5132.md")
_REAL_LEGACY_TWO_FILE_DETAIL = _REAL_LEGACY_TWO_FILE_PLAN.with_name(f"{_REAL_LEGACY_TWO_FILE_PLAN.stem}.detail.md")
_TOOLKIT_PREFIX = "agent-" + "toolkit"
_TWO_FILE_MIGRATION = "旧二ファイル書式である。新規作成・改訂では現行の1ファイル書式へ移行する"
_FILENAME_ERROR_MARKER = "計画作業root直下の計画ファイル名が保存工程の受理条件を満たさない"

type _MigrationInputFactory = collections.abc.Callable[[pathlib.Path], tuple[str, str]]


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用リポジトリでgitを実行して標準出力を返す。"""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """計画検査用のGitリポジトリを作成する。"""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("test\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _plan(repo: pathlib.Path, base: str, *, bug: bool = False, exclusions: bool = True) -> str:
    """旧単一ファイル形式の正規計画を組み立てる。"""
    return _plan_fixture.single_file_plan(repo=repo.resolve(), base=base, bug=bug, exclusions=exclusions)


def _new_format_plan(
    repo: pathlib.Path, base: str, *, bug: bool = False, detail_name: str = "plan.detail.md"
) -> tuple[str, str]:
    """旧二ファイル形式（計画ファイル（メイン）・計画ファイル（詳細））の正規計画を組み立てて返す。"""
    work_type = "バグ対応" if bug else "通常変更"
    main = _plan_fixture.two_file_main(
        repo=repo.resolve(),
        base=base,
        detail_name=detail_name,
        work_type=work_type,
    )
    bug_section = ""
    if bug:
        bug_stem = detail_name.removesuffix(_plan_format.PLAN_DETAIL_SUFFIX)
        bug_section = _plan_fixture.bug_reference_section((repo / f"{bug_stem}.bugs.md").resolve())
    return main, _plan_fixture.two_file_detail(bug_section=bug_section)


def human_new_format_plan(repo: pathlib.Path) -> tuple[str, str]:
    """新規作成用の人間向け計画ファイル（メイン）・計画ファイル（詳細）fixtureを返す。"""
    return _plan_fixture.human_main(repo=repo.resolve()), _plan_fixture.human_detail()


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
    main, detail = _new_format_plan(repo, _git(repo, "rev-parse", "HEAD"))
    start = main.index(f"## {_plan_format.PLAN_H2_MATERIALS}")
    end = main.index(f"## {_plan_format.PLAN_H2_LEGACY_HISTORY}", start)
    main = main[:start] + main[end:]
    detail_field = f"- {_plan_format.PLAN_METADATA_DETAIL_FIELD}: `plan.detail.md`"
    related_field = f"- {_plan_format.PLAN_METADATA_RELATED_WI_FIELD}: なし"
    return main.replace(detail_field, related_field, 1), detail


def _migration_legacy_materials_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の提示素材表を持つ二ファイル計画を返す。"""
    main, detail = _new_format_plan(repo, _git(repo, "rev-parse", "HEAD"))
    main = main.replace(
        _plan_fixture.TWO_FILE_ACTION_TABLE,
        _plan_fixture.human_action_table(wi=False),
        1,
    )
    start = main.index(f"## {_plan_format.PLAN_H2_MATERIALS}")
    end = main.index(f"## {_plan_format.PLAN_H2_LEGACY_HISTORY}", start)
    return main[:start] + _plan_fixture.LEGACY_MATERIALS_SECTION + main[end:], detail


def _legacy_plan(repo: pathlib.Path, base: str) -> str:
    """旧形式の素材と合意表を持つ計画fixtureを返す。"""
    return _plan_fixture.legacy_materials_single_file_plan(repo=repo.resolve(), base=base)


def _check_new(
    repo: pathlib.Path,
    main_content: str,
    detail_content: str,
    *,
    plan_name: str = "plan.md",
    create_bug_file: bool = True,
    bug_file_content: str | None = None,
    reject_migration_warnings: bool = False,
    reject_progress_log_rows: bool = False,
) -> tuple[list[str], list[str]]:
    """新書式の計画（計画ファイル（メイン）・計画ファイル（詳細））を一時ファイルへ保存して検査する。"""
    path = repo / plan_name
    path.write_text(main_content, encoding="utf-8")
    detail_path = repo / f"{path.stem}.detail.md"
    detail_path.write_text(detail_content, encoding="utf-8")
    reference = _plan_format.extract_bug_file_reference(detail_content)
    if create_bug_file and reference is not None:
        if _plan_file.is_plan_adjunct_reference(reference):
            bug_path = _plan_file.resolve_plan_adjunct_reference(reference, plan_path=path)
        else:
            bug_path = pathlib.Path(reference)
        bug_path.write_text(bug_file_content or _plan_fixture.bug_file(), encoding="utf-8")
    return check_plan_file.check(
        path,
        repo,
        reject_migration_warnings=reject_migration_warnings,
        reject_progress_log_rows=reject_progress_log_rows,
    )


def _check(repo: pathlib.Path, content: str) -> tuple[list[str], list[str]]:
    """計画を一時ファイルへ保存して検査する。"""
    path = repo / "plan.md"
    path.write_text(content, encoding="utf-8")
    return check_plan_file.check(path, repo)


def _replace_action_table(content: str, rows: list[str], *, legacy: bool = False) -> str:
    """fixtureの現行列構成に依存せず、実施内容表を指定した新旧形式へ置き換える。"""
    header = (
        f"| {' | '.join(_plan_format.PLAN_LEGACY_ACTION_TABLE_HEADER)} |\n| --- | --- | --- |"
        if legacy
        else f"| {' | '.join(_plan_format.PLAN_ACTION_TABLE_HEADER)} |\n| --- | --- | --- | --- |"
    )
    start = content.index(f"## {_plan_format.PLAN_H2_ACTION}")
    end = content.index(f"\n## {_plan_format.PLAN_H2_MATERIALS}", start)
    rows_text = "\n".join(rows)
    section = f"## {_plan_format.PLAN_H2_ACTION}\n\n{header}\n{rows_text}\n"
    return content[:start] + section + content[end:]


@pytest.mark.parametrize(("bug", "exclusions"), [(False, True), (False, False), (True, True)])
def test_accepts_canonical_plan(repo: tuple[pathlib.Path, str], *, bug: bool, exclusions: bool) -> None:
    """通常・バグ対応と任意表の有無を受理する。"""
    work_dir, base = repo
    content = _plan(work_dir, base, bug=bug, exclusions=exclusions)
    errors, warnings = _check(work_dir, content)
    assert not errors
    expected = (
        ["実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する"]
        if _plan_format.has_legacy_action_table(content)
        else []
    )
    if bug:
        expected.append("バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する")
    expected.append("`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する")
    assert warnings == expected


@pytest.mark.parametrize(("total_lines", "expected_warnings"), [(1200, 0), (1201, 1)])
def test_warns_above_line_threshold_only(repo: tuple[pathlib.Path, str], total_lines: int, expected_warnings: int) -> None:
    """行数の閾値ちょうどでは警告せず、1行超過で警告1件を返す。"""
    work_dir, base = repo
    content = _plan(work_dir, base)
    padding = total_lines - len(content.splitlines())
    content = content.replace("対象の構造を更新する。", "\n".join(["対象の構造を更新する。"] * (padding + 1)), 1)
    assert len(content.splitlines()) == total_lines
    errors, warnings = _check(work_dir, content)
    assert not errors, errors
    assert len(warnings) == expected_warnings + 1, warnings


def test_cli_accepts_mixed_agreements_and_numeric_target(repo: tuple[pathlib.Path, str]) -> None:
    """条項分解した実施・除外・保持と数値目標を含む正規fixtureをCLIで受理する。"""
    work_dir, base = repo
    path = work_dir / "mixed-plan.md"
    path.write_text(_plan(work_dir, base), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--work-dir", str(work_dir), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "`## 提示素材`が旧形式である" in result.stderr


def test_cli_rejects_action_reference_to_rejected_requirement(repo: tuple[pathlib.Path, str]) -> None:
    """CLI経由でも実施内容から不採用要求への参照を拒否する。"""
    work_dir, base = repo
    content = _plan(work_dir, base).replace(
        "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-001 |",
        "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-002 |",
        1,
    )
    path = work_dir / "rejected-reference-plan.md"
    path.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--work-dir", str(work_dir), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "不採用要求を参照できない: R-P-001-002" in result.stderr


def test_cli_reports_missing_completion_once(repo: tuple[pathlib.Path, str]) -> None:
    """完了条件の欠落はCLI経由でも診断1件だけを返す。"""
    work_dir, base = repo
    path = work_dir / "missing-completion.md"
    content = _plan(work_dir, base).replace(
        "## 完了条件\n\n基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。\n\n",
        "",
        1,
    )
    path.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--work-dir", str(work_dir), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    diagnostics = [line for line in result.stderr.splitlines() if line and not line.startswith(("[warn]", "次の操作: "))]
    assert result.returncode == 1
    assert len(diagnostics) == 1, diagnostics
    assert "`## 完了条件`は1件必要" in diagnostics[0]
    assert result.stderr.splitlines()[-1].startswith("次の操作: ")
    assert "同じコマンドで再検査する" in result.stderr.splitlines()[-1]


def test_cli_warns_for_legacy_materials_without_changing_exit_code(repo: tuple[pathlib.Path, str]) -> None:
    """旧形式は移行warningを出力するが終了コード0で受理する。"""
    work_dir, base = repo
    path = work_dir / "legacy-plan.md"
    path.write_text(_legacy_plan(work_dir, base), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--work-dir", str(work_dir), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "旧形式" in result.stderr


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            (
                "## 完了条件\n\n基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。\n\n",
                "",
            ),
            "固定H2",
        ),
        (("## 実装資料", "## 任意資料"), "固定H2"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | 実装経過 | P-001 |"), "`起点`は"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | ユーザー発言 | 要約 |"), "素材IDだけを書く"),
        (("### 変更説明", "## 追加H2"), "固定H2"),
    ],
)
def test_rejects_structure_violations(repo: tuple[pathlib.Path, str], mutation: tuple[str, str], message: str) -> None:
    """固定H2、変更履歴、自由見出しの違反を拒否する。"""
    work_dir, base = repo
    errors, _warnings = _check(work_dir, _plan(work_dir, base).replace(*mutation, 1))
    assert any(message in error for error in errors), errors


def test_rejects_unclosed_fence(repo: tuple[pathlib.Path, str]) -> None:
    """閉じていないMarkdownフェンスを拒否する。"""
    work_dir, base = repo
    content = _legacy_plan(work_dir, base).replace("```\n\n## 変更履歴", "\n\n## 変更履歴", 1)
    errors, _warnings = _check(work_dir, content)
    assert any("閉じていないMarkdownフェンス" in error for error in errors)


def test_accepts_unresolvable_base_reference(repo: tuple[pathlib.Path, str]) -> None:
    """計画作成時点の参考値は対象リポジトリで解決できなくても受理する。"""
    work_dir, base = repo
    errors, _warnings = _check(work_dir, _plan(work_dir, base).replace(base, "f" * 40))
    assert not errors, errors


def test_rejects_target_repo_mismatched_with_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """宣言リポジトリと作業ディレクトリのGitルートが異なる計画を拒否する。"""
    work_dir, base = repo
    content = _plan(work_dir, base).replace(f"- 対象リポジトリ: `{work_dir.resolve()}`", "- 対象リポジトリ: `/other`")
    errors, _warnings = _check(work_dir, content)
    assert any("対象リポジトリが作業ディレクトリのGitルートと一致しない" in error for error in errors), errors


def test_cli_requires_metadata_target_repo_instead_of_linked_worktree(
    repo: tuple[pathlib.Path, str],
) -> None:
    """構造検査は同じGitリポジトリの別作業ツリーを対象リポジトリとして代用しない。"""
    work_dir, _base = repo
    linked_worktree = work_dir.parent / f"{work_dir.name}-linked"
    _git(work_dir, "worktree", "add", "-q", str(linked_worktree), "HEAD")
    main_content, detail_content = human_new_format_plan(work_dir)
    plan_path = work_dir / "target-repo-plan.md"
    plan_path.write_text(main_content, encoding="utf-8")
    plan_path.with_name("target-repo-plan.detail.md").write_text(detail_content, encoding="utf-8")
    command = [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--work-dir"]

    target_result = subprocess.run(
        [*command, str(work_dir), str(plan_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    linked_result = subprocess.run(
        [*command, str(linked_worktree), str(plan_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert target_result.returncode == 0, target_result.stderr
    assert linked_result.returncode == 1
    assert "対象リポジトリが作業ディレクトリのGitルートと一致しない" in linked_result.stderr


def test_accepts_relative_target_repo_matching_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """相対表記の対象リポジトリを正規化してGitルートと照合する。"""
    work_dir, base = repo
    content = _plan(work_dir, base).replace(f"- 対象リポジトリ: `{work_dir.resolve()}`", "- 対象リポジトリ: `.`")
    errors, _warnings = _check(work_dir, content)
    assert not errors, errors


@pytest.mark.parametrize("spacing", ["", " "])
@pytest.mark.parametrize(
    ("invocation", "expected_reference"),
    [
        ("Skillツールで{spacing}`missing-skill`を起動する。", "missing-skill"),
        ("`missing-skill`{spacing}スキルを呼び出す。", "missing-skill"),
        (f"`{_TOOLKIT_PREFIX}:missing-skill`{{spacing}}を起動する。", f"{_TOOLKIT_PREFIX}:missing-skill"),
        ("スキル{spacing}`missing-skill`{spacing}を呼び出す。", "missing-skill"),
    ],
)
def test_rejects_missing_skill_invocations(
    repo: tuple[pathlib.Path, str], invocation: str, expected_reference: str, spacing: str
) -> None:
    """空白の有無にかかわらず実在しないスキルの起動指示を拒否する。"""
    work_dir, base = repo
    content = _plan(work_dir, base).replace("対象の構造を更新する。", invocation.format(spacing=spacing))
    errors, _warnings = _check(work_dir, content)
    matched = [error for error in errors if error.startswith(f"実在しないスキル参照: {expected_reference}。")]
    assert len(matched) == 1, errors
    assert "実在するスキル名へ直すか、起動の形の参照をやめる" in matched[0]


def test_missing_skill_reference_suggests_close_existing_name(repo: tuple[pathlib.Path, str]) -> None:
    """綴りの近い実在スキルがあれば候補として示す。"""
    work_dir, base = repo
    invocation = f"Skillツールで`{_TOOLKIT_PREFIX}:plan-mod`を起動する。"
    errors, _warnings = _check(work_dir, _plan(work_dir, base).replace("対象の構造を更新する。", invocation))
    assert any(f"候補: {_TOOLKIT_PREFIX}:plan-mode" in error for error in errors), errors


@pytest.mark.parametrize("spacing", ["", " "])
def test_accepts_new_skill_description_without_invocation(repo: tuple[pathlib.Path, str], spacing: str) -> None:
    """起動動詞を伴わない新設予定スキルの叙述を受理する。"""
    work_dir, base = repo
    description = f"新スキル{spacing}`{_TOOLKIT_PREFIX}:missing-skill`{spacing}を新設する。"
    errors, _warnings = _check(work_dir, _plan(work_dir, base).replace("対象の構造を更新する。", description))
    assert not any(error.startswith(f"実在しないスキル参照: {_TOOLKIT_PREFIX}:missing-skill") for error in errors)


def test_rejects_missing_agent_reference(repo: tuple[pathlib.Path, str]) -> None:
    """実在しない専用agentの参照を拒否する。"""
    work_dir, base = repo
    content = _plan(work_dir, base).replace("対象の構造を更新する。", "Agentツールで`missing-agent`を使う。")
    errors, _warnings = _check(work_dir, content)
    assert any("実在しないサブエージェント参照" in error for error in errors), errors


def test_resolves_plugin_resources_outside_plugin_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """利用先worktreeに複製されないplugin同梱resourceをplugin rootから解決する。"""
    work_dir, base = repo
    content = _plan(work_dir, base).replace(
        "対象の構造を更新する。",
        "`agent-toolkit:plan-mode`を起動し、`agent-toolkit:delegation`の経路選択に従う。",
    )
    errors, _warnings = _check(work_dir, content)
    assert not errors, errors


def test_resolves_project_local_skill_from_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """プロジェクトローカルskillは利用先worktreeから解決する。"""
    work_dir, base = repo
    skill = work_dir / ".claude" / "skills" / "local-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# local\n", encoding="utf-8")
    content = _plan(work_dir, base).replace("対象の構造を更新する。", "スキル`local-skill`を起動する。")
    errors, _warnings = _check(work_dir, content)
    assert not errors, errors


def test_cli_has_no_base_commit_option() -> None:
    """廃止した対象一覧照合オプションを公開しない。"""
    parser_result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--base-commit" not in parser_result.stdout


# --- 新書式（計画2ファイル）の検査 ---


@pytest.mark.parametrize("bug", [False, True])
def test_accepts_canonical_new_format_plan(repo: tuple[pathlib.Path, str], *, bug: bool) -> None:
    """新書式の計画ファイル（メイン）・計画ファイル（詳細）の組を通常・バグ対応いずれも受理する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=bug)
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    # 旧二ファイル形式のテスト入力は付属ファイル参照を絶対パスで持つため、バグ対応のときだけこの移行警告が加わる。
    expected = (
        ["計画本文の付属ファイル参照が旧表記である。新規作成・改訂では`~/.claude/plans/<ファイル名>`へ移行する"] if bug else []
    )
    expected += [
        "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
        "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する",
        "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する",
    ]
    assert warnings == expected, warnings


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


def test_accepts_human_readable_two_file_plan_with_migration_warning(
    repo: tuple[pathlib.Path, str],
) -> None:
    """旧二ファイル計画を受理し、現行書式への移行を警告する。"""
    work_dir, _base = repo
    main_content, detail_content = human_new_format_plan(work_dir)
    errors, warnings = _check_new(work_dir, main_content, detail_content, plan_name="human.md")
    assert not errors, errors
    assert warnings == [_TWO_FILE_MIGRATION]


def test_accepts_current_single_file_plan(repo: tuple[pathlib.Path, str]) -> None:
    """現行の8節1ファイル計画を移行警告なしで受理する。"""
    work_dir, _base = repo

    errors, warnings = _check(work_dir, _plan_fixture.current_plan(repo=work_dir.resolve()))

    assert not errors, errors
    assert not warnings, warnings


def test_cli_checks_all_returned_agent_rule_paths(repo: tuple[pathlib.Path, str], capsys: pytest.CaptureFixture[str]) -> None:
    """返却するパス一覧を同じ本文へ照合し、全欠落と修正後の成功を公開入口から確認する。"""
    work_dir, _base = repo
    path = work_dir / "paths.md"
    content = _plan_fixture.current_plan(repo=work_dir.resolve())
    path.write_text(content, encoding="utf-8")
    paths = ("AGENTS.md", ".claude/rules/new[1]+.md")
    args = ["--work-dir", str(work_dir), str(path)]
    for relative in paths:
        args.extend(["--agent-rule-path", relative])

    assert check_plan_file.main(args) == 1
    error = capsys.readouterr().err
    assert all(relative in error for relative in paths)
    assert "計画本文へ明記" in error
    assert "同じ一覧" in error

    path.write_text(content.replace("対象の公開契約を更新する。", "、".join(paths)), encoding="utf-8")
    assert all(not (work_dir / relative).exists() for relative in paths)
    assert check_plan_file.main(args) == 0
    assert not capsys.readouterr().err
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == 0
    assert not capsys.readouterr().err


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
    """選定レーンのWI集合を計画メタ情報と照合し、欠落と余剰を示す。"""
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
    """凍結済み計画を再検査せず、追加計画と先行計画の和集合を検査する。"""
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


def _run_lane_check_with_resumed(
    work_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    related: tuple[str, ...],
    decisions: list[dict[str, str]],
) -> tuple[int, str]:
    """再開位置を含む選定結果をそのまま`plan-check`のCLI入口へ渡し、終了コードと標準エラーを返す。

    CLI入口は由来照合の正本をキュー管理リポジトリから探すため、実行環境の実物に依存しないよう
    一時のキュー管理リポジトリへ計画の人間由来行が指す正本を置く。
    """
    private_notes = work_dir / "private-notes"
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True)
    (inbox / _plan_fixture.WI_FILES[0][0]).write_text("---\nstatus: inbox\n---\n\n# 要求\n\n本文。\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    plan_path = work_dir / "plan.md"
    plan_path.write_text(
        _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=tuple((name, "要求") for name in related)),
        encoding="utf-8",
    )
    selection_path = work_dir / "selection.yaml"
    selection_path.write_text(yaml.safe_dump({"decisions": decisions}, allow_unicode=True), encoding="utf-8")
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        code = check_plan_file.main(
            [
                "--reject-migration-warnings",
                "--selection-file",
                str(selection_path),
                "--lane",
                "lane-01",
                "--work-dir",
                str(work_dir),
                str(plan_path),
            ]
        )
    return code, stderr.getvalue()


_NEW_AWI = _plan_fixture.WI_FILES[0][0]
_RESUMED_AWI = "20260831-000000-002.md"
_RESUMED_DECISION = {"awi": _RESUMED_AWI, "lane": "lane-01", "再開位置": "/tmp/plans/prior.md の反映後の観測だけが残る"}


@pytest.mark.parametrize("new_resume_value", [None, "なし"], ids=["omitted", "explicit-none"])
def test_lane_selection_excludes_resumed_decisions(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch, new_resume_value: str | None
) -> None:
    """再開位置あり・なしが混在するレーンでも、再開位置なしの集合と一致する新規計画を受理する。"""
    work_dir, _base = repo
    new_decision = {"awi": _NEW_AWI, "lane": "lane-01"}
    if new_resume_value is not None:
        new_decision["再開位置"] = new_resume_value
    code, stderr = _run_lane_check_with_resumed(work_dir, monkeypatch, (_NEW_AWI,), [new_decision, _RESUMED_DECISION])
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
    """人間由来の実施行に根拠がない計画は、選定レーン付き検査で失敗する。"""
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


def test_current_plan_acceptance_table_accepts_new_header(repo: tuple[pathlib.Path, str]) -> None:
    """現行の列名を持つ受入シナリオ表は新規作成・改訂の検査でも移行警告の対象にならない。"""
    work_dir, _base = repo
    content = _plan_fixture.current_plan(repo=work_dir.resolve())
    path = work_dir / "acceptance.md"
    path.write_text(content, encoding="utf-8")

    errors, warnings = check_plan_file.check(path, work_dir, reject_migration_warnings=True)

    assert _acceptance_header_row(_plan_format.PLAN_ACCEPTANCE_TABLE_HEADER) in content
    assert _plan_format.PLAN_ACCEPTANCE_TABLE_HEADER[-1] == "テスト"
    assert not errors, errors
    assert not any("受入シナリオ表" in warning for warning in warnings), warnings


def test_current_plan_legacy_acceptance_header_is_migration_only(repo: tuple[pathlib.Path, str]) -> None:
    """改名前の列名を持つ受入シナリオ表は読取時に警告して受理し、新規作成・改訂では拒否する。"""
    work_dir, _base = repo
    new_header = _acceptance_header_row(_plan_format.PLAN_ACCEPTANCE_TABLE_HEADER)
    legacy_header = _acceptance_header_row(_plan_format.PLAN_LEGACY_ACCEPTANCE_TABLE_HEADER)
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(new_header, legacy_header, 1)
    path = work_dir / "legacy-acceptance.md"
    path.write_text(content, encoding="utf-8")

    read_errors, read_warnings = check_plan_file.check(path, work_dir)
    create_errors, _create_warnings = check_plan_file.check(path, work_dir, reject_migration_warnings=True)

    assert legacy_header in content
    assert not read_errors, read_errors
    assert any("受入シナリオ表の列名が旧形式" in warning for warning in read_warnings), read_warnings
    assert any("受入シナリオ表の列名が旧形式" in error for error in create_errors), create_errors


def test_current_plan_legacy_acceptance_table_still_requires_filled_cells(repo: tuple[pathlib.Path, str]) -> None:
    """改名前の列名の表も現行の表と同じく空セルを拒否する。"""
    work_dir, _base = repo
    new_header = _acceptance_header_row(_plan_format.PLAN_ACCEPTANCE_TABLE_HEADER)
    legacy_header = _acceptance_header_row(_plan_format.PLAN_LEGACY_ACCEPTANCE_TABLE_HEADER)
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(new_header, legacy_header, 1)
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


@pytest.mark.parametrize(
    "relative",
    [pathlib.Path("30-計画保存先移行-d4f9.md"), pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")],
)
def test_accepts_direct_and_date_hierarchy_working_paths(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    relative: pathlib.Path,
) -> None:
    """直下形式と既存の日付階層形式の作業計画を同じ構造検査で受理する。"""
    work_dir, _base = repo
    home = tmp_path / "home"
    main_path = home / ".claude/plans" / relative
    detail_path = main_path.with_name(main_path.stem + ".detail.md")
    main_path.parent.mkdir(parents=True, exist_ok=True)
    main_content, detail_content = human_new_format_plan(work_dir)
    main_path.write_text(main_content, encoding="utf-8")
    detail_path.write_text(detail_content, encoding="utf-8")

    errors, warnings = check_plan_file.check(main_path, work_dir, home=home)

    assert not errors, errors
    assert warnings == [_TWO_FILE_MIGRATION]


@pytest.mark.parametrize(
    ("filename", "rejected"),
    [
        ("example-plan.md", True),
        ("13-計画名検査-a1b2.md", False),
        ("13-legacy.md", False),
    ],
)
def test_working_plan_filename_follows_save_stage_condition(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    filename: str,
    rejected: bool,
) -> None:
    """計画作業root直下の計画ファイル名を保存工程と同じ受理条件で検査する。"""
    work_dir, base = repo
    home = tmp_path / "home"
    plan_path = home / ".claude/plans" / filename
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(_plan(work_dir, base), encoding="utf-8")

    errors, _warnings = check_plan_file.check(plan_path, work_dir, home=home)

    filename_errors = [error for error in errors if _FILENAME_ERROR_MARKER in error]
    assert bool(filename_errors) is rejected, errors
    if rejected:
        assert "dd-{名称}-{小文字16進数4桁}.md" in filename_errors[0]


def test_working_plan_filename_is_not_checked_outside_working_root(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
) -> None:
    """計画作業root直下に無い計画ファイルの名前は検査しない。"""
    work_dir, base = repo
    home = tmp_path / "home"
    plan_path = home / ".claude/plans/2026/08/example-plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(_plan(work_dir, base), encoding="utf-8")

    errors, _warnings = check_plan_file.check(plan_path, work_dir, home=home)

    assert not [error for error in errors if _FILENAME_ERROR_MARKER in error], errors


def test_new_format_reports_one_diagnostic_for_one_duplicate_heading(
    repo: tuple[pathlib.Path, str],
) -> None:
    """一件の重複見出しに対する診断を二ファイル検査で一回だけ返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    detail_content = detail_content.replace(
        "## 完了条件",
        "### 重複見出し\n\n一つ目。\n\n### 重複見出し\n\n二つ目。\n\n## 完了条件",
    )

    errors, _warnings = _check_new(work_dir, main_content, detail_content)

    duplicate_errors = [error for error in errors if "同じ見出しが重複している" in error]
    assert len(duplicate_errors) == 1, errors
    assert "`### 重複見出し`" in duplicate_errors[0]


def test_new_format_detected_by_detail_file_presence(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（詳細）が存在しない同名の計画ファイル（メイン）は旧形式として検査される。"""
    work_dir, base = repo
    errors, _warnings = _check(work_dir, _plan(work_dir, base))
    assert not errors, errors


def test_old_two_file_format_ignores_detail_reference_value(repo: tuple[pathlib.Path, str]) -> None:
    """旧二ファイル形式の詳細参照値はstem導出へ移行したため対応判定に使わない。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 計画ファイル（詳細）: `plan.detail.md`", "- 計画ファイル（詳細）: `other.detail.md`")
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    assert any("stemから対応付ける" in warning for warning in warnings), warnings


def test_new_format_requires_related_wi_metadata_field(repo: tuple[pathlib.Path, str]) -> None:
    """詳細参照を除いた新書式には`関連WI`が必要である。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 計画ファイル（詳細）: `plan.detail.md`\n", "")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("`関連WI`" in error for error in errors), errors


def test_new_format_rejects_missing_verification_section(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（メイン）に`## 検証区分`が無い新書式を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace(
        f"## {_plan_format.PLAN_H2_VERIFICATION}\n\n{_plan_fixture.VERIFICATION_TABLE}\n",
        "",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("固定H2" in error for error in errors), errors


def test_new_format_rejects_bug_section_placed_in_main(repo: tuple[pathlib.Path, str]) -> None:
    """`## バグ調査結果`は計画ファイル（詳細）専用であり計画ファイル（メイン）に置くと拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace(
        "## 検証区分",
        "## バグ調査結果\n\n未使用。\n\n## 検証区分",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("固定H2は" in error for error in errors), errors


def test_new_format_rejects_missing_bug_sidecar(repo: tuple[pathlib.Path, str]) -> None:
    """バグ対応の計画ファイル（詳細）に記載した分離先ファイルが無い場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    errors, _warnings = _check_new(work_dir, main_content, detail_content, create_bug_file=False)
    assert any("バグ調査ファイルが実在しない" in error for error in errors), errors


def test_new_format_rejects_bug_sidecar_stem_mismatch(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（バグ）のstemが計画ファイル（メイン）と異なる場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    detail_content = detail_content.replace("plan.bugs.md", "other.bugs.md")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("計画stemと一致しない" in error for error in errors), errors


def test_new_format_rejects_bug_sidecar_structure_violation(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（バグ）の固定行の調査表欠落を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    invalid_bug_file = _plan_fixture.bug_file().replace(f"{_plan_fixture.bug_row(_plan_format.PLAN_BUG_TABLE_ROWS[0])}\n", "")
    errors, _warnings = _check_new(work_dir, main_content, detail_content, bug_file_content=invalid_bug_file)
    assert any(f"固定{len(_plan_format.PLAN_BUG_TABLE_ROWS)}行" in error for error in errors), errors


def test_new_format_accepts_legacy_bug_table_rows_with_warning(repo: tuple[pathlib.Path, str]) -> None:
    """統廃合前の行構成を持つ調査表を読み取りで受理し、移行warningを返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    legacy_bug_file = _plan_fixture.bug_file(variant=_plan_fixture.BUG_VARIANT_LEGACY_ROWS)
    errors, warnings = _check_new(work_dir, main_content, detail_content, bug_file_content=legacy_bug_file)
    assert not errors, errors
    assert any("統廃合前の行構成" in warning for warning in warnings), warnings


def test_creation_rejects_legacy_bug_table_rows(repo: tuple[pathlib.Path, str]) -> None:
    """新規作成では統廃合前の行構成を持つ調査表をエラーにする。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    legacy_bug_file = _plan_fixture.bug_file(variant=_plan_fixture.BUG_VARIANT_LEGACY_ROWS)
    errors, _warnings = _check_new(
        work_dir,
        main_content,
        detail_content,
        bug_file_content=legacy_bug_file,
        reject_migration_warnings=True,
    )
    assert any("統廃合前の行構成" in error for error in errors), errors


def test_new_format_rejects_empty_bug_sidecar_content(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（バグ）の`内容`空欄を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    invalid_bug_file = _plan_fixture.bug_file().replace(
        _plan_fixture.bug_row("直接的原因"),
        "| 直接的原因 |  |",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content, bug_file_content=invalid_bug_file)
    assert any("空の`内容`" in error for error in errors), errors


def test_new_format_rejects_detail_structure_violation(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（詳細）の固定H2欠落も検査対象となる。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    detail_content = detail_content.replace(
        "## 完了条件\n\n基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。\n",
        "",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("固定H2" in error for error in errors), errors


def test_new_format_reports_short_action_row_without_index_error(repo: tuple[pathlib.Path, str]) -> None:
    """列不足の新4列表は例外を送出せず、既存の構造診断として拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = _replace_action_table(
        main_content,
        ["| 診断件数を2件から1件へ減らす |"],
    )

    errors, _warnings = _check_new(work_dir, main_content, detail_content)

    assert any("実施内容`の表の列数が一致しない" in error and "`\\|`へエスケープ" in error for error in errors), errors


def test_new_format_warns_for_legacy_inline_bug_table(repo: tuple[pathlib.Path, str]) -> None:
    """2ファイル書式でも本文内の旧バグ調査表を受理し、移行warningを返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    reference = _plan_format.extract_bug_file_reference(detail_content)
    if reference is not None:
        detail_content = detail_content.replace(
            _plan_fixture.bug_reference_section(reference),
            _plan_fixture.inline_bug_section(variant=_plan_fixture.BUG_VARIANT_LEGACY_STANDALONE),
        )
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    expected = ["バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する"]
    if _plan_format.has_legacy_action_table(main_content):
        expected.append("実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する")
    expected.extend(
        [
            "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
            "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する",
        ]
    )
    expected.append("二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する")
    assert warnings == expected


def _adjunct_reference_plan(repo: pathlib.Path, base: str, *, stem: str = "plan") -> tuple[str, str]:
    """計画ファイル（バグ）を新しい参照値で指す計画を組み立てて返す。"""
    main, detail = _new_format_plan(repo, base, bug=True, detail_name=f"{stem}.detail.md")
    absolute = str((repo / f"{stem}.bugs.md").resolve())
    reference = f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}{stem}.bugs.md"
    return main, detail.replace(absolute, reference)


def test_new_format_accepts_adjunct_bug_file_reference(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイルと同じディレクトリを基準に新しい参照値を解決する。"""
    work_dir, base = repo
    main_content, detail_content = _adjunct_reference_plan(work_dir, base)
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    assert not any("付属ファイル参照が旧表記" in warning for warning in warnings), warnings


def test_adjunct_bug_file_reference_resolves_from_any_plan_directory(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """同じ参照値が、計画を置いたどちらのディレクトリでも同じ計画の実体を指す。"""
    work_dir, base = repo
    main_content, detail_content = _adjunct_reference_plan(work_dir, base)
    saved_directory = tmp_path / "saved" / "2026" / "09"
    saved_directory.mkdir(parents=True)
    main_path = saved_directory / "plan.md"
    main_path.write_text(main_content, encoding="utf-8")
    (saved_directory / "plan.detail.md").write_text(detail_content, encoding="utf-8")
    (saved_directory / "plan.bugs.md").write_text(_plan_fixture.bug_file(), encoding="utf-8")
    errors, _warnings = check_plan_file.check(main_path, work_dir)
    assert not errors, errors


def test_new_format_rejects_adjunct_reference_with_path_separator(repo: tuple[pathlib.Path, str]) -> None:
    """新しい参照値にパス区切り文字を含む場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _adjunct_reference_plan(work_dir, base)
    detail_content = detail_content.replace(
        f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}plan.bugs.md",
        f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}2026/09/plan.bugs.md",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content, create_bug_file=False)
    assert any("参照値が不正です" in error for error in errors), errors


def test_new_format_warns_for_legacy_adjunct_reference_notation(repo: tuple[pathlib.Path, str]) -> None:
    """絶対パスの参照は読み取りで受理し、新しい参照値への移行warningを返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    assert any("付属ファイル参照が旧表記" in warning for warning in warnings), warnings


def test_new_format_accepts_portable_bug_file_reference(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """新rootのバグ調査ファイルを固定portable参照で検査できる。"""
    work_dir, base = repo
    private_notes = work_dir / "private-notes"
    stem = "30-計画保存先移行-d4f9"
    detail_name = f"{stem}.detail.md"
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True, detail_name=detail_name)
    absolute_bug_path = (work_dir / f"{stem}.bugs.md").resolve()
    portable_bug_path = f"$(atk config get private_notes)/plans/2026/08/{stem}.bugs.md"
    detail_content = detail_content.replace(str(absolute_bug_path), portable_bug_path)

    plan_directory = private_notes / "plans/2026/08"
    plan_directory.mkdir(parents=True)
    main_path = plan_directory / f"{stem}.md"
    main_path.write_text(main_content, encoding="utf-8")
    (plan_directory / detail_name).write_text(detail_content, encoding="utf-8")
    (plan_directory / f"{stem}.bugs.md").write_text(_plan_fixture.bug_file(), encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))

    errors, _warnings = check_plan_file.check(main_path, work_dir, private_notes=private_notes)

    assert not errors, errors


def test_cli_accepts_new_format_plan(repo: tuple[pathlib.Path, str]) -> None:
    """CLI経由でも新書式の計画ファイル（メイン）・計画ファイル（詳細）の組を受理する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, detail_name="new-format-plan.detail.md")
    path = work_dir / "new-format-plan.md"
    path.write_text(main_content, encoding="utf-8")
    (work_dir / "new-format-plan.detail.md").write_text(detail_content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--work-dir", str(work_dir), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "[warn] 計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける\n"
        "[warn] `## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する\n"
        "[warn] 二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する\n"
    )


def test_cli_rejects_migration_warnings_on_revision(repo: tuple[pathlib.Path, str]) -> None:
    """改訂用CLI入力では読み取り互換の移行警告をエラーとして返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, detail_name="revision-plan.detail.md")
    path = work_dir / "revision-plan.md"
    path.write_text(main_content, encoding="utf-8")
    (work_dir / "revision-plan.detail.md").write_text(detail_content, encoding="utf-8")

    result = subprocess.run(
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
        check=False,
    )

    assert result.returncode == 1
    assert "[warn]" not in result.stderr
    assert "二ファイル計画が旧ID形式である" in result.stderr


def test_cli_allows_progress_rows_when_rejecting_migration_warnings(repo: tuple[pathlib.Path, str]) -> None:
    """改訂用CLI入力は移行警告を拒否しても実装工程の進捗行を保持する。"""
    work_dir, _base = repo
    main_content = _plan_fixture.current_plan(repo=work_dir.resolve())
    main_content = main_content.replace(
        _plan_fixture.PROGRESS_TABLE,
        _plan_fixture.PROGRESS_TABLE + _plan_fixture.PROGRESS_ROW,
        1,
    )
    path = work_dir / "progress-revision.md"
    path.write_text(main_content, encoding="utf-8")

    result = subprocess.run(
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
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not result.stderr


@pytest.mark.skipif(
    not (_REAL_LEGACY_TWO_FILE_PLAN.is_file() and _REAL_LEGACY_TWO_FILE_DETAIL.is_file()),
    reason="実在する旧二ファイル計画がこの環境に無い",
)
def test_cli_accepts_review_ids_in_real_legacy_two_file_plan() -> None:
    """実在する旧二ファイル計画を公式CLIで検査し、旧IDをエラーにしない。"""
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
    """新規作成・改訂の経路では改名前の項目名を拒否する。"""
    work_dir, _base = repo
    main_content, detail_content = human_new_format_plan(work_dir)
    main_content = _plan_fixture.legacy_wi_names(main_content)
    errors, _warnings = _check_new(work_dir, main_content, detail_content, reject_migration_warnings=True)
    assert any(_plan_format.PLAN_METADATA_RELATED_WI_FIELD in error for error in errors), errors


def _origin_plan(repo: pathlib.Path, private_notes: pathlib.Path, *, source: bool) -> pathlib.Path:
    """由来照合の対象となる計画一式と正本を配置し、計画ファイル（メイン）のパスを返す。"""
    main_content = _plan_fixture.human_main(repo=repo.resolve(), related_wi=_plan_fixture.WI_FILES)
    main_path = repo / "plan.md"
    main_path.write_text(main_content, encoding="utf-8")
    (repo / "plan.detail.md").write_text(_plan_fixture.human_detail(), encoding="utf-8")
    frontmatter = ["---", "status: inbox"]
    if source:
        frontmatter.append(f"{_plan_format.PLAN_WI_SOURCE_KEY}: agent-toolkit:session-review")
    frontmatter.append("---")
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True)
    (inbox / _plan_fixture.WI_FILES[0][0]).write_text(
        "\n".join([*frontmatter, "", "# 要求", "", "本文。", ""]), encoding="utf-8"
    )
    return main_path


def test_origin_mismatch_is_warning_on_read(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    """保存済み計画の読み取りでは由来の不一致を移行warningに留める。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    main_path = _origin_plan(work_dir, private_notes, source=True)
    errors, warnings = check_plan_file.check(main_path, work_dir, private_notes=private_notes)
    assert not errors, errors
    assert any("正本の由来と一致しない" in warning for warning in warnings), warnings


def test_origin_mismatch_is_error_on_creation(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    """新規作成では同じ不一致をエラーとして報告する。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    main_path = _origin_plan(work_dir, private_notes, source=True)
    errors, _warnings = check_plan_file.check(
        main_path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert any("正本の由来と一致しない" in error for error in errors), errors


def _assert_creation_check_passes_silently(
    work_dir: pathlib.Path, path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開CLIの新規作成向け検査が終了コード0で、警告を出力しないことを確かめる。"""
    result = check_plan_file.main(["--reject-migration-warnings", "--work-dir", str(work_dir), str(path)])
    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert captured.err == ""


_AGENT_WI_ADOPTED_ROW = f"| 入力の境界を追加確認する | エージェント由来のWI ({_plan_fixture.WI_FILES[0][0]}) | 採用 | - |"


def _agent_wi_plan(repo: pathlib.Path, private_notes: pathlib.Path, wi_sections: str | None) -> pathlib.Path:
    """根拠が`-`の`エージェント由来のWI`採用行を持つ計画と、指定した節を持つ正本を配置する。

    `wi_sections`が`None`の場合は正本を置かない。
    """
    main_content = _plan_fixture.current_plan(repo=repo.resolve(), related_wi=_plan_fixture.WI_FILES)
    main_content = main_content.replace(_plan_fixture.WI_ACTION_ROW, _AGENT_WI_ADOPTED_ROW, 1)
    path = repo / "agent-wi.md"
    path.write_text(main_content, encoding="utf-8")
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    if wi_sections is not None:
        frontmatter = f"---\n{_plan_format.PLAN_WI_SOURCE_KEY}: process-wi\n---\n\n"
        (inbox / _plan_fixture.WI_FILES[0][0]).write_text(f"{frontmatter}# 要求\n\n本文。\n\n{wi_sections}", encoding="utf-8")
    return path


def test_agent_wi_adopted_action_without_reason_is_error_on_creation(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """正本が`## 適用範囲`を持たないエージェント由来のWIの採用行は、新規作成でエラーに移す。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _agent_wi_plan(work_dir, private_notes, "## 反映内容と反映先\n\n対象。\n")
    read_errors, read_warnings = check_plan_file.check(path, work_dir, private_notes=private_notes)
    errors, warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert not read_errors, read_errors
    assert any("適用範囲を再導出した結果と根拠" in warning for warning in read_warnings), read_warnings
    assert any("適用範囲を再導出した結果と根拠" in error for error in errors), errors
    assert not any("適用範囲を再導出した結果と根拠" in warning for warning in warnings), warnings


@pytest.mark.parametrize(
    "scope",
    [
        "## 適用範囲\n\n",
        "```markdown\n## 適用範囲\n\n誤りの機構が依存する条件。\n```\n",
    ],
    ids=["empty", "fenced"],
)
def test_agent_wi_adopted_action_without_reason_rejected_when_wi_scope_empty(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path, scope: str
) -> None:
    """正本の`## 適用範囲`が空かコードフェンス内にしか無い場合は、参照だけで根拠を省略できるとみなさない。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _agent_wi_plan(work_dir, private_notes, f"{scope}\n## 実現性\n\n確認済み。\n")
    errors, _warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert any("適用範囲を再導出した結果と根拠" in error for error in errors), errors


def test_agent_wi_adopted_action_without_reason_accepted_when_wi_has_scope(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正本が非空の`## 適用範囲`を持つエージェント由来のWIの採用行は、根拠`-`のまま新規作成で受理する。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    path = _agent_wi_plan(work_dir, private_notes, "## 適用範囲\n\n誤りの機構が依存する条件。\n\n## 実現性\n\n確認済み。\n")
    _assert_creation_check_passes_silently(work_dir, path, capsys)


def test_agent_wi_adopted_action_without_reason_skips_when_wi_unresolvable(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """正本を解決できない場合は照合の省略を助言に留め、新規作成を遮断しない。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _agent_wi_plan(work_dir, private_notes, None)
    errors, warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert not errors, errors
    assert any("正本を解決できない" in warning for warning in warnings), warnings


def _bug_plan_without_bug_file(repo: pathlib.Path, private_notes: pathlib.Path, *, related_wi: bool) -> pathlib.Path:
    """`計画ファイル（バグ）`行を持たないバグ対応計画と、人間由来の正本を配置する。"""
    related = _plan_fixture.WI_FILES if related_wi else ()
    path = repo / "bug-plan.md"
    path.write_text(_plan_fixture.current_plan(repo=repo.resolve(), work_type="バグ対応", related_wi=related), encoding="utf-8")
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for name, _summary in _plan_fixture.WI_FILES:
        (inbox / name).write_text("# 要求\n\n本文。\n", encoding="utf-8")
    return path


def test_bug_plan_without_bug_file_reference_accepted_with_related_wi(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """関連WIの原因分析を正本とするバグ対応計画は、計画ファイル（バグ）行なしで新規作成の検査を通る。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    path = _bug_plan_without_bug_file(work_dir, private_notes, related_wi=True)
    _assert_creation_check_passes_silently(work_dir, path, capsys)


def test_bug_plan_without_bug_file_reference_rejected_without_related_wi(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """関連WIが無いバグ対応計画は計画ファイル（バグ）行を必須とする。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _bug_plan_without_bug_file(work_dir, private_notes, related_wi=False)
    errors, _warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert any(_plan_format.PLAN_METADATA_BUG_FIELD in error for error in errors), errors


def test_origin_skip_stays_advisory_on_creation(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    """照合を省略した事実は助言に留め、新規作成を遮断しない。"""
    work_dir, _base = repo
    private_notes = tmp_path / "absent"
    main_content = _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=_plan_fixture.WI_FILES)
    main_path = work_dir / "plan.md"
    main_path.write_text(main_content, encoding="utf-8")
    errors, warnings = check_plan_file.check(
        main_path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert not errors, errors
    assert any("由来照合を省略した" in warning for warning in warnings), warnings


@pytest.mark.parametrize("missing_queue", [True, False], ids=["missing-queue", "missing-wi"])
def test_cli_origin_skip_reports_one_next_action(
    repo: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    missing_queue: bool,
) -> None:
    """由来を照合できない警告から原因の解消と解消不能時の報告へ進める。"""
    work_dir, _base = repo
    queue = work_dir / "queue"
    if not missing_queue:
        queue.mkdir()
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    path = work_dir / "origin.md"
    path.write_text(_plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=_plan_fixture.WI_FILES), encoding="utf-8")
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == 0
    lines = capsys.readouterr().err.splitlines()
    assert any(line.startswith("[warn]") and "由来照合を省略した" in line for line in lines)
    actions = [line for line in lines if line.startswith("次の操作: ")]
    assert len(actions) == 1
    expected = "AGENT_TOOLKIT_PRIVATE_NOTES" if missing_queue else "atk wi show"
    assert expected in actions[0]
    assert "解消できない" in actions[0] and "報告" in actions[0]


def test_cli_origin_read_failure_reports_recovery(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """正本の取得に失敗しても省略を助言に保ち、取得エラーの解消を案内する。"""
    work_dir, _base = repo
    queue = work_dir / "queue"
    path = _bug_plan_without_bug_file(work_dir, queue, related_wi=True)
    source = queue / "inbox" / _plan_fixture.WI_FILES[0][0]
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    original = pathlib.Path.read_text

    def read_text(file: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> str:
        if file == source:
            raise PermissionError("検証用の読取拒否")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == 0
    lines = capsys.readouterr().err.splitlines()
    assert any("正本を取得できない" in line and source.name in line for line in lines)
    actions = [line for line in lines if line.startswith("次の操作: ")]
    assert len(actions) == 1
    assert "エラーの原因を解消" in actions[0]
