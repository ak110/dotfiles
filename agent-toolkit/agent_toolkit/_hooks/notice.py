"""Hook識別子を固定したLLM通知整形関数を生成する。"""

import inspect
from collections.abc import Callable

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._hooks import message_format as _message_format
from agent_toolkit._hooks.session_state import increment_warn_notice_count as _increment_warn_notice_count

_WARN_REPEAT_THRESHOLD = 2
_WARN_TAG = "warn"
_warning_context: dict[str, object] = {"session_id": "", "blocks": []}


def set_warning_session_id(session_id: str) -> None:
    """現在のhook payloadのセッションIDをwarn通知を整形する処理へ渡す。"""
    _warning_context["session_id"] = session_id
    _warning_context["blocks"] = []


def consume_warning_blocks() -> list[str]:
    """反復した除去可能warnから生成したblock通知を取り出す。"""
    blocks = _warning_context.get("blocks")
    _warning_context["blocks"] = []
    return list(blocks) if isinstance(blocks, list) else []


def _repeat_summary(body: str) -> str:
    """反復通知から対象と保存先の行を残す。次の操作の行は呼び出し側が引数から必ず付け直す。"""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return "同じ原因の通知が再発した。"
    selected = [lines[0]]
    for line in lines[1:]:
        if line.startswith(("対象:", "保存先:")) and line not in selected:
            selected.append(line)
    return "\n".join(selected)


def _with_fix(body: str, fix: str | None) -> str:
    """解消手段があれば本文の後へ次の操作の行を続ける。"""
    return body if fix is None else _next_action.with_next_action(body, fix)


def formatter(hook_id: str, *, default_tag: str = "") -> Callable[..., str]:
    """`hook_id`と、タグを省略した場合に使う値を固定した通知整形関数を返す。"""

    def format_notice(  # noqa: PLR0913 -- 通知の種別ごとに必須となる引数をキーワードで受け取る
        body: str,
        *,
        tag: str = default_tag,
        fix: str | None = None,
        removable_cause: bool | None = None,
        escalate_on_repeat: bool = False,
        summary: str | None = None,
    ) -> str:
        """通知を整形する。`warn`区分では解消手段`fix`を必須とする。

        `notice`区分はそれ自体が行動の指示を本文とする定型の配送であるため、`fix`を任意とする。
        """
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        cause = caller.f_code.co_name.removeprefix("_check_").removeprefix("_collect_") if caller else "unknown"
        if tag == _WARN_TAG:
            if removable_cause is None:
                raise ValueError("warn通知のremovable_causeは真偽値で指定する必要がある")
            if fix is None:
                raise ValueError("warn通知のfixは空文字列以外で指定する必要がある")
            session_id = _warning_context.get("session_id")
            return warning_formatter(hook_id)(
                body,
                fix=fix,
                cause=cause,
                session_id=session_id if isinstance(session_id, str) else "",
                removable_cause=removable_cause,
                escalate_on_repeat=escalate_on_repeat,
                summary=summary,
            )
        if summary is not None or body:
            # 1件目で判断材料は到達済みであり、2件目以降は対象と件数だけを返す。
            # 反復の集約はタグの重大度と独立の性質であるため、warn以外のタグでも同じ扱いにする。
            session_id = _warning_context.get("session_id")
            count = _increment_warn_notice_count(
                session_id if isinstance(session_id, str) else "",
                f"{hook_id}|{cause}",
            )
            if count >= 2:
                body = f"{summary or _repeat_summary(body)}\nこの通知は同一セッションで{count}件目である。"
        return _message_format.llm_notice(_with_fix(body, fix), hook_id, tag=tag)

    return format_notice


def block_formatter(hook_id: str) -> Callable[..., str]:
    """`hook_id`を固定し、解消手段を必須とするblock通知整形関数を返す。"""

    def format_block(body: str, *, fix: str) -> str:
        if not fix.strip():
            raise ValueError("block通知のfixは空文字列以外で指定する必要がある")
        return _message_format.llm_notice(_next_action.with_next_action(body, fix), hook_id, tag="block")

    return format_block


def warning_formatter(hook_id: str) -> Callable[..., str]:
    """`hook_id`を固定し、`warn_notice_counts`で原因別反復を集約する整形関数を返す。

    `fix`は解消手段、`removable_cause`は反復注記の可否、`escalate_on_repeat`は遮断の可否を表す。
    解消手段は反復の2件目以降と遮断への昇格でも同じ文面を次の操作の行として残す。
    """

    def format_warning(  # noqa: PLR0913 -- 反復の判定に要する値をキーワードで受け取る
        body: str,
        *,
        fix: str,
        cause: str,
        session_id: str,
        removable_cause: bool,
        escalate_on_repeat: bool = False,
        summary: str | None = None,
    ) -> str:
        if not fix.strip():
            raise ValueError("warn通知のfixは空文字列以外で指定する必要がある")
        if escalate_on_repeat and not removable_cause:
            raise ValueError("反復遮断には除去可能な原因が必要である")
        count = _increment_warn_notice_count(session_id, f"{hook_id}|{cause}")
        removable_count = _increment_warn_notice_count(session_id, f"{hook_id}|{cause}|removable") if removable_cause else 0
        if escalate_on_repeat and removable_count >= _WARN_REPEAT_THRESHOLD:
            block = block_formatter(hook_id)(
                f"{body}\nこの通知は同一セッションで{removable_count}件目である。同じ原因の操作を遮断した。",
                fix=fix,
            )
            blocks = _warning_context.setdefault("blocks", [])
            if isinstance(blocks, list):
                blocks.append(block)
            body = f"{body}\nこの通知は同一セッションで{removable_count}件目である。原因を除去してから続行する。"
        elif removable_count >= _WARN_REPEAT_THRESHOLD:
            body = f"{body}\nこの通知は同一セッションで{removable_count}件目である。"
        if count >= 2:
            # 1件目で判断材料は到達済みであり、2件目以降は対象と件数と次の操作だけを返す。
            body = f"{summary or _repeat_summary(body)}\nこの通知は同一セッションで{count}件目である。"
        return _message_format.llm_notice(_next_action.with_next_action(body, fix), hook_id, tag=_WARN_TAG)

    return format_warning
