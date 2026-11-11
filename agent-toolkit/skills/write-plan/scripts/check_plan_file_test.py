# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""計画の基本構造と参照を検証する。"""

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
    _plan,
    human_new_format_plan,
)
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo

_TOOLKIT_PREFIX = "agent-" + "toolkit"


def _legacy_plan(repo: pathlib.Path, base: str) -> str:
    """旧形式の素材と合意表を持つ計画fixtureを返す。"""
    return _plan_fixture.legacy_materials_single_file_plan(repo=repo.resolve(), base=base)


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
    assert "同じコマンドでもう一度確かめる" in result.stderr.splitlines()[-1]


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
    """構造を判定するとき、同じGitリポジトリの別作業ツリーを対象リポジトリとして代用しない。"""
    work_dir, _base = repo
    linked_worktree = work_dir.parent / f"{work_dir.name}-linked"
    git_repository.git_output(work_dir, "worktree", "add", "-q", str(linked_worktree), "HEAD")
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
    """相対表記の対象リポジトリを正規化してGitルートと一致するか確かめる。"""
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
    assert any(f"候補: {_TOOLKIT_PREFIX}:write-plan" in error for error in errors), errors


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
        "`agent-toolkit:write-plan`を起動し、`agent-toolkit:delegation`の実行手段の選択に従う。",
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


# --- 新書式（計画2ファイル）が基準を満たすか確かめる ---


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
    """返却するパス一覧の全件が同じ本文に含まれるか確かめ、全欠落と修正後の成功を公開されたコマンドから確認する。"""
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


__all__ = ["fixture_repo"]
