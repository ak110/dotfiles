"""正式なツール結果から段階別の初回品質を集計する。欠落は合格へ補わない。"""

import collections
import datetime
import json
import re
from pathlib import Path
from typing import Any

from session_evidence_extract import _CollectedRecord, _parse_timestamp, _record_timestamp
from session_evidence_tool_calls_records import _record_tool_calls, _recorded_input, _recorded_result


def _result_text(value: Any, depth: int = 0) -> str:
    if depth > 32:
        return ""
    if isinstance(value, str):
        try:
            decoded, _ = json.JSONDecoder().raw_decode(value[value.index("{") :])
        except ValueError, RecursionError:
            return value
        return _result_text(decoded, depth + 1) or value
    if isinstance(value, list):
        return "\n".join(_result_text(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return "\n".join(
            _result_text(value[key], depth + 1) for key in ("output", "content", "text", "agent_message") if key in value
        )
    return ""


def _shown_run(value: str, depth: int = 0) -> dict[str, Any] | None:
    """公開show-runのJSONを包絡から取り出す。別のJSONを成功runへ変換しない。"""
    if depth > 32:
        return None
    try:
        decoded, _ = json.JSONDecoder().raw_decode(value[value.index("{") :])
    except ValueError, RecursionError:
        return None
    if not isinstance(decoded, dict):
        return None
    if decoded.get("kind") == "meta" and isinstance(decoded.get("run_id"), str) and isinstance(decoded.get("argv"), list):
        try:
            rows = [json.loads(line) for line in value.splitlines() if line.strip().startswith("{")]
        except ValueError, RecursionError:
            return None
        return {
            "run_id": decoded["run_id"],
            "meta": decoded,
            "commands": [row for row in rows if isinstance(row, dict) and row.get("kind") == "command"],
        }
    if (
        isinstance(decoded.get("run_id"), str)
        and isinstance(decoded.get("meta"), dict)
        and isinstance(decoded.get("commands"), list)
    ):
        return decoded
    for key in ("output", "text"):
        if isinstance(decoded.get(key), str) and (found := _shown_run(decoded[key], depth + 1)) is not None:
            return found
    return None


def _review_result(body: str) -> str:
    pointer = re.search(r"(?m)^正式原本: (.+)$", body)
    if pointer is None:
        return body
    path = Path(pointer[1].strip())
    if not path.is_absolute() or path.suffix != ".md":
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return ""


def _condition_outcome(tail: str, findings: int) -> str:
    statuses = re.findall(r"(?m)^(?:\S+ )?(?:完成条件\d+|原文要求\d+「.+」): (達成|未達|根拠不足)(?: — 根拠:.*)?$", tail)
    return "failure" if findings or "未達" in statuses else "unknown" if not statuses or "根拠不足" in statuses else "success"


def collect_quality_events(collected: list[_CollectedRecord], workflow: str) -> list[dict[str, Any]]:
    """同じ担当記録を作業単位とし、再送はrun IDまたは正式判定の本文で識別する。"""
    events: list[dict[str, Any]] = []
    for item in collected:
        pending: dict[str, str] = {}
        entries = {record.line: record.entry for record in item.records}
        calls = _record_tool_calls(item.records)
        tables = sorted(set(re.findall(r"([^\s/\\\"'`<>]+\.exec-review\.tsv)", "\n".join(call.text for call in calls))))
        cwd = next(
            (
                entry.get("cwd") or entry.get("payload", {}).get("cwd")
                for entry in entries.values()
                if isinstance(entry.get("cwd"), str)
                or isinstance(entry.get("payload"), dict)
                and isinstance(entry["payload"].get("cwd"), str)
            ),
            None,
        )
        for call in calls:
            if call.result_line is None or call.timestamp is None:
                continue
            try:
                timestamp = _parse_timestamp(call.timestamp)
            except ValueError:
                continue
            inputs = _recorded_input(entries[call.line], call.call_id)
            handle = inputs.get("session_id", inputs.get("cell_id"))
            if handle is None and (match := re.search(r"(?:session_id|cell_id)\s*:\s*[\"']?(\d+)", call.text)):
                handle = match[1]
            command = pending.get(str(handle), call.text)
            raw = _recorded_result(entries[call.result_line], call.call_id) or ""
            for handle in re.findall(r'"session_id"\s*:\s*(\d+)', raw):
                pending[handle] = command
            if match := re.search(r"Script running with cell ID (\S+)", raw):
                pending[match[1]] = command
            body = _result_text(raw)
            common: dict[str, Any] = {
                "work": item.record_id,
                "timestamp": timestamp,
                "workflow": workflow,
                "source": f"{item.record_id}:{call.result_line}",
                "environment": {"runtime": item.runtime},
                "review_tables": tables,
            }
            versions = set(re.findall(r"\b[0-9a-f]{40}\b", command))
            common["version"] = next(iter(versions)) if len(versions) == 1 else None
            if (
                "show-run" in command
                and (shown := _shown_run(_recorded_result(entries[call.result_line], call.call_id) or "")) is not None
            ):
                argv = shown["meta"].get("argv", [])
                if (
                    isinstance(argv, list)
                    and argv
                    and all(isinstance(token, str) for token in argv)
                    and argv[0] in {"run", "ci"}
                    and "--no-fix" in argv
                    and "--no-always-targets" not in argv
                    and "--only-failed" not in argv
                    and cwd is not None
                    and shown["meta"].get("cwd") == cwd
                ):
                    results = shown["commands"]
                    pytest_results = [row for row in results if isinstance(row, dict) and row.get("command") == "pytest"]
                    outcome = (
                        "unknown"
                        if not pytest_results
                        or any(
                            not isinstance(row, dict)
                            or row.get("status") not in {"succeeded", "failed", "skipped", "formatted"}
                            for row in results
                        )
                        else "success"
                        if all(row.get("status") == "succeeded" for row in pytest_results)
                        and all(row.get("status") in {"succeeded", "skipped"} for row in results)
                        and shown["meta"].get("exit_code", 0) == 0
                        else "failure"
                    )
                    events.append(
                        {
                            **common,
                            "stage": "final-verification",
                            "identity": f"run:{shown['run_id']}",
                            "outcome": outcome,
                            "environment": {
                                "cwd": shown["meta"].get("cwd"),
                                "pyfltr": shown["meta"].get("version"),
                                "python": shown["meta"].get("python"),
                                "platform": shown["meta"].get("platform"),
                            },
                        }
                    )
            if "agents wait" in command or "agents_server" in call.tool and call.tool.endswith("wait"):
                body = _review_result(body)
                pattern = (
                    r"状態: completed\nレビューHEAD: ([0-9a-f]{40})\n未解決の指摘数: ([0-9]+)\n([\s\S]*?前回確認版HEAD: [^\n]+)"
                )
                for match in re.finditer(pattern, body):
                    initial = all(
                        label in match[3]
                        for label in ("読者別探索:", "レビュー開始時点:", "前回確認版の開始時点: なし", "前回確認版HEAD: なし")
                    )
                    tail = body[match.end() :].split("本文終了:", 1)[0]
                    outcome = _condition_outcome(tail, int(match[2]))
                    events.append(
                        {
                            **common,
                            "stage": "review",
                            "version": match[1],
                            "initial": initial,
                            "outcome": outcome,
                            "findings": int(match[2]),
                            "identity": match[0],
                        }
                    )
            if "--verify-run" in command and "pyfltr_precommit" in command:
                match = re.search(
                    r"pyfltr-precommit: run (\S+)は現在のHEADの内容(でpytestに成功している|の成功と確かめられない)", body
                )
                if match:
                    events.append(
                        {
                            **common,
                            "stage": "final-verification",
                            "identity": f"run:{match[1]}",
                            "outcome": "success" if match[2].startswith("で") else "failure",
                        }
                    )
            if "wait_ci" in command or "wait-ci" in command:
                for match in re.finditer(
                    r"(?m)^([^\n:]+): completed/(success|failure|timed_out|cancelled|skipped) (https?://\S+)", body
                ):
                    outcome = (
                        "success"
                        if match[2] == "success"
                        else "failure"
                        if match[2] in {"failure", "timed_out"}
                        else "not-performed"
                    )
                    events.append(
                        {
                            **common,
                            "work": f"{item.record_id}:{match[1]}",
                            "stage": "ci",
                            "identity": match[3],
                            "outcome": outcome,
                        }
                    )
    # 結果が無い段階も表示する。記録の読取不能自体の件数は既存coverageが所有する。
    works = {event["work"] for event in events if event["stage"] != "ci"}
    if not works and collected:
        works.add(collected[0].record_id)
    timestamps = [value for item in collected for record in item.records if (value := _record_timestamp(record)) is not None]
    if timestamps:
        for work in works:
            for stage in ("review", "final-verification", "ci"):
                if not any(
                    (event["work"] == work or stage == "ci" and event["work"].startswith(work + ":"))
                    and event["stage"] == stage
                    for event in events
                ):
                    events.append(
                        {
                            "work": work,
                            "stage": stage,
                            "timestamp": min(timestamps),
                            "workflow": workflow,
                            "outcome": "unknown",
                            "source": work,
                            "identity": "missing",
                            "version": None,
                            "environment": None,
                        }
                    )
    return events


def first_pass_metrics(
    events: list[dict[str, Any]], since: datetime.datetime, until: datetime.datetime
) -> list[dict[str, Any]]:
    """段階別の分子・観測分母を返し、版変更でも初回失敗を保持する。"""
    units: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for event in events:
        units[event["work"], event["stage"]].append(event)
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (work, stage), records in sorted(units.items()):
        unique: dict[str, dict[str, Any]] = {}
        for row in sorted(records, key=lambda row: (row["timestamp"], row["source"])):
            identity = row.get("identity", row["source"])
            if identity in unique:
                unique[identity] = {**unique[identity], "latest_outcome": row["outcome"], "latest_source": row["source"]}
            else:
                unique[identity] = row
        records = list(unique.values())
        first = records[0]
        if not since <= first["timestamp"] <= until:
            continue
        day = first["timestamp"].astimezone().date()
        week = (day - datetime.timedelta(days=day.weekday())).isoformat()
        key = (week, first["workflow"], stage)
        group = groups.setdefault(
            key,
            {
                "kind": "cost-metrics-first-pass",
                "week": week,
                "workflow": key[1],
                "stage": stage,
                "since": since.isoformat(),
                "until": until.isoformat(),
                "first_success_count": 0,
                "observed_count": 0,
                "unknown_count": 0,
                "not_performed_count": 0,
                "unresolved_count": 0,
                "units": [],
            },
        )
        outcome = first["outcome"] if first.get("initial", True) else "unknown"
        group["observed_count"] += outcome in {"success", "failure"}
        group["first_success_count"] += outcome == "success"
        group["unknown_count"] += outcome == "unknown"
        group["not_performed_count"] += outcome == "not-performed"
        group["unresolved_count"] += records[-1].get("latest_outcome", records[-1]["outcome"]) != "success"
        group["units"].append(
            {
                "work": work,
                "first_outcome": outcome,
                "version": first.get("version"),
                "environment": first.get("environment"),
                "source": first["source"],
                "latest_source": records[-1].get("latest_source", records[-1]["source"]),
                "attempts": len(records),
                "repeat_count": max(0, len(records) - 1),
                "finding_count": None,
                "accepted_finding_count": None,
                "review_tables": first.get("review_tables", []),
                "scope_change_count": None,
                "unresolved_finding_observations": sum(row.get("findings", 0) for row in records)
                if stage == "review"
                else None,
                "correction_count": None,
                "elapsed_seconds": None,
            }
        )
    for group in groups.values():
        count = group["observed_count"]
        group["first_success_ratio"] = group["first_success_count"] / count if count else None
    return list(groups.values())


def quality_metrics(collected: list[_CollectedRecord], workflow: str = "unknown") -> list[dict[str, Any]]:
    """通常の証拠抽出とbundleが同じ集計を使う。"""
    events = collect_quality_events(collected, workflow)
    if not events:
        return []
    return first_pass_metrics(events, min(row["timestamp"] for row in events), max(row["timestamp"] for row in events))
