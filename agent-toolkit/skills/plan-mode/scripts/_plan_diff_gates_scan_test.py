"""差分ブロック走査系モジュール`_plan_diff_gates_scan`のテスト。

`check_plan_diff_gates_test.py`から走査系テストクラスを分離したもの。
"""

# 対象モジュールは内部モジュールであり公開APIを持たないが、
# 個別関数の抽出仕様・副作用・境界を単体レベルで検証するためprotected-accessを許容する。
# pylint: disable=protected-access,unused-argument

from __future__ import annotations

import pathlib
import subprocess

import pytest
from _plan_diff_gates_test_helpers import _completed, _load_module, _stub_subprocess

_SCRIPT = pathlib.Path(__file__).resolve().parent / "_plan_diff_gates_scan.py"
_MOD = _load_module(_SCRIPT)


class TestExtractDiffBlocks:
    """`_iter_diff_blocks`の抽出仕様を網羅する。"""

    def test_new_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nnew content line1\nnew content line2\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        label, _line, body, _ext = blocks[0]
        assert label == "`foo.md`"
        # ラベル行はfence内側1行目に配置され、本文集計・textlint検査から除外される。
        assert body == "new content line1\nnew content line2"

    def test_replacement_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[置換後]\nreplaced body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "replaced body"

    def test_replacement_full_label_block_is_extracted(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[置換後（全文）]\nwhole file content\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "whole file content"

    def test_current_label_block_is_excluded(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[現行]\nold body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_deletion_rationale_label_block_is_excluded(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[削除根拠]\n削除の理由\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_variable_length_fence_four_backticks_is_extracted(self) -> None:
        """外側4バッククォートのfenceも検査対象として認識される。"""
        text = "## 変更内容\n\n### `foo.md`\n\n````text\n[置換後]\ninner ``` allowed\n````\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "inner ``` allowed"

    def test_new_h3_marker_extracts_body(self) -> None:
        text = "## 変更内容\n\n### `new_file.py`（新設）\n\n新設スクリプト。\n\n```text\nsome body under new h3\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][0].startswith("`new_file.py`")

    def test_trigger_line_extracts_following_block(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n追記内容:\n\n```text\nadded line\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "added line"

    def test_reduction_heading_extracts_body(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n#### 縮減対象\n\n```text\nold verbose text\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1

    def test_non_text_fence_is_ignored(self) -> None:
        text = "## 変更内容\n\n### `foo.py`\n\n```python\ndef f(): pass\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_no_change_section_returns_empty(self) -> None:
        text = "## 背景\n\n本文のみ。\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert not blocks

    def test_block_start_line_is_computed(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[新設]\nline\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert blocks[0][1] > 0

    def test_addition_trigger_tokens_include_compression_after(self) -> None:
        assert "圧縮後:" in _MOD._ADDITION_TRIGGER_TOKENS

    def test_extract_diff_blocks_compression_after_trigger(self) -> None:
        text = "## 変更内容\n\n### `foo.md`\n\n圧縮後:\n\n```text\ncompressed body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "compressed body"

    def test_addition_label_block_is_extracted(self) -> None:
        """`[追記]`ラベル単独ブロック（隣接文言に「追記」「追加」の語なし）も検査対象へ入る。"""
        text = "## 変更内容\n\n### `foo.md`\n\n特に前置きなし。\n\n```text\n[追記]\naddition body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        # ラベル行はfence内側1行目に配置され本文抽出時に除外される。
        assert blocks[0][2] == "addition body"

    def test_classify_block_returns_addition_for_addition_label(self) -> None:
        """`_classify_block`は`[追記]`ラベル単独行を`"addition"`種別として返す。"""
        assert _MOD._classify_block(["[追記]", "body"], False, False, False) == "addition"

    def test_classify_block_returns_addition_for_multiplier_label(self) -> None:
        """`_classify_block`は`[追記×N]`ラベル単独行も`"addition"`種別として返す。"""
        assert _MOD._classify_block(["[追記×2]", "body"], False, False, False) == "addition"

    def test_multiplier_addition_label_block_is_extracted(self) -> None:
        """`[追記×N]`ラベルの単独ブロックも検査対象へ入り、ラベル行は本文抽出時に除外される。"""
        text = "## 変更内容\n\n### `foo.md`\n\n特に前置きなし。\n\n```text\n[追記×2]\naddition body\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][2] == "addition body"


class TestIsAdditionLabelLine:
    """`_is_addition_label_line`の完全一致判定を検証する。"""

    @pytest.mark.parametrize(
        "stripped",
        ["[追記]", "[追記×1]", "[追記×2]", "[追記×10]", "[追記（frontmatter）]"],
    )
    def test_accepts_valid_forms(self, stripped: str) -> None:
        assert _MOD._is_addition_label_line(stripped) is True

    @pytest.mark.parametrize(
        "stripped",
        [
            "[追記×0]",
            "[追記×２]",
            "[追記×2（frontmatter）]",
            "[追記（frontmatter）×2]",
            "以下は[追記]内容",
            "[追記",
        ],
    )
    def test_rejects_invalid_forms(self, stripped: str) -> None:
        assert _MOD._is_addition_label_line(stripped) is False

    @pytest.mark.parametrize("label", ["[追記（frontmatter）]", "[置換後（frontmatter）]"])
    def test_frontmatter_label_block_yields_empty_ext_for_md_host(self, label: str) -> None:
        """frontmatterサブラベル配下の本文は、ホストファイルが`.md`でも拡張子を空文字列で返す。

        本文は`#`始まりのYAML/Pythonコメント文言のため、独立抽出時にtextlintがATX見出しと
        誤認する（`jtf-style/1.1.2.見出し`偽陽性）。ホストファイルの実拡張子に関わらずtextlint対象外とする。
        """
        text = f"## 変更内容\n\n### `foo.md`\n\n```text\n{label}\n# コメント文言。\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][3] == ""

    def test_non_frontmatter_label_block_keeps_host_ext_for_md(self) -> None:
        """通常ラベル配下は従来どおりホストファイルの拡張子をそのまま返す。"""
        text = "## 変更内容\n\n### `foo.md`\n\n```text\n[追記]\n通常の追記文言\n```\n"
        blocks = list(_MOD._iter_diff_blocks(text))
        assert len(blocks) == 1
        assert blocks[0][3] == ".md"

    @pytest.mark.parametrize(
        ("rest", "expected"),
        [
            ("agent-toolkit/scripts/pretooluse.py", ".py"),
            ("agent-toolkit/skills/foo/SKILL.md", ".md"),
            ("templates/example.md.tmpl", ".md.tmpl"),
            ("`foo.py`", ".py"),
            ("agent-toolkit/scripts/foo.py（新設）", ".py"),
            ("`foo.py`（新設）", ".py"),
            ("README", ""),
        ],
    )
    def test_extract_h3_ext(self, rest: str, expected: str) -> None:
        """H3見出し本文の各表記から対象ファイル拡張子を抽出する。"""
        assert _MOD._extract_h3_ext(rest) == expected


class TestRunScopeEscalation:
    """`_run_scope_escalation`のsubprocessモック検証。"""

    def test_returns_category_when_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=2, scope_stdout="pattern-conformance\n")
        assert _MOD._run_scope_escalation("some body") == "pattern-conformance"

    def test_returns_none_when_not_matched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, scope_returncode=0)
        assert _MOD._run_scope_escalation("clean body") is None

    def test_returns_none_for_empty_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _stub_subprocess(monkeypatch)
        assert _MOD._run_scope_escalation("") is None
        assert not calls


class TestRunTextlint:
    """`_run_textlint`のsubprocessモック検証と一時ファイル拡張子検証。"""

    def test_returns_none_when_no_violation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, textlint_returncode=0)
        assert _MOD._run_textlint("body") is None

    def test_returns_stderr_when_violation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_subprocess(monkeypatch, textlint_returncode=1, textlint_stdout="violation!")
        result = _MOD._run_textlint("body")
        assert result is not None
        assert "violation" in result

    def test_tmpfile_extension_is_md(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return _completed(0)

        monkeypatch.setattr("subprocess.run", fake_run)
        _MOD._run_textlint("hello")
        assert calls
        # 最後の引数（一時ファイルパス）が`.md`で終わる。
        assert calls[0][-1].endswith(".md")

    def test_subprocess_args_include_colloquial_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_run_textlint`はcolloquial-check併走のため`--commands`と`--enable`引数を含める。"""
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return _completed(0)

        monkeypatch.setattr("subprocess.run", fake_run)
        _MOD._run_textlint("hello")
        assert calls
        assert "--commands=textlint,colloquial-check" in calls[0]
        assert "--enable=colloquial-check" in calls[0]

    def test_batch_subprocess_args_include_colloquial_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """`_run_textlint_batch`もcolloquial-check併走引数を含める。"""
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return _completed(0)

        monkeypatch.setattr("subprocess.run", fake_run)
        _MOD._run_textlint_batch([tmp_path / "a.md"])
        assert calls
        assert "--commands=textlint,colloquial-check" in calls[0]
        assert "--enable=colloquial-check" in calls[0]


class TestRewriteLocations:
    """`_rewrite_locations`の置換ロジックを検証する。"""

    def test_replaces_all_registered_paths(self) -> None:
        """位置マップに登録された全パスがマーカーへ置換される。"""
        output = "/tmp/a.md:3: A\n/tmp/b.md:5: B\n"
        location_map = {"/tmp/a.md": "plan.md: H3=`x` L1", "/tmp/b.md": "plan.md: H3=`y` L2"}
        result = _MOD._rewrite_locations(output, location_map)
        assert "plan.md: H3=`x` L1:3: A" in result
        assert "plan.md: H3=`y` L2:5: B" in result

    def test_leaves_unmatched_content_intact(self) -> None:
        """位置マップに存在しないパスはそのまま残る。"""
        output = "/tmp/other.md:1: something"
        assert _MOD._rewrite_locations(output, {"/tmp/known.md": "marker"}) == output
