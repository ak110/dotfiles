"""agent-toolkit/skills/plan-mode/scripts/check_wc_projection.py のテスト。

計画ファイル内の[現行]/[置換後]対比ブロックを機械適用し、対象ファイル実体との一意一致
（転記の陳腐化防止）を検証する検算スクリプトをfork-server経由（フォールバック時はsubprocess）で
起動して検証する。正常系・[現行]文面不一致・対象ファイル不在・H3見出し無し・複数ファイル対象・
対比ブロック無し・削除ペア先頭ラベル行の縮減量除外・`[追記]`ラベル直接検出・
`[現行]`/`[置換後]`ペア差分集計の各シナリオを網羅する。
220行超過縮減対象H4検査の既存シナリオは`check_wc_projection_extra_test.py`が担う。
"""

import importlib.util
import pathlib
import subprocess
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import _fork_runner  # noqa: E402  # pylint: disable=wrong-import-position

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
    return _fork_runner.run_script(_SCRIPT, argv=tuple(str(p) for p in plan_paths), cwd=cwd)


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
    """対象ファイル一覧`（現行N行）`宣言と追記/縮減対象ブロックを持つ計画ファイル本文を組み立てる。"""
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
    """[現行]/[置換後]対比ブロックの機械適用と一意一致検査の主要シナリオをまとめて検証する。"""

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
                checkbox_line="- [ ] `foo.md`（現行3行）",
                h3_heading="### `foo.md`",
                current_text=current_text,
                replacement_text="new",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        if current_text == "stale old line":
            assert "0回検出（転記誤りの可能性）" in result.stderr
        else:
            assert "3回検出（一意化不足）" in result.stderr
            assert "[現行]ブロックへ周辺行を含めて一意化する必要" in result.stderr
        assert "[置換後]文面の反映も確認できない" in result.stderr

    def test_already_applied_replacement_is_treated_as_up_to_date(self, tmp_path: pathlib.Path) -> None:
        """実装完了後の再実行時、対象ファイルが既に[置換後]文面のみを含む場合は適用済みとして通過する。"""
        _write(tmp_path / "foo.md", "new line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行3行）",
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
                checkbox_line="- [ ] `foo.md`（現行2行）",
                h3_heading="### `foo.md`",
                current_text="old line\n",
                replacement_text="",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_pure_addition_diff_block_already_applied_is_not_double_counted(self, tmp_path: pathlib.Path) -> None:
        """純追記パターン（過去に発生した回帰）は実装完了後の再実行で二重適用されない。"""
        current_text = "old line"
        replacement_text = "old line\nnew line 1\nnew line 2\nnew line 3"
        _write(tmp_path / "foo.md", f"{replacement_text}\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行1行）",
                h3_heading="### `foo.md`",
                current_text=current_text,
                replacement_text=replacement_text,
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_replacement_coincidentally_present_elsewhere_does_not_skip_current_replacement(
        self, tmp_path: pathlib.Path
    ) -> None:
        """通常の書き換えパターンでは[置換後]が偶然別箇所に存在しても[現行]が優先される（純追記限定判定の回帰確認）。"""
        current_text = "old\nline2\nline3\nline4"
        replacement_text = "existing"
        _write(tmp_path / "foo.md", f"{replacement_text}\n{current_text}\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行5行）",
                h3_heading="### `foo.md`",
                current_text=current_text,
                replacement_text=replacement_text,
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
                checkbox_line="- [ ] `missing.md`（現行3行）",
                h3_heading="### `missing.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "対象ファイル不在" in result.stderr
        assert "missing.md" in result.stderr

    def test_no_matching_h3_heading_passes(self, tmp_path: pathlib.Path) -> None:
        """チェックボックス項目はあるが対応する対比ブロックが無い場合は検査対象が無く成功する。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行3行）\n\n"
            "### `foo.md`\n\n現行の記述を変更する（対比ブロックなし・プローズのみ）。\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_multiple_target_files_are_each_checked(self, tmp_path: pathlib.Path) -> None:
        """複数ファイルを対象とする計画で、各ファイルが独立に検査される。"""
        _write(tmp_path / "foo.md", "old foo\nsecond\nthird\n")
        _write(tmp_path / "bar.md", "unrelated content\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行）\n- [ ] `bar.md`（現行3行）\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold foo\n```\n\n```text\n[置換後]\nnew foo\n```\n\n"
            "### `bar.md`\n\n```text\n[現行]\nold bar\n```\n\n```text\n[置換後]\nnew bar\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "foo.md" not in result.stderr  # foo.mdは一致のため違反なし
        assert "bar.md" in result.stderr
        assert "0回検出（転記誤りの可能性）" in result.stderr

    def test_multiple_blocks_for_same_file_applied_sequentially(self, tmp_path: pathlib.Path) -> None:
        """同一ファイルに複数の対比ブロックがある場合は出現順に逐次適用される。"""
        _write(tmp_path / "foo.md", "alpha\nbeta\ngamma\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行3行）\n\n"
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
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行3行）\n\n"
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
                checkbox_line="- [ ] `missing.md`（現行3行）",
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
                checkbox_line="- [ ] `foo.md`(現行3行)",
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
            "- [ ] `foo.md`(現行3行)\n\n"
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
            "- [ ] `foo.md`(現行3行)\n\n"
            "### `foo.md`\n\n```text\n[現行]\nalpha\n```\n\n"
            "```text\n[現行]\nbeta\n```\n\n```text\n[置換後]\nbeta2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "[現行]ブロックに対応する[置換後]ブロックが存在しない" in result.stderr

    def test_extensionless_filename_in_known_paths_is_targeted(self, tmp_path: pathlib.Path) -> None:
        """拡張子・区切りを持たないファイル名（Makefile等）でも既知パス集合に含まれれば対象となる。"""
        _write(tmp_path / "Makefile", "unrelated content\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `Makefile`(現行2行)",
                h3_heading="### `Makefile`",
                current_text="old target:",
                replacement_text="new target:",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "Makefile" in result.stderr
        assert "0回検出（転記誤りの可能性）" in result.stderr

    def test_bracket_labels_are_supported(self, tmp_path: pathlib.Path) -> None:
        """`[現行]`・`[置換後]`角括弧付きラベル（fence内側形式）の対比ブロックも機械適用対象として認識される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行）\n\n"
            "### `foo.md`\n\n```text\n[現行]\nold line\n```\n\n"
            "```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_new_file_checkbox_pattern_is_supported(self, tmp_path: pathlib.Path) -> None:
        """`（新設）`形式の新設ファイルもチェックボックス走査対象として認識される。"""
        _write(tmp_path / "foo.md", "old line\nsecond\nthird\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（新設）\n\n"
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
            "- [ ] `foo.md`（現行3行）\n\n"
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
                checkbox_line="- [ ] `foo.md`（現行3行）",
                h3_heading="### `foo.md`",
                current_text="old line",
                replacement_text="new line",
            ),
        )
        ng_plan = _write(
            tmp_path / "ng.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行3行）",
                h3_heading="### `foo.md`",
                current_text="stale text not present",
                replacement_text="new line",
            ),
        )
        result = _run(ok_plan, ng_plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "0回検出（転記誤りの可能性）" in result.stderr

    def test_reduction_block_detected_by_h4_heading(self) -> None:
        """`#### 縮減対象`のH4見出し直後のブロックを縮減対象として検出する（`reduction`フィールドへ加算）。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "#### 縮減対象（foo.md）\n\n```text\nold1\nold2\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["reduction"] == 2

    def test_annotation_only_first_line_excluded_from_count(self) -> None:
        """フェンス内1行目が「（挿入先注記）」のみの場合、実挿入内容ではないため集計から除外する。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "追記文言案は次のとおり。\n\n"
            "```text\n（挿入先の説明）\nline1\nline2\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 2

    def test_addition_blocks_detected_across_multiple_consecutive_blocks(self) -> None:
        """「追記文言案は次のとおり」直後からH3節境界までの連続textブロックを全て追記として検出する。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "追記文言案は次のとおり。\n\n"
            "```text\nline1\nline2\n```\n\n"
            "```text\nline3\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 3

    def test_addition_trigger_stops_at_h3_boundary(self) -> None:
        """追記トリガー継続中フラグは次のH3見出しで解除され、後続H3のブロックは追記扱いされない。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n- [ ] `bar.md`（現行5行）\n\n"
            "### `foo.md`\n\n"
            "追記文言案は次のとおり。\n\n"
            "```text\nline1\n```\n\n"
            "### `bar.md`\n\n"
            "既存の記述を変更する（対比ブロックなし・プローズのみ）。\n\n"
            "```text\n参考コード1\n参考コード2\n参考コード3\n参考コード4\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 1
        assert "bar.md" not in result

    def test_reduction_not_detected_by_non_h4_heading(self) -> None:
        """`### 縮減対象`等H4以外の見出しでは縮減対象として検出されず、追記のみ集計される。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "追記:\n\n```text\nline1\n```\n\n"
            "### 縮減対象\n\n```text\nold1\nold2\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 1
        assert result["foo.md"]["reduction"] == 0

    def test_fence_containing_h2_like_line_does_not_truncate_section(self, tmp_path: pathlib.Path) -> None:
        """`## 変更内容`節内のフェンスに他H2見出し様の行が含まれても、節本文が誤って途中終端しない

        （フェンス内行を境界判定から除外しない実装では後続ブロックが節外扱いされ検査対象から漏れる）。
        """
        _write(tmp_path / "foo.md", "existing text that never matches\n")
        plan = _write(
            tmp_path / "plan.md",
            "# テスト計画\n\n"
            "## 変更内容\n\n"
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行）\n\n"
            "### `foo.md`\n\n"
            "出力例:\n\n```text\n## 出力例の見出し\n```\n\n"
            "```text\n[現行]\nold line\n```\n\n"
            "```text\n[置換後]\nnew line\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "0回検出（転記誤りの可能性）" in result.stderr

    def test_deletion_pattern_current_block_counted_as_reduction(self) -> None:
        """[削除根拠]付き[現行]ブロックの[現行]行数が縮減対象集計へ加算される。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\nold2\n```\n\n"
            "```text\n[削除根拠]\n冗長なため削除する\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["reduction"] == 2
        assert result["foo.md"]["replacement_pair_count"] == 1

    def test_deletion_pattern_excludes_leading_current_label_from_reduction(self) -> None:
        """[削除根拠]付き[現行]ブロックの先頭ラベル行`[現行]`は縮減量から除外される。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行12行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\na1\na2\n```\n\n"
            "```text\n[削除根拠]\n冗長\n```\n\n"
            "```text\n[現行]\nb1\nb2\n```\n\n"
            "```text\n[削除根拠]\n冗長\n```\n\n"
            "```text\n[現行]\nc1\nc2\n```\n\n"
            "```text\n[削除根拠]\n冗長\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        # ラベル込みなら3件×3行=9になるはずだが、除外後は3件×2行=6となる。
        assert result["foo.md"]["reduction"] == 6
        assert result["foo.md"]["replacement_pair_count"] == 3

    def test_addition_label_block_counted_without_trigger(self) -> None:
        """隣接文言に「追記」「追加」の語が無い`[追記]`ブロックはラベル付きのためラベルなし集計に載らない。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "特に前置き無し。\n\n"
            "```text\n[追記]\n追加行A\n追加行B\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 0

    def test_addition_label_line_and_annotation_both_excluded(self) -> None:
        """`[追記]`ラベル行と位置注記行の両方が集計から除外される（ラベル付きのため`addition_labelless`は0）。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記]\n（挿入先: 節末尾）\n追加行A\n追加行B\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 0

    def test_reduction_block_warning_skipped_when_replacement_diff_present(self, tmp_path: pathlib.Path) -> None:
        """220行超過ファイルで`[現行]`/`[置換後]`ペアがあればH4欠落警告は発生しない。"""
        _write(tmp_path / "foo.md", "line0\n" + "\n".join(f"line{i}" for i in range(1, 230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nline0\n```\n\n"
            "```text\n[置換後]\nline0\nadded1\nadded2\nadded3\nadded4\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "H4見出しが不在" not in result.stderr

    def test_reduction_block_warning_skipped_when_zero_diff_replacement_pair_present(self, tmp_path: pathlib.Path) -> None:
        """同行数の`[現行]`/`[置換後]`ペアでもH4欠落警告は発生しない。"""
        _write(tmp_path / "foo.md", "old1\nold2\n" + "\n".join(f"line{i}" for i in range(2, 230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\nold2\n```\n\n"
            "```text\n[置換後]\nnew1\nnew2\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "H4見出しが不在" not in result.stderr

    def test_reduction_block_warning_still_fires_when_no_diff_mechanism(self, tmp_path: pathlib.Path) -> None:
        """差分機構が無い220行超過ファイルでは従来どおりH4欠落警告が発生する。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行230行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "H4見出しが不在" in result.stderr

    def test_mixed_replacement_and_deletion_pairs_computed_together(self) -> None:
        """同一ファイルへ`[現行]`/`[置換後]`と`[現行]`/`[削除根拠]`ペアが混在しても、両ペアが合算される。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行4行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\n```\n\n"
            "```text\n[置換後]\nnew1\n```\n\n"
            "```text\n[現行]\nold2\n```\n\n"
            "```text\n[削除根拠]\n冗長のため削除\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["replacement_pair_count"] == 2
        assert result["foo.md"]["reduction"] == 1

    def test_absolute_path_without_allowed_root_warns(self, tmp_path: pathlib.Path) -> None:
        """許容ルート未宣言の絶対パスが対象ファイル一覧に含まれる場合、警告が出力される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `/home/aki/other-repo/foo.md`（現行10行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "許容ルート未宣言の絶対パスを検出" in result.stderr

    def test_absolute_path_with_allowed_root_no_warning(self, tmp_path: pathlib.Path) -> None:
        """`<!-- allowed-repo-root: /abs/path -->`宣言済みルート配下の絶対パスは警告対象から除外される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n<!-- allowed-repo-root: /home/aki/other-repo -->\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `/home/aki/other-repo/foo.md`（現行10行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "許容ルート未宣言の絶対パスを検出" not in result.stderr


class TestCheckboxCurrentLineFormat:
    """新書式チェックボックス`（現行N行）`単独値のパースを検証する。"""

    # pylint: disable=protected-access

    def test_current_line_count_is_parsed(self) -> None:
        """`（現行N行）`形式から現行行数が抽出される。"""
        section = "- [ ] `foo.md`（現行42行）\n"
        result = _MOD._collect_current_line_counts(section)
        assert result["foo.md"] == 42

    def test_new_file_is_parsed_as_zero(self) -> None:
        """`（新設）`形式は現行行数0として抽出される。"""
        section = "- [ ] `foo.md`（新設）\n"
        result = _MOD._collect_current_line_counts(section)
        assert result["foo.md"] == 0

    def test_ascii_parentheses_variant_is_accepted(self) -> None:
        """全角丸括弧・半角丸括弧いずれの表記も受理される。"""
        section = "- [ ] `foo.md`(現行3行)\n"
        result = _MOD._collect_current_line_counts(section)
        assert result["foo.md"] == 3

    def test_old_projected_format_is_no_longer_matched_as_current(self) -> None:
        """旧書式`（現行N行, 見込みM行）`は新regexの単独値パターンと一致せず現行行数を抽出しない。"""
        section = "- [ ] `foo.md`（現行3行, 見込み5行）\n"
        result = _MOD._collect_current_line_counts(section)
        assert "foo.md" not in result


class TestExtractAdditionReductionBlocksMultiplierLabel:
    """`[追記×N]`書式の受理範囲（ラベル判定・不正値の扱い）を検証する。

    `addition`・`addition_labelled`フィールドは廃止済みのため、ラベルとして正しく認識された
    ブロックは`addition_labelless`へ計上されない（ラベル付き追記は`_check_labelless_addition_for_over_threshold_files`の
    警告対象外となる）ことを基準に検証する。
    """

    _KNOWN_PATH_HEADER = "### 対象ファイル一覧\n\n- [ ] `agent-toolkit/skills/x.md`\n\n### `agent-toolkit/skills/x.md`\n\n"

    def test_multiplier_label_two_is_recognized_as_labelled(self) -> None:
        """`[追記×2]`はラベル行として認識され、`addition_labelless`へ計上されない。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×2]\nline1\nline2\nline3\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition_labelless"] == 0

    def test_bare_addition_label_is_recognized_as_labelled(self) -> None:
        """倍率修飾子無しの`[追記]`もラベル行として認識される。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記]\nline1\nline2\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition_labelless"] == 0

    def test_frontmatter_variant_is_recognized_as_labelled(self) -> None:
        """`[追記（frontmatter）]`サブラベルもラベル行として認識される。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記（frontmatter）]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition_labelless"] == 0

    def test_rejects_multiplier_zero(self) -> None:
        """`[追記×0]`は正規表現で不受理となり、ラベル行自体が本文行として計上される。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×0]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition_labelless"] == 2

    def test_rejects_frontmatter_multiplier_mix(self) -> None:
        """`[追記×2（frontmatter）]`は`×N`とサブラベルの併用形式のため不受理となる。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×2（frontmatter）]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition_labelless"] == 2

    def test_rejects_unicode_digit_multiplier(self) -> None:
        """全角数字`２`はASCII整数限定の正規表現で不受理となる。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×２]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition_labelless"] == 2
