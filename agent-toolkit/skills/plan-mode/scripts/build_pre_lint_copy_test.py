"""build_pre_lint_copy.pyの検証。"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "build_pre_lint_copy.py"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from build_pre_lint_copy import build_pre_lint_copy  # noqa: E402

# pylint: enable=wrong-import-position


def test_strips_fenced_blocks_only_in_background() -> None:
    """`## 背景`配下のフェンスのみを除外し、他節のフェンスは保持する。"""
    text = "\n".join(
        [
            "# 計画",
            "",
            "## 背景",
            "",
            "前置き。",
            "",
            "```text",
            "原文フェンス内",
            "詳細",
            "```",
            "",
            "後置き。",
            "",
            "## 変更内容",
            "",
            "```text",
            "保持対象",
            "```",
            "",
        ]
    )
    result = build_pre_lint_copy(text)
    assert "原文フェンス内" not in result
    assert "詳細" not in result
    assert "保持対象" in result
    assert "前置き。" in result
    assert "後置き。" in result


def test_normalizes_consecutive_blank_lines() -> None:
    """フェンス除去痕として生じた3行以上の連続空行を1個の空行へ正規化する。"""
    text = "\n".join(
        [
            "## 背景",
            "",
            "```text",
            "除外対象",
            "```",
            "",
            "",
            "",
            "本文",
            "",
        ]
    )
    result = build_pre_lint_copy(text)
    # 「3行以上の連続空行」が残らないことを確認する。
    assert "\n\n\n" not in result
    assert "本文" in result


def test_preserves_fence_outside_background() -> None:
    """`## 変更内容`配下のフェンスは保持する。"""
    text = "\n".join(
        [
            "## 変更内容",
            "",
            "```text",
            "保持されるべき内容",
            "```",
            "",
        ]
    )
    result = build_pre_lint_copy(text)
    assert "保持されるべき内容" in result
    assert "```text" in result


def test_cli_reads_and_writes_files(tmp_path: pathlib.Path) -> None:
    """CLIが入力ファイルを読み、出力ファイルへ結果を出力する。"""
    input_path = tmp_path / "plan.md"
    output_path = tmp_path / "out.md"
    input_path.write_text(
        "## 背景\n\n```text\n除外\n```\n\n## 変更内容\n\n本文\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), str(input_path), str(output_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    out_text = output_path.read_text(encoding="utf-8")
    assert "除外" not in out_text
    assert "本文" in out_text


@pytest.mark.parametrize(
    "fence_open, fence_close",
    [
        ("```text", "```"),
        ("````text", "````"),
        ("~~~text", "~~~"),
    ],
)
def test_multiple_fence_styles(fence_open: str, fence_close: str) -> None:
    """バッククォート4個・チルダなど多様なフェンス種を除外する。"""
    text = "\n".join(
        [
            "## 背景",
            "",
            fence_open,
            "除外対象",
            fence_close,
            "",
            "後続本文",
            "",
        ]
    )
    result = build_pre_lint_copy(text)
    assert "除外対象" not in result
    assert "後続本文" in result
