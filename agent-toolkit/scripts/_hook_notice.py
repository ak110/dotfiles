"""Hook識別子を固定したLLM通知整形関数を生成する。"""

from collections.abc import Callable

import _message_format


def formatter(hook_id: str, *, default_tag: str = "") -> Callable[..., str]:
    """`hook_id`と既定タグを固定した通知整形関数を返す。"""

    def format_notice(body: str, *, tag: str = default_tag) -> str:
        return _message_format.llm_notice(body, hook_id, tag=tag)

    return format_notice
