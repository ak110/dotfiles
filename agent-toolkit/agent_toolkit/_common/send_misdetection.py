"""送達本文の判定と誤検出申告の名前を共有する。 pyfltr: allow-comment"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from typing import TypedDict

lazy from agent_toolkit._common import session_record_paths, session_state
lazy from agent_toolkit._common.file_lock import locked_rotate_and_append


class Claim(TypedDict):
    check: str
    reason: str
    delivery_id: str | None


RETRY = frozenset(
    {
        "response-language",
    }
)
DISPLAYED = frozenset(
    {
        "clock-claim",
        "status-unnecessary-kind",
        "status-planned-kind",
        "uninvestigated-reason",
        "deferral-comparison",
    }
)
_WARNING = re.compile(r'<atk-auto source="send-to-user" kind="warn" check="([a-z-]+)" delivery_id="([a-f0-9]{32})">')


def parse(value: object) -> tuple[list[Claim], str | None]:
    if value is None:
        return [], None
    if not isinstance(value, list) or not value:
        return [], "misdetectionには判定名と理由の組を1件以上指定する。"
    claims: list[Claim] = []
    pairs: set[tuple[str, str | None]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"check", "reason", "delivery_id"}:
            return [], "申告はcheck・reasonと任意のdelivery_idで指定する。"
        check, reason, delivery = (item.get(key) for key in ("check", "reason", "delivery_id"))
        if not isinstance(check, str) or check not in (RETRY if delivery is None else DISPLAYED):
            return [], f"申告できる判定: 未送達={', '.join(sorted(RETRY))}、表示済み={', '.join(sorted(DISPLAYED))}。"
        if not isinstance(reason, str) or not reason.strip() or len(reason.splitlines()) != 1:
            return [], "申告のreasonには、誤検出と判断した理由を1行で書く。"
        if delivery is not None and (not isinstance(delivery, str) or not re.fullmatch(r"[a-f0-9]{32}", delivery)):
            return [], "delivery_idには警告に付いた送達識別子を指定する。"
        pair = (check, delivery)
        if pair in pairs:
            return [], "判定と送達識別子の同じ組を重ねて指定できない。"
        pairs.add(pair)
        claims.append({"check": check, "reason": reason.strip(), "delivery_id": delivery})
    if any(c["delivery_id"] is None for c in claims) and any(c["delivery_id"] is not None for c in claims):
        return [], "表示済み警告の申告と未送達本文の再送は同じ呼び出しに混ぜない。"
    return claims, None


def retry_checks(claims: list[Claim]) -> set[str]:
    return {claim["check"] for claim in claims if claim["delivery_id"] is None}


def retry_hint(check: str) -> str:
    return f" 判定名は `{check}`。"


def rejection_check(reason: str) -> str | None:
    return next((check for check in sorted(RETRY) if f"判定名は `{check}`。" in reason), None)


def _body_key(message: str) -> str:
    return hashlib.sha256(message.encode(errors="surrogatepass")).hexdigest()


_REJECTIONS = "send_misdetection_body_rejections"


class _Rejection(TypedDict):
    status: str
    sources: dict[str, str]


def remember_rejection(session_id: str, check: str, message: str, location: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return False
    key = _body_key(message)

    def update(state: dict) -> dict:
        seen: dict[str, _Rejection] = state.setdefault(_REJECTIONS, {})
        entry = seen.pop(key, None)
        if entry is None or entry["status"] not in {"pending", "reserved"}:
            entry = _Rejection(status="pending", sources={})
        entry["sources"][check] = location
        seen[key] = entry
        if len(seen) > 200:
            oldest = next((item for item, value in seen.items() if value["status"] != "reserved"), None)
            if oldest is not None:
                seen.pop(oldest)
        return state

    return session_state.update_state(session_id, update)


def rejection_sources(session_id: str, checks: set[str], message: object) -> dict[str, str]:
    if not checks or not isinstance(message, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return {}
    entry = session_state.read_state(session_id).get(_REJECTIONS, {}).get(_body_key(message), {})
    if entry.get("status") != "pending":
        return {}
    return {check: source for check, source in entry["sources"].items() if check in checks}


def warning_pair(text: str) -> tuple[str, str] | None:
    match = _WARNING.match(text)
    if match is None or match[1] not in DISPLAYED:
        return None
    return match[1], match[2]


def resolved(session_id: str) -> set[tuple[str, str]]:
    return {tuple(pair) for pair in session_state.read_state(session_id).get("send_misdetection_resolved", [])}


class RejectionStateError(RuntimeError):
    """確認済みの未送達記録を更新できなかった。 pyfltr: allow-comment"""


def take_rejections(session_id: str, checks: set[str], message: str) -> dict[str, str] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return None if checks else {}
    key = _body_key(message)
    current = session_state.read_state(session_id).get(_REJECTIONS, {}).get(key)
    if current is None:
        return None if checks else {}
    taken: dict[str, str] = {}
    evaluated = False

    def update(state: dict) -> dict | None:
        nonlocal evaluated
        evaluated = True
        entry = state.get(_REJECTIONS, {}).get(key)
        if entry is None or entry["status"] != "pending" or checks - entry["sources"].keys():
            return None
        taken.update(entry["sources"])
        entry["status"] = "reserved"
        return state

    if session_state.update_state(session_id, update):
        return taken
    if not evaluated or taken:
        raise RejectionStateError("未送達記録を更新できない。")
    return None


def finish_rejection(session_id: str, message: str, *, failed: bool) -> None:
    def update(state: dict) -> dict | None:
        entry = state.get(_REJECTIONS, {}).get(_body_key(message))
        if entry is None or entry["status"] != "reserved":
            return None
        entry["status"] = "pending" if failed else "sent"
        return state

    session_state.update_state(session_id, update)


def record(session_id: str, claims: list[Claim], delivery_id: str | None, *, sources: dict[str, str] | None = None) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return False
    handled = [(item["check"], identifier) for item in claims if (identifier := item["delivery_id"]) is not None]
    if handled:

        def update(state: dict) -> dict:
            state["send_misdetection_resolved"] = [
                *state.get("send_misdetection_resolved", []),
                *handled,
            ][-200:]
            return state

        if not session_state.update_state(session_id, update):
            return False
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    for item in claims:
        logged = json.dumps(
            {
                **item,
                "source": item["delivery_id"] or (sources or {}).get(item["check"], delivery_id),
                "phase": "displayed" if item["delivery_id"] else "retry",
                "session_id": session_id,
            },
            ensure_ascii=False,
        )
        locked_rotate_and_append(
            session_record_paths.stop_log_path(session_id),
            f"{timestamp} decision=send_misdetection claim={logged}\n",
            1_000_000,
        )
    return True


def summary(session_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return ""
    active = session_record_paths.stop_log_path(session_id)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for path in (active.with_suffix(active.suffix + ".1"), active):
        try:
            rows = path.read_text(encoding="utf-8").splitlines()
        except OSError, UnicodeError:
            continue
        for row in rows:
            _, marker, data = row.partition(" decision=send_misdetection claim=")
            if not marker:
                continue
            try:
                claim = json.loads(data)
            except ValueError:
                continue
            if isinstance(claim, dict) and isinstance(claim.get("check"), str) and claim["check"] in RETRY | DISPLAYED:
                grouped[claim["check"]].append(claim)
    if not grouped:
        return ""
    lines = [
        "",
        "## 送達本文の判定に対する誤検出の申告",
        "",
        "対象セッションの取得できた判定ログにある申告件数であり、誤検出を確認した件数ではない。多い判定から修正・撤去の要否を検討する。",
    ]
    for check, claims in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"- {check}: {len(claims)}件")
        lines.extend(f"  - {claim.get('source')}: {claim.get('reason')}" for claim in claims)
    return "\n".join(lines) + "\n"


def validate_displayed(session_id: str, claims: list[Claim]) -> str | None:
    requested = [(item["check"], identifier) for item in claims if (identifier := item["delivery_id"]) is not None]
    if not requested:
        return None
    state = session_state.read_state(session_id)
    seen = [tuple(pair) for pair in state.get("send_misdetection_warnings", [])]
    handled = {tuple(pair) for pair in state.get("send_misdetection_resolved", [])}
    candidates = [pair for pair in seen if pair not in handled]
    if any(candidates.count(pair) != 1 for pair in requested):
        listed = ", ".join(f"{check}/{identifier}" for check, identifier in candidates) or "なし"
        return f"申告の対象を一意に確定できない。未対応の候補: {listed}。"
    return None


def remember_warnings(session_id: str, warnings: list[str]) -> None:
    pairs = [pair for text in warnings if (pair := warning_pair(text)) is not None]
    if not pairs or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        return

    def update(state: dict) -> dict:
        state["send_misdetection_warnings"] = [*state.get("send_misdetection_warnings", []), *pairs][-200:]
        return state

    session_state.update_state(session_id, update)
