"""agent-toolkit/skills/plan-mode/scripts/check_wc_projection.py のテスト（追加シナリオ）。

`check_wc_projection_test.py`からの責務分割先とし、220行超過縮減対象H4検査・
220行到達済みラベルなし追記検査・fence内側形式ラベルの直接検証・frontmatterサブラベルの
存在有無集計・可変長フェンス対応の各シナリオを扱う。ヘルパー関数は`check_wc_projection_test`から再利用する。
"""

import pathlib

from check_wc_projection_test import _MOD, _run, _write


class TestOverThresholdReductionCheck:
    """`_check_reduction_block_for_over_threshold_files`の警告出力仕様を検証する。

    現行220行超のファイルを対象に、対応する`#### 縮減対象（<ファイル名>）`
    H4見出しの存在を検査する。警告は情報提供扱いで違反件数には計上しない（returncode 0）。
    """

    def test_over_threshold_file_without_reduction_heading_warns(self, tmp_path: pathlib.Path) -> None:
        """220行超過ファイル対象・縮減対象H4不在時に警告が出力される。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行230行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "[warn]" in result.stderr
        assert "220行超過ファイル" in result.stderr
        assert "`#### 縮減対象（foo.md）`H4見出しが不在" in result.stderr

    def test_over_threshold_file_with_reduction_heading_passes(self, tmp_path: pathlib.Path) -> None:
        """220行超過ファイル対象・縮減対象H4完備時は警告が出ない。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行230行）\n\n"
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
            "- [ ] `foo.md`（現行230行）\n"
            "- [ ] `bar.md`（現行235行）\n\n"
            "### `foo.md`\n\n"
            "#### 縮減対象（foo.md）\n\n```text\n[削除根拠]\nold\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "bar.md" in result.stderr
        assert "220行超過ファイルfoo.md" not in result.stderr

    def test_at_threshold_file_skips_check(self, tmp_path: pathlib.Path) -> None:
        """現行220行ちょうど・以下のファイルは検査対象外となる（220行以下収束の完了条件）。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行220行）\n",
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
            "- [ ] `agent-toolkit/skills/agent-standards/SKILL.md`（現行230行）\n\n"
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
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.md`（現行300行）\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0
        assert "220行超過ファイル" in result.stderr
        assert "`#### 縮減対象（foo.md）`H4見出しが不在" in result.stderr

    def test_py_extension_over_threshold_does_not_warn(self, tmp_path: pathlib.Path) -> None:
        """`.py`ファイルが220行超過でもH4見出し警告は発生しない（拡張子フィルタ）。"""
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n- [ ] `foo.py`（現行230行）\n",
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
            "- [ ] `foo.md`（現行230行）\n\n"
            "### `foo.md`\n\n追記文言案:\n\n"
            "```text\n追加行A\n追加行B\n追加行C\n追加行D\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "[warn]" in result.stderr
        assert "220行到達済みファイルfoo.md" in result.stderr
        assert "差分ラベル付与を検討" in result.stderr

    def test_labeled_addition_over_threshold_no_warning(self, tmp_path: pathlib.Path) -> None:
        """現行220行超で`[現行]`/`[置換後]`ペア記述時は警告が出ない。"""
        _write(tmp_path / "foo.md", "line0\n" + "\n".join(f"line{i}" for i in range(1, 231)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行231行）\n\n"
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
            "- [ ] `foo.md`（現行230行）\n\n"
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
            "- [ ] `foo.md`（現行230行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記]\nラベル付き行A\nラベル付き行B\n```\n\n"
            "追記文言案:\n\n"
            "```text\nラベルなし行A\nラベルなし行B\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "[warn]" in result.stderr
        assert "220行到達済みファイルfoo.md" in result.stderr
        assert "差分ラベル付与を検討" in result.stderr

    def test_under_threshold_labelless_addition_no_warning(self, tmp_path: pathlib.Path) -> None:
        """現行220行以下のファイルはラベルなし追記でも警告が出ない。"""
        _write(tmp_path / "foo.md", "\n".join(f"line{i}" for i in range(100)) + "\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n"
            "## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行100行）\n\n"
            "### `foo.md`\n\n追記文言案:\n\n"
            "```text\n追加行A\n追加行B\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert "差分ラベル付与を検討" not in result.stderr


class TestLeadingLabel:
    """`_leading_label`のfence内側形式ラベル検出のうち、公開インターフェース経由で代替できない
    `replacement-full`・`new`・ラベル無し境界値のみ直接検証する（他4種は既存結合テストで間接カバー済み）。
    """

    # pylint: disable=protected-access

    def test_replacement_full_takes_precedence_over_replacement(self) -> None:
        """「置換後（全文）」判定が「置換後」判定より先に評価される。"""
        assert _MOD._leading_label(["[置換後（全文）]", "whole file body"]) == "replacement-full"

    def test_new_label_inside_fence_is_detected(self) -> None:
        assert _MOD._leading_label(["[新設]", "new file body"]) == "new"

    def test_no_label_returns_none(self) -> None:
        assert _MOD._leading_label(["regular body"]) is None
        assert _MOD._leading_label([]) is None


class TestFrontmatterLabelExtraction:
    """frontmatterサブラベル（`[追記（frontmatter）]`等4種）の存在有無集計を検証する。"""

    def test_addition_frontmatter_sublabel_not_counted_as_labelless(self) -> None:
        """`[追記（frontmatter）]`ブロックはラベル付きのため`addition_labelless`へ計上されない。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行4行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記（frontmatter）]\nsummary: s\ntags: []\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 0

    def test_current_replacement_frontmatter_pair_applied(self, tmp_path: pathlib.Path) -> None:
        """`[現行（frontmatter）]`/`[置換後（frontmatter）]`対比ペアが実ファイルへ適用され一意一致検査を通過する。"""
        _write(tmp_path / "foo.md", "---\ntitle: old\n---\nbody\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行4行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行（frontmatter）]\ntitle: old\n```\n\n"
            "```text\n[置換後（frontmatter）]\ntitle: new\nsummary: s\n```\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr

    def test_deletion_frontmatter_sublabel_counted_as_reduction(self) -> None:
        """`[削除根拠（frontmatter）]`ブロックの直前`[現行（frontmatter）]`行数が縮減量として集計される。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行（frontmatter）]\nold-line1\nold-line2\n```\n\n"
            "```text\n[削除根拠（frontmatter）]\n陳腐化のため削除\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["reduction"] == 2

    def test_frontmatter_and_body_addition_both_labelled_not_counted(self) -> None:
        """frontmatter変更（`[追記（frontmatter）]`）と本体変更（`[追記]`）はいずれもラベル付きのため計上されない。"""
        section = (
            "### 対象ファイル一覧\n\n- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[追記（frontmatter）]\nfm-line1\n```\n\n"
            "```text\n[追記]\nbody-line1\nbody-line2\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["addition_labelless"] == 0


class TestDeletionPairUnifiedAccounting:
    """削除ペアが置換ペアと同型の`diff`計算経路へ統合されたことを検証する（FB1）。"""

    def test_deletion_pair_counted_into_replacement_pair_count(self) -> None:
        """`[削除根拠]`ペアも`replacement_pair_count`へ計上される。"""
        section = (
            "### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行10行）\n\n"
            "### `foo.md`\n\n"
            "```text\n[現行]\nold1\nold2\n```\n\n"
            "```text\n[削除根拠]\n冗長なため削除する\n```\n"
        )
        result = _MOD.extract_addition_reduction_blocks(section)
        assert result["foo.md"]["replacement_pair_count"] == 1
        assert result["foo.md"]["reduction"] == 2


class TestVariableLengthFence:
    """可変長フェンス（4バッククォート以上）のfence内側形式対応を検証する。"""

    def test_four_backtick_fence_diff_block_is_processed(self, tmp_path: pathlib.Path) -> None:
        """外側4バッククォートで囲んだfence内側形式ラベルの対比ブロックも機械適用対象。"""
        _write(tmp_path / "foo.md", "```\ninner code\n```\n")
        plan = _write(
            tmp_path / "plan.md",
            "# T\n\n## 変更内容\n\n### 対象ファイル一覧\n\n"
            "- [ ] `foo.md`（現行3行）\n\n"
            "### `foo.md`\n\n"
            "````text\n[現行]\n```\ninner code\n```\n````\n\n"
            "````text\n[置換後]\n```\nnew inner\n```\n````\n",
        )
        result = _run(plan, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
