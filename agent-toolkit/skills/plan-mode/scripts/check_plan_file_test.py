"""意味契約中心の計画検査を検証する。"""

import json
import pathlib
import subprocess
import sys

import check_plan_file
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_REAL_LEGACY_TWO_FILE_PLAN = pathlib.Path("/home/aki/.claude/plans/fb-hooks-45ab5132.md")
_REAL_LEGACY_TWO_FILE_DETAIL = _REAL_LEGACY_TWO_FILE_PLAN.with_name(f"{_REAL_LEGACY_TWO_FILE_PLAN.stem}.detail.md")
_REAL_LEGACY_TWO_FILE_WITH_PROGRESS_PLAN = pathlib.Path("/home/aki/.claude/plans/review-scope-consolidation-2609f04f.md")
_REAL_LEGACY_TWO_FILE_WITH_PROGRESS_DETAIL = _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_PLAN.with_name(
    f"{_REAL_LEGACY_TWO_FILE_WITH_PROGRESS_PLAN.stem}.detail.md"
)
_REAL_LEGACY_TWO_FILE_WITH_PROGRESS_REVIEW = _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_PLAN.with_suffix(".tsv")


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


def _bug_file_content() -> str:
    """バグ調査付属ファイルの正規内容を組み立てる。"""
    return (
        "# 計画の主題\n\n### 対象の不整合\n\n"
        + _rows_table(_plan_format.PLAN_BUG_TABLE_ROWS, "発生条件と実際値を記載する。")
        + "\n"
    )


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

| 実施内容 | 採否 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- | --- |
| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-001 |
{exclusion}
## 提示素材

| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |
| --- | --- | --- | --- | --- |
| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |
| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |

| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |
| --- | --- | --- | --- | --- | --- | --- |
| R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 | 指示と合意を反映するため。 |
| R-P-001-002 | P-001 | 対象外の検査を追加しない。 | 不採用 | 非該当 | 対象外の検査 | 実装上不要であるため。 |

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


def _new_format_plan(
    repo: pathlib.Path, base: str, *, bug: bool = False, detail_name: str = "plan.detail.md"
) -> tuple[str, str]:
    """新書式（計画ファイル（メイン）・計画ファイル（詳細））の正規計画を組み立てて返す。"""
    work_type = "バグ対応" if bug else "通常変更"
    main = f"""# 計画の主題

## 概要

成果を得る。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repo.resolve()}`
- 作業種別: {work_type}
- ベースコミット: `{base}`
- 実装詳細: `{detail_name}`

## 実施内容

| 実施内容 | 採否 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- | --- |
| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-001 |

## 提示素材

| 素材ID | 種別 | キューID | 投入元 | 引用範囲 |
| --- | --- | --- | --- | --- |
| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |

| 要求ID | 素材参照 | 実装に必要な要件 | 採否 | 採用範囲 | 除外範囲 | 根拠 |
| --- | --- | --- | --- | --- | --- | --- |
| R-P-001-001 | P-001 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 | 指示を反映するため。 |

## 変更履歴

| ID | 起点 | 指摘内容 | 採否・現在の結論 | 同期先 |
| --- | --- | --- | --- | --- |
| H-001 | ユーザー発言 | P-001 | 採用した。 | `## 実施内容` |

## 検証区分

| 区分 | 検証コマンド |
| --- | --- |
| レーン内検証 | `pytest check_plan_file_test.py` |
| 統合後検証 | `make test` |

## 終端工程

なし

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
"""
    bug_section = ""
    if bug:
        bug_stem = detail_name.removesuffix(_plan_format.PLAN_DETAIL_SUFFIX)
        bug_path = (repo / f"{bug_stem}.bugs.md").resolve()
        bug_section = f"## バグ調査結果\n\n- バグ調査ファイル: {bug_path}\n\n"
    permanence = "調査表の処置を正本として参照する。" if bug else _permanence_table()
    detail = f"""{bug_section}## 恒久化・リファクタリング内容

### 恒久化

{permanence}

### リファクタリング

{_rows_table(_plan_format.PLAN_REFACTORING_TABLE_ROWS, "検討結果を記載する。")}

### 類似見直し

{_rows_table(_plan_format.PLAN_SIMILAR_REVIEW_TABLE_ROWS, "検討結果を記載する。")}

## 実装資料

### 実装単位

| 単位ID | 目的 | 先行依存 | 統合順 | 近接検証 |
| --- | --- | --- | --- | --- |
| U-001 | 診断件数を更新する | なし | 1 | `pytest check_plan_file_test.py` |

### 変更説明

対象の構造を更新する。

## 完了条件

基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。
"""
    return main, detail


def _canonical_main_format(content: str) -> str:
    """旧見出しfixtureを新規計画の固定H2へ変換する。"""
    return (
        content.replace(
            "\n## 提示素材\n",
            "\n## エージェント判断\n\nなし\n\n## 提示素材\n",
            1,
        )
        .replace("## 変更履歴", "## 変更履歴（計画時）", 1)
        .replace("## 進捗ログ", "## 進捗ログ（実行時）", 1)
    )


def _human_new_format_plan(repo: pathlib.Path, detail_name: str = "human.detail.md") -> tuple[str, str]:
    """新規作成用の人間向け計画ファイル（メイン）・計画ファイル（詳細）fixtureを返す。"""
    main = f"""# 計画の主題

## 概要

対象の公開契約を更新する。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repo.resolve()}`
- 作業種別: 通常変更
- ベースコミット: `作成時点の参照値`
- 実装詳細: `{detail_name}`

## 実施内容

| 実施内容 | 由来 | 採否 | 根拠 |
| --- | --- | --- | --- |
| 公開契約の境界を更新する | ユーザー指示 | 採用 | - |
| 影響のない類似箇所は変更しない | エージェント提案 | 対象外 | 公開契約への影響が無いため。 |

## 提示素材

なし

## 変更履歴

### ユーザー発言: 本セッションの直接指示

```text
公開契約の境界だけを更新する。
```

### レビューで確定した変更

対象範囲を確認して反映した。

## 検証区分

| 区分 | 検証コマンド |
| --- | --- |
| レーン内検証 | `pytest` |
| 統合後検証 | `make test` |

## 終端工程

なし

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
"""
    detail = """## 恒久化・リファクタリング内容

### 恒久化

| 知見 | 出所 | 反映先 | 根拠 |
| --- | --- | --- | --- |
| 公開契約の境界を維持する | 実装時調査 | 対象モジュール | 更新後も契約を確認するため。 |

### リファクタリング

| 項目 | 内容 |
| --- | --- |
| 対象 | 対象モジュール。 |
| 現状の問題 | 判定が分散している。 |
| 対応 | 判定を統合する。 |
| 本計画に含めるか | 含める。 |

### 類似見直し

| 項目 | 内容 |
| --- | --- |
| 母集団 | 対象モジュール。 |
| 点検観点 | 公開契約への影響。 |
| 該当箇所 | 該当なし。 |

## 実装資料

### 実装単位

| 実装単位 | 目的 | 先行依存 | 統合順 | 近接検証 |
| --- | --- | --- | --- | --- |
| 公開契約の境界更新 | 公開契約の判定を更新する | なし | 1 | `pytest` |

### 調査結果

検索母集団は対象モジュールと関連テストである。
検索コマンド: `rg -n "公開契約|境界" agent-toolkit`
検索結果: 一致は2箇所で、対象外の接続面は不一致として除外した。

### 確定文面

```markdown
公開契約の判定は対象境界に限定する。
```

## 完了条件

近接検証と統合後検証が成功し、確定文面を対象ファイルへ反映する。
"""
    return main, detail


def _check_new(
    repo: pathlib.Path,
    main_content: str,
    detail_content: str,
    *,
    plan_name: str = "plan.md",
    create_bug_file: bool = True,
    bug_file_content: str | None = None,
) -> tuple[list[str], list[str]]:
    """新書式の計画（計画ファイル（メイン）・計画ファイル（詳細））を一時ファイルへ保存して検査する。"""
    path = repo / plan_name
    path.write_text(main_content, encoding="utf-8")
    detail_path = repo / f"{path.stem}.detail.md"
    detail_path.write_text(detail_content, encoding="utf-8")
    reference = _plan_format.extract_bug_file_reference(detail_content)
    if create_bug_file and reference is not None:
        pathlib.Path(reference).write_text(bug_file_content or _bug_file_content(), encoding="utf-8")
    return check_plan_file.check(path, repo)


def _check(repo: pathlib.Path, content: str) -> tuple[list[str], list[str]]:
    """計画を一時ファイルへ保存して検査する。"""
    path = repo / "plan.md"
    path.write_text(content, encoding="utf-8")
    return check_plan_file.check(path, repo)


def _review_table_row(round_value: str = "1") -> str:
    """レビュー表の8列JSON文字列行を組み立てる。"""
    values = [round_value, "plan-review", "中程度", "計画本文", "確認が必要な欠落", "", "", ""]
    return "\t".join(json.dumps(value, ensure_ascii=False) for value in values) + "\n"


def _replace_action_table(content: str, rows: list[str], *, legacy: bool = False) -> str:
    """fixtureの現行列構成に依存せず、実施内容表を指定した新旧形式へ置き換える。"""
    header = (
        "| 実施内容 | ユーザー指示との関係 | 根拠 |\n| --- | --- | --- |"
        if legacy
        else "| 実施内容 | 採否 | ユーザー指示との関係 | 根拠 |\n| --- | --- | --- | --- |"
    )
    start = content.index("## 実施内容")
    end = content.index("\n## 提示素材", start)
    rows_text = "\n".join(rows)
    section = f"## 実施内容\n\n{header}\n{rows_text}\n"
    return content[:start] + section + content[end:]


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
        "| 実施内容 | 採否 | ユーザー指示との関係 | 根拠 |\n"
        "| --- | --- | --- | --- |\n"
        "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-001 |",
        "| 実施内容 | ユーザー指示との関係 | 根拠 |\n"
        "| --- | --- | --- |\n"
        "| 診断件数を2件から1件へ減らす | 指示どおり | P-001 |",
    )
    content = content.replace("素材・要求参照", "原文参照")
    content = content.replace("P-001, R-P-001-001", "P-001")
    return content


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
    assert result.stderr == ""


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


# --- 新書式（計画2ファイル）の検査 ---


@pytest.mark.parametrize("bug", [False, True])
def test_accepts_canonical_new_format_plan(repo: tuple[pathlib.Path, str], *, bug: bool) -> None:
    """新書式の計画ファイル（メイン）・計画ファイル（詳細）の組を通常・バグ対応いずれも受理する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=bug)
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    expected = ["二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する"]
    assert warnings == expected, warnings


def test_accepts_human_readable_new_format_plan_without_migration_warning(
    repo: tuple[pathlib.Path, str],
) -> None:
    """新規作成用の人間向け計画ファイル（メイン）・計画ファイル（詳細）をwarningなしで受理する。"""
    work_dir, _base = repo
    main_content, detail_content = _human_new_format_plan(work_dir)
    errors, warnings = _check_new(work_dir, main_content, detail_content, plan_name="human.md")
    assert not errors, errors
    assert not warnings, warnings


def test_new_format_detected_by_detail_file_presence(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（詳細）が存在しない同名の計画ファイル（メイン）は旧形式として検査される（`実装詳細`欠落を新書式エラーにしない）。"""
    work_dir, base = repo
    errors, _warnings = _check(work_dir, _plan(work_dir, base))
    assert not errors, errors


def test_new_format_rejects_detail_reference_mismatch(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（メイン）の`実装詳細`がstem導出値と一致しない場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 実装詳細: `plan.detail.md`", "- 実装詳細: `other.detail.md`")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("stem導出値と一致しない" in error for error in errors), errors


def test_new_format_rejects_missing_detail_metadata_field(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（メイン）の計画メタ情報に`実装詳細`が無い場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 実装詳細: `plan.detail.md`\n", "")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("`実装詳細`が無い" in error for error in errors), errors


def test_new_format_rejects_missing_verification_section(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（メイン）に`## 検証区分`が無い新書式を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace(
        "## 検証区分\n\n"
        "| 区分 | 検証コマンド |\n"
        "| --- | --- |\n"
        "| レーン内検証 | `pytest check_plan_file_test.py` |\n"
        "| 統合後検証 | `make test` |\n\n",
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
    """バグ調査付属ファイルのstemが計画本体と異なる場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    detail_content = detail_content.replace("plan.bugs.md", "other.bugs.md")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("計画stemと一致しない" in error for error in errors), errors


def test_new_format_rejects_bug_sidecar_structure_violation(repo: tuple[pathlib.Path, str]) -> None:
    """バグ調査付属ファイルの固定14行表欠落を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    invalid_bug_file = _bug_file_content().replace("| 直接的原因 | 発生条件と実際値を記載する。 |\n", "")
    errors, _warnings = _check_new(work_dir, main_content, detail_content, bug_file_content=invalid_bug_file)
    assert any("固定14行" in error for error in errors), errors


def test_new_format_rejects_empty_bug_sidecar_content(repo: tuple[pathlib.Path, str]) -> None:
    """バグ調査付属ファイルの`内容`空欄を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    invalid_bug_file = _bug_file_content().replace(
        "| 直接的原因 | 発生条件と実際値を記載する。 |",
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
        inline_section = (
            "## バグ調査結果\n\n### 対象の不整合\n\n"
            + _rows_table(_plan_format.PLAN_BUG_TABLE_ROWS, "発生条件と実際値を記載する。")
            + "\n\n"
        )
        detail_content = detail_content.replace(
            f"## バグ調査結果\n\n- バグ調査ファイル: {reference}\n\n",
            inline_section,
        )
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    expected = ["バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する"]
    if _plan_format.has_legacy_action_table(main_content):
        expected.append("実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する")
    expected.append("二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する")
    assert warnings == expected


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
    assert result.stderr == "[warn] 二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する\n"


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


@pytest.mark.skipif(
    not all(
        path.is_file()
        for path in (
            _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_PLAN,
            _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_DETAIL,
            _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_REVIEW,
        )
    ),
    reason="進捗表を持つ実在する旧二ファイル計画がこの環境に無い",
)
def test_cli_accepts_real_legacy_two_file_plan_without_progress_round_check() -> None:
    """実在する旧二ファイル計画へ新形式の進捗照合を適用せず、本文と表を保持する。"""
    paths = (
        _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_PLAN,
        _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_DETAIL,
        _REAL_LEGACY_TWO_FILE_WITH_PROGRESS_REVIEW,
    )
    before = {path: path.read_bytes() for path in paths}
    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(check_plan_file.__file__)),
            "--work-dir",
            "/home/aki/dotfiles",
            str(_REAL_LEGACY_TWO_FILE_WITH_PROGRESS_PLAN),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "レビュー表の最大round" not in result.stderr
    assert {path: path.read_bytes() for path in paths} == before


def test_review_table_absence_does_not_add_round_error(repo: tuple[pathlib.Path, str]) -> None:
    """同stemのレビュー表が無い計画はround照合を省略する。"""
    work_dir, base = repo
    errors, _warnings = _check(work_dir, _plan(work_dir, base))
    assert not errors, errors


def test_legacy_single_file_plan_does_not_use_progress_round_check(repo: tuple[pathlib.Path, str]) -> None:
    """旧単一形式も新形式の進捗照合を適用せず、旧本文を受理する。"""
    work_dir, base = repo
    (work_dir / "plan.tsv").write_text("not-a-json-row\n", encoding="utf-8")
    errors, _warnings = _check(work_dir, _plan(work_dir, base))
    assert not any("同stemのレビュー表" in error for error in errors), errors


def test_review_table_max_round_matches_progress_rows(repo: tuple[pathlib.Path, str]) -> None:
    """レビュー表の最大roundと進捗行数が一致する場合を受理する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    content = _canonical_main_format(main_content).replace(
        "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n",
        "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n| 2026-08-27 | レビュー | 完了。 |\n",
        1,
    )
    (work_dir / "plan.tsv").write_text(_review_table_row(), encoding="utf-8")
    errors, _warnings = _check_new(work_dir, content, detail_content)
    assert not errors, errors


def test_review_table_missing_rounds_are_reported(repo: tuple[pathlib.Path, str]) -> None:
    """最大roundが進捗行数を超える場合は不足番号を診断する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    content = _canonical_main_format(main_content).replace(
        "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n",
        "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n| 2026-08-27 | レビュー | 進行中。 |\n",
        1,
    )
    (work_dir / "plan.tsv").write_text(_review_table_row("1") + _review_table_row("3"), encoding="utf-8")
    errors, _warnings = _check_new(work_dir, content, detail_content)
    assert any("不足round: 2, 3" in error for error in errors), errors


def test_review_table_malformed_input_is_a_plan_error(repo: tuple[pathlib.Path, str]) -> None:
    """同stemのレビュー表が破損する場合は計画入力エラーとして返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    (work_dir / "plan.tsv").write_text("not-a-json-row\n", encoding="utf-8")
    errors, _warnings = _check_new(work_dir, _canonical_main_format(main_content), detail_content)
    assert any("同stemのレビュー表を検証できない" in error for error in errors), errors


def test_new_canonical_headings_are_accepted_and_legacy_aliases_warn(repo: tuple[pathlib.Path, str]) -> None:
    """新しい固定H2を受理し、正規書式へ旧見出しを混在させた場合は移行warningを返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, detail_name="canonical-plan.detail.md")
    main_content = (
        main_content.replace(
            "\n## 提示素材\n",
            "\n## エージェント判断\n\nなし\n\n## 提示素材\n",
            1,
        )
        .replace("## 変更履歴", "## 変更履歴（計画時）", 1)
        .replace("## 進捗ログ", "## 進捗ログ（実行時）", 1)
    )
    errors, warnings = _check_new(work_dir, main_content, detail_content, plan_name="canonical-plan.md")
    assert not errors, errors
    assert warnings == ["二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する"], warnings

    mixed = main_content.replace("canonical-plan.detail.md", "mixed-plan.detail.md", 1).replace(
        "## 変更履歴（計画時）", "## 変更履歴", 1
    )
    errors, warnings = _check_new(work_dir, mixed, detail_content, plan_name="mixed-plan.md")
    assert not errors, errors
    assert "二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する" in warnings, warnings
    assert any("変更履歴の見出しが旧形式" in warning for warning in warnings), warnings
