"""`_hook_notice`のblock通知整形契約を検証する。"""

import pytest
from _hook_notice import block_formatter


@pytest.mark.parametrize("fix", ["", " ", "\t"])
def test_block_formatter_rejects_empty_fix(fix: str) -> None:
    """空文字列または空白文字だけの解消手段を拒否する。"""
    format_block = block_formatter("test/hook")

    with pytest.raises(ValueError):
        format_block("blocked", fix=fix)


def test_block_formatter_adds_fix_tag_and_suffix() -> None:
    """blockタグ、Fix行、共通サフィックスを同時に付与する。"""
    format_block = block_formatter("test/hook")

    message = format_block("blocked", fix="retry")

    assert message.startswith("[auto-generated: test/hook][block] blocked\nFix: retry ")
    assert message.endswith("（自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）")
