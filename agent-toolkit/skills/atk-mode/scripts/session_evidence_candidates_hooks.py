from __future__ import annotations

from typing import Any

from agent_toolkit._common import response_language_check as _response_language_check
from session_evidence_extract import _HOOK_FAILURE_PREFIX
from session_evidence_hook_notices import _hook_notice_keys, _hook_notice_kind_text

_HOOK_NOTICE_VARIANT_LIMIT = 5


_HOOK_NOTICE_CANDIDATE_TAGS = frozenset({"block", "warn"})


_HOOK_ORIGIN_MIN_MATCH_LENGTH = 20
"""hook通知の本文どうしを前方一致で比較するときに一致を求める最小の文字数。短い定型句だけの一致で別の通知を同一視しないための下限とする。"""


def _hook_notice_candidate_exclusion(tag: Any) -> str | None:
    if tag in _HOOK_NOTICE_CANDIDATE_TAGS:
        return None
    if tag in {"info", "notice"}:
        return "hook-notice-informational"
    if tag is None:
        return "hook-notice-untagged"
    return "hook-notice-context"


def _is_response_language_notice(event: dict[str, Any]) -> bool:
    if event.get("hook") not in {"pretooluse", "response-language"} or event.get("tag") not in {"warn", "block"}:
        return False
    text = event.get("text")
    if not isinstance(text, str) or not text:
        return False
    for body in (_response_language_check.WARNING_BODY, _response_language_check.BLOCK_BODY):
        expected = _hook_notice_kind_text(body)
        length = min(len(text), len(expected))
        if text[:length] == expected[:length]:
            return True
    return False


def _hook_originated_event(
    candidate_kind: str,
    event: dict[str, Any],
    hook_notices: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """hookが出力したと記録から判定できる失敗または警告を、hook通知の候補イベントへ変換して返す。

    ツール失敗は本文が`<フック名> hook error:`で始まり、通知の標識が`block`または`warn`の区分を持つ場合に変換する。
    警告は本文が同じセッションのhook通知の本文と一致する場合に、その通知の発生源と区分で変換する。
    変換しない場合は`None`を返す。
    """
    text = event.get("text")
    if not isinstance(text, str):
        return None
    if candidate_kind == "tool-failure":
        hook_name, separator, _ = text.partition(" hook error:")
        if not separator:
            return None
        body = _HOOK_FAILURE_PREFIX.sub("", text, count=1)
        for key in _hook_notice_keys(body, hook_name.strip() or None):
            if key.hook and key.tag in _HOOK_NOTICE_CANDIDATE_TAGS:
                return {
                    **event,
                    "kind": "hook-notice",
                    "text": key.kind_text,
                    "hook": key.hook,
                    "hook_name": key.hook_name,
                    "tag": key.tag,
                }
        return None
    normalized = _hook_notice_kind_text(text)
    for notice in hook_notices:
        notice_text = str(notice.get("text", ""))
        if _same_hook_notice_text(normalized, notice_text):
            return {
                **event,
                "kind": "hook-notice",
                "text": notice_text,
                "hook": notice.get("hook"),
                "hook_name": notice.get("hook_name"),
                "tag": notice.get("tag"),
            }
    return None


def _bounded_hook_variant_indexes(ranked: list[tuple[tuple[str, ...], list[dict[str, Any]]]]) -> set[int]:
    kept: set[int] = set()
    kept_keys: list[tuple[str, ...]] = []
    seen_tools: set[str] = set()
    for index, (key, events) in enumerate(ranked):
        if any(key[5] == kept_key[5] and _same_hook_notice_text(key[4], kept_key[4]) for kept_key in kept_keys):
            continue
        tools = {str(event.get("hook_name") or "").partition(":")[2] for event in events}
        if len(kept) < _HOOK_NOTICE_VARIANT_LIMIT or not tools <= seen_tools:
            kept.add(index)
            kept_keys.append(key)
            seen_tools |= tools
    return kept


def _same_hook_notice_text(left: str, right: str) -> bool:
    length = min(len(left), len(right))
    return length >= _HOOK_ORIGIN_MIN_MATCH_LENGTH and left[:length] == right[:length]


def _same_hook_event(candidate_kind: str, event: dict[str, Any], notice: dict[str, Any]) -> bool:
    """同じ記録位置の警告または失敗が構造化hook通知から生じたかを返す。"""
    text = event.get("text")
    notice_text = notice.get("text")
    if not isinstance(text, str) or not isinstance(notice_text, str):
        return False
    if candidate_kind == "warning":
        if _same_hook_notice_text(_hook_notice_kind_text(text), notice_text):
            return True

        normalized = " ".join(text.split())
        return bool(normalized) and normalized in " ".join(notice_text.split())
    hook_name = notice.get("hook_name")
    return isinstance(hook_name, str) and bool(hook_name) and text.startswith(hook_name) and "hook error:" in text


def _is_bounded_hook_group(key: tuple[str, ...]) -> bool:
    return len(key) > 3 and key[0] == "hook-notice" and key[3] in {"block", "warn"}
