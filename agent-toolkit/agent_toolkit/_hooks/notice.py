"""Hook識別子を固定したLLM通知整形関数を生成する。"""

lazy import contextlib
lazy import hashlib
lazy import inspect
lazy import json
lazy from collections.abc import Callable, Generator, Sequence

lazy from agent_toolkit._atk.wi.formatters import NewWorkItem
lazy from agent_toolkit._common import message_format as _common_message_format
lazy from agent_toolkit._common.output_policy import rules
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


def stage_delivery(
    text: str, session_id: str, field: str, key: str, value: str, *, replace: bool = False, merge_snapshot: bool = False
) -> None:
    if text and session_id:
        receipt = {"text": text, "session_id": session_id, "field": field, "key": key, "value": value}
        if merge_snapshot:
            receipt["merge_snapshot"] = "1"
        elif replace:
            receipt["replace"] = "1"
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
            if item.get("merge_snapshot") == "1":
                updates = _snapshot_names(values) | _snapshot_names([item["value"]])
                values = [json.dumps(sorted(updates), ensure_ascii=False)]
            elif item.get("replace") == "1":
                values = [item["value"]]
            elif item["value"] not in values:
                values.append(item["value"])
            mapping[item["key"]] = values
            state[item["field"]] = mapping
            return state

        update_state(receipt["session_id"], record)
        _delivery_candidates.remove(receipt)


_WI_SNAPSHOT_FIELD = "new_wi_notice_snapshot"


def _snapshot_names(previous: object) -> set[str]:
    if not isinstance(previous, list) or not previous or not isinstance(previous[-1], str):
        return set()
    try:
        identities = json.loads(previous[-1])
    except json.JSONDecodeError:
        return set()
    return {item for item in identities if isinstance(item, str)} if isinstance(identities, list) else set()


def new_wi_preview(session_id: str, recipient: str, rows: Sequence[NewWorkItem]) -> tuple[str, str]:
    identifiers = {row.filename: row for row in rows}
    saved = read_state(session_id).get(_WI_SNAPSHOT_FIELD)
    known = _snapshot_names(saved.get(recipient)) if isinstance(saved, dict) else set()
    policy = rules()
    added = [
        row.label(omit_defaults=policy["omit_defaults"])
        for key, row in identifiers.items()
        if not policy["suppress_repeats"] or key not in known
    ]
    text = "処理可能な新着WI:"
    if policy["show_total_with_delta"]:
        text += f" 計{len(identifiers)}件"
    if added:
        text += "。今回: " + "、".join(added)
    return text + "。", json.dumps(sorted(identifiers), ensure_ascii=False)


def stage_new_wi_preview(text: str, session_id: str, recipient: str, snapshot: str, *, scoped: bool = False) -> None:
    stage_delivery(text, session_id, _WI_SNAPSHOT_FIELD, recipient, snapshot, replace=True, merge_snapshot=scoped)


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


QUALITY_RESEND_KEY = "quality_gate_resend_by_actor"


def consume_resend(  # noqa: PLR0913 -- 記録の区別に要る値をキーワードで受け取る
    session_id: str, actor: str, state_key: str, text: str, *, scope: str = "", limit: int = 16
) -> bool:
    """同じ主体が同じ本文を再発行した時に真を返す。初回は記録して偽を返す。  pyfltr: allow-comment

    遮断の解消を特定の手段（スキルの起動、資料の取得）だけに頼らず、誤検出や手段が使えない時にも進められるようにする。
    記録は通しても消さない。同じ操作に重なる品質の遮断が`QUALITY_RESEND_KEY`を共有すれば、1回の再発行で全て通る。
    """
    if not session_id:
        return False
    fingerprint = hashlib.sha256(f"{scope}\n{text}".encode()).hexdigest()
    repeated = False

    def update(state: dict) -> dict | None:
        nonlocal repeated
        prior = state.get(state_key, {})
        prior = dict(prior) if isinstance(prior, dict) else {}
        values = [value for value in prior.get(actor, []) if isinstance(value, str)]
        repeated = fingerprint in values
        if repeated:
            return None
        prior[actor] = [*values, fingerprint][-limit:]
        state[state_key] = prior
        return state

    if not update_state(session_id, update) and not repeated:
        return True
    return repeated


def forget_resends(session_id: str, actor: str, state_key: str) -> None:
    """遮断の条件が解消した時に、その主体の再発行の記録を消す。再発した時は初回として改めて止める。  pyfltr: allow-comment"""

    def update(state: dict) -> dict | None:
        prior = state.get(state_key)
        if not isinstance(prior, dict) or not prior.get(actor):
            return None
        state[state_key] = {key: value for key, value in prior.items() if key != actor}
        return state

    if session_id:
        update_state(session_id, update)


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
