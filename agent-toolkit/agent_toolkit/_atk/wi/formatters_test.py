"""`formatters`モジュールの本文要約におけるBOMと本文文字の保持のテスト。"""

import pytest

from agent_toolkit._atk.wi import formatters as _formatters  # noqa: E402  # pylint: disable=wrong-import-position

# `parse_alert_keys`のテストは`alerts_test.py`側で公開関数`existing_alert_keys`
# 経由で行う（private関数直接テストを避けるため。`agent-basics:writing-standards`の
# `references/testing.md`「private関数の直接テスト禁止」節参照）。


@pytest.mark.parametrize("kind", ["awi", "uwi"])
def test_summary_accepts_leading_bom_and_preserves_body_character(kind: str) -> None:
    """先頭BOMの有無で要約が一致し、本文内のU+FEFFを保持する。"""
    body = "表\ufeff題" if kind == "awi" else "## 質問\n\n表\ufeff題\n\n## 回答\n"
    text = f"---\ntype: {kind}\n---\n\n{body}\n"
    summarize = _formatters.body_summary if kind == "awi" else _formatters.uwi_body_summary
    assert summarize("\ufeff" + text) == summarize(text) == "表\ufeff題"
