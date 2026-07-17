"""agent-toolkit/skills/plan-mode/scripts/check_wc_projection.py のテスト。

計画ファイル内の[現行]/[置換後]対比ブロックを機械適用し、wc -l実測値と
見込み行数の乖離を検出する検算スクリプトをfork-server経由（フォールバック時はsubprocess）で起動して検証する。
正常系・乖離検出（純追記パターンの二重適用防止・通常パターンでの[現行]優先確認を含む）・
[現行]文面不一致・対象ファイル不在・見込み行数記載欠落・H3見出し無し・複数ファイル対象・
対比ブロック無し・削除ペア先頭ラベル行の縮減量除外・`[追記]`ラベル直接検出・
ラベル付き/なし追記の分離集計・`[現行]`/`[置換後]`ペア差分集計の各シナリオを網羅する。
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

    def test_pure_addition_diff_block_already_applied_is_not_double_counted(self, tmp_path: pathlib.Path) -> None:
        """純追記パターン（過去に発生した回帰）は実装完了後の再実行で二重適用されない

        （追記行数3行で乖離が`_ALLOWED_DRIFT`を超える構成にし、判定順序を戻すと失敗することを保証する）。
        """
        current_text = "old line"
        replacement_text = "old line\nnew line 1\nnew line 2\nnew line 3"
        _write(tmp_path / "foo.md", f"{replacement_text}\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行1行, 見込み4行）",
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
                checkbox_line="- [ ] `foo.md`（現行5行, 見込み2行）",
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

    def test_ledger_consistent_diff_zero(self, tmp_path: pathlib.Path) -> None:
        """「差引0行」宣言と`[現行]`/`[置換後]`差分0行が一致する場合は違反を報告しない。"""
        _write(tmp_path / "foo.md", "old1\nold2\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行2行, 見込み2行）",
                h3_heading="### `foo.md`\n\n差引0行。",
                current_text="old1\nold2",
                replacement_text="new1\nnew2",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_ledger_mismatch_reports_violation(self, tmp_path: pathlib.Path) -> None:
        """「差引0行」宣言に対して実測差が2行の場合は違反を報告する。"""
        _write(tmp_path / "foo.md", "old1\nold2\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行2行, 見込み4行）",
                h3_heading="### `foo.md`\n\n差引0行。",
                current_text="old1\nold2",
                replacement_text="new1\nnew2\nnew3\nnew4",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "行数収支主張`差引0行`" in result.stderr
        assert "実測差+2行" in result.stderr

    def test_multiple_expressions_captured(self, tmp_path: pathlib.Path) -> None:
        """純増・相殺・差引の表記変種がそれぞれ収支主張として抽出される。"""
        _write(tmp_path / "foo.md", "old\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line="- [ ] `foo.md`（現行1行, 見込み5行）",
                h3_heading=(
                    "### `foo.md`\n\n純増1行。\n\n追記2行、圧縮1行。\n\n"
                    "現行1行, 実測5行（追記＋2行, 圧縮－2行で相殺）。\n\n差引+1行。"
                ),
                current_text="old",
                replacement_text="new1\nnew2\nnew3\nnew4\nnew5",
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "行数収支主張`純増1行`" in result.stderr
        assert "行数収支主張`追記2行、圧縮1行`" in result.stderr
        assert "行数収支主張`＋2行、－2行で相殺`" in result.stderr
        assert "行数収支主張`差引+1行`" in result.stderr

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
        """`### 縮減対象`等H4以外の見出しでは縮減対象として検出されず、追記のみ集計され乖離扱いとなる。"""
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
        """`## 変更内容`節内のフェンスに他H2見出し様の行が含まれても、節本文が誤って途中終端しない

        （フェンス内行を境界判定から除外しない実装では後続ブロックが節外扱いされ検査対象から漏れる）。
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

    @pytest.mark.parametrize(
        ("current_text", "replacement_text", "current_decl", "projected_decl"),
        [
            pytest.param(
                "old1\nold2\nold3\nold4\nold5",
                "new1\nnew2\nnew3\nnew4\nnew5\nnew6\nnew7\nnew8\nnew9",
                5,
                9,
                id="positive-diff-adds-to-addition",
            ),
            pytest.param(
                "old1\nold2\nold3\nold4\nold5\nold6\nold7\nold8\nold9",
                "new1\nnew2\nnew3\nnew4\nnew5",
                9,
                5,
                id="negative-diff-adds-to-reduction",
            ),
            pytest.param("old1\nold2\nold3", "new1\nnew2\nnew3", 3, 3, id="zero-diff-no-change"),
        ],
    )
    def test_projection_includes_current_to_replacement_diff(
        self,
        tmp_path: pathlib.Path,
        current_text: str,
        replacement_text: str,
        current_decl: int,
        projected_decl: int,
    ) -> None:
        """`[現行]`/`[置換後]`ペアの行数差が追記量・縮減量へ自動算入され乖離が検出されない。

        旧経路（差分算入なし）では`現行N行 + 追記0行 - 縮減0行`が宣言済み見込み行数と食い違い、
        乖離超過として`returncode == 1`になるはずの入力を用いて新経路の動作を検証する。
        """
        _write(tmp_path / "foo.md", current_text + "\n")
        plan = _write(
            tmp_path / "plan.md",
            _plan_with_single_block(
                checkbox_line=f"- [ ] `foo.md`（現行{current_decl}行, 見込み{projected_decl}行）",
                h3_heading="### `foo.md`",
                current_text=current_text,
                replacement_text=replacement_text,
            ),
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_projection_current_replacement_diff_not_counted_as_labelless(self, tmp_path: pathlib.Path) -> None:
        """`[現行]`/`[置換後]`差分はラベルなし追記警告の対象にならない。"""
        _write(tmp_path / "foo.md", "line0\n" + "\n".join(f"line{i}" for i in range(1, 230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行, 見込み234行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nline0\n```\n\n"
            "```text\n[置換後]\nline0\nadded1\nadded2\nadded3\nadded4\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "差分ラベル付与を検討" not in result.stderr

    def test_projection_current_deletion_pair_unaffected(self, tmp_path: pathlib.Path) -> None:
        """`[削除根拠]`ペアの縮減量集計は`[置換後]`差分経路の追加後も維持される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行, 見込み8行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\nold2\n```\n\n"
            "```text\n[削除根拠]\n冗長なため削除する\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_projection_current_replacement_full_diff_applied(self, tmp_path: pathlib.Path) -> None:
        """`[置換後（全文）]`ラベルでも`[現行]`との差分が追記量へ加算され乖離が検出されない。

        `[置換後（全文）]`は文字列置換適用（`_check_one_file`）の対象外のため対象ファイルの実在は不要。
        旧経路（差分算入なし）では宣言済み見込み4行と`現行2行+追記0行`が食い違い違反になるはずの入力を用いる。
        """
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行2行, 見込み4行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\nold2\n```\n\n"
            "```text\n[置換後（全文）]\nnew1\nnew2\nnew3\nnew4\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_reduction_block_warning_skipped_when_replacement_diff_present(self, tmp_path: pathlib.Path) -> None:
        """220行超過ファイルで`[現行]`/`[置換後]`ペアがあればH4欠落警告は発生しない。"""
        _write(tmp_path / "foo.md", "line0\n" + "\n".join(f"line{i}" for i in range(1, 230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行, 見込み234行）\n\n"
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
            "- [ ] `foo.md`（現行230行, 見込み230行）\n\n"
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
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行230行, 見込み230行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "H4見出しが不在" in result.stderr

    def test_mixed_replacement_and_deletion_pairs_computed_together(self, tmp_path: pathlib.Path) -> None:
        """同一ファイルへ`[現行]`/`[置換後]`と`[現行]`/`[削除根拠]`ペアが混在しても、
        両ペアが同型の集計経路で合算され乖離0なら警告なく通過する。"""
        _write(tmp_path / "foo.md", "old1\nold2\nold3\nold4\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行4行, 見込み3行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\n```\n\n"
            "```text\n[置換後]\nnew1\n```\n\n"
            "```text\n[現行]\nold2\n```\n\n"
            "```text\n[削除根拠]\n冗長のため削除\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "見込み行数検算経路をどちらかへ統一" not in result.stderr

    def test_absolute_path_without_allowed_root_warns(self, tmp_path: pathlib.Path) -> None:
        """許容ルート未宣言の絶対パスが対象ファイル一覧に含まれる場合、警告が出力される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `/home/aki/other-repo/foo.md`（現行10行, 見込み10行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "許容ルート未宣言の絶対パスを検出" in result.stderr

    def test_absolute_path_with_allowed_root_no_warning(self, tmp_path: pathlib.Path) -> None:
        """`<!-- allowed-repo-root: /abs/path -->`宣言済みルート配下の絶対パスは警告対象から除外される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n<!-- allowed-repo-root: /home/aki/other-repo -->\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `/home/aki/other-repo/foo.md`（現行10行, 見込み10行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "許容ルート未宣言の絶対パスを検出" not in result.stderr

    def test_projection_drift_with_replacement_diff_detection(self, tmp_path: pathlib.Path) -> None:
        """実ファイル適用経路と差分集計経路の乖離判定が同一ペアで同じ見込み値を報告する。"""
        _write(tmp_path / "foo.md", "line0\n" + "\n".join(f"line{i}" for i in range(1, 230)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行, 見込み231行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nline0\n```\n\n"
            "```text\n[置換後]\nline0\nadded1\nadded2\nadded3\nadded4\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 1
        assert "見込み231行, 実測234行" in result.stderr
        assert "追記/縮減対象集計からの見込み234行" in result.stderr


class TestExtractAdditionReductionBlocksMultiplierLabel:
    """`[追記×N]`書式の集計仕様（倍率抽出・既存書式互換・無効値拒否）を検証する。"""

    _KNOWN_PATH_HEADER = "### 対象ファイル一覧\n\n- [ ] `agent-toolkit/skills/x.md`\n\n### `agent-toolkit/skills/x.md`\n\n"

    def test_multiplier_label_two(self) -> None:
        """`[追記×2]`は同一文面3行を2倍した6行を集計する。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×2]\nline1\nline2\nline3\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition"] == 6
        assert result["agent-toolkit/skills/x.md"]["addition_labelled"] == 6

    def test_bare_addition_label_defaults_to_one(self) -> None:
        """倍率修飾子無しの`[追記]`は従来どおりN=1として集計する。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記]\nline1\nline2\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition"] == 2

    def test_multiplier_one_explicit(self) -> None:
        """`[追記×1]`は`[追記]`と同等の集計結果になる。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×1]\nline1\nline2\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition"] == 2

    def test_frontmatter_variant_still_supported(self) -> None:
        """`[追記（frontmatter）]`サブラベルは`×N`修飾子追加後も従来どおり動作する。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記（frontmatter）]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition"] == 1

    def test_rejects_multiplier_zero(self) -> None:
        """`[追記×0]`は正規表現で不受理となり、ラベル行自体が本文行として計上される。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×0]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition"] == 2

    def test_rejects_frontmatter_multiplier_mix(self) -> None:
        """`[追記×2（frontmatter）]`は`×N`とサブラベルの併用形式のため不受理となる。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×2（frontmatter）]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition"] == 2

    def test_rejects_unicode_digit_multiplier(self) -> None:
        """全角数字`２`はASCII整数限定の正規表現で不受理となる。"""
        section = self._KNOWN_PATH_HEADER + "```text\n[追記×２]\nline1\n```\n"
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["agent-toolkit/skills/x.md"]["addition"] == 2
