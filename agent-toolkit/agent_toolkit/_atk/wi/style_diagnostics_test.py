"""過去の記録を読むための表記警告の識別を検証する。"""

import pytest

from agent_toolkit._atk.wi import style_diagnostics


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("警告: 本文:2:3: 口語表現 口語（候補: 書き言葉）", True),
        ("警告: 本文:1:1: ダッシュ —", True),
        ('警告: WI本文の表記診断: 入力元="原稿.md" 論理本文:2:3: ダッシュ — | 文脈: 開始【—】終了', True),
        ('警告: WI本文の表記診断: 入力元="原稿.md" 論理本文:2:3: 口語表現 口語', True),
        ("警告: WI本文の表記診断: 2件", True),
        ("警告: WI本文の表記診断: 0件", False),
        ("警告: 別の診断", False),
    ],
)
def test_identifies_historical_body_style_warnings(line: str, expected: bool) -> None:
    """過去の座標・文脈・件数の形式だけを、WI本文の表記診断として識別する。"""
    assert style_diagnostics.is_body_style_diagnostic_warning(line) is expected
