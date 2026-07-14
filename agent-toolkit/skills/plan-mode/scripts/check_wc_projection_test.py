"""agent-toolkit/skills/plan-mode/scripts/check_wc_projection.py のテスト。

計画ファイル内の[現行]/[置換後]対比ブロックを機械適用し、wc -l実測値と
見込み行数の乖離を検出する検算スクリプトをsubprocessで起動して検証する。
正常系・乖離検出・[現行]文面不一致・対象ファイル不在・見込み行数記載欠落・220行超過縮減対象H4検査・220行到達済みラベルなし追記検査・
H3見出し無し・複数ファイル対象・対比ブロック無し・削除ペア先頭ラベル行の縮減量除外・
`[追記]`ラベル直接検出とラベル付き/なし追記の分離集計・frontmatterサブラベル
（`[追記（frontmatter）]`等4種）の行数集計と本体変更との合算の各シナリオを網羅する。
"""

import importlib.util
import pathlib
import subprocess
import sys
import types

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_wc_projection.py"


def _load_module() -> types.ModuleType:
    """`check_wc_projection.py`をテスト用にimportし、内部関数への直接アクセスを可能にする。"""
    spec = importlib.util.spec_from_file_location("check_wc_projection", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()


def _run(*plan_paths: pathlib.Path, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *(str(p) for p in plan_paths)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )


def _write(path: pathlib.Path, content: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _plan_with_single_block(*, checkbox_line: str, h3_heading: str, current_text: str, replacement_text: str) -> str:
    """対象ファイル一覧と1個のH3対比ブロックを持つ計画ファイル本文を組み立てる。

    ラベルはfence内側形式（fence直後1行目にプレーンテキストで配置）で出力する。
    """
    return (
        "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
        f"{checkbox_line}\n\n{h3_heading}\n\n"
        f"```text\n[現行]\n{current_text}\n```\n\n"
        f"```text\n[置換後]\n{replacement_text}\n```\n"
    )


def _plan_with_addition_reduction(
    *,
    checkbox_line: str,
    addition_lines: str,
    reduction_block: str = "",
) -> str:
    """対象ファイル一覧`（現行N行, 見込みM行）`宣言と追記/縮減対象ブロックを持つ計画ファイル本文を組み立てる。"""
    return (
        "# テスト計画\n\n"
        "## 変更内容\n\n"
        "### 対象ファイル一覧\n\n"
        f"{checkbox_line}\n\n"
        "### `foo.md`\n\n"
        f"追記:\n\n```text\n{addition_lines}\n```\n\n"
        f"{reduction_block}"
    )


class TestCheckWcProjection:
    """[現行]/[置換後]対比ブロックの機械適用と検算の主要シナリオをまとめて検証する。"""

    @pytest.mark.parametrize(
        ("projected", "expect_ok"),
        [(3, True), (5, True), (10, False)],
        ids=["exact-match", "within-drift", "over-drift"],
    )
    def test_projection_drift_threshold(self, tmp_path: pathlib.Path, projected: int, expect_ok: bool) -> None:
        """見込み行数との乖離が許容幅（2行）以内なら成功し、超えれば違反として報告される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line=f"- [ ] `foo.md`（現行3行, 見込み{projected}行）",
                h3_heading="### `foo.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        if expect_ok:
            assert result.returncode == 0, result.stderr
            assert result.stderr == ""
        else:
            assert result.returncode == 1
            assert f"見込み{projected}行" in result.stderr
            assert "実測3行" in result.stderr

    @pytest.mark.parametrize(
        ("foo_content", "current_text"),
        [("actual content\nsecond\nthird\n", "stale old line"), ("dup\ndup\ndup\n", "dup")],
        ids=["no-match", "ambiguous-match"],
    )
    def test_current_text_mismatch_is_detected(self, tmp_path: pathlib.Path, foo_content: str, current_text: str) -> None:
        """[現行]文面が正本に存在しない、または複数箇所へマッチする場合は違反として報告される。"""
        _write(tmp_path / "foo.md", foo_content)
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行3行, 見込み3行）",
                h3_heading="### `foo.md`",
                current_text=current_text,
                replacement_text="new",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "正本と一致せず、[置換後]文面の反映も確認できない" in result.stderr

    def test_already_applied_replacement_is_treated_as_up_to_date(self, tmp_path: pathlib.Path) -> None:
        """実装完了後の再実行時、対象ファイルが既に[置換後]文面のみを含む場合は適用済みとして通過する。"""
        _write(tmp_path / "foo.md", "new line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行3行, 見込み3行）",
                h3_heading="### `foo.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_already_applied_deletion_is_treated_as_up_to_date(self, tmp_path: pathlib.Path) -> None:
        """削除パターン（[置換後]が空文字列）で対象ファイルから既に[現行]文面が消失している場合も通過する。"""
        _write(tmp_path / "foo.md", "second\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行3行, 見込み2行）",
                h3_heading="### `foo.md`",
                current_text="old line\n",
                replacement_text="",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_missing_target_file_is_detected(self, tmp_path: pathlib.Path) -> None:
        """H3見出しが指す対象ファイルが存在しない場合は違反として報告される。"""
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `missing.md`（現行3行, 見込み3行）",
                h3_heading="### `missing.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "対象ファイル不在" in result.stderr
        assert "missing.md" in result.stderr

    def test_missing_projection_entry_is_detected(self, tmp_path: pathlib.Path) -> None:
        """対象ファイル一覧に見込み行数の記載が無い場合は違反として報告される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（新設）\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold line\n```\n\n```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "見込み行数が対象ファイル一覧に未記載" in result.stderr

    def test_no_matching_h3_heading_passes(self, tmp_path: pathlib.Path) -> None:
        """チェックボックス項目はあるが対応する対比ブロックが無い場合は検査対象が無く成功する。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行3行, 見込み3行）\n\n"
            "### `foo.md`\n\n現行の記述を変更する（対比ブロックなし・プローズのみ）。\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_multiple_target_files_are_each_checked(self, tmp_path: pathlib.Path) -> None:
        """複数ファイルを対象とする計画で、各ファイルが独立に検査される。"""
        _write(tmp_path / "foo.md", "old foo\nsecond\nthird\n")
        _write(tmp_path / "bar.md", "old bar\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行, 見込み3行）\n- [ ] `bar.md`（現行3行, 見込み99行）\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold foo\n```\n\n```text\n[置換後]\nnew foo\n```\n\n"
            "### `bar.md`\n\n```text\n[現行]\nold bar\n```\n\n```text\n[置換後]\nnew bar\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "foo.md" not in result.stderr  # foo.mdは見込み一致のため違反なし
        assert "bar.md" in result.stderr
        assert "見込み99行" in result.stderr

    def test_multiple_blocks_for_same_file_applied_sequentially(self, tmp_path: pathlib.Path) -> None:
        """同一ファイルに複数の対比ブロックがある場合は出現順に逐次適用される。"""
        _write(tmp_path / "foo.md", "alpha\nbeta\ngamma\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行3行, 見込み3行）\n\n"
            "### `foo.md`\n\n置換1。\n\n```text\n[現行]\nalpha\n```\n\n```text\n[置換後]\nalpha2\n```\n\n"
            "置換2。\n\n```text\n[現行]\nbeta\n```\n\n```text\n[置換後]\nbeta2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_pure_addition_block_without_current_label_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """[現行]ラベルを伴わない単独ブロック（追記）は対比対象外として無視される。"""
        _write(tmp_path / "foo.md", "line1\nline2\nline3\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行3行, 見込み3行）\n\n"
            "### `foo.md`\n\n追記（対比なし）:\n\n```text\n追加される新規行\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_plan_without_any_diff_block_passes(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`自体が無い、または対比ブロックを全く含まない計画ファイルはexit 0。"""
        plan = _write(tmp_path / "plan.md", "# テスト計画\n\n## 背景\n\n本文のみで対比ブロックを含まない。\n")
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert result.stderr == ""

    def test_output_format_includes_plan_path(self, tmp_path: pathlib.Path) -> None:
        """違反メッセージには計画ファイルパスが含まれる。"""
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `missing.md`（現行3行, 見込み3行）",
                h3_heading="### `missing.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert str(plan) in result.stderr

    def test_source_file_not_modified(self, tmp_path: pathlib.Path) -> None:
        """[現行]/[置換後]対比ブロックの機械適用で、正本ファイルの内容が改変されないことを検証する。"""
        source = _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        before = source.read_text(encoding="utf-8")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`(現行3行, 見込み3行)",
                h3_heading="### `foo.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        after = source.read_text(encoding="utf-8")
        assert before == after

    def test_orphan_current_block_without_replacement_is_detected(self, tmp_path: pathlib.Path) -> None:
        """[現行]ブロックに対応する[置換後]ブロックが存在しない場合は違反として報告される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`(現行3行, 見込み3行)\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold line\n```\n\n"
            "（[置換後]ブロックが欠落）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "[現行]ブロックに対応する[置換後]ブロックが存在しない" in result.stderr

    def test_consecutive_current_blocks_are_detected_as_orphan(self, tmp_path: pathlib.Path) -> None:
        """同一H3内で[現行]ブロックが[置換後]を介さず連続した場合、先行[現行]が違反として報告される。"""
        _write(tmp_path / "foo.md", "alpha\nbeta\ngamma\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`(現行3行, 見込み3行)\n\n"
            "### `foo.md`\n\n```text\n[現行]\nalpha\n```\n\n"
            "```text\n[現行]\nbeta\n```\n\n```text\n[置換後]\nbeta2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "[現行]ブロックに対応する[置換後]ブロックが存在しない" in result.stderr

    def test_extensionless_filename_in_known_paths_is_targeted(self, tmp_path: pathlib.Path) -> None:
        """拡張子・区切りを持たないファイル名（Makefile等）でも既知パス集合に含まれれば対象となる。"""
        _write(tmp_path / "Makefile", "old target:\n\t@echo old\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `Makefile`(現行2行, 見込み99行)",
                h3_heading="### `Makefile`",
                current_text="old target:",
                replacement_text="new target:",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "Makefile" in result.stderr
        assert "見込み99行" in result.stderr

    def test_bracket_labels_are_supported(self, tmp_path: pathlib.Path) -> None:
        """`[現行]`・`[置換後]`角括弧付きラベル（fence内側形式）の対比ブロックも機械適用対象として認識される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行, 見込み3行）\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold line\n```\n\n"
            "```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_new_file_checkbox_pattern_is_supported(self, tmp_path: pathlib.Path) -> None:
        """`（新設, 見込みN行）`形式の新設ファイルもチェックボックス走査対象として認識される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（新設, 見込み3行）\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold line\n```\n\n"
            "```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_deletion_pattern_does_not_report_as_orphan(self, tmp_path: pathlib.Path) -> None:
        """削除パターン（現行文言＋削除根拠の組）は対比対象外として扱い、未消費違反として報告しない。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行, 見込み3行）\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold line\n```\n\n"
            "```text\n[削除根拠]\n冗長な旧記述のため削除する\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "[現行]ブロックに対応する[置換後]ブロックが存在しない" not in result.stderr

    def test_multiple_plan_files_are_all_checked(self, tmp_path: pathlib.Path) -> None:
        """複数の計画ファイルを位置引数で渡した場合、全件が検査される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        ok_plan = _write(
            tmp_path / "ok.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行3行, 見込み3行）",
                h3_heading="### `foo.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        ng_plan = _write(
            tmp_path / "ng.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行3行, 見込み99行）",
                h3_heading="### `foo.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        result = _run(ok_plan, ng_plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "見込み99行" in result.stderr

    def test_addition_reduction_alignment_ok(self, tmp_path: pathlib.Path) -> None:
        """現行行数+追記量-縮減量が見込み行数と一致する場合は乖離検出なし。"""
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_addition_reduction(
                checkbox_line="- [ ] `foo.md`（現行10行, 見込み11行）",
                addition_lines="line1\nline2",
                reduction_block="#### 縮減対象\n\n```text\nold\n```\n",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_addition_reduction_drift_exceeds_threshold(self, tmp_path: pathlib.Path) -> None:
        """現行行数+追記量-縮減量と見込み行数の乖離が2行超の場合は乖離検出。"""
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_addition_reduction(
                checkbox_line="- [ ] `foo.md`（現行10行, 見込み11行）",
                addition_lines="line1\nline2\nline3\nline4\nline5\nline6",
                reduction_block="#### 縮減対象\n\n```text\nold\n```\n",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "乖離が許容幅を超える" in result.stderr

    def test_addition_without_reduction(self, tmp_path: pathlib.Path) -> None:
        """追記のみで縮減対象記述が無い場合は追記量そのままで見込み行数と照合。"""
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_addition_reduction(
                checkbox_line="- [ ] `foo.md`（現行10行, 見込み13行）",
                addition_lines="line1\nline2\nline3",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_reduction_block_detected_by_h4_heading(self, tmp_path: pathlib.Path) -> None:
        """`#### 縮減対象`のH4見出し直後のブロックを縮減対象として検出する。"""
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_addition_reduction(
                checkbox_line="- [ ] `foo.md`（現行10行, 見込み9行）",
                addition_lines="line1",
                reduction_block="#### 縮減対象（foo.md）\n\n```text\nold1\nold2\n```\n",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_annotation_only_first_line_excluded_from_count(self, tmp_path: pathlib.Path) -> None:
        """フェンス内1行目が「（挿入先注記）」のみの場合、実挿入内容ではないため集計から除外する。"""
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み12行）\n\n"
            "### `foo.md`\n\n"
            "追記文言案は次のとおり。\n\n"
            "```text\n（挿入先の説明）\nline1\nline2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_addition_blocks_detected_across_multiple_consecutive_blocks(self, tmp_path: pathlib.Path) -> None:
        """「追記文言案は次のとおり」直後からH3節境界までの連続textブロックを全て追記として検出する。"""
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み15行）\n\n"
            "### `foo.md`\n\n"
            "追記文言案は次のとおり。\n\n"
            "```text\nline1\nline2\n```\n\n"
            "```text\nline3\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_addition_trigger_stops_at_h3_boundary(self, tmp_path: pathlib.Path) -> None:
        """追記トリガー継続中フラグは次のH3見出しで解除され、後続H3のブロックは追記扱いされない。"""
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み11行）\n"
            "- [ ] `bar.md`（現行5行, 見込み5行）\n\n"
            "### `foo.md`\n\n"
            "追記文言案は次のとおり。\n\n"
            "```text\nline1\n```\n\n"
            "### `bar.md`\n\n"
            "既存の記述を変更する（対比ブロックなし・プローズのみ）。\n\n"
            "```text\n参考コード1\n参考コード2\n参考コード3\n参考コード4\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_reduction_not_detected_by_non_h4_heading(self, tmp_path: pathlib.Path) -> None:
        """`### 縮減対象`等H4以外の見出しでは縮減対象として検出されず、乖離扱いとなる。

        H3見出し配下は縮減対象ブロックと判定されないため、追記1行のみが集計される
        （現行10行+追記1行=見込み11行のはずが宣言が5行のため乖離となる）。
        """
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み5行）\n\n"
            "### `foo.md`\n\n"
            "追記:\n\n```text\nline1\n```\n\n"
            "### 縮減対象\n\n```text\nold1\nold2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "乖離が許容幅を超える" in result.stderr

    def test_addition_reduction_skips_py_extension(self, tmp_path: pathlib.Path) -> None:
        """`.py`ファイルは`.md`限定分岐で追記/縮減対象集計対象外となる（乖離しても違反として報告されない）。"""
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.py`（現行10行, 見込み5行）\n\n"
            "### `foo.py`\n\n"
            "追記:\n\n```text\nprint('a')\nprint('b')\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_addition_reduction_detects_md_extension(self, tmp_path: pathlib.Path) -> None:
        """`.md`ファイルは追記/縮減対象集計の検査対象として乖離を検出する。"""
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み5行）\n\n"
            "### `foo.md`\n\n"
            "追記:\n\n```text\nline1\nline2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "乖離が許容幅を超える" in result.stderr

    def test_fence_containing_h2_like_line_does_not_truncate_section(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`節内のフェンスに他H2見出し様の行が含まれても、節本文が誤って途中終端しない。

        フェンス内行を境界判定から除外しない実装では、フェンス内の`## 出力例の見出し`行を
        節境界と誤認識し、後続の[現行]/[置換後]ブロックが`## 変更内容`節の外側として扱われ
        検査対象から漏れる（見込み行数の乖離を検出できず誤って通過する）。
        """
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行, 見込み99行）\n\n"
            "### `foo.md`\n\n"
            "出力例:\n\n```text\n## 出力例の見出し\n```\n\n"
            "```text\n[現行]\nold line\n```\n\n"
            "```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "見込み99行" in result.stderr

    def test_deletion_pattern_current_block_counted_as_reduction(self, tmp_path: pathlib.Path) -> None:
        """[削除根拠]付き[現行]ブロックの[現行]行数が縮減対象集計へ加算され、乖離0で通過する。"""
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み8行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\nold2\n```\n\n"
            "```text\n[削除根拠]\n冗長なため削除する\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_deletion_pattern_excludes_leading_current_label_from_reduction(self, tmp_path: pathlib.Path) -> None:
        """[削除根拠]付き[現行]ブロックの先頭ラベル行`[現行]`は縮減量から除外される。"""
        # 削除ペア3件・実本文各2行。ラベル込み（3行×3）ならreduction=9で
        # expected=12-9=3、見込み6行との差3で_ALLOWED_DRIFTを超過する。
        # 除外後（2行×3）ならreduction=6でexpected=6、差0で通過する。
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行12行, 見込み6行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\na1\na2\n```\n\n"
            "```text\n[削除根拠]\n冗長\n```\n\n"
            "```text\n[現行]\nb1\nb2\n```\n\n"
            "```text\n[削除根拠]\n冗長\n```\n\n"
            "```text\n[現行]\nc1\nc2\n```\n\n"
            "```text\n[削除根拠]\n冗長\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_addition_label_block_counted_without_trigger(self, tmp_path: pathlib.Path) -> None:
        """隣接文言に「追記」「追加」の語が無い`[追記]`ブロックも集計対象へ入る。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(10)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み12行）\n\n"
            "### `foo.md`\n\n"
            "特に前置き無し。\n\n"
            "```text\n[追記]\n追加行A\n追加行B\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_addition_label_line_and_annotation_both_excluded(self, tmp_path: pathlib.Path) -> None:
        """`[追記]`ラベル行と位置注記行の両方が集計から除外される。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(10)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み12行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記]\n（挿入先: 節末尾）\n追加行A\n追加行B\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_addition_reduction_detects_md_tmpl_extension(self, tmp_path: pathlib.Path) -> None:
        """`.md.tmpl`ファイルも追記/縮減対象集計の検査対象として乖離を検出する。"""
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md.tmpl`（現行10行, 見込み5行）\n\n"
            "### `foo.md.tmpl`\n\n"
            "追記:\n\n```text\nline1\nline2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "乖離が許容幅を超える" in result.stderr


class TestOverThresholdReductionCheck:
    """`_check_reduction_block_for_over_threshold_files`の警告出力仕様を検証する。

    見込み220行超のファイルを対象に、対応する`#### 縮減対象（<ファイル名>）`
    H4見出しの存在を検査する。警告は情報提供扱いで違反件数には計上しない（returncode 0）。
    """

    def test_over_threshold_file_without_reduction_heading_warns(self, tmp_path: pathlib.Path) -> None:
        """220行超過ファイル対象・縮減対象H4不在時に警告が出力される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行220行, 見込み230行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "220行超過ファイル" in result.stderr
        assert "`#### 縮減対象（foo.md）`H4見出しが不在" in result.stderr

    def test_over_threshold_file_with_reduction_heading_passes(self, tmp_path: pathlib.Path) -> None:
        """220行超過ファイル対象・縮減対象H4完備時は警告が出ない。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行220行, 見込み230行）\n\n"
            "### `foo.md`\n\n"
            "#### 縮減対象（foo.md）\n\n```text\n[削除根拠]\nold verbose\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "220行超過ファイル" not in result.stderr

    def test_over_threshold_files_partial_headings_warn_only_missing(self, tmp_path: pathlib.Path) -> None:
        """220行超過ファイル対象・一部のみH4完備時は不在ファイルにのみ警告が出力される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行220行, 見込み230行）\n"
            "- [ ] `bar.md`（現行220行, 見込み235行）\n\n"
            "### `foo.md`\n\n"
            "#### 縮減対象（foo.md）\n\n```text\n[削除根拠]\nold\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "bar.md" in result.stderr
        assert "220行超過ファイルfoo.md" not in result.stderr

    def test_at_threshold_file_skips_check(self, tmp_path: pathlib.Path) -> None:
        """見込み220行ちょうど・以下のファイルは検査対象外となる（220行以下収束の完了条件）。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行150行, 見込み220行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "220行超過ファイル" not in result.stderr

    def test_over_threshold_file_with_qualified_name_heading_passes(self, tmp_path: pathlib.Path) -> None:
        """修飾名（例:「agent-standards SKILL.md」）で書かれた縮減対象H4見出しも突合成功する。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `agent-toolkit/skills/agent-standards/SKILL.md`（現行220行, 見込み230行）\n\n"
            "### `agent-toolkit/skills/agent-standards/SKILL.md`\n\n"
            "#### 縮減対象（agent-standards SKILL.md）\n\n```text\n[削除根拠]\nold verbose\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "220行超過ファイル" not in result.stderr

    def test_far_over_threshold_file_also_warns(self, tmp_path: pathlib.Path) -> None:
        """220行を大きく超えるファイル（300行以上）でも220行超過として警告される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行300行, 見込み300行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "220行超過ファイル" in result.stderr
        assert "`#### 縮減対象（foo.md）`H4見出しが不在" in result.stderr

    def test_py_extension_over_threshold_does_not_warn(self, tmp_path: pathlib.Path) -> None:
        """`.py`ファイルが220行超過でもH4見出し警告は発生しない（拡張子フィルタ）。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.py`（現行220行, 見込み250行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "220行超過ファイル" not in result.stderr


class TestOverThresholdLabellessAdditionCheck:
    """`_check_labelless_addition_for_over_threshold_files`の警告出力仕様を検証する。

    現行220行超のファイルへの追記がラベルなしtextフェンスのみで縮減量集計に載らない場合を検出する。
    警告は情報提供扱いで違反件数には計上しない（returncode 0）。
    """

    def test_labelless_addition_over_threshold_emits_warning(self, tmp_path: pathlib.Path) -> None:
        """現行220行超・追記のみラベルなしで縮減0の場合、差分ラベル付与を促す警告が出る。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行, 見込み234行）\n\n"
            "### `foo.md`\n\n追記文言案:\n\n"
            "```text\n追加行A\n追加行B\n追加行C\n追加行D\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "220行到達済みファイルfoo.md" in result.stderr
        assert "差分ラベル付与を検討" in result.stderr

    def test_labeled_addition_over_threshold_no_warning(self, tmp_path: pathlib.Path) -> None:
        """現行220行超で`[現行]`/`[置換後]`ペア記述時は警告が出ない。"""
        _write(tmp_path / "foo.md", "line0\n" + "\n".join(f"line{i}" for i in range(1, 231)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行231行, 見込み231行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nline0\n```\n\n"
            "```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "差分ラベル付与を検討" not in result.stderr

    def test_addition_label_only_over_threshold_no_warning(self, tmp_path: pathlib.Path) -> None:
        """現行220行超で`[追記]`ラベル単独使用時は警告が出ない（`addition_labelless`が0のため）。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行, 見込み234行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記]\n追加行A\n追加行B\n追加行C\n追加行D\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "差分ラベル付与を検討" not in result.stderr

    def test_addition_label_and_labelless_mix_over_threshold_warns(self, tmp_path: pathlib.Path) -> None:
        """`[追記]`ラベル付きとラベルなし追記が混在時、ラベルなし追記が残っていれば警告対象となる。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行, 見込み236行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記]\nラベル付き行A\nラベル付き行B\n```\n\n"
            "追記文言案:\n\n"
            "```text\nラベルなし行A\nラベルなし行B\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "220行到達済みファイルfoo.md" in result.stderr
        assert "差分ラベル付与を検討" in result.stderr

    def test_under_threshold_labelless_addition_no_warning(self, tmp_path: pathlib.Path) -> None:
        """現行220行以下のファイルはラベルなし追記でも警告が出ない。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(100)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行100行, 見込み104行）\n\n"
            "### `foo.md`\n\n追記文言案:\n\n"
            "```text\n追加行A\n追加行B\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "差分ラベル付与を検討" not in result.stderr


class TestCheckboxProjectionAcceptance:
    """`_CHECKBOX_RE`と`_CHECKBOX_UNDETERMINED_RE`の書式受理範囲を検証する。"""

    def test_short_form_projection_is_accepted(self, tmp_path: pathlib.Path) -> None:
        """「見込」表記（送り仮名なし）のチェックボックスも受理される。"""
        source = tmp_path / "foo.md"
        source.write_text("line1\nline2\nline3\n", encoding="utf-8")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行3行, 見込3行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "未記載" not in result.stderr

    def test_undetermined_projection_skips_drift_check(self, tmp_path: pathlib.Path) -> None:
        """「実装後未確定」表記は乖離判定をスキップし違反にならない。"""
        source = tmp_path / "foo.py"
        source.write_text("line1\nline2\n", encoding="utf-8")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.py`（現行2行, 実装後未確定）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "未記載" not in result.stderr

    def test_non_md_path_without_projection_is_accepted(self, tmp_path: pathlib.Path) -> None:
        """`.py`パスは見込み行数チェックボックスが存在しなくても違反にならない。"""
        source = tmp_path / "foo.py"
        source.write_text("line1\nline2\nline3\n", encoding="utf-8")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.py`（新設）\n\n"
            "### `foo.py`\n\n```text\n[現行]\nline1\n```\n\n```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "未記載" not in result.stderr


class TestLeadingLabel:
    """`_leading_label`のfence内側形式ラベル検出を単体レベルで検証する。"""

    # pylint: disable=protected-access

    def test_current_label_inside_fence_is_detected(self) -> None:
        assert _MOD._leading_label(["[現行]", "old body"]) == "current"

    def test_replacement_label_inside_fence_is_detected(self) -> None:
        assert _MOD._leading_label(["[置換後]", "new body"]) == "replacement"

    def test_deletion_rationale_label_inside_fence_is_detected(self) -> None:
        assert _MOD._leading_label(["[削除根拠]", "冗長のため削除"]) == "deletion"

    def test_replacement_full_takes_precedence_over_replacement(self) -> None:
        """「置換後（全文）」判定が「置換後」判定より先に評価される。"""
        assert _MOD._leading_label(["[置換後（全文）]", "whole file body"]) == "replacement-full"

    def test_new_label_inside_fence_is_detected(self) -> None:
        assert _MOD._leading_label(["[新設]", "new file body"]) == "new"

    def test_no_label_returns_none(self) -> None:
        assert _MOD._leading_label(["regular body"]) is None
        assert _MOD._leading_label([]) is None

    def test_addition_label_inside_fence_is_detected(self) -> None:
        """`[追記]`ラベルは`"addition"`種別として返却される。"""
        assert _MOD._leading_label(["[追記]", "追記本文"]) == "addition"


class TestFrontmatterLabelExtraction:
    """frontmatterサブラベル（`[追記（frontmatter）]`等4種）の行数集計を検証する。"""

    def test_addition_frontmatter_sublabel_counted(self, tmp_path: pathlib.Path) -> None:
        """`[追記（frontmatter）]`ブロックの本文行数が追記量として集計される。"""
        _write(tmp_path / "foo.md", "---\ntitle: t\n---\nbody\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行4行, 見込み6行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記（frontmatter）]\nsummary: s\ntags: []\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_current_replacement_frontmatter_pair_applied(self, tmp_path: pathlib.Path) -> None:
        """`[現行（frontmatter）]`/`[置換後（frontmatter）]`対比ペアが実ファイルへ適用され、見込み行数と照合される。"""
        _write(tmp_path / "foo.md", "---\ntitle: old\n---\nbody\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行4行, 見込み5行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行（frontmatter）]\ntitle: old\n```\n\n"
            "```text\n[置換後（frontmatter）]\ntitle: new\nsummary: s\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_deletion_frontmatter_sublabel_counted_as_reduction(self, tmp_path: pathlib.Path) -> None:
        """`[削除根拠（frontmatter）]`ブロックの直前`[現行（frontmatter）]`行数が縮減量として集計される。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(10)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み8行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行（frontmatter）]\nold-line1\nold-line2\n```\n\n"
            "```text\n[削除根拠（frontmatter）]\n陳腐化のため削除\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_frontmatter_and_body_addition_summed(self, tmp_path: pathlib.Path) -> None:
        """frontmatter変更（`[追記（frontmatter）]`）と本体変更（`[追記]`）が同一H3内で合算される。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(10)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み13行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記（frontmatter）]\nfm-line1\n```\n\n"
            "```text\n[追記]\nbody-line1\nbody-line2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr


class TestVariableLengthFence:
    """可変長フェンス（4バッククォート以上）のfence内側形式対応を検証する。"""

    def test_four_backtick_fence_diff_block_is_processed(self, tmp_path: pathlib.Path) -> None:
        """外側4バッククォートで囲んだfence内側形式ラベルの対比ブロックも機械適用対象。"""
        _write(tmp_path / "foo.md", "```\ninner code\n```\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行, 見込み3行）\n\n"
            "### `foo.md`\n\n"
            "````text\n[現行]\n```\ninner code\n```\n````\n\n"
            "````text\n[置換後]\n```\nnew inner\n```\n````\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
