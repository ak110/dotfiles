"""`_atk_fb_formatters`モジュール（本文要約の表示幅ベース切り詰め）のテスト。

`atk_test.py`側の肥大化（pylint `too-many-lines`）回避のため、
`_atk_fb_formatters._body_summary`の切り詰め境界ケースを本ファイルへ分離する。
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_fb_formatters as _formatters  # noqa: E402  # pylint: disable=wrong-import-position


class TestBodySummaryTruncation:
    """_body_summary: 表示幅（`available_width`）境界での切り詰め動作を検証する。"""

    @pytest.mark.parametrize(
        ("body", "available_width", "expected"),
        [
            ("あ" * 20, 40, "あ" * 20),  # ちょうど収まる全角文字列
            ("い" * 10, 10, "い" * 3 + "..."),  # 全角文字列の切り詰め
            ("先頭行\n2行目", 40, "先頭行"),  # 複数行は先頭行のみ使用
            ("abcdefghijklmnopqrstuvwxyz", 20, "abcdefghijklmnopq..."),  # 狭い端末幅(20列)
            ("先頭行", 0, ""),  # available_width==0(プレフィクス表示幅で余地が無いケース)
            ("先頭行", -5, ""),  # available_widthが負
            ("あa" * 10, 10, "あaあa..."),  # 全角・半角混在
            ("", 40, ""),  # 空要約
            ("い" * 10, 3, "..."),
            ("い" * 10, 2, ".."),
            ("い" * 10, 1, "."),  # 上記3件は`...`分の幅確保の境界（3/2/1）
        ],
    )
    def test_truncation_boundaries(self, body: str, available_width: int, expected: str) -> None:
        """`available_width`境界における切り詰め結果を表形式で検証する。"""
        text = f"---\ntarget_repo: github.com/example/foo\n---\n\n{body}\n"
        result = _formatters._body_summary(text, available_width=available_width)  # noqa: SLF001  # pylint: disable=protected-access
        assert result == expected
