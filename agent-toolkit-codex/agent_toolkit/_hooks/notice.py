"""Hook識別子を固定したLLM通知整形関数を生成する。"""

import inspect
from collections.abc import Callable

from agent_toolkit._hooks import message_format as _message_format
from agent_toolkit._hooks.session_state import increment_warn_notice_count as _increment_warn_notice_count

_WARN_REPEAT_THRESHOLD = 3
_WARN_TAG = "warn"
_warning_context = {"session_id": ""}


def set_warning_session_id(session_id: str) -> None:
    """現在のhook payloadのセッションIDをwarn整形経路へ渡す。"""
    _warning_context["session_id"] = session_id


def formatter(hook_id: str, *, default_tag: str = "") -> Callable[..., str]:
    """`hook_id`と既定タグを固定した通知整形関数を返す。"""

    def format_notice(body: str, *, tag: str = default_tag, removable_cause: bool | None = None) -> str:
        if tag == _WARN_TAG:
            if removable_cause is None:
                raise ValueError("warn通知のremovable_causeは真偽値で指定する必要がある")
            frame = inspect.currentframe()
            caller = frame.f_back if frame is not None else None
            cause = caller.f_code.co_name.removeprefix("_check_").removeprefix("_collect_") if caller else "unknown"
            return warning_formatter(hook_id)(
                body,
                cause=cause,
                session_id=_warning_context["session_id"],
                removable_cause=removable_cause,
            )
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


def warning_formatter(hook_id: str) -> Callable[..., str]:
    """`hook_id`を固定し、`warn_notice_counts`で原因別反復を集約する整形関数を返す。

    `removable_cause`が偽でも件数は集計し、受領側が除去できない原因へ
    原因の除去を求める反復注記だけを省く。
    """

    def format_warning(body: str, *, cause: str, session_id: str, removable_cause: bool) -> str:
        count = _increment_warn_notice_count(session_id, f"{hook_id}|{cause}")
        if removable_cause and count >= _WARN_REPEAT_THRESHOLD:
            body = (
                f"{body}\nこの通知は同一セッションで{count}件目である。"
                "同じ原因の通知が反復しているため、原因を除去してから同種の操作を続ける。"
            )
        return _message_format.llm_notice(body, hook_id, tag=_WARN_TAG)

    return format_warning
