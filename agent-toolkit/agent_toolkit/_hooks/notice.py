"""Hook識別子を固定したLLM通知整形関数を生成する。"""

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import contextlib
lazy import hashlib
lazy import inspect
lazy import json
lazy from collections.abc import Callable, Generator

lazy from agent_toolkit._common import next_action as _next_action
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._hooks import message_format as _message_format

_WARN_TAG = "warn"
_warning_context: dict[str, object] = {"session_id": "", "blocks": []}
_delivery_candidates: list[dict[str, str]] = []
_output_context = {"deferred": 0}


def begin_delivery() -> None:
    """最上位の呼出しで、前回の未採用候補を破棄する。"""
    if not _output_context["deferred"]:
        _delivery_candidates.clear()


def stage_delivery(text: str, session_id: str, field: str, key: str, value: str) -> None:
    """最終出力へ採用されたときに既存の集合へ追加する記録を予約する。"""
    if text and session_id:
        receipt = {"text": text, "session_id": session_id, "field": field, "key": key, "value": value}
        if receipt not in _delivery_candidates:
            _delivery_candidates.append(receipt)


def delivery_receipts(text: str) -> list[dict[str, str]]:
    """保留本文の配送識別を返す。保留自体は配送と扱わない。"""
    return [dict(item) for item in _delivery_candidates if item["text"] in text]


def restore_delivery_receipts(receipts: object) -> None:
    """既存の保留状態から配送識別を復元する。"""
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
    """ホスト変換中の内部出力を配送済みにしない。"""
    _output_context["deferred"] += 1
    try:
        yield
    finally:
        _output_context["deferred"] -= 1


def record_delivery(text: str) -> None:
    """ホストへ実出力した本文に含まれる通知だけを記録する。"""
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
    """各ホストがエージェントへ渡すフィールドだけを連結する。"""
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
    """単一の応答を出力してから、採用した通知を記録する。"""
    print(json.dumps(response, ensure_ascii=False))
    # 出力後の記録障害を再送すると単一JSONの契約を破る。未記録の通知は次回再通知できる。
    with contextlib.suppress(Exception):
        record_delivery(output_text(response))


def resolve_notice(session_id: str, hook_id: str, cause: str, *, subject: str | None = None) -> None:
    """解消を確認した原因だけを解除する。主体指定時はその主体の全操作範囲へ適用する。"""
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


def set_warning_session_id(session_id: str) -> None:
    """現在のhook payloadのセッションIDをwarn通知を整形する処理へ渡す。"""
    _warning_context["session_id"] = session_id
    if not _warning_context.get("cycle"):
        _warning_context["blocks"] = []


@contextlib.contextmanager
def notice_cycle(session_id: str, scope: str, subject: str) -> Generator[None]:
    """通知の主体と操作範囲を選ぶ。未判定の原因の記録は保持する。"""
    previous = dict(_warning_context)
    prefix = f"{scope}|{subject}|"
    _warning_context.update(session_id=session_id, cycle=prefix, blocks=[])

    try:
        yield
    finally:
        _warning_context.clear()
        _warning_context.update(previous)


def _claim_notice(session_id: str, hook_id: str, cause: str, body: str, fix: str | None, *, tag: str = "") -> bool:
    """原因・対象・対処が同じ通知を、条件が続く間は一度だけ届ける。"""
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
        if not _claim_notice(session_id, hook_id, cause, body, fix, tag=_WARN_TAG):
            return ""
        return _message_format.llm_notice(_next_action.with_next_action(body, fix), hook_id, tag=_WARN_TAG)

    return format_warning
