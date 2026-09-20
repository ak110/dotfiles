"""Hook識別子を固定したLLM通知整形関数を生成する。"""

import inspect
from collections.abc import Callable

from agent_toolkit._hooks import message_format as _message_format
from agent_toolkit._hooks.session_state import increment_warn_notice_count as _increment_warn_notice_count

_WARN_REPEAT_THRESHOLD = 2
_WARN_TAG = "warn"
_warning_context: dict[str, object] = {"session_id": "", "blocks": []}


def set_warning_session_id(session_id: str) -> None:
    """現在のhook payloadのセッションIDをwarn整形経路へ渡す。"""
    _warning_context["session_id"] = session_id
    _warning_context["blocks"] = []


def consume_warning_blocks() -> list[str]:
    """反復した除去可能warnから生成したblock通知を取り出す。"""
    blocks = _warning_context.get("blocks")
    _warning_context["blocks"] = []
    return list(blocks) if isinstance(blocks, list) else []


_GENERIC_FIX = "この通知が挙げた原因を除去してから同じ操作を実行する。"


def _warning_body_and_fix(body: str) -> tuple[str, str]:
    """warn本文をblock理由と解消手段へ分ける。

    解消手段のマーカーを持たない本文では、本文を複製せず汎用の解消手段を返す。
    `Fix:`欄は解消手段を示す欄であり、理由の複製を置くと読む側が実行する操作を得られない。
    """
    for marker in ("\n対処: ", "\nFix: "):
        if marker in body:
            reason, fix = body.rsplit(marker, maxsplit=1)
            if fix.strip():
                return reason, fix
    return body, _GENERIC_FIX


def formatter(hook_id: str, *, default_tag: str = "") -> Callable[..., str]:
    """`hook_id`と既定タグを固定した通知整形関数を返す。"""

    def format_notice(
        body: str,
        *,
        tag: str = default_tag,
        removable_cause: bool | None = None,
        summary: str | None = None,
    ) -> str:
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        cause = caller.f_code.co_name.removeprefix("_check_").removeprefix("_collect_") if caller else "unknown"
        if tag == _WARN_TAG:
            if removable_cause is None:
                raise ValueError("warn通知のremovable_causeは真偽値で指定する必要がある")
            session_id = _warning_context.get("session_id")
            return warning_formatter(hook_id)(
                body,
                cause=cause,
                session_id=session_id if isinstance(session_id, str) else "",
                removable_cause=removable_cause,
                summary=summary,
            )
        if summary is not None:
            # 1件目で判断材料は到達済みであり、2件目以降は対象と件数だけを返す。
            # 反復の集約はタグの重大度と独立の性質であるため、warn以外のタグでも同じ扱いにする。
            session_id = _warning_context.get("session_id")
            count = _increment_warn_notice_count(
                session_id if isinstance(session_id, str) else "",
                f"{hook_id}|{cause}",
            )
            if count >= 2:
                body = f"{summary}\nこの通知は同一セッションで{count}件目である。"
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

    def format_warning(
        body: str,
        *,
        cause: str,
        session_id: str,
        removable_cause: bool,
        summary: str | None = None,
    ) -> str:
        count = _increment_warn_notice_count(session_id, f"{hook_id}|{cause}")
        removable_count = _increment_warn_notice_count(session_id, f"{hook_id}|{cause}|removable") if removable_cause else 0
        if removable_count >= _WARN_REPEAT_THRESHOLD:
            reason, fix = _warning_body_and_fix(body)
            block = block_formatter(hook_id)(
                f"{reason}\nこの通知は同一セッションで{removable_count}件目である。同じ原因の操作を遮断した。",
                fix=fix,
            )
            blocks = _warning_context.setdefault("blocks", [])
            if isinstance(blocks, list):
                blocks.append(block)
            body = f"{body}\nこの通知は同一セッションで{removable_count}件目である。原因を除去してから続行する。"
        if summary is not None and count >= 2:
            # 1件目で判断材料は到達済みであり、2件目以降は対象と件数だけを返す。
            return _message_format.llm_notice(
                f"{summary}\nこの通知は同一セッションで{count}件目である。",
                hook_id,
                tag=_WARN_TAG,
            )
        return _message_format.llm_notice(body, hook_id, tag=_WARN_TAG)

    return format_warning
