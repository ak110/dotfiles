"""_plan_format モジュールの単体テスト。

H2見出し抽出・H2節順検査・SSOT整合性を検査する。
フェンス内H2除外は本モジュールの`extract_h2_sections`が担う。
"""

import pathlib
import re

import _plan_format

_PLAN_FILE_REF = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "references" / "plan-file-guidelines.md"

_VALID_CONTENT = (
    "# タイトル\n\n"
    "## 変更履歴\n\nx\n\n"
    "## 背景\n\nx\n\n"
    "## 対応方針\n\nx\n\n"
    "## 調査結果\n\nx\n\n"
    "## 変更内容\n\nx\n\n"
    "## 実行方法\n\nx\n\n"
    "## 進捗ログ\n\nx\n\n"
    "## 計画ファイル（本ファイル）のパス\n\nx\n"
)


class TestExtractH2Sections:
    """extract_h2_sections の基本動作を検査する。"""

    def test_returns_all_h2_titles(self):
        content = "# H1\n\n## AAA\n\n## BBB\n"
        assert _plan_format.extract_h2_sections(content) == ["AAA", "BBB"]

    def test_empty_content_returns_empty(self):
        assert not _plan_format.extract_h2_sections("")

    def test_no_h2_returns_empty(self):
        assert not _plan_format.extract_h2_sections("# タイトルのみ\n\nテキスト\n")

    def test_trailing_whitespace_stripped(self):
        content = "## 背景  \n\nテキスト\n"
        assert _plan_format.extract_h2_sections(content) == ["背景"]

    def test_h2_inside_backtick_fence_is_excluded(self):
        content = "```\n## フェンス内\n```\n## 実在\n"
        assert _plan_format.extract_h2_sections(content) == ["実在"]

    def test_h2_inside_tilde_fence_is_excluded(self):
        content = "~~~\n## フェンス内\n~~~\n## 実在\n"
        assert _plan_format.extract_h2_sections(content) == ["実在"]

    def test_h2_inside_fence_with_info_string_is_excluded(self):
        """info string付きフェンス（```python等）の中身もフェンス内として除外する。"""
        content = "```python\n## フェンス内\n```\n## 実在\n"
        assert _plan_format.extract_h2_sections(content) == ["実在"]

    def test_inner_info_string_fence_does_not_close_outer(self):
        """フェンス内に出現する info string 付きフェンスを閉じ判定にしない。"""
        content = "```\n```python\n## フェンス内\n```\n## 外側\n"
        # 1行目 ``` で開き、2行目 ```python は閉じ判定にならず、4行目 ``` で閉じる
        # 5行目 ## 外側 が抽出される
        assert _plan_format.extract_h2_sections(content) == ["外側"]


class TestCheckH2Order:
    """check_h2_order の各違反パターンを検査する。"""

    def test_valid_plan_returns_empty(self):
        assert not _plan_format.check_h2_order(_VALID_CONTENT)

    def test_missing_required_section(self):
        content = "## 変更履歴\n\n## 背景\n\n## 対応方針\n\n"
        violations = _plan_format.check_h2_order(content)
        assert any("missing required H2 sections" in v for v in violations)

    def test_unexpected_section(self):
        content = _VALID_CONTENT + "\n## 予期せぬセクション\n\nx\n"
        violations = _plan_format.check_h2_order(content)
        assert any("unexpected H2 sections" in v for v in violations)

    def test_out_of_order(self):
        # 背景と対応方針を入れ替えて順序違反にする
        content = (
            "## 変更履歴\n\nx\n\n"
            "## 対応方針\n\nx\n\n"
            "## 背景\n\nx\n\n"
            "## 調査結果\n\nx\n\n"
            "## 変更内容\n\nx\n\n"
            "## 実行方法\n\nx\n\n"
            "## 進捗ログ\n\nx\n\n"
            "## 計画ファイル（本ファイル）のパス\n\nx\n"
        )
        violations = _plan_format.check_h2_order(content)
        assert any("out of order" in v for v in violations)

    def test_empty_content_reports_all_missing(self):
        violations = _plan_format.check_h2_order("")
        assert any("missing required H2 sections" in v for v in violations)

    def test_h2_inside_fence_not_counted_as_unexpected(self):
        content = _VALID_CONTENT + "\n```\n## フェンス内\n```\n"
        assert not _plan_format.check_h2_order(content)


class TestIterMarkdownBodyLines:
    """iter_markdown_body_lines の除外領域とフェンス判定を検査する。"""

    def test_skips_frontmatter(self) -> None:
        content = "---\nkey: value\n---\n\nbody line\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "key: value" not in rendered
        assert "body line" in rendered

    def test_skips_code_fence_content(self) -> None:
        content = "before\n```text\ninside\n```\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "before" in rendered
        assert "after" in rendered
        assert "inside" not in rendered

    def test_skips_multiline_html_comment(self) -> None:
        content = "before\n<!--\nhidden\n-->\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "before" in rendered
        assert "after" in rendered
        assert "hidden" not in rendered

    def test_keeps_single_line_html_comment_line(self) -> None:
        content = "before\n<!-- visible -->\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "<!-- visible -->" in rendered

    def test_long_backtick_fence_close(self) -> None:
        """4文字以上のバックティックフェンスでも閉じ判定が機能する。"""
        content = "````text\ninner\n````\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "after" in rendered
        assert "inner" not in rendered

    def test_tilde_fence_close(self) -> None:
        content = "~~~text\ninner\n~~~\nafter\n"
        rendered = [line for _, line in _plan_format.iter_markdown_body_lines(content)]
        assert "after" in rendered
        assert "inner" not in rendered

    def test_lineno_starts_at_one(self) -> None:
        content = "first\nsecond\nthird\n"
        pairs = list(_plan_format.iter_markdown_body_lines(content))
        assert pairs == [(1, "first"), (2, "second"), (3, "third")]


class TestPlanFormatSsot:
    """PLAN_REQUIRED_H2がplan-file-guidelines.mdと整合することを検査する。"""

    def test_required_h2_appear_in_plan_file_ref(self):
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        for heading in _plan_format.PLAN_REQUIRED_H2:
            assert f"## {heading}" in text, f"plan-file-guidelines.md に `## {heading}` が無い"

    def test_section_definition_order_matches_required_h2(self):
        """`plan-file-guidelines.md`のセクション定義H3と`PLAN_REQUIRED_H2`の順序が一致することを検査する。

        セクション定義H3は`### XXX（`## YYY`）`形式で記述されており、
        バッククォート内のH2名（YYY）が登場順に`PLAN_REQUIRED_H2`と完全一致するべき。
        """
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        pattern = re.compile(r"^### .+?（`## ([^`]+)`）", re.MULTILINE)
        defined_h2 = tuple(pattern.findall(text))
        assert defined_h2 == _plan_format.PLAN_REQUIRED_H2, (
            f"plan-file-guidelines.md のセクション定義順 {defined_h2} が"
            f" PLAN_REQUIRED_H2 {_plan_format.PLAN_REQUIRED_H2} と一致しない"
        )
