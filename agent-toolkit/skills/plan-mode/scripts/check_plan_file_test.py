"""意味契約中心の計画検査を検証する。"""

import pathlib
import subprocess
import sys

import check_plan_file
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error


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


def _rows_table(rows: tuple[str, ...], filler: str) -> str:
    """行名を固定した2列表を組み立てる。"""
    return "\n".join(["| 項目 | 内容 |", "| --- | --- |", *(f"| {row} | {filler} |" for row in rows)])


def _permanence_table() -> str:
    """恒久化表の固定列を持つ横持ち表を組み立てる。"""
    header = _plan_format.PLAN_PERMANENCE_TABLE_HEADER
    return "\n".join(
        [
            f"| {' | '.join(header)} |",
            f"| {' | '.join('---' for _column in header)} |",
            "| 知見 | エージェント判断 | 反映先 | 検討結果を記載する。 |",
        ]
    )


def _plan(repo: pathlib.Path, base: str, *, bug: bool = False, exclusions: bool = True) -> str:
    """共有構造モジュールの定数から正規計画を組み立てる。"""
    work_type = "バグ対応" if bug else "通常変更"
    exclusion = ""
    if exclusions:
        exclusion = """
### 合意済みの除外・保持

| 合意内容 | 対象と箇所 | 素材・要求参照 | 確認方法 |
| --- | --- | --- | --- |
| 公開契約を維持する | 公開API | P-001, R-P-001-001 | 差分を確認する |
| 対象外の挙動を変更しない | 対象外の入力処理 | P-001, R-P-001-001 | 回帰テストを実行する |
"""
    bug_section = ""
    if bug:
        bug_section = (
            "## バグ調査結果\n\n### 対象の不整合\n\n"
            + _rows_table(_plan_format.PLAN_BUG_TABLE_ROWS, "発生条件と実際値を記載する。")
            + "\n\n"
        )
    permanence = "調査表の処置を正本として参照する。" if bug else _permanence_table()
    return f"""# 計画の主題

## 概要

成果を得る。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repo.resolve()}`
- 作業種別: {work_type}
- ベースコミット: `{base}`

## 実施内容

| 実施内容 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- |
| 診断件数を2件から1件へ減らす | 指示どおり | R-P-001-001 |
{exclusion}
## 提示素材

| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |
| --- | --- | --- | --- | --- |
| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |
| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |

| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |
| --- | --- | --- | --- | --- | --- | --- |
| R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 | 指示と合意を反映するため。 |

## 変更履歴

| ID | 起点 | 指摘内容 | 採否・現在の結論 | 同期先 |
| --- | --- | --- | --- | --- |
| H-001 | ユーザー発言 | P-001 | 採用した。 | `## 実施内容` |

{bug_section}## 恒久化・リファクタリング内容

### 恒久化

{permanence}

### リファクタリング

{_rows_table(_plan_format.PLAN_REFACTORING_TABLE_ROWS, "検討結果を記載する。")}

### 類似見直し

{_rows_table(_plan_format.PLAN_SIMILAR_REVIEW_TABLE_ROWS, "検討結果を記載する。")}

## 実装資料

### 変更説明

対象の構造を更新する。

## 完了条件

基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
"""


def _check(repo: pathlib.Path, content: str) -> tuple[list[str], list[str]]:
    """計画を一時ファイルへ保存して検査する。"""
    path = repo / "plan.md"
    path.write_text(content, encoding="utf-8")
    return check_plan_file.check(path, repo)


def _legacy_plan(repo: pathlib.Path, base: str) -> str:
    """旧形式の素材と合意表を持つ計画fixtureを返す。"""
    content = _plan(repo, base)
    start = content.index("## 提示素材")
    end = content.index("## 変更履歴")
    legacy = """## 提示素材

P-001:

```text
診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。
```

"""
    content = content[:start] + legacy + content[end:]
    content = content.replace(
        "| 診断件数を2件から1件へ減らす | 指示どおり | R-P-001-001 |", "| 診断件数を2件から1件へ減らす | 指示どおり | P-001 |"
    )
    content = content.replace("素材・要求参照", "原文参照")
    content = content.replace("P-001, R-P-001-001", "P-001")
    return content


@pytest.mark.parametrize(("bug", "exclusions"), [(False, True), (False, False), (True, True)])
def test_accepts_canonical_plan(repo: tuple[pathlib.Path, str], *, bug: bool, exclusions: bool) -> None:
    """通常・バグ対応と任意表の有無を受理する。"""
    work_dir, base = repo
    errors, warnings = _check(work_dir, _plan(work_dir, base, bug=bug, exclusions=exclusions))
    assert not errors
    assert not warnings


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
    assert len(warnings) == expected_warnings, warnings


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
    assert not result.stderr


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
    diagnostics = [line for line in result.stderr.splitlines() if line]
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


def test_rejects_unresolvable_base_commit(repo: tuple[pathlib.Path, str]) -> None:
    """対象リポジトリで解決できないベースコミットを拒否する。"""
    work_dir, base = repo
    errors, _warnings = _check(work_dir, _plan(work_dir, base).replace(base, "f" * 40))
    assert any("対象リポジトリでベースコミットを解決できない" in error for error in errors), errors


def test_rejects_target_repo_mismatched_with_worktree(repo: tuple[pathlib.Path, str]) -> None:
    """宣言リポジトリと作業ディレクトリのGitルートが異なる計画を拒否する。"""
    work_dir, base = repo
    content = _plan(work_dir, base).replace(f"- 対象リポジトリ: `{work_dir.resolve()}`", "- 対象リポジトリ: `/other`")
    errors, _warnings = _check(work_dir, content)
    assert any("対象リポジトリが作業ディレクトリのGitルートと一致しない" in error for error in errors), errors


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
        "`agent-toolkit:plan-mode`を起動し、Agentツールで`agent-toolkit:plan-impl-executor`を起動する。",
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
