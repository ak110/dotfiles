"""保存済みの実行レビュー指摘管理表から、指摘の採用・不採用を期間ごとに集計する。

振り返りのbatchが、不採用の多いレビュー担当のモデルやlevel、同じ理由の不採用の反復を改善候補にするための入力を返す。
表の読込と期間の割当は`cost_metrics.saved_review_tables`を共有する。
レビュー担当のモデルは、表のファイル名を本文に含むセッション記録から、役割の配送標識か組み込み委譲の記録を使って求める。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import re
import shlex
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

from agent_toolkit._atk import review_table_format
from agent_toolkit._common import host_homes, private_notes, session_record_paths
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.runtime_identity import distinct_identities
from cost_metrics import _history, saved_review_tables
from session_evidence_execution import command_execution_input
from session_evidence_stats import _parse_cli_timestamp

_ROLE_MARKER = re.compile(r"出所: \S*(?:share|skills/atk-mode/roles)/exec-review\.subagent\.md")
# Codexの記録で、モデルの出力として現れる`response_item`の種類。これより前の入力だけを依頼文の候補にする。
_CODEX_OUTPUT_TYPES = frozenset({"reasoning", "function_call", "custom_tool_call", "local_shell_call", "web_search_call"})
_CODEX_TOOL_TYPES = frozenset({"function_call", "custom_tool_call", "local_shell_call", "web_search_call"})
_ROLE_DOCUMENT = re.compile(r"(?<![\w.-])roles/exec-review\.subagent\.md(?![\w.-])")
_ASSIGNED = re.compile(r'<atk-auto source="agents-server" kind="delivery">\s*(.*?)</atk-auto>', re.DOTALL)
_REVIEW_PATH = re.compile(r'((?:[A-Za-z]:)?[/\\][^\s"\x60]+?)(\.md|\.exec-review\.tsv)(?=\s|$|["\x60])')
_ADDED = re.compile(r"^成功: 指摘行を[1-9][0-9]*件追加した: (.+\.exec-review\.tsv)（[0-9]+件）$", re.MULTILINE)
_RESULT_TABLE = re.compile(r"^(?:- )?レビュー指摘管理表: (.+?\.exec-review\.tsv)`?(?:（[^\n]*）)?$", re.MULTILINE)
_RESULT_ORIGINAL = re.compile(r"^(?:- )?正式原本: ([^\n]+)$", re.MULTILINE)
_MODEL_UNKNOWN = "モデル不明"
_LOCATION, _LEVEL, _NO_RESPONSE = 2, 4, 6


def _input_texts(entry: dict[str, Any]) -> list[str] | None:
    message = entry.get("message")
    payload = entry.get("payload")
    content: Any = None
    if entry.get("type") == "assistant":
        return None
    if entry.get("type") == "user" and isinstance(message, dict):
        content = message.get("content")
    elif entry.get("type") == "response_item" and isinstance(payload, dict):
        if payload.get("type") in _CODEX_OUTPUT_TYPES:
            return None
        if payload.get("type") == "message":
            if payload.get("role") == "assistant":
                return None
            if payload.get("role") == "user":
                content = payload.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
    return []


class _RecordRole(NamedTuple):
    thread: str | None = None
    marked: bool = False
    review_parent: str | None = None
    requested_tables: tuple[str, ...] = ()


def _requested_tables(texts: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                plan.replace("\\", "/") + ".exec-review.tsv"
                for text in texts
                for match in _ASSIGNED.finditer(text)
                for prompt in (match[1],)
                if "独立実行レビュー" in prompt.partition("\n")[0]
                for plan, _extension in _REVIEW_PATH.findall(prompt)
            }
        )
    )


def _meta(payload: object) -> tuple[str | None, str | None]:
    """Codex記録のスレッドIDと、組み込み委譲による親IDを得る。"""
    if not isinstance(payload, dict):
        return None, None
    thread = payload.get("id")
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    return thread if isinstance(thread, str) and thread else None, parent if isinstance(parent, str) and parent else None


def _calls_role_document(payload: dict[str, Any]) -> bool:
    if payload["type"] == "local_shell_call":
        action = payload.get("action")
        command = action.get("command") if isinstance(action, dict) else None
        return isinstance(command, list) and any(isinstance(arg, str) and _ROLE_DOCUMENT.search(arg) for arg in command)
    command = payload.get("arguments") if payload["type"] == "function_call" else payload.get("input")
    return isinstance(command, str) and _ROLE_DOCUMENT.search(command) is not None


def _record_role(path: Path) -> _RecordRole:
    thread = parent = None
    before_output = True
    requested: set[str] = set()
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "session_meta" and before_output and thread is None:
                    thread, parent = _meta(entry.get("payload"))
                if before_output:
                    texts = _input_texts(entry)
                    if texts is None:
                        before_output = False
                        if parent is None:
                            break
                    elif any(_ROLE_MARKER.search(text) for text in texts):
                        return _RecordRole(thread, marked=True)
                    else:
                        requested.update(_requested_tables(texts))
                payload = entry.get("payload")
                if (
                    parent
                    and entry.get("type") == "response_item"
                    and isinstance(payload, dict)
                    and payload.get("type") in _CODEX_TOOL_TYPES
                ):
                    return _RecordRole(
                        thread,
                        review_parent=parent if _calls_role_document(payload) else None,
                        requested_tables=tuple(sorted(requested)),
                    )
    except OSError:
        return _RecordRole()
    return _RecordRole(thread, requested_tables=tuple(sorted(requested)))


def _review_records(roots: list[Path]) -> dict[Path, tuple[str, ...] | None]:
    roles: dict[Path, _RecordRole] = {}
    by_thread: dict[str, set[_RecordRole]] = collections.defaultdict(set)
    for root in roots:
        for path in sorted(root.rglob("*.jsonl")):
            if path in roles or not path.is_file():
                continue
            role = roles[path] = _record_role(path)
            if role.thread:
                by_thread[role.thread].add(role)

    result: dict[Path, tuple[str, ...] | None] = {}
    for path, role in roles.items():
        if role.marked or (
            role.review_parent != role.thread
            and (parents := by_thread.get(role.review_parent or ""))
            and not any(parent.marked or parent.review_parent for parent in parents)
        ):
            result[path] = None
        elif role.requested_tables:
            result[path] = role.requested_tables
    return result


def _successful_output(value: object) -> str:
    items = value if isinstance(value, list) else [value]
    for item in items:
        raw = item.get("text") if isinstance(item, dict) else item
        try:
            result = json.loads(raw) if isinstance(raw, str) else None
        except ValueError, RecursionError:
            continue
        if not isinstance(result, dict):
            continue
        code = result.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool) and code == 0 and isinstance(result.get("output"), str):
            return result["output"]
    return ""


def _formal_result(text: str) -> bool:
    return (
        "状態: completed" in text.splitlines()
        and re.search(r"^レビューHEAD: [a-f0-9]{7,40}$", text, re.MULTILINE) is not None
        and re.search(r"^未解決の指摘数: [0-9]+$", text, re.MULTILINE) is not None
    )


def _reported_paths(pattern: re.Pattern[str], body: str) -> set[str]:
    return {path.strip().strip("`").replace("\\", "/") for path in pattern.findall(body)}


def _read_original_name(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if tokens[:2] == ["cat", "--"]:
        tokens.pop(1)
    if len(tokens) == 2 and tokens[0] == "cat" and (Path(tokens[1]).is_absolute() or PureWindowsPath(tokens[1]).is_absolute()):
        return tokens[1].replace("\\", "/")
    return None


def _confirmed_free_review_tables(text: str, requested: set[str]) -> set[str]:
    calls: dict[str, str] = {}
    registered: set[str] = set()
    completed: set[str] = set()
    inspected: dict[str, set[str]] = collections.defaultdict(set)
    for line in split_lines(text):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = entry.get("payload") if isinstance(entry, dict) and entry.get("type") == "event_msg" else None
        command_record = event.get("item") if isinstance(event, dict) and event.get("type") == "item_completed" else None
        if isinstance(command_record, dict) and command_record.get("type") == "CommandExecution":
            original = _read_original_name(command_execution_input(command_record.get("command", "")))
            output = command_record.get("aggregated_output")
            if (
                original is not None
                and command_record.get("exit_code") == 0
                and isinstance(output, str)
                and _formal_result(output)
            ):
                inspected[original].update(_reported_paths(_RESULT_TABLE, output) & requested & registered)
            continue
        payload = entry.get("payload") if isinstance(entry, dict) and entry.get("type") == "response_item" else None
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "message" and payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
            content = payload.get("content")
            if not isinstance(content, list):
                continue
            message = "\n".join(
                block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
            message = "\n".join(line.rstrip() for line in split_lines(message))
            if _formal_result(message):
                completed.update(_reported_paths(_RESULT_TABLE, message) & requested & registered)
            if "状態: completed" in split_lines(message):
                for path in _reported_paths(_RESULT_ORIGINAL, message):
                    completed.update(inspected.get(path, set()) & registered)
            continue
        if not isinstance(call := payload.get("call_id"), str):
            continue
        if kind in _CODEX_TOOL_TYPES:
            command = payload.get("input", payload.get("arguments"))
            if isinstance(command, str):
                calls[call] = command
        elif kind in {"custom_tool_call_output", "function_call_output"} and call in calls:
            command = calls.pop(call)
            output = _successful_output(payload.get("output"))
            if re.search(r"\breview-table add\b[^\n]*\s--track(?:\s+|=)exec-review(?:\s|$)", command):
                registered.update(path.replace("\\", "/") for path in _ADDED.findall(output))
            if _formal_result(output):
                reported = _reported_paths(_RESULT_TABLE, output + "\n" + command) & requested & registered
                originals = _reported_paths(_RESULT_ORIGINAL, output + "\n" + command)
                if original := _read_original_name(command):
                    originals.add(original)
                for original in originals:
                    inspected[original].update(reported)
    return {path.rsplit("/", 1)[-1] for path in completed}


def _record_identity(path: Path, text: str) -> str:
    runtime = "codex" if "/.codex/" in path.as_posix() or path.name.startswith("rollout-") else "claude"
    entries = []
    for line in split_lines(text):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    identities = distinct_identities(entries, runtime)
    return ",".join(identity.machine for identity in identities) or _MODEL_UNKNOWN


def _table_models(names: list[str], roots: list[Path]) -> dict[str, str]:
    if not names:
        return {}
    models: dict[str, set[str]] = collections.defaultdict(set)
    names_set = set(names)
    lengths = {len(name) for name in names_set}
    table_suffix = re.compile(r"\.exec-review\.tsv")
    for record, requested in _review_records(roots).items():
        try:
            text = record.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        matched = (
            _confirmed_free_review_tables(text, {path for path in requested if path.rsplit("/", 1)[-1] in names_set})
            if requested is not None
            else {
                text[end - length : end]
                for suffix in table_suffix.finditer(text)
                for end in (suffix.end(),)
                for length in lengths
                if length <= end and text[end - length : end] in names_set
            }
        )
        if not matched:
            continue
        identity = _record_identity(record, text)
        for name in matched:
            models[name].add(identity)
    return {name: ",".join(sorted(values)) for name, values in models.items()}


def _empty_counts() -> dict[str, int]:
    return {"adopted": 0, "rejected": 0, "transferred": 0, "unanswered": 0}


def _period_events(
    period: str, since: datetime.datetime, until: datetime.datetime, tables: list[Any], models: dict[str, str]
) -> list[dict[str, Any]]:
    summary: dict[str, Any] = {
        "kind": "review-adoption-period",
        "period": period,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "table_count": len(tables),
        "excluded_judgment_count": 0,
        "excluded_judgments": [],
        "unknown_model_tables": [],
        **_empty_counts(),
        "by_level": collections.defaultdict(_empty_counts),
        "by_model": collections.defaultdict(lambda: {**_empty_counts(), "tables": 0}),
        "by_reason": collections.Counter(),
    }
    rejections: list[dict[str, Any]] = []
    for table in tables:
        model = models.get(table.path.name, _MODEL_UNKNOWN)
        summary["by_model"][model]["tables"] += 1
        if model == _MODEL_UNKNOWN:
            summary["unknown_model_tables"].append(str(table.path.absolute()))
        summary["excluded_judgment_count"] += len(table.judgment_rows)
        summary["excluded_judgments"].extend({"table": str(table.path), "row": number} for number, _ in table.judgment_rows)
        for number, row in table.rows:
            state = review_table_format.response_state(row)
            level = row[_LEVEL] or "未記録"
            for counts in (summary, summary["by_level"][level], summary["by_model"][model]):
                counts[state] += 1
            if state == "rejected":
                reason = review_table_format.rejection_reason_class(row[_NO_RESPONSE])
                summary["by_reason"][reason] += 1
                rejections.append(
                    {
                        "kind": "review-adoption-rejection",
                        "period": period,
                        "table": str(table.path),
                        "row": number,
                        "level": level,
                        "model": model,
                        "reason_class": reason,
                        "location": row[_LOCATION],
                    }
                )
    summary["by_level"] = dict(sorted(summary["by_level"].items()))
    summary["by_model"] = dict(sorted(summary["by_model"].items()))
    summary["by_reason"] = dict(sorted(summary["by_reason"].items()))
    return [summary, *rejections]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, metavar="DATE", help="今回の期間の開始日時（必須）。時差なしはローカル時刻。")
    parser.add_argument("--until", metavar="DATE", help="今回の期間の終了日時。省略時は実行時刻。")
    parser.add_argument(
        "--private-notes",
        type=Path,
        metavar="DIR",
        help="private-notesのGit作業ツリー。省略時はatk config get private_notesと同じ解決。",
    )
    parser.add_argument(
        "--claude-projects-dir", type=Path, metavar="DIR", help="Claude記録の所在。省略時は~/.claude/projects。"
    )
    parser.add_argument(
        "--codex-sessions-dir", type=Path, metavar="DIR", help="Codex記録の所在。省略時はCodexの通常記録とアーカイブ。"
    )
    args = parser.parse_args(argv)
    try:
        since = _parse_cli_timestamp(args.since)
        until = _parse_cli_timestamp(args.until) if args.until else datetime.datetime.now().astimezone()
        if since >= until:
            raise ValueError("開始が終了以後である")
        previous_since = since - (until - since)
        notes = (args.private_notes or private_notes.default_private_notes()).expanduser().resolve()
        history = _history(notes)
        current = saved_review_tables(notes, history, since, until)
        previous = saved_review_tables(notes, history, previous_since, since - datetime.timedelta(microseconds=1))
        codex_roots = (
            (args.codex_sessions_dir.expanduser(),)
            if args.codex_sessions_dir is not None
            else session_record_paths.codex_record_roots(host_homes.codex_home())
        )
        roots = [(args.claude_projects_dir or Path.home() / ".claude" / "projects").expanduser(), *codex_roots]
        models = _table_models([table.path.name for table in (*current.tables, *previous.tables)], roots)
    except (ValueError, OSError) as error:
        print(
            json.dumps(
                {
                    "kind": "error",
                    "text": str(error),
                    "next_action": "次の操作: 日時、private-notesとセッション記録の所在、Git履歴の読取権限を確認して再実行する",
                },
                ensure_ascii=False,
            )
        )
        return 2
    events = [
        *_period_events("previous", previous_since, since, previous.tables, models),
        *_period_events("current", since, until, current.tables, models),
        {
            "kind": "review-adoption-coverage",
            "unreadable_table_count": current.unreadable,
            "tables_without_history_count": current.without_history,
            "record_roots": [str(root) for root in roots],
            "model_unknown_note": "記録が残っていない表と実行レビュー担当を特定できない表はモデル不明として数える",
        },
    ]
    for event in events:
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
