"""証拠抽出の`--hook-notices`照会。hookが挿入した通知を種類ごとにまとめて返す。"""

from __future__ import annotations

import collections
import re
from typing import Any, NamedTuple

from session_evidence_extract import (
    _CANDIDATE_VARIABLE,
    _CANDIDATE_VARIABLE_PLACEHOLDER,
    _HOOK_FAILURE_PREFIX,
    _HOOK_NOTICE_MARKER,
    _CollectedRecord,
    _is_hook_record,
    _json_object,
    _Record,
)

from agent_toolkit._common import message_format as _message_format

_HOOK_XML_END_MARKER = re.compile(rf"</(?:{_message_format.AUTO_ELEMENT_NAME_PATTERN})>")


_CANDIDATE_KIND_LENGTH = 80


_EXIT_CODE_PREFIX = re.compile(r"^Exit code\s+\d+\s*(?:\r?\n)+", re.IGNORECASE)


class _HookNoticeKey(NamedTuple):
    """通知の分類軸。標識を持たない通知では`hook`と`tag`が`None`になる。"""

    hook: str | None
    hook_name: str | None
    tag: str | None
    kind_text: str


def _hook_notice_events(records: list[_Record]) -> list[dict[str, Any]]:
    """hook実行の記録から通知本文だけを集計し、分類軸ごとの件数を件数降順で返す。

    母集団はhook実行の記録4種であり、`--warn`のような本文への文字列一致は用いない。
    hookの発動を伴わない本文（ソースの引用、会話中の言及）は記録の種別で除かれる。
    同一ツール呼び出しの通知は実行成功記録の標準出力と追加コンテキストの双方へ格納されるため、
    分類軸とツール呼び出し識別子の組で重複を除いてから数える。
    """
    seen: set[tuple[str | None, _HookNoticeKey]] = set()
    counts: collections.Counter[_HookNoticeKey] = collections.Counter()
    for record in records:
        for hook_record in _hook_records(record.entry):
            tool_use_id = hook_record.get("toolUseID")
            hook_name = hook_record.get("hookName")
            for body in _hook_notice_bodies(hook_record):
                for key in _hook_notice_keys(body, hook_name if isinstance(hook_name, str) else None):
                    identity = (tool_use_id if isinstance(tool_use_id, str) else None, key)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    counts[key] += 1
    events: list[dict[str, Any]] = [
        {
            "kind": "hook-notice",
            "hook": key.hook,
            "hook_name": key.hook_name,
            "tag": key.tag,
            "kind_text": key.kind_text,
            "count": count,
        }
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], tuple(str(part) for part in item[0])))
    ]
    events.append({"kind": "summary", "count": sum(counts.values())})
    return events


def _hook_notice_candidate_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """通知の集計前の位置を、一次選別候補として重複なく返す。"""
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, _HookNoticeKey]] = set()
    for item in collected:
        for record in item.records:
            for hook_record in _hook_records(record.entry):
                tool_use_id = hook_record.get("toolUseID")
                normalized_id = tool_use_id if isinstance(tool_use_id, str) else None
                hook_name = hook_record.get("hookName")
                for body in _hook_notice_bodies(hook_record):
                    for key in _hook_notice_keys(body, hook_name if isinstance(hook_name, str) else None):
                        identity = (item.record_id, normalized_id, key)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        events.append(
                            {
                                "kind": "hook-notice",
                                "record": item.record_id,
                                "line": record.line,
                                "text": key.kind_text,
                                "hook": key.hook,
                                "hook_name": key.hook_name,
                                "tag": key.tag,
                            }
                        )
    return events


def _hook_records(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """エントリを再帰的にたどり、hook実行の記録を出現順に集める。

    記録の格納先は`attachment`配下などruntimeの版で変わるため、位置ではなく`type`で判定する。
    """
    found: list[dict[str, Any]] = []
    _collect_hook_records(entry, found)
    return found


def _collect_hook_records(value: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if _is_hook_record(value):
            found.append(value)
        for item in value.values():
            _collect_hook_records(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_hook_records(item, found)


def _hook_notice_bodies(hook_record: dict[str, Any]) -> list[str]:
    """hook実行の記録から通知本文を、記録の種別に応じた格納先から取り出す。

    実行成功記録は標準出力の追加コンテキストと標準エラー出力の双方を通知の格納先とする。
    追加コンテキストを伴わずに標準エラー出力だけで警告を返すhookがあるため、両方を対象とする。
    """
    record_type = hook_record.get("type")
    content = hook_record.get("content")
    if record_type == "hook_additional_context":
        return [item for item in content if isinstance(item, str)] if isinstance(content, list) else []
    if record_type == "hook_system_message":
        return [content] if isinstance(content, str) else []
    if record_type == "hook_blocking_error":
        blocking_error = hook_record.get("blockingError")
        body = blocking_error.get("blockingError") if isinstance(blocking_error, dict) else None
        return [body] if isinstance(body, str) else []
    bodies: list[str] = []
    stdout = _json_object(hook_record.get("stdout"))
    specific_output = stdout.get("hookSpecificOutput") if stdout is not None else None
    additional_context = specific_output.get("additionalContext") if isinstance(specific_output, dict) else None
    if isinstance(additional_context, str):
        bodies.append(additional_context)
    stderr = hook_record.get("stderr")
    if isinstance(stderr, str):
        bodies.append(stderr)
    return bodies


def _hook_notice_keys(body: str, hook_name: str | None) -> list[_HookNoticeKey]:
    """外側の通知境界ごとに発動元、重要度および本文を返す。"""
    if not body.strip():
        return []
    openings = list(_HOOK_NOTICE_MARKER.finditer(body))
    if not openings:
        return [_HookNoticeKey(None, hook_name, None, _hook_notice_kind_text(body))]
    boundaries = sorted(
        [*openings, *_HOOK_XML_END_MARKER.finditer(body)],
        key=lambda marker: marker.start(),
    )
    keys: list[_HookNoticeKey] = []
    outer: re.Match[str] | None = None
    depth = 0

    def append_notice(marker: re.Match[str], end: int) -> None:
        source = marker.group("hook_xml") or marker.group("hook_legacy")
        if source is not None:
            source = source.removeprefix("agent-toolkit/")
        tag = marker.group("tag_xml") or marker.group("tag_legacy")
        keys.append(_HookNoticeKey(source or None, hook_name, tag or None, _hook_notice_kind_text(body[marker.end() : end])))

    for marker in boundaries:
        if marker.re is _HOOK_XML_END_MARKER:
            if depth:
                depth -= 1
                if not depth and outer is not None:
                    append_notice(outer, marker.start())
                    outer = None
            continue
        if depth:
            if marker.group("hook_xml") is not None:
                depth += 1
            continue
        if outer is not None:
            append_notice(outer, marker.start())
        outer = marker
        depth = 1 if marker.group("hook_xml") is not None else 0
    if outer is not None:
        append_notice(outer, len(body))
    return keys


def _hook_notice_kind_text(text: str) -> str:
    """hook通知の本文を、通知の種類を表す種類本文へ正規化する。

    種類本文は反復注記の除去、可変部の置換および切り詰めを経た損失のある値であり、通知の事象は元の本文を保持しない。
    通知の種類本文の生成と、警告行などの別の本文を通知と比べる全ての箇所がこの関数を通すことで、
    正規化を変えても比較の両側へ同時に届く。片側だけ別の正規化で比べると、同じ通知を別の事象として数える。
    """
    return _normalize_candidate_kind_text(_HOOK_REPEAT_ANNOTATION.sub("", text))


def _normalize_candidate_kind_text(text: str) -> str:
    """本文を、可変部を置換した先頭一定長の種別テキストへ正規化する。

    可変部を残すと同じ原因の事象が複数の候補へ分かれ、長さが不足すると別原因の事象が
    同一候補へ統合されるため、長さは実際の記録を確かめて決める。
    """
    without_hook_prefix = _HOOK_FAILURE_PREFIX.sub("", text, count=1)
    without_common_prefix = _EXIT_CODE_PREFIX.sub("", without_hook_prefix, count=1)
    normalized = _CANDIDATE_VARIABLE.sub(_CANDIDATE_VARIABLE_PLACEHOLDER, " ".join(without_common_prefix.split()))
    return normalized[:_CANDIDATE_KIND_LENGTH]


def _normalize_hook_candidate_text(text: str) -> str:
    """hook通知の定型標識を除いた是正本文を候補種別へ正規化する。"""
    match = _HOOK_NOTICE_MARKER.search(text)
    if match is None:
        return text[:_CANDIDATE_KIND_LENGTH]
    return _normalize_candidate_kind_text(text[match.end() :].strip())


_HOOK_REPEAT_ANNOTATION = re.compile(r"この通知は同一セッションで\d+件目である。[^\n]*")
"""hookの通知基盤が2件目以降の通知へ付ける反復注記。件数は原因を区別しないため、種類の本文から除く。"""
