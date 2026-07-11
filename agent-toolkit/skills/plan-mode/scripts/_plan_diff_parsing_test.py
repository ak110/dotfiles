"""agent-toolkit/skills/plan-mode/scripts/_plan_diff_parsing.py のテスト。

共有モジュールの公開定数（コンパイル済み正規表現）と`iter_non_fenced_lines`関数の
仕様を単体レベルで検証する。呼び出し側スクリプト（`check_plan_diff_gates.py`・
`check_wc_projection.py`）はこれらの挙動へ依存する。
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import types

_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "_plan_diff_parsing.py"


def _load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("_plan_diff_parsing", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MOD = _load_module()


class TestPublicConstants:
    """公開定数群がコンパイル済み正規表現であることを確認する。"""

    def test_public_constants_are_compiled_patterns(self) -> None:
        for name in ("TEXT_FENCE_OPEN_RE", "FENCE_CLOSE_RE", "FENCE_RE", "REDUCTION_HEADING_RE"):
            pattern = getattr(_MOD, name)
            assert isinstance(pattern, re.Pattern), name

    def test_text_fence_open_matches_text_only(self) -> None:
        assert _MOD.TEXT_FENCE_OPEN_RE.match("```text")
        assert _MOD.TEXT_FENCE_OPEN_RE.match("```text  ")
        assert not _MOD.TEXT_FENCE_OPEN_RE.match("```python")
        assert not _MOD.TEXT_FENCE_OPEN_RE.match("```bash")
        assert not _MOD.TEXT_FENCE_OPEN_RE.match("```")

    def test_fence_close_matches_bare_backticks(self) -> None:
        assert _MOD.FENCE_CLOSE_RE.match("```")
        assert _MOD.FENCE_CLOSE_RE.match("```   ")
        # `TEXT_FENCE_OPEN_RE`との排他性: ```textには一致しない。
        assert not _MOD.FENCE_CLOSE_RE.match("```text")

    def test_fence_re_matches_multiple_backticks_and_tildes(self) -> None:
        assert _MOD.FENCE_RE.match("```")
        assert _MOD.FENCE_RE.match("````")
        assert _MOD.FENCE_RE.match("~~~")
        assert _MOD.FENCE_RE.match("~~~~")
        assert _MOD.FENCE_RE.match("```python")
        assert _MOD.FENCE_RE.match("  ```")  # 先頭空白許容

    def test_reduction_heading_re_matches_h4_only(self) -> None:
        assert _MOD.REDUCTION_HEADING_RE.match("#### 縮減対象")
        assert _MOD.REDUCTION_HEADING_RE.match("#### 縮減対象（一部）")
        assert not _MOD.REDUCTION_HEADING_RE.match("### 縮減対象")
        assert not _MOD.REDUCTION_HEADING_RE.match("##### 縮減対象")


class TestIterNonFencedLines:
    """`iter_non_fenced_lines`のフェンス除外仕様を検証する。"""

    def test_iter_non_fenced_lines_skips_fenced_content(self) -> None:
        text = "outer1\n```\ninside\n```\nouter2\n"
        lines = text.splitlines()
        yielded = [line for _idx, line in _MOD.iter_non_fenced_lines(lines)]
        assert "inside" not in yielded
        assert "outer1" in yielded
        assert "outer2" in yielded

    def test_iter_non_fenced_lines_respects_start_offset(self) -> None:
        lines = ["a", "b", "c", "d"]
        yielded_idxs = [idx for idx, _line in _MOD.iter_non_fenced_lines(lines, start=2)]
        assert yielded_idxs == [2, 3]

    def test_iter_non_fenced_lines_handles_nested_fence_markers(self) -> None:
        # ~~~フェンス内の```はフェンス終了扱いにならない（マーカー先頭文字の一致で判定）。
        text = "outer\n~~~\n```\ninside\n```\n~~~\ntail\n"
        lines = text.splitlines()
        yielded = [line for _idx, line in _MOD.iter_non_fenced_lines(lines)]
        assert "inside" not in yielded
        assert "outer" in yielded
        assert "tail" in yielded

    def test_iter_non_fenced_lines_handles_variable_length_open_close_match(self) -> None:
        """3バッククォート開始→3バッククォート閉じ（既存挙動維持）。"""
        text = "outer1\n```\ninside\n```\nouter2\n"
        lines = text.splitlines()
        yielded = [line for _idx, line in _MOD.iter_non_fenced_lines(lines)]
        assert yielded == ["outer1", "outer2"]

    def test_iter_non_fenced_lines_handles_four_backtick_pair(self) -> None:
        """4バッククォート開始→4バッククォート閉じは正しく閉じる。"""
        text = "outer1\n````\ninside\n````\nouter2\n"
        lines = text.splitlines()
        yielded = [line for _idx, line in _MOD.iter_non_fenced_lines(lines)]
        assert yielded == ["outer1", "outer2"]

    def test_iter_non_fenced_lines_three_open_four_close(self) -> None:
        """3バッククォート開始→4バッククォート閉じは閉じとして扱う（同数以上）。"""
        text = "outer1\n```\ninside\n````\nouter2\n"
        lines = text.splitlines()
        yielded = [line for _idx, line in _MOD.iter_non_fenced_lines(lines)]
        assert "inside" not in yielded
        assert "outer2" in yielded

    def test_iter_non_fenced_lines_four_open_three_close_does_not_close(self) -> None:
        """4バッククォート開始→3バッククォート閉じは閉じとして扱わない。"""
        text = "outer1\n````\ninside\n```\nstill_inside\n````\nouter2\n"
        lines = text.splitlines()
        yielded = [line for _idx, line in _MOD.iter_non_fenced_lines(lines)]
        assert "still_inside" not in yielded
        assert "outer2" in yielded

    def test_iter_non_fenced_lines_nested_outer_four_inner_three(self) -> None:
        """外側4バッククォート・内側3バッククォートのネスト構造を正しく扱う。"""
        text = "outer1\n````\n```\ninner_code\n```\nouter_content\n````\nouter2\n"
        lines = text.splitlines()
        yielded = [line for _idx, line in _MOD.iter_non_fenced_lines(lines)]
        assert "inner_code" not in yielded
        assert "outer_content" not in yielded
        assert yielded == ["outer1", "outer2"]


class TestIterReductionHeadings:
    """`iter_reduction_headings`の返却文字列の透過性を検証する。"""

    def test_iter_reduction_headings_returns_qualified_name_as_is(self) -> None:
        section = "#### 縮減対象（agent-standards SKILL.md）\n本文\n"
        assert list(_MOD.iter_reduction_headings(section)) == ["agent-standards SKILL.md"]

    def test_iter_reduction_headings_returns_basename_and_relative_path(self) -> None:
        section = "#### 縮減対象（SKILL.md）\n本文1\n#### 縮減対象（agent-toolkit/skills/agent-standards/SKILL.md）\n本文2\n"
        assert list(_MOD.iter_reduction_headings(section)) == [
            "SKILL.md",
            "agent-toolkit/skills/agent-standards/SKILL.md",
        ]


class TestIsMatchingClose:
    """`is_matching_close`ヘルパーの動作を検証する。"""

    def test_three_open_three_close_matches(self) -> None:
        assert _MOD.is_matching_close("```", "```")

    def test_four_open_four_close_matches(self) -> None:
        assert _MOD.is_matching_close("````", "````")

    def test_three_open_four_close_matches(self) -> None:
        """3バッククォート開始は4バッククォート閉じでも整合する（同数以上）。"""
        assert _MOD.is_matching_close("```", "````")

    def test_four_open_three_close_does_not_match(self) -> None:
        assert not _MOD.is_matching_close("````", "```")

    def test_non_close_line_returns_false(self) -> None:
        assert not _MOD.is_matching_close("```", "regular text")

    def test_close_marker_with_trailing_whitespace(self) -> None:
        assert _MOD.is_matching_close("```", "```   ")
