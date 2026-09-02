"""意味契約中心の計画検査を検証する。"""

import collections.abc
import pathlib
import subprocess
import sys

import check_plan_file
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _plan_fixture  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_REAL_LEGACY_TWO_FILE_PLAN = pathlib.Path("/home/aki/.claude/plans/fb-hooks-45ab5132.md")
_REAL_LEGACY_TWO_FILE_DETAIL = _REAL_LEGACY_TWO_FILE_PLAN.with_name(f"{_REAL_LEGACY_TWO_FILE_PLAN.stem}.detail.md")

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
    return main.replace(_plan_fixture.human_action_table(feedback=False), new_table, 1), detail


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
    related_field = f"- {_plan_format.PLAN_METADATA_RELATED_FEEDBACK_FIELD}: なし"
    return main.replace(detail_field, related_field, 1), detail


def _migration_legacy_materials_input(repo: pathlib.Path) -> tuple[str, str]:
    """旧形式の提示素材表を持つ二ファイル計画を返す。"""
    main, detail = _new_format_plan(repo, _git(repo, "rev-parse", "HEAD"))
    main = main.replace(
        _plan_fixture.TWO_FILE_ACTION_TABLE,
        _plan_fixture.human_action_table(feedback=False),
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
    reject_legacy_format: bool = False,
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
    return check_plan_file.check(path, repo, reject_legacy_format=reject_legacy_format)


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
    expected.append("`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連フィードバック`へ移行する")
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
    diagnostics = [line for line in result.stderr.splitlines() if line and not line.startswith("[warn]")]
    assert result.returncode == 1
    assert len(diagnostics) == 1, diagnostics
    assert "`## 完了条件`は1件必要" in diagnostics[0]


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
        ("`agent-toolkit:missing-skill`{spacing}を起動する。", "agent-toolkit:missing-skill"),
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
    assert f"実在しないスキル参照: {expected_reference}" in errors


@pytest.mark.parametrize("spacing", ["", " "])
def test_accepts_new_skill_description_without_invocation(repo: tuple[pathlib.Path, str], spacing: str) -> None:
    """起動動詞を伴わない新設予定スキルの叙述を受理する。"""
    work_dir, base = repo
    description = f"新スキル{spacing}`agent-toolkit:missing-skill`{spacing}を新設する。"
    errors, _warnings = _check(work_dir, _plan(work_dir, base).replace("対象の構造を更新する。", description))
    assert "実在しないスキル参照: agent-toolkit:missing-skill" not in errors


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
        "`agent-toolkit:plan-mode`を起動し、Agentツールで`agent-toolkit:plan-executor`を起動する。",
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
    # 旧二ファイル形式の検体は付属ファイル参照を絶対パスで持つため、バグ対応でだけ当該移行警告が加わる。
    expected = (
        ["計画本文の付属ファイル参照が旧表記である。新規作成・改訂では`~/.claude/plans/<ファイル名>`へ移行する"] if bug else []
    )
    expected += [
        "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
        "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連フィードバック`へ移行する",
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


def test_accepts_human_readable_new_format_plan_without_migration_warning(
    repo: tuple[pathlib.Path, str],
) -> None:
    """新規作成用の人間向け計画ファイル（メイン）・計画ファイル（詳細）をwarningなしで受理する。"""
    work_dir, _base = repo
    main_content, detail_content = human_new_format_plan(work_dir)
    errors, warnings = _check_new(work_dir, main_content, detail_content, plan_name="human.md")
    assert not errors, errors
    assert not warnings, warnings


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
            "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連フィードバック`へ移行する",
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
        reject_legacy_format=True,
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
        reject_legacy_format=True,
    )

    assert not read_errors, read_errors
    assert not read_warnings, read_warnings
    assert any("起草時に内容行を置かない" in error for error in create_errors), create_errors


def test_keeps_plan_size_advisory_when_rejecting_legacy_format(repo: tuple[pathlib.Path, str]) -> None:
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
        reject_legacy_format=True,
    )

    assert not errors, errors
    assert len(warnings) == 1
    assert warnings[0].startswith("計画の行数が閾値を超えている")


def test_rejects_all_migration_warnings_in_legacy_two_file_plan(repo: tuple[pathlib.Path, str]) -> None:
    """旧二ファイル形式で同時に発生する移行警告を全てエラーへ移す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    expected = [
        "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
        "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連フィードバック`へ移行する",
        "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する",
    ]

    errors, warnings = _check_new(
        work_dir,
        main_content,
        detail_content,
        reject_legacy_format=True,
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
    assert not warnings, warnings


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


def test_new_format_requires_related_feedback_metadata_field(repo: tuple[pathlib.Path, str]) -> None:
    """詳細参照を除いた新書式には関連フィードバックが必要である。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 計画ファイル（詳細）: `plan.detail.md`\n", "")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("`関連フィードバック`" in error for error in errors), errors


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
        reject_legacy_format=True,
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

    assert any("実施内容`の表に空cellまたは列数不一致の行がある" in error for error in errors), errors


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
            "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連フィードバック`へ移行する",
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
        "[warn] `## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連フィードバック`へ移行する\n"
        "[warn] 二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する\n"
    )


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
