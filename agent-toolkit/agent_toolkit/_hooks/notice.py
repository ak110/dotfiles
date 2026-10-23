"""Hook識別子を固定したLLM通知整形関数を生成する。"""

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import contextlib
lazy import hashlib
lazy import inspect
lazy import json
lazy from collections.abc import Callable, Generator

lazy from agent_toolkit._common import next_action as _next_action
lazy from agent_toolkit._common.session_state import update_state
lazy from agent_toolkit._hooks import message_format as _message_format

_WARN_TAG = "warn"
_warning_context: dict[str, object] = {"session_id": "", "blocks": []}


def set_warning_session_id(session_id: str) -> None:
    """現在のhook payloadのセッションIDをwarn通知を整形する処理へ渡す。"""
    _warning_context["session_id"] = session_id
    if not _warning_context.get("cycle"):
        _warning_context["blocks"] = []


@contextlib.contextmanager
def notice_cycle(session_id: str, scope: str, subject: str) -> Generator[None]:
    """同じhookと主体で現在も成立する通知を記録し、解消した原因を解除する。"""
    previous = dict(_warning_context)
    seen: dict[str, list[str]] = {}
    prefix = f"{scope}|{subject}|"
    _warning_context.update(session_id=session_id, cycle=prefix, seen=seen, blocks=[])

    def finish(state: dict) -> dict | None:
        notices = state.get("notice_fingerprints", {})
        notices = dict(notices) if isinstance(notices, dict) else {}
        current = {key: value for key, value in notices.items() if not key.startswith(prefix)}
        current.update(seen)
        if current == notices:
            return None
        state["notice_fingerprints"] = current
        return state

    try:
        yield
    finally:
        update_state(session_id, finish)
        _warning_context.clear()
        _warning_context.update(previous)


def _claim_notice(session_id: str, hook_id: str, cause: str, body: str, fix: str | None) -> bool:
    """原因・対象・対処が同じ通知を、条件が続く間は一度だけ届ける。"""
    if not session_id:
        return True
    prefix = _warning_context.get("cycle") or f"{hook_id}|main|"
    key = f"{prefix}{hook_id}|{cause}"
    fingerprint = hashlib.sha256(json.dumps([body, fix], ensure_ascii=False).encode("utf-8")).hexdigest()
    seen = _warning_context.get("seen")
    if isinstance(seen, dict):
        values = seen.setdefault(key, [])
        if fingerprint not in values:
            values.append(fingerprint)
    claimed = True

    def claim(state: dict) -> dict | None:
        nonlocal claimed
        notices = state.get("notice_fingerprints", {})
        notices = dict(notices) if isinstance(notices, dict) else {}
        previous = notices.get(key, [])
        previous = previous if isinstance(previous, list) else []
        if fingerprint in previous:
            claimed = False
            return None
        notices[key] = [*previous, fingerprint]
        state["notice_fingerprints"] = notices
        return state

    update_state(session_id, claim)
    return claimed


def consume_warning_blocks() -> list[str]:
    """旧呼出元が持つ昇格通知の回収口。現在は昇格通知を生成しない。"""
    blocks = _warning_context.get("blocks")
    _warning_context["blocks"] = []
    return list(blocks) if isinstance(blocks, list) else []


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
        subject: str = "",
    ) -> str:
        """通知を整形する。`warn`区分では解消手段`fix`を必須とする。

        `notice`区分はそれ自体が行動の指示を本文とする定型の配送であるため、`fix`を任意とする。
        """
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        cause = caller.f_code.co_name.removeprefix("_check_").removeprefix("_collect_") if caller else "unknown"
        if subject:
            cause += "|" + hashlib.sha256(subject.encode("utf-8")).hexdigest()
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
        session_id = _warning_context.get("session_id")
        if tag != "block" and not _claim_notice(session_id if isinstance(session_id, str) else "", hook_id, cause, body, fix):
            return ""
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
    """原因・対象・対処が変わった警告を届け、同じ条件の反復を抑止する。"""

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
        del removable_cause, escalate_on_repeat, summary
        if not _claim_notice(session_id, hook_id, cause, body, fix):
            return ""
        return _message_format.llm_notice(_next_action.with_next_action(body, fix), hook_id, tag=_WARN_TAG)

    return format_warning
