"""証拠抽出の問題候補（session_evidence_candidates_evidence）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from session_evidence_detail import _clip_structure, _entry_detail_events, _entry_timestamp
from session_evidence_extract import _CollectedRecord, _DetailBudget, _result_call_ids
from session_evidence_hook_notices import _hook_records

_CANDIDATE_EVIDENCE_LENGTH = 2000


UNTRUNCATED_EVIDENCE_KINDS = frozenset(
    {
        "user-intervention",
        "confirmation-request",
        "wi-user-response",
        "command-failure",
        "tool-failure",
        "delegate-return",
        "escalation",
        "post-delivery-long-text",
    }
)
"""個別証拠の本文を切り詰めない候補種別。

これらの本文は振り返りの分析主体が原因を判断するための一次資料であり、切り詰めると
裏取りのために元記録を読み直す工程が生じる。hook通知など定型本文の種別だけに上限を残す。
"""


_TOOL_USE_EVIDENCE_KINDS = frozenset({"hook-notice", "command-failure", "tool-failure", "permission-denial"})
"""個別証拠へ対象のツール呼び出しの入力を加える候補種別。"""


_CANDIDATE_EVIDENCE_DIRNAME = "candidate-evidence"
"""候補ごとの証拠を保存するbundle直下のディレクトリ名。"""


def _write_candidate_evidence_files(directory: Path, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """候補ごとの証拠を個別ファイルへ保存し、索引の行を返す。

    完全分析は候補単位で行うため、証拠の保存単位も候補単位とする。
    1ファイルへ集約すると、消費側は全候補の長文証拠を読み込むか、
    出力上限に達した後で範囲を指定して取得し直すことになる。
    索引は候補ID、証拠件数、個別ファイルの相対パスおよび総文字数を持ち、
    一次選別で除外した候補の本文を読まずに完全分析の対象を選べるようにする。
    """
    evidence_dir = directory / _CANDIDATE_EVIDENCE_DIRNAME
    evidence_dir.mkdir(exist_ok=True)
    index: list[dict[str, Any]] = []
    for item in evidence:
        candidate_id = str(item["candidate_id"])
        body = json.dumps(item, ensure_ascii=False)
        (evidence_dir / f"{candidate_id}.json").write_text(f"{body}\n", encoding="utf-8")
        index.append(
            {
                "kind": "candidate-evidence-index",
                "candidate_id": candidate_id,
                "analysis_group_hint": item["analysis_group_hint"],
                "locators": item["locators"],
                "evidence_count": len(item["events"]),
                "path": f"{_CANDIDATE_EVIDENCE_DIRNAME}/{candidate_id}.json",
                "total_chars": len(body),
            }
        )
    return index


def _candidate_evidence_events(
    collected: list[_CollectedRecord],
    candidates: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    hook_notices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """候補ごとに完全分析へ必要な位置付き証拠を、上限で切り詰めた本文とともにまとめる。"""
    raw_entries = {(record.record_id, entry.line): entry.entry for record in collected for entry in record.records}
    indexed: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for event in (*timeline, *warnings, *hook_notices):
        record, line = event.get("record"), event.get("line")
        if isinstance(record, str) and isinstance(line, int):
            indexed.setdefault((record, line), []).append(event)
    tool_uses = _tool_use_index(collected)
    evidence: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("kind") != "candidate":
            continue
        untruncated = candidate["candidate_kind"] in UNTRUNCATED_EVIDENCE_KINDS
        text_limit = sys.maxsize if untruncated else _CANDIDATE_EVIDENCE_LENGTH
        budget = _DetailBudget(text_limit)
        details: list[dict[str, Any]] = []
        source_chars = 0
        evidence_locators = list(candidate["locators"])
        if candidate["candidate_kind"] == "post-delivery-long-text":
            evidence_locators.append({"record": candidate["locators"][0]["record"], "line": candidate["delivery_line"]})
        for locator in evidence_locators:
            key = (str(locator["record"]), int(locator["line"]))
            raw_entry = raw_entries.get(key)
            if raw_entry is not None:
                source_chars += len(json.dumps(raw_entry, ensure_ascii=False))
                details.extend(
                    _clip_structure({"record": key[0], **event}, budget)
                    for event in _entry_detail_events(key[1], raw_entry, limit=text_limit)
                )
                if candidate["candidate_kind"] in _TOOL_USE_EVIDENCE_KINDS:
                    for call_id in _evidence_call_ids(raw_entry):
                        tool_use = tool_uses.get((key[0], call_id))
                        if tool_use is not None:
                            details.append(_clip_structure({"record": key[0], **tool_use}, budget))
            else:
                for event in indexed.get(key, ()):  # 同一位置の別走査結果も保持する。
                    detail = {"kind": str(event.get("kind", "")), "record": key[0], "line": key[1]}
                    for field in ("tool", "hook", "hook_name", "tag"):
                        if field in event:
                            detail[field] = event[field]
                    if isinstance(event.get("text"), str):
                        source_chars += len(event["text"])
                        detail["text"] = budget.clip(event["text"])
                    details.append(detail)
        if not details:
            details.append(
                {
                    "kind": str(candidate["candidate_kind"]),
                    "record": str(candidate["locators"][0]["record"]),
                    "line": int(candidate["locators"][0]["line"]),
                    "text": budget.clip(str(candidate.get("text", ""))),
                }
            )
        item = {
            "kind": "candidate-evidence",
            "candidate_id": candidate["candidate_id"],
            "analysis_group_hint": candidate["analysis_group_hint"],
            "locators": candidate["locators"],
            "source_chars": source_chars,
            "text_limit": None if untruncated else _CANDIDATE_EVIDENCE_LENGTH,
            "events": details,
        }
        if candidate["candidate_kind"] in {"definition-body-read", "definition-read-unresolved"}:
            item["definition_reads"] = candidate["calls"]
        if budget.omitted:
            item["omitted"] = True
        evidence.append(item)
    return evidence


def _tool_use_index(collected: list[_CollectedRecord]) -> dict[tuple[str, str], dict[str, Any]]:
    """記録ごとに、呼び出し識別子からツール呼び出しの入力と記録位置を取得できる索引を作成する。

    hook通知と失敗したツール結果は呼び出し識別子だけを持ち、対象のコマンドは別の行にある。
    Claude Codeは`tool_use`ブロックの`id`、Codexは`function_call`等の`call_id`で対応付ける。
    """
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in collected:
        for record in item.records:
            message = record.entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                        index[(item.record_id, block["id"])] = {
                            "kind": "tool-use",
                            "line": record.line,
                            "timestamp": _entry_timestamp(record.entry),
                            "name": str(block.get("name", "")),
                            "input": block.get("input"),
                        }
            payload = record.entry.get("payload")
            if (
                isinstance(payload, dict)
                and payload.get("type") in {"function_call", "custom_tool_call"}
                and isinstance(payload.get("call_id"), str)
            ):
                index[(item.record_id, payload["call_id"])] = {
                    "kind": "tool-use",
                    "line": record.line,
                    "timestamp": _entry_timestamp(record.entry),
                    "name": str(payload.get("name", "")),
                    "input": payload.get("arguments", payload.get("input")),
                }
    return index


def _evidence_call_ids(entry: dict[str, Any]) -> list[str]:
    """候補の記録行が参照する呼び出し識別子を出現順に重複なく返す。"""
    call_ids: list[str] = []
    for hook_record in _hook_records(entry):
        tool_use_id = hook_record.get("toolUseID")
        if isinstance(tool_use_id, str) and tool_use_id not in call_ids:
            call_ids.append(tool_use_id)
    for call_id in sorted(_result_call_ids(entry)):
        if call_id not in call_ids:
            call_ids.append(call_id)
    return call_ids
