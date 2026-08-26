"""Hook識別子を固定したLLM通知整形関数を生成する。"""

from collections.abc import Callable

import _message_format


def formatter(hook_id: str, *, default_tag: str = "") -> Callable[..., str]:
    """`hook_id`と既定タグを固定した通知整形関数を返す。"""

    def format_notice(body: str, *, tag: str = default_tag) -> str:
        return _message_format.llm_notice(body, hook_id, tag=tag)

    return format_notice


def block_formatter(hook_id: str) -> Callable[..., str]:
    """`hook_id`を固定し、解消手段を必須とするblock通知整形関数を返す。"""

    def format_block(body: str, *, fix: str) -> str:
        if not fix.strip():
            raise ValueError("block通知のfixは空文字列以外で指定する必要がある")
        block_body = f"{body}\nFix: {fix}"
        return _message_format.llm_notice(block_body, hook_id, tag="block")

    return format_block
