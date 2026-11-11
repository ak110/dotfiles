"""証拠抽出の問題候補（session_evidence_candidates_hooks）。"""

from __future__ import annotations

from typing import Any

from session_evidence_extract import _HOOK_FAILURE_PREFIX
from session_evidence_hook_notices import _hook_notice_keys, _hook_notice_kind_text

from agent_toolkit._common import response_language_check as _response_language_check

_HOOK_NOTICE_VARIANT_LIMIT = 5


_HOOK_NOTICE_CANDIDATE_TAGS = frozenset({"block", "warn"})


_HOOK_ORIGIN_MIN_MATCH_LENGTH = 20
"""hook通知の本文どうしを前方一致で比較するときに一致を求める最小の文字数。短い定型句だけの一致で別の通知を同一視しないための下限とする。"""


def _hook_notice_candidate_exclusion(tag: Any) -> str | None:
    """是正を求めない区分のhook通知を問題候補から除く場合に、除外の種別名を返す。

    この判定は、是正を求める通知が`block`または`warn`の区分を必ず持つという前提に基づく。
    残す区分を列挙するのは、規範の注入や配送の種別のように是正の要否と無関係な値を`kind`へ持つ出力が
    今後追加されても、それを是正要求として扱わないためである。除く区分を列挙すると、列挙に無い新しい値が是正要求として残る。
    区分を持たない通知は、区分を示す必要が無い通知として発行されるため情報提示と同じ扱いとする。
    """
    if tag in _HOOK_NOTICE_CANDIDATE_TAGS:
        return None
    if tag in {"info", "notice"}:
        return "hook-notice-informational"
    if tag is None:
        return "hook-notice-untagged"
    return "hook-notice-context"


def _is_response_language_notice(event: dict[str, Any]) -> bool:
    """応答言語hookの警告を表すhook通知かを返す。

    応答言語hookは直前の応答を検出後に通知するだけの遮断後に対処する型であり、
    振り返りのたびに同じ見送り判定になるため、問題候補から除いて件数だけを数える。
    同じ`pretooluse`の`warn`通知には他の判定処理が返す通知も含まれるため、発生源と区分に加えて本文の先頭を
    hook側の本文定数と比べる。定数を参照するのは、hook側の文言を変えたときに判定を追随させるためである。
    """
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
    """発生源と区分が同じhook通知の種類のうち、候補に残す種類の順位を返す。

    発生件数の多い順に上限数まで残す。同じ通知の2件目以降は1件目の本文の要約で届き、別の種類になるため、
    残した種類と遮断理由が同じで本文が前方一致する種類は、同じ通知として上限を消費させずに省く。
    加えて、フック名のツール部分ごとに最多の種類を1件ずつ残し、件数の少ないツールの通知も
    他のツールの多数の通知とともに候補に残す。
    """
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
    """正規化したhook通知本文の一方が他方の前方部分であるかを返す。"""
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
        # 前方一致の最小一致長に満たない短い警告は、通知の種類本文に含まれるかで代表を判定する。
        normalized = " ".join(text.split())
        return bool(normalized) and normalized in " ".join(notice_text.split())
    hook_name = notice.get("hook_name")
    return isinstance(hook_name, str) and bool(hook_name) and text.startswith(hook_name) and "hook error:" in text


def _is_bounded_hook_group(key: tuple[str, ...]) -> bool:
    """発生源ごとに件数上位の種類だけへ限る処理を適用する候補キーかを返す。

    対象はblockまたはwarnのhook通知とする。他の種別のキーは軸の数が異なるため、
    タグの位置を参照する前に種別と軸の数を確認する。
    """
    return len(key) > 3 and key[0] == "hook-notice" and key[3] in {"block", "warn"}
