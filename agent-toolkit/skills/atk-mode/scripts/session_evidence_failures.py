"""失敗結果の固定文字列による、親子集約を行わない記録照会。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from session_evidence_candidates_failures import classify_failure_candidate
from session_evidence_extract import (
    _CollectedRecord,
    _collection_events,
    _extract_records,
    _load_records_checked,
    _parse_timestamp,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_hook_notices import _hook_notice_candidate_events
from session_evidence_stats import _parse_cli_timestamp
from session_evidence_tool_calls import _tool_call_collection_events, _tool_call_summary
from session_evidence_tool_calls_records import _recorded_input, _restored_result


def failure_calls(collected: list[_CollectedRecord], events: list[dict[str, Any]], phrase: str) -> list[dict[str, Any]]:
    """成功結果と発話の引用を除き、元の入力と結果の位置を保持する。"""
    selected = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        failures = [event for event in _extract_records(item.records) if event.get("kind") == "failed-tool"]
        notices = _hook_notice_candidate_events([item])
        for call in events:
            if call.get("kind") != "tool-call" or call.get("record") != item.record_id:
                continue
            line = call.get("result_line")
            if line not in entries:
                continue
            result = _restored_result(entries[line], call.get("call_id"))
            if result is None or phrase not in result:
                continue
            matched_failures = [
                event
                for event in failures
                if event.get("line") == line and (item.runtime != "claude" or event.get("tool") == call.get("call_id"))
            ]
            if not matched_failures and item.runtime == "codex":
                try:
                    structured = json.loads(result)
                except ValueError:
                    structured = None
                code = structured.get("exit_code") if isinstance(structured, dict) else None
                match = re.search(r"^Process exited with code (-?\d+)\s*$", result, re.MULTILINE)
                if code is None and match is not None:
                    code = int(match[1])
                if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                    inputs = _recorded_input(entries[call["line"]], call.get("call_id"))
                    command = inputs.get("cmd", inputs.get("command", ""))
                    if isinstance(structured, dict):
                        parts: list[str] = []
                        for key in ("output", "stdout", "stderr"):
                            value = structured.get(key)
                            if isinstance(value, str):
                                parts.append(value)
                        diagnostic = "\n".join(parts).strip()
                    else:
                        diagnostic = (result[: match.start()] + result[match.end() :]).strip() if match else result
                    matched_failures.append(
                        {
                            "kind": "failed-tool",
                            "line": line,
                            "tool": "CommandExecution",
                            "exit_code": code,
                            "text": diagnostic,
                            "diagnostic": diagnostic,
                            "command": json.dumps(["bash", "-c", command]),
                        }
                    )
                elif isinstance(structured, dict) and structured.get("isError") is True:
                    matched_failures.append({"kind": "failed-tool", "line": line, "tool": call["tool"], "text": result})
            for event in matched_failures:
                if event.get("line") != line:
                    continue
                kind = "command-failure" if event.get("tool") == "CommandExecution" else "tool-failure"
                event = {**event, "hook_notices": [notice for notice in notices if notice.get("line") == line]}
                exclusion, hook = classify_failure_candidate(kind, event)
                if exclusion is None and hook is None:
                    selected.append(call)
                    break
    return selected


def scan_failures(args: argparse.Namespace) -> tuple[list[dict[str, Any]], int]:
    """候補ファイルだけを解析し、指定窓内の失敗呼出しを記録別に返す。"""
    root = Path(args.scan_claude_projects or args.scan_codex_history)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("走査対象は実在する絶対ディレクトリで指定する")
    since = _parse_cli_timestamp(args.since)
    boundary = _parse_cli_timestamp(args.observation_boundary)
    if boundary < since:
        raise ValueError("観測境界は開始境界以後を指定する")
    pattern = re.compile(args.input_regex) if args.input_regex is not None else None
    output: list[dict[str, Any]] = []
    code = 0
    candidates = 0
    for path in sorted(root.rglob("*.jsonl")):
        raw = path.read_bytes()
        # エスケープと退避参照は保守的に候補へ残す。生の文言だけで除くと復元後の一致を失う。
        if args.failure_fixed_string.encode("utf-8") not in raw and b"\\" not in raw and b"persisted" not in raw:
            continue
        candidates += 1
        loaded = _load_records_checked(str(path), allow_partial_records=args.allow_partial_records)
        if loaded.records is None:
            failure = loaded.failure
            output.extend(
                _unresolved_events(
                    [
                        _UnresolvedRecord(
                            str(path),
                            0,
                            reason=failure.reason if failure else "record-read-failed",
                            failure_line=failure.line if failure else None,
                        )
                    ]
                )
            )
            code = 2
            continue
        item = _CollectedRecord(
            str(path),
            path,
            loaded.records,
            "claude" if args.scan_claude_projects else "codex",
            None,
            None,
            None,
            "main",
            (),
            loaded.recoveries,
            loaded.gaps,
        )
        calls = _tool_call_collection_events([item], [], args.tool, pattern, since)
        calls = [
            event for event in calls if event.get("kind") == "tool-call" and _parse_timestamp(event["timestamp"]) <= boundary
        ]
        selected = failure_calls([item], calls, args.failure_fixed_string)
        gaps = [event for event in _collection_events([item], []) if event["kind"] == "record-gap"]
        output.extend([{**event, **({"partial": True} if gaps else {})} for event in [*selected, *gaps]])
    output.append(
        {
            **_tool_call_summary([event for event in output if event["kind"] == "tool-call"]),
            "candidate_record_count": candidates,
            "partial": any(event.get("partial") for event in output),
        }
    )
    return output, code
