"""証拠抽出の問題候補の抽出。`--bundle`が記録から失敗・警告・介入などの候補と個別の証拠を組み立てる。"""

from __future__ import annotations

import collections
import re
from typing import Any

from session_evidence_candidates_commands import _powershell_inner_args
from session_evidence_candidates_evidence import _candidate_evidence_events, _write_candidate_evidence_files
from session_evidence_candidates_failures import (
    FAILURE_CANDIDATE_KINDS,
    FAILURE_EVENT_FIELDS,
    _is_permission_denial,
    classify_failure_candidate,
    failure_candidate_exclusion,
    failure_signature,
    recalculate_failure_signature,
)
from session_evidence_candidates_hooks import (
    _HOOK_NOTICE_CANDIDATE_TAGS,
    _HOOK_NOTICE_VARIANT_LIMIT,
    _bounded_hook_variant_indexes,
    _hook_notice_candidate_exclusion,
    _hook_originated_event,
    _is_bounded_hook_group,
    _is_response_language_notice,
    _same_hook_event,
)
from session_evidence_candidates_returns import (
    _DELEGATE_COMPLETION_VALUES,
    _ESCALATION_REASON_PREFIXES,
    _RETURN_STATUS_PREFIXES,
    _SHELL_DELEGATION_MARKER,
    _delegate_notice_events,
    _delegation_record_kinds,
    _is_delegate_return,
    _is_escalation_return,
    _is_normal_delegate_return,
)
from session_evidence_candidates_users import _initial_request_locator, _initial_skill_input_locators, _user_candidate_exclusion
from session_evidence_candidates_wi import _adhoc_processing_events, _wi_candidate_events
from session_evidence_extract import _CANDIDATE_VARIABLE, _CANDIDATE_VARIABLE_PLACEHOLDER
from session_evidence_hook_notices import _normalize_candidate_kind_text, _normalize_hook_candidate_text

from agent_toolkit._atk.wi import style_diagnostics as _style_diagnostics

_BUNDLE_BODY_LENGTH = 200


_CANDIDATE_CONTEXT_FIELDS = (
    "assistant_context",
    "user_response",
    "wi_file",
    "wi_state",
    "wi_section",
    "question_line",
    "answer_line",
    "question_call_id",
    "question_index",
)
"""候補へ写す判断材料の項目。確認の質問・選択肢・回答と、記入元のWIの所在を候補一覧へ載せるために使う。"""


def _candidate_events(
    timeline: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    hook_notices: list[dict[str, Any]],
    *,
    main_record_id: str = "main",
    adhoc_processing: list[dict[str, Any]] | None = None,
    wi_events: list[dict[str, Any]] | None = None,
    definition_reads: list[dict[str, Any]] | None = None,
    delegate_notices: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """決定的に除外できる入力を省き、同種の候補を全位置付きで集約する。

    母集団はhook通知、ユーザー介入、ユーザー確認、WIの記入欄の是正、失敗したツール実行、警告、工程の返却値
    およびその場のコードによる加工、定義本文の取得とする。
    ユーザー確認（`confirmation-request`）は、提示した選択肢をそのまま選んだ確認への回答と、
    対象セッションが投入したUWI（`wi_events`のうち`submitted-uwi`）とする。確認が必要だったかは振り返りが判定するため、
    回答の形からは除外しない。WIの記入欄の是正（`wi-user-response`）は`_wi_candidate_events`が定める。
    返却値を含めるのは、本文に誤りがある委譲結果が他の事象には現れず、本文の判定前に候補集合に含まれなくなるためである。
    正常な完了だけを示し、想定外事象を持たない返却は、判定すべき本文を持たないため除外する。
    その場のコードによる加工（`adhoc-processing`）は成功した呼び出しも含み、記録ごとに1件の候補へまとめ、
    呼び出しごとの記録位置と代表入力を`calls`へ保持する。判定条件は`_is_adhoc_processing`が定める。

    同じ位置の同一hook発火は構造化されたhook通知を代表とする。それ以外は、同じ位置でも候補種別またはhookタグが異なる事象を別候補として保持する。同じ位置、候補種別およびhookタグの
    組だけを重複として除外する。`permission-denial`は`failed-tool`の一部でもあるため、同じ位置の
    `tool-failure`も保持し、許可ルールと実行失敗の双方の見直しへ対応付ける。

    候補件数の削減は、正規化した本文での集約と、恒久対策の要否が記録の構造から定まる事象の除外だけで行う。
    除外するのは、ユーザー介入ではない入力、正常な完了だけの委譲返却、検索の一致0件などの正常な否定結果、
    および起草者が保存前に処置するWI本文の表記診断の警告である。
    hookの標識を持つツール失敗と、hook通知と同じ本文の警告は、hook通知として発生源別の上限の対象にする。
    上限は発生源と区分の組ごとに適用し、フック名のツール部分ごとに最多の種類を残して、件数の少ないツールの通知も候補に残す。
    それ以外に件数上限を設けない。振り返りの契約は、候補が保持する位置の集合と
    判定表の位置の集合の一致を求めるため、位置を失う削減はこの判定と両立しない。
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    seen: set[tuple[str, int, str, str]] = set()
    excluded: collections.Counter[str] = collections.Counter()
    notices_by_locator: dict[tuple[str, int], list[dict[str, Any]]] = collections.defaultdict(list)
    for notice in hook_notices:
        if (
            notice.get("kind") == "hook-notice"
            and isinstance(notice.get("record"), str)
            and isinstance(notice.get("line"), int)
        ):
            notices_by_locator[(notice["record"], notice["line"])].append(notice)
    hook_notice_origins = [
        notice
        for notice in hook_notices
        if notice.get("kind") == "hook-notice" and notice.get("tag") in _HOOK_NOTICE_CANDIDATE_TAGS
    ]
    initial_skill_request, initial_skill_body = _initial_skill_input_locators(timeline, main_record_id=main_record_id)
    shell_records, resumed_records = _delegation_record_kinds(timeline, main_record_id=main_record_id)
    first_main_user = _initial_request_locator(timeline, main_record_id=main_record_id)
    sources = (
        ("hook-notice", (event for event in hook_notices if event.get("kind") == "hook-notice")),
        ("user-intervention", (event for event in timeline if event.get("kind") == "user")),
        ("permission-denial", (event for event in timeline if _is_permission_denial(event))),
        (
            "command-failure",
            (event for event in timeline if event.get("kind") == "failed-tool" and event.get("tool") == "CommandExecution"),
        ),
        (
            "tool-failure",
            (event for event in timeline if event.get("kind") == "failed-tool" and event.get("tool") != "CommandExecution"),
        ),
        ("warning", (event for event in warnings if event.get("kind") == "warning")),
        ("escalation", (event for event in timeline if _is_escalation_return(event))),
        (
            "delegate-return",
            iter(
                [
                    *(event for event in timeline if _is_delegate_return(event, main_record_id=main_record_id)),
                    *(delegate_notices or ()),
                ]
            ),
        ),
        ("adhoc-processing", iter(adhoc_processing or ())),
        ("definition-body-read", (event for event in definition_reads or () if event["kind"] == "definition-body-read")),
        (
            "definition-read-unresolved",
            (event for event in definition_reads or () if event["kind"] == "definition-read-unresolved"),
        ),
        ("confirmation-request", (event for event in wi_events or () if event.get("kind") == "submitted-uwi")),
        ("wi-user-response", (event for event in wi_events or () if event.get("kind") == "wi-user-response")),
    )
    for candidate_kind, events in sources:
        for event in events:
            event = dict(event)
            record = event.get("record")
            line = event.get("line")
            if not isinstance(record, str) or not isinstance(line, int):
                continue
            identity = (record, line, candidate_kind, str(event.get("tag", "")))
            if identity in seen:
                excluded["duplicate-candidate"] += 1
                continue
            seen.add(identity)
            text = event.get("text")
            normalized_text = " ".join(text.split()) if isinstance(text, str) else ""
            if candidate_kind == "warning" and any(
                _same_hook_event(candidate_kind, event, notice) for notice in notices_by_locator.get((record, line), [])
            ):
                excluded["hook-notice-represented"] += 1
                continue
            event_kind = candidate_kind
            if candidate_kind in FAILURE_CANDIDATE_KINDS:
                event["hook_notices"] = [
                    {field: notice.get(field) for field in ("text", "hook_name")}
                    for notice in notices_by_locator.get((record, line), [])
                ]
                exclusion, hook_event = classify_failure_candidate(candidate_kind, event)
                if exclusion is not None:
                    excluded[exclusion] += 1
                    continue
                if hook_event is not None:
                    event_kind, event = "hook-notice", hook_event
                    normalized_text = " ".join(str(event["text"]).split())
            if candidate_kind == "delegate-return" and _is_normal_delegate_return(
                event, shell=record in shell_records, resumed=record in resumed_records
            ):
                excluded["normal-delegate-return"] += 1
                continue
            if candidate_kind == "warning" and _style_diagnostics.is_body_style_diagnostic_warning(normalized_text):
                excluded["wi-style-diagnostic"] += 1
                continue
            if candidate_kind == "warning":
                hook_event = _hook_originated_event(candidate_kind, event, hook_notice_origins)
                if hook_event is not None:
                    event_kind, event = "hook-notice", hook_event
                    normalized_text = " ".join(str(event["text"]).split())
            if candidate_kind == "user-intervention":
                exclusion = _user_candidate_exclusion(
                    event,
                    record,
                    line,
                    first_main_user,
                    initial_skill_request=initial_skill_request,
                    initial_skill_body=initial_skill_body,
                    main_record_id=main_record_id,
                )
                if exclusion == "question-answer":
                    event_kind = "confirmation-request"
                elif exclusion is not None:
                    excluded[exclusion] += 1
                    continue
            if event_kind == "hook-notice":
                exclusion = _hook_notice_candidate_exclusion(event.get("tag"))
                if exclusion is not None:
                    excluded[exclusion] += 1
                    continue
                if _is_response_language_notice(event):
                    excluded["response-language-notice"] += 1
                    continue
            key = _candidate_key(event_kind, event, normalized_text)
            if event_kind in FAILURE_CANDIDATE_KINDS:
                event["failure_signature"], event["failure_summary"] = failure_signature(event_kind, event)
            groups.setdefault(key, []).append(event)

    selected_groups: list[tuple[tuple[str, ...], list[dict[str, Any]], int, int]] = []
    bounded_hook_groups: dict[tuple[str, ...], list[tuple[tuple[str, ...], list[dict[str, Any]]]]] = {}
    for key, events in groups.items():
        if _is_bounded_hook_group(key):
            bounded_hook_groups.setdefault((key[1], key[3]), []).append((key, events))
        else:
            selected_groups.append((key, events, len(events), 0))
    for variants in bounded_hook_groups.values():
        ranked = sorted(variants, key=lambda item: (-len(item[1]), item[0]))
        kept = _bounded_hook_variant_indexes(ranked)
        for index, (key, events) in enumerate(ranked):
            if index not in kept:
                excluded["hook-notice-detail-budget"] += len(events)
                continue
            representative = min(events, key=lambda event: (str(event["record"]), int(event["line"])))
            omitted_count = len(events) - 1
            # Counterは0の加算でもキーを生成するため、省略が無い種類では加算せず、値0の区分を除外件数へ残さない。
            if omitted_count:
                excluded["hook-notice-detail-budget"] += omitted_count
            selected_groups.append((key, [representative], len(events), omitted_count))

    candidates: list[dict[str, Any]] = []
    included_locators: list[dict[str, Any]] = []
    included_locator_keys: set[tuple[str, int]] = set()
    for index, (key, events, occurrence_count, omitted_locator_count) in enumerate(
        sorted(selected_groups, key=lambda item: item[0]),
        start=1,
    ):
        locators: list[dict[str, Any]] = [{"record": str(event["record"]), "line": int(event["line"])} for event in events]
        if key and key[0] in {"definition-body-read", "definition-read-unresolved"}:
            for event in events:
                for operation in event.get("operations", []):
                    line = operation.get("execution_line") if isinstance(operation, dict) else None
                    if isinstance(line, int):
                        locators.append({"record": str(event["record"]), "line": line})
        locators = list({(locator["record"], locator["line"]): locator for locator in locators}.values())
        locators.sort(key=lambda locator: (str(locator["record"]), int(locator["line"])))
        for locator in locators:
            locator_key = (str(locator["record"]), int(locator["line"]))
            if locator_key not in included_locator_keys:
                included_locator_keys.add(locator_key)
                included_locators.append(locator)
        candidate: dict[str, Any] = {
            "kind": "candidate",
            "candidate_id": f"c{index:04d}",
            "candidate_kind": key[0],
            "analysis_group_hint": list(
                key[1:] if key[0] in {"escalation", "hook-notice", "tool-failure"} else key[1:-1] if len(key) > 2 else key[1:]
            ),
            "event_key": list(key[1:]),
            "count": len(locators),
            "locators": locators,
        }
        text = events[0].get("text")
        if isinstance(text, str):
            candidate["text"] = text
        for field in _CANDIDATE_CONTEXT_FIELDS:
            if field in events[0]:
                candidate[field] = events[0][field]
        question_locators = [
            {"record": str(event["record"]), "line": event["question_line"]} for event in events if "question_line" in event
        ]
        if question_locators:
            candidate["question_locators"] = question_locators
        if key and key[0] in FAILURE_CANDIDATE_KINDS:
            candidate["failure_signature"] = events[0]["failure_signature"]
            candidate["failure_summary"] = events[0]["failure_summary"]
            candidate["failure_event"] = {field: events[0][field] for field in FAILURE_EVENT_FIELDS if field in events[0]}
        if _is_bounded_hook_group(key):
            candidate["occurrence_count"] = occurrence_count
            candidate["omitted_locator_count"] = omitted_locator_count
        if key and key[0] in {"adhoc-processing", "definition-body-read", "definition-read-unresolved"}:
            ordered = sorted(events, key=lambda event: (str(event["record"]), int(event["line"])))
            candidate["calls"] = [
                {"record": str(event["record"]), "line": int(event["line"]), "text": str(event.get("text", ""))}
                for event in ordered
            ]
            if key[0] in {"definition-body-read", "definition-read-unresolved"}:
                fields = (
                    "role",
                    "role_document",
                    "tool",
                    "targets",
                    "read_range",
                    "result_line",
                    "result_observation",
                    "purpose",
                    "related_operations",
                    "acquisition",
                    "operations",
                    "recorded_input",
                    "launch_check",
                )
                for row, event in zip(candidate["calls"], ordered, strict=True):
                    # `launch_check`は読込と確定した読取（definition-body-read）だけが持つ。
                    row.update({field: event[field] for field in fields if field in event})
        candidates.append(candidate)
    included_locators.sort(key=lambda locator: (locator["record"], locator["line"]))
    return [
        *candidates,
        {
            "kind": "candidate-summary",
            "count": len(candidates),
            "included_locator_count": len(included_locators),
            "included_locators": included_locators,
            "excluded": dict(sorted(excluded.items())),
        },
    ]


def _candidate_mechanism(candidate_kind: str, event: dict[str, Any]) -> str:
    """定型接頭辞の後にある原因を候補キーの追加軸として返す。"""
    raw = event.get("text")
    if not isinstance(raw, str):
        return ""
    if candidate_kind == "escalation":
        reason = next(
            (line.partition(":")[2].strip() for line in raw.splitlines() if line.startswith(_ESCALATION_REASON_PREFIXES)), ""
        )
    elif candidate_kind == "hook-notice" and event.get("tag") == "block":
        marker = re.search(r"\b(?:blocked|block):\s*", raw, flags=re.IGNORECASE)
        detail = raw[marker.end() :].strip() if marker else ""
        reason = detail.splitlines()[0].split("。", 1)[0].strip() if detail else ""
    else:
        return ""
    return _CANDIDATE_VARIABLE.sub(_CANDIDATE_VARIABLE_PLACEHOLDER, " ".join(reason.split()))


def _candidate_key(candidate_kind: str, event: dict[str, Any], normalized_text: str) -> tuple[str, ...]:
    """候補種別ごとの正規化軸を、並べ替え可能な文字列tupleで返す。

    軸には原則として正規化した本文だけを置く。診断を欠くCommandExecutionでは本文による原因の
    区別が成立しないため、構造化コマンド、さらにコマンドも無い場合は記録位置を用いる。
    """
    if candidate_kind == "hook-notice":
        tag = str(event.get("tag", ""))
        return (
            candidate_kind,
            str(event.get("hook", "")),
            "" if tag in {"block", "warn"} else str(event.get("hook_name", "")),
            tag,
            _normalize_hook_candidate_text(normalized_text),
            _candidate_mechanism(candidate_kind, event),
        )
    if candidate_kind in {"command-failure", "tool-failure"}:
        return candidate_kind, failure_signature(candidate_kind, event)[0]
    if candidate_kind == "escalation":
        return candidate_kind, _normalize_candidate_kind_text(normalized_text), _candidate_mechanism(candidate_kind, event)
    if candidate_kind in {"adhoc-processing", "definition-body-read", "definition-read-unresolved"}:
        # 記録（thread）ごとに1件へまとめ、委譲先が多い実行でも候補の件数が呼び出しの件数に比例しないようにする。
        return candidate_kind, str(event["record"])
    if candidate_kind in {"confirmation-request", "wi-user-response"}:
        # 確認ごとに要否を判定するため、同じ回答の文字列（選択肢のlabelなど）でも別の確認をまとめない。
        return candidate_kind, str(event["record"]), f"{int(event['line']):010d}", str(event.get("tag", ""))
    return candidate_kind, _normalize_candidate_kind_text(normalized_text)


__all__ = [
    "_DELEGATE_COMPLETION_VALUES",
    "_HOOK_NOTICE_VARIANT_LIMIT",
    "_RETURN_STATUS_PREFIXES",
    "_SHELL_DELEGATION_MARKER",
    "_adhoc_processing_events",
    "_candidate_evidence_events",
    "_delegate_notice_events",
    "_powershell_inner_args",
    "_wi_candidate_events",
    "_write_candidate_evidence_files",
    "failure_candidate_exclusion",
    "recalculate_failure_signature",
]
