"""Hook識別子を固定したLLM通知整形関数を生成する。"""

lazy import contextlib
lazy import hashlib
lazy import inspect
lazy import json
lazy from collections.abc import Callable, Generator, Sequence

lazy from agent_toolkit._common import message_format as _common_message_format
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._hooks import message_format as _message_format

_WARN_TAG = "warn"
_warning_context: dict[str, object] = {"session_id": "", "blocks": []}
_delivery_candidates: list[dict[str, str]] = []
_output_context = {"deferred": 0}

_informational: list[str] = []


def begin_delivery() -> None:
    if not _output_context["deferred"]:
        _delivery_candidates.clear()


def reset_informational() -> None:
    _informational.clear()


def informational(text: str) -> str:
    if text:
        _informational.append(text)
    return text


def user_copy(response: dict, generated: Sequence[_common_message_format.GeneratedMessage]) -> str:
    text = output_text(response)
    present = [item for item in dict.fromkeys(generated) if item.text in text]
    outer = [
        item
        for item in present
        if item.text not in _informational and not any(other is not item and item.text in other.text for other in present)
    ]
    outer.sort(key=lambda item: text.find(item.text))
    copies = [item.text for item in outer]
    return "\n\n".join(dict.fromkeys(copies))


def stage_delivery(text: str, session_id: str, field: str, key: str, value: str) -> None:
    if text and session_id:
        receipt = {"text": text, "session_id": session_id, "field": field, "key": key, "value": value}
        if receipt not in _delivery_candidates:
            _delivery_candidates.append(receipt)


def delivery_receipts(text: str) -> list[dict[str, str]]:
    return [dict(item) for item in _delivery_candidates if item["text"] in text]


def restore_delivery_receipts(receipts: object) -> None:
    if not isinstance(receipts, list):
        return
    for item in receipts:
        if (
            isinstance(item, dict)
            and all(isinstance(item.get(key), str) and item[key] for key in ("text", "session_id", "field", "key", "value"))
            and item not in _delivery_candidates
        ):
            _delivery_candidates.append(item)


@contextlib.contextmanager
def defer_delivery() -> Generator[None]:
    _output_context["deferred"] += 1
    try:
        yield
    finally:
        _output_context["deferred"] -= 1


def record_delivery(text: str) -> None:
    if _output_context["deferred"]:
        return
    for receipt in delivery_receipts(text):

        def record(state: dict, item: dict[str, str] = receipt) -> dict:
            mapping = state.get(item["field"], {})
            mapping = dict(mapping) if isinstance(mapping, dict) else {}
            values = mapping.get(item["key"], [])
            values = list(values) if isinstance(values, list) else []
            if item["value"] not in values:
                values.append(item["value"])
            mapping[item["key"]] = values
            state[item["field"]] = mapping
            return state

        update_state(receipt["session_id"], record)
        _delivery_candidates.remove(receipt)


def output_text(response: dict) -> str:
    texts: list[str] = []
    specific = response.get("hookSpecificOutput")
    if isinstance(specific, dict):
        for key in ("additionalContext", "permissionDecisionReason"):
            if isinstance(specific.get(key), str):
                texts.append(specific[key])
    if response.get("decision") in {"block", "deny", "continue"} and isinstance(response.get("reason"), str):
        texts.append(response["reason"])
    steps = response.get("injectSteps")
    if isinstance(steps, list):
        texts.extend(
            step["ephemeralMessage"]
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("ephemeralMessage"), str)
        )
    return "\n\n".join(texts)


def emit_response(response: dict) -> None:
    print(json.dumps(response, ensure_ascii=False))
    # 出力後の記録障害を再送すると単一JSONの契約を破る。未記録の通知は次回再通知できる。
    with contextlib.suppress(Exception):
        record_delivery(output_text(response))


def resolve_notice(session_id: str, hook_id: str, cause: str, *, subject: str | None = None) -> None:
    update_state(session_id, lambda state: resolve_notice_in_state(state, hook_id, cause, subject=subject))


def resolve_notice_in_state(state: dict, hook_id: str, cause: str, *, subject: str | None = None) -> dict | None:
    """呼出側の状態更新と同じロック内で、指定した原因の通知を解除する。"""
    prefix = _warning_context.get("cycle") or f"{hook_id}|main|"
    key = f"{prefix}{hook_id}|{cause}"
    notices = state.get("notice_fingerprints")
    if not isinstance(notices, dict):
        return None
    marker = f"|{subject}|{hook_id}|"
    keys = (
        {name for name in notices if name == key}
        if subject is None
        else {name for name in notices if marker in name and name.rsplit(marker, 1)[1].split("|", 1)[0] == cause}
    )
    if not keys:
        return None
    state["notice_fingerprints"] = {name: value for name, value in notices.items() if name not in keys}
    return state


def forget_subject_notices(session_id: str, subject: str) -> None:

    def forget(state: dict) -> dict | None:
        notices = state.get("notice_fingerprints")
        if not isinstance(notices, dict):
            return None
        kept = {name: value for name, value in notices.items() if name.split("|", 2)[1:2] != [subject]}
        if len(kept) == len(notices):
            return None
        state["notice_fingerprints"] = kept
        return state

    update_state(session_id, forget)


def set_warning_session_id(session_id: str) -> None:
    _warning_context["session_id"] = session_id
    if not _warning_context.get("cycle"):
        _warning_context["blocks"] = []


@contextlib.contextmanager
def notice_cycle(session_id: str, scope: str, subject: str) -> Generator[None]:
    previous = dict(_warning_context)
    prefix = f"{scope}|{subject}|"
    _warning_context.update(session_id=session_id, cycle=prefix, blocks=[])

    try:
        yield
    finally:
        _warning_context.clear()
        _warning_context.update(previous)


def _claim_notice(session_id: str, hook_id: str, cause: str, body: str, fix: str | None, *, tag: str = "") -> bool:
    if not session_id:
        return True
    prefix = _warning_context.get("cycle") or f"{hook_id}|main|"
    key = f"{prefix}{hook_id}|{cause}"
    fingerprint = hashlib.sha256(json.dumps([body, fix], ensure_ascii=False).encode("utf-8")).hexdigest()
    notices = read_state(session_id).get("notice_fingerprints", {})
    values = notices.get(key, []) if isinstance(notices, dict) else []
    if isinstance(values, list) and fingerprint in values:
        return False
    text = _message_format.llm_notice(_with_fix(body, fix), hook_id, tag=tag)
    stage_delivery(text, session_id, "notice_fingerprints", key, fingerprint)
    return True


def consume_warning_blocks() -> list[str]:
    blocks = _warning_context.get("blocks")
    _warning_context["blocks"] = []
    return list(blocks) if isinstance(blocks, list) else []


def _with_fix(body: str, fix: str | None) -> str:
    return body if fix is None else f"{body}\n{fix}"


def formatter(hook_id: str, *, default_tag: str = "") -> Callable[..., str]:

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
        if tag != "block" and not _claim_notice(
            session_id if isinstance(session_id, str) else "",
            hook_id,
            cause,
            body,
            fix,
            tag=tag,
        ):
            return ""
        return _message_format.llm_notice(_with_fix(body, fix), hook_id, tag=tag)

    return format_notice


def block_formatter(hook_id: str) -> Callable[..., str]:

    def format_block(body: str, *, fix: str) -> str:
        if not fix.strip():
            raise ValueError("block通知のfixは空文字列以外で指定する必要がある")
        return _message_format.llm_notice(_with_fix(body, fix), hook_id, tag="block")

    return format_block


def warning_formatter(hook_id: str) -> Callable[..., str]:

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
        if not _claim_notice(session_id, hook_id, cause, body, fix, tag=_WARN_TAG):
            return ""
        return _message_format.llm_notice(_with_fix(body, fix), hook_id, tag=_WARN_TAG)

    return format_warning
