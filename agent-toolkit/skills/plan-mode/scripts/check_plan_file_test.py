"""簡素化した計画構造検査の回帰テスト。"""

from __future__ import annotations

import pathlib
import subprocess

import check_plan_file
import pytest

_BASE = "a" * 40


def _plan(
    path: str = "existing.py",
    *,
    suffix: str = "（現行1行）",
    body: str = "変更する",
    work_type: str = "通常変更",
    background: str = "",
) -> str:
    return f"""# 主題

## 変更履歴

- なし

## 背景

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `/tmp/repo`
- 作業種別: {work_type}
- ベースコミット: `{_BASE}`

{background}

## 対応方針

- 実施する

## 実装資料

- 調査済み

## 変更内容

### 対象ファイル一覧

- [ ] `{path}`{suffix}

### `{path}`

```text
{body}
```

## 実行方法

- 検証する

## 進捗ログ

- 未着手

## 計画ファイル（本ファイル）のパス

`/tmp/plan.md`
"""


def _run(tmp_path: pathlib.Path, text: str) -> tuple[list[str], list[str]]:
    (tmp_path / "existing.py").write_text("x\n", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text(text, encoding="utf-8")
    return check_plan_file.check(plan, tmp_path, None)


def test_valid_plan_passes(tmp_path: pathlib.Path) -> None:
    assert _run(tmp_path, _plan()) == ([], [])


@pytest.mark.parametrize("literal", ["`<!--`", "`` `<!--` ``", r"`<!--\`", r"\<!--"])
def test_html_comment_literal_does_not_hide_required_h2(tmp_path: pathlib.Path, literal: str) -> None:
    text = _plan(background=f"{literal}は説明用のリテラルである。")
    assert _run(tmp_path, text) == ([], [])


def test_multiline_code_span_html_comment_literal_does_not_hide_required_h2(tmp_path: pathlib.Path) -> None:
    text = _plan(background="説明 `開始\n行内 <!--\n終了`")
    assert _run(tmp_path, text) == ([], [])


def test_unclosed_backtick_in_previous_block_does_not_hide_required_h2(tmp_path: pathlib.Path) -> None:
    text = _plan(background="先行 `未成立\n\n後続 `<!--`") + "\n-->\n"
    assert _run(tmp_path, text) == ([], [])


def test_optional_completion_section_passes(tmp_path: pathlib.Path) -> None:
    text = _plan().replace("## 進捗ログ", "## 完了条件\n\n- 検証結果を確認できる\n\n## 進捗ログ", 1)
    assert _run(tmp_path, text) == ([], [])


def test_required_h2_inside_multiline_html_comment_is_ignored(tmp_path: pathlib.Path) -> None:
    text = _plan(background="<!--\n## 背景\n-->")
    assert _run(tmp_path, text) == ([], [])


@pytest.mark.parametrize("literal", ["```text", "<!--"])
def test_frontmatter_block_scalar_literal_does_not_hide_body(tmp_path: pathlib.Path, literal: str) -> None:
    frontmatter = f"---\ndescription: |\n  {literal}\n---\n"
    assert _run(tmp_path, frontmatter + _plan()) == ([], [])


def test_midline_multiline_html_comment_is_ignored(tmp_path: pathlib.Path) -> None:
    background = "説明の途中 <!--\n## コメント内\n- 計画形式移行: 調査結果から実装資料\n-->"
    assert _run(tmp_path, _plan(background=background)) == ([], [])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_plan().replace("# 主題", "主題", 1), "H1"),
        (_plan() + "\n# 追加\n", "H1"),
        (_plan().replace("### `existing.py`\n", "", 1), "H3"),
        (_plan().replace("- [ ] `existing.py`（現行1行）\n", "", 1), "対象ファイル一覧"),
        (_plan().replace("```text\n変更する\n```", "変更する"), "コードブロック"),
        (_plan("missing.py"), "実在確認"),
        (_plan().replace("## 実装資料\n\n- 調査済み\n\n", "", 1), "必須H2"),
        (
            _plan().replace(
                "## 対応方針\n\n- 実施する\n\n## 実装資料\n\n- 調査済み",
                "## 実装資料\n\n- 調査済み\n\n## 対応方針\n\n- 実施する",
                1,
            ),
            "順序",
        ),
        (_plan().replace("### 計画メタ情報", "### メタ情報", 1), "計画メタ情報"),
    ],
)
def test_structural_errors(tmp_path: pathlib.Path, text: str, message: str) -> None:
    errors, _warnings = _run(tmp_path, text)
    assert any(message in error for error in errors)


def test_new_path_does_not_require_existence(tmp_path: pathlib.Path) -> None:
    assert _run(tmp_path, _plan("new.py", suffix="（新設）")) == ([], [])


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("- 起動経路: `agent-toolkit:plan-mode`\n", "", "起動経路"),
        ("- 対象リポジトリ: `/tmp/repo`\n", "", "対象リポジトリ"),
        ("- 作業種別: 通常変更", "- 作業種別: 調査", "作業種別"),
        (f"- ベースコミット: `{_BASE}`", "- ベースコミット: `abc`", "ベースコミット"),
    ],
)
def test_plan_metadata_requires_unique_valid_fields(
    tmp_path: pathlib.Path,
    old: str,
    new: str,
    message: str,
) -> None:
    errors, _warnings = _run(tmp_path, _plan().replace(old, new, 1))
    assert any(message in error for error in errors)


def test_base_commit_outside_metadata_does_not_satisfy_requirement(tmp_path: pathlib.Path) -> None:
    text = _plan().replace(f"- ベースコミット: `{_BASE}`\n", "", 1) + f"\n- 計画着手前SHA: `{_BASE}`\n"

    errors, _warnings = _run(tmp_path, text)

    assert any("ベースコミット" in error for error in errors)


def test_duplicate_plan_metadata_field_is_error(tmp_path: pathlib.Path) -> None:
    text = _plan().replace(
        "- 起動経路: `agent-toolkit:plan-mode`",
        "- 起動経路: `agent-toolkit:plan-mode`\n- 起動経路: その他",
        1,
    )

    errors, _warnings = _run(tmp_path, text)

    assert any("起動経路" in error and "2件" in error for error in errors)


def test_deleted_path_requires_delete_instruction(tmp_path: pathlib.Path) -> None:
    errors, _warnings = _run(tmp_path, _plan(suffix="（現行1行、廃止・削除）", body="変更する"))
    assert any("削除指示" in error for error in errors)


def test_deleted_path_with_instruction_passes(tmp_path: pathlib.Path) -> None:
    assert _run(tmp_path, _plan(suffix="（現行1行、廃止・削除）", body="削除する")) == ([], [])


def test_deleted_path_with_h3_suffix_passes(tmp_path: pathlib.Path) -> None:
    text = _plan(suffix="（廃止・削除）", body="削除する").replace("### `existing.py`", "### `existing.py`（廃止・削除）")
    assert _run(tmp_path, text) == ([], [])


def test_unclosed_fence_is_error(tmp_path: pathlib.Path) -> None:
    errors, _warnings = _run(tmp_path, _plan().replace("```text\n変更する\n```", "```text\n変更する", 1))
    assert any("フェンス" in error for error in errors)


def test_table_cell_mismatch_is_error(tmp_path: pathlib.Path) -> None:
    text = _plan() + "\n| A | B |\n| --- | --- |\n| x |\n"
    errors, _warnings = _run(tmp_path, text)
    assert any("セル数" in error for error in errors)


def test_escaped_pipe_in_table_cell_passes(tmp_path: pathlib.Path) -> None:
    text = _plan() + "\n| A | B |\n| --- | --- |\n| x\\|y | z |\n"

    assert _run(tmp_path, text) == ([], [])


def test_table_header_separator_mismatch_is_error(tmp_path: pathlib.Path) -> None:
    text = _plan() + "\n| A | B |\n| --- |\n| x | y |\n"

    errors, _warnings = _run(tmp_path, text)

    assert any("セル数" in error for error in errors)


def test_missing_skill_reference_is_error(tmp_path: pathlib.Path) -> None:
    text = _plan().replace("- 検証する", "- Skillツールで`agent-toolkit:not-found`を起動する")
    errors, _warnings = _run(tmp_path, text)
    assert any("スキル参照" in error for error in errors)


def test_references_in_code_fence_are_ignored(tmp_path: pathlib.Path) -> None:
    text = _plan().replace(
        "- 検証する",
        "- 検証する\n\n```text\nSkillツールで`agent-toolkit:not-found`を起動する\n"
        "Agentツールで`agent-toolkit:not-found`を起動する\n```",
        1,
    )

    assert _run(tmp_path, text) == ([], [])


@pytest.mark.parametrize("agent", ["claude", "Explore", "Plan"])
def test_generic_agent_reference_does_not_require_definition(tmp_path: pathlib.Path, agent: str) -> None:
    text = _plan().replace("- 検証する", f"- Agentツールで`{agent}`を起動する", 1)

    assert _run(tmp_path, text) == ([], [])


def test_base_commit_changed_file_mismatch_is_warning(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "existing.py").write_text("x\n", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text(_plan(), encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "other.py\n", ""),
    )
    errors, warnings = check_plan_file.check(plan, tmp_path, _BASE)
    assert not errors
    assert len(warnings) == 1


def test_main_returns_two_for_unreadable_plan(tmp_path: pathlib.Path) -> None:
    assert check_plan_file.main([str(tmp_path / "missing.md")]) == 2


_BUG_ROWS = (
    "観測事象",
    "期待する契約",
    "直接的原因",
    "混入要因",
    "動機的要因",
    "見逃し原因",
    "根本原因",
    "原因分析の根拠",
    "類似見直しの観点",
    "類似見直し結果",
    "是正処置",
    "横展開処置",
    "再発防止処置",
    "設計意図の記録",
)


def _bug_table(name: str, *, rows: tuple[str, ...] = _BUG_ROWS, header: str = "| 項目 | 内容 |") -> str:
    body = "\n".join(f"| {row} | 確認済み |" for row in rows)
    return f"### バグ調査結果: {name}\n\n{header}\n| --- | --- |\n{body}\n"


def test_transition_marker_accepts_legacy_implementation_materials_h2(tmp_path: pathlib.Path) -> None:
    text = _plan(background="- 計画形式移行: 調査結果から実装資料").replace("## 実装資料", "## 調査結果")
    assert _run(tmp_path, text) == ([], [])


def test_legacy_implementation_materials_h2_without_marker_is_error(tmp_path: pathlib.Path) -> None:
    errors, _warnings = _run(tmp_path, _plan().replace("## 実装資料", "## 調査結果"))
    assert any("実装資料" in error for error in errors)


def test_transition_marker_rejects_mixed_implementation_materials_h2(tmp_path: pathlib.Path) -> None:
    text = _plan(background="- 計画形式移行: 調査結果から実装資料").replace(
        "## 実装資料",
        "## 調査結果\n\n- 旧資料\n\n## 実装資料",
        1,
    )
    errors, _warnings = _run(tmp_path, text)
    assert any("unexpected H2 sections" in error for error in errors)


def test_bug_investigation_table_accepts_multiple_named_tables(tmp_path: pathlib.Path) -> None:
    background = _bug_table("保存失敗") + "\n" + _bug_table("再試行停止")
    assert _run(tmp_path, _plan(work_type="バグ対応", background=background)) == ([], [])


def test_bug_investigation_table_warns_for_duplicate_bug_names(tmp_path: pathlib.Path) -> None:
    background = _bug_table("保存失敗") + "\n" + _bug_table("保存失敗")
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=background))
    assert any("重複" in warning and "保存失敗" in warning for warning in warnings)


def test_bug_investigation_table_warns_for_legacy_unnamed_heading(tmp_path: pathlib.Path) -> None:
    background = _bug_table("保存失敗").replace("### バグ調査結果: 保存失敗", "### バグ調査結果", 1)
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=background))
    assert any("旧形式" in warning for warning in warnings)


def test_bug_investigation_table_warns_for_empty_name(tmp_path: pathlib.Path) -> None:
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=_bug_table("")))
    assert any("空" in warning for warning in warnings)


def test_bug_investigation_table_warns_for_empty_name_with_atx_closing_sequence(tmp_path: pathlib.Path) -> None:
    background = _bug_table("").replace("### バグ調査結果: ", "### バグ調査結果: ###", 1)
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=background))
    assert any("空" in warning for warning in warnings)


def test_bug_investigation_table_warns_for_legacy_heading_with_atx_closing_sequence(tmp_path: pathlib.Path) -> None:
    background = _bug_table("保存失敗").replace("### バグ調査結果: 保存失敗", "### バグ調査結果 ###", 1)
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=background))
    assert any("旧形式" in warning for warning in warnings)


def test_bug_investigation_table_normalizes_atx_closing_sequence_before_duplicate_check(
    tmp_path: pathlib.Path,
) -> None:
    first = _bug_table("保存失敗")
    second = _bug_table("保存失敗").replace("### バグ調査結果: 保存失敗", "### バグ調査結果: 保存失敗 ###", 1)
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=first + "\n" + second))
    assert any("重複" in warning and "保存失敗" in warning for warning in warnings)


def test_bug_investigation_table_ignores_heading_inside_multiline_html_comment(tmp_path: pathlib.Path) -> None:
    commented = "<!--\n### バグ調査結果: コメント内\n-->\n"
    assert _run(
        tmp_path,
        _plan(work_type="バグ対応", background=commented + "\n" + _bug_table("保存失敗")),
    ) == ([], [])


def test_bug_investigation_table_ignores_heading_inside_blockquote(tmp_path: pathlib.Path) -> None:
    quoted_heading = _bug_table("引用内").replace("### バグ調査結果: 引用内", "> ### バグ調査結果: 引用内", 1)
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=quoted_heading))
    assert any("1件以上必要" in warning for warning in warnings)


def test_bug_investigation_table_ignores_parent_h2_inside_blockquote(tmp_path: pathlib.Path) -> None:
    background = "> ## 引用内\n\n" + _bug_table("保存失敗")
    assert _run(tmp_path, _plan(work_type="バグ対応", background=background)) == ([], [])


def test_bug_investigation_table_warns_for_wrong_parent(tmp_path: pathlib.Path) -> None:
    text = _plan(work_type="バグ対応", background=_bug_table("保存失敗"))
    text = text.replace(_bug_table("保存失敗") + "\n\n## 対応方針", "\n## 対応方針\n\n" + _bug_table("保存失敗"), 1)
    _errors, warnings = _run(tmp_path, text)
    assert any("親H2" in warning and "保存失敗" in warning for warning in warnings)


def test_bug_investigation_table_reports_each_invalid_named_table(tmp_path: pathlib.Path) -> None:
    first = _bug_table("保存失敗", rows=_BUG_ROWS[:-1])
    second = _bug_table("再試行停止", header="| 項目 | 内容 | 補足 |")
    _errors, warnings = _run(tmp_path, _plan(work_type="バグ対応", background=first + "\n" + second))
    assert any("保存失敗" in warning and "必須14行" in warning for warning in warnings)
    assert any("再試行停止" in warning and "2列" in warning for warning in warnings)
