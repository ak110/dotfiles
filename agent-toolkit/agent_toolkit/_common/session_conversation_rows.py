from __future__ import annotations

import collections
import re
from typing import Any, NamedTuple

_CODEX_EXIT_CODE = re.compile(r"^Process exited with code (\d+)$", re.MULTILINE)


_WAIT_ONLY = re.compile(
    r"(?:待機中: [A-Za-z0-9_-]+|(?:結果|完了|応答|連絡)を待ちます[。.]?|"
    r"(?:作業を)?(?:続けます|継続します)[。.]?)"
)

_STATUS_LINE = re.compile(
    r"(状態|進捗|検証結果|再発防止|次の操作|次の区切り|残作業|別案|処置|原因|残している分|"
    r"問い|要求|上位の対象|lane-[0-9]+(?:（[^）]+）)?)[：:] *[^\s].*"
)


def _compact_status_lines(text: str) -> tuple[str, int]:
    if any(mark in text for mark in ("```", "~~~", "<atk-auto")):
        return text.strip(), 0
    kept: list[str] = []
    removed = 0
    for line in text.splitlines(keepends=True):
        message = line.strip().removeprefix("- ")
        if (
            kept
            and kept[-1].strip().removeprefix("- ") == message
            and not line.startswith(("    ", "\t"))
            and not kept[-1].startswith(("    ", "\t"))
            and _STATUS_LINE.fullmatch(message)
        ):
            removed += 1
            continue
        kept.append(line)
    return "".join(kept).strip(), removed


_COMPACT_UNDERSTANDING = re.compile(r"発話の理解: ([^\n]+。)補う前提: ([^\n]+)\n別案: ([^\n]+)\n分類: (是正|非是正)")
_LISTED_UNDERSTANDING = re.compile(r"発話の理解\n分類: (是正|非是正)\n- ([^\n]+)\n- 補う前提: ([^\n]+)\n- 別案: ([^\n]+)")


def _report_identity(text: str) -> str | tuple[str, ...]:
    stripped = text.strip()
    if compact := _COMPACT_UNDERSTANDING.fullmatch(stripped):
        return compact.groups()
    if listed := _LISTED_UNDERSTANDING.fullmatch(stripped):
        return listed[2], listed[3], listed[4], listed[1]
    return _compact_status_lines(stripped)[0]


def compact_assistant_reports(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    reported: dict[str, tuple[str | tuple[str, ...], int]] = {}
    kept: list[dict[str, Any] | None] = []
    omitted = 0
    for event in events:
        kind = event.get("kind")
        role = event.get("role")
        record = event.get("record")
        if not isinstance(record, str) or kind != "utterance" or role != "assistant":
            if isinstance(record, str) and (kind == "delegation-request" or kind == "utterance" and role == "user"):
                reported.pop(record, None)
            kept.append(event)
            continue
        text = event.get("text")
        if not isinstance(text, str) or not text.strip() or event.get("text_truncated"):
            reported.pop(record, None)
            kept.append(event)
            continue
        if _WAIT_ONLY.fullmatch(text.strip()):
            omitted += 1
            continue
        delivered = event.get("delivery") == "send_to_user"
        identity = _report_identity(text)
        previous_report = reported.get(record)
        if previous_report is not None and previous_report[0] == identity:
            old_position = previous_report[1]
            previous = kept[old_position]
            omitted += 1
            if not delivered or (previous is not None and previous.get("delivery") == "send_to_user"):
                continue
            kept[old_position] = None
        reported[record] = (identity, len(kept))
        kept.append(event)
    compacted: list[dict[str, Any]] = []
    repeated = 0
    for event in kept:
        if event is None:
            continue
        if (
            event.get("kind") == "utterance"
            and event.get("role") == "assistant"
            and event.get("delivery") != "send_to_user"
            and not event.get("text_truncated")
        ):
            text = event.get("text")
            if isinstance(text, str):
                body, removed = _compact_status_lines(text)
                if removed:
                    event = {**event, "text": body}
                    repeated += removed
        compacted.append(event)
    return compacted, {"redundant_assistant_count": omitted, "redundant_status_line_count": repeated}


def _activity_summary(events: list[dict[str, Any]]) -> str:
    calls = [item for item in events if item.get("kind") == "tool-call"]
    results = [item for item in events if item.get("kind") in {"tool-result", "tool-failure"}]
    failures = {
        (item["record"], item["line"])
        for item in results
        if item.get("kind") == "tool-failure" or _codex_failed(str(item.get("text", "")))
    }
    tools = collections.Counter(str(item.get("tool") or "unknown") for item in calls)
    tool_text = "、".join(f"{name}×{count}" for name, count in tools.most_common(6)) or "なし"
    if len(tools) > 6:
        tool_text += f"、他{len(tools) - 6}種"
    input_bytes = sum(item.get("text_utf8_bytes", len(str(item.get("text", "")).encode("utf-8"))) for item in calls)
    if calls:
        first = f"{calls[0]['record']}:{calls[0]['line']}"
        last = f"{calls[-1]['record']}:{calls[-1]['line']}"
        locations = first if first == last else f"{first}〜{last}"
    else:
        locations = "なし"
    result_locations = [f"{item['record']}:{item['line']}" for item in results]
    if result_locations:
        result_range = result_locations[0] if len(result_locations) == 1 else f"{result_locations[0]}〜{result_locations[-1]}"
    else:
        result_range = "なし"
    return (
        f"呼出{len(calls)}件（{tool_text}）、失敗{len(failures)}件、入力{input_bytes}バイト、"
        f"呼出記録{locations}、結果記録{result_range}"
    )


def main_turns(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for event in events:
        if event.get("kind") == "utterance" and event.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(event)
    if current:
        turns.append(current)
    return turns


class ConversationRow(NamedTuple):
    prefix: str
    text: str
    rank: int
    locator: str
    timestamp: str | None = None
    source_index: int | None = None


_RANK_USER = 0


_RANK_DELEGATION = 1


_RANK_ASSISTANT = 2


_RANK_ACTIVITY = 3


def main_turn_rows(events: list[dict[str, Any]], *, compact_calls: int = 0) -> list[ConversationRow]:
    failures = {
        item.get("call_id")
        for item in events
        if item.get("kind") == "tool-failure" or item.get("kind") == "tool-result" and _codex_failed(str(item.get("text", "")))
    }
    failed_lines = {
        item["line"]
        for item in events
        if item.get("kind") == "tool-failure" or item.get("kind") == "tool-result" and _codex_failed(str(item.get("text", "")))
    }
    displayed = [
        item
        for item in events
        if item.get("kind") == "tool-call" or item.get("kind") == "utterance" and str(item.get("text", "")).strip()
    ]
    rows: list[ConversationRow] = []
    index = 0
    while index < len(displayed):
        item = displayed[index]
        locator = f"{item['record']}:{item['line']}"
        if compact_calls and item["kind"] == "tool-call":
            end = index + 1
            while end < len(displayed) and displayed[end]["kind"] == "tool-call":
                end += 1
            if end - index > 1 or compact_calls >= 2:
                calls = displayed[index:end]
                ids = {call.get("call_id") for call in calls}
                result_lines = {call.get("result_line") for call in calls}
                results = [
                    event
                    for event in events
                    if event.get("kind") in {"tool-result", "tool-failure"}
                    and (event.get("call_id") in ids or event.get("line") in result_lines)
                ]
                rows.append(
                    ConversationRow(
                        "- " + _activity_summary([*calls, *results]), "", _RANK_ACTIVITY, locator, calls[0].get("timestamp")
                    )
                )
                index = end
                continue
        if item["kind"] == "utterance":
            user = item.get("role") == "user"
            label = "ユーザー" if user else "アシスタント"
            prefix = f"- {label} {item.get('timestamp') or '時刻なし'} {locator}: "
            rank = _RANK_USER if user else _RANK_ASSISTANT
        else:
            failed = item.get("call_id") in failures or item.get("result_line") in failed_lines
            prefix = f"- 呼出 {item.get('tool') or 'unknown'} {locator}{'（失敗1件）' if failed else ''}: "
            rank = _RANK_ACTIVITY
        rows.append(
            ConversationRow(prefix, str(item.get("text", "")), rank, locator, item.get("timestamp"), item.get("source_index"))
        )
        index += 1
    return rows


def delegate_rows(events: list[dict[str, Any]]) -> list[ConversationRow]:
    users = [
        item
        for item in events
        if item.get("kind") == "delegation-request" or item.get("kind") == "utterance" and item.get("role") == "user"
    ]
    assistants = [item for item in events if item.get("kind") == "utterance" and item.get("role") == "assistant"]
    rows: list[ConversationRow] = []
    if users:
        first = users[0]
        locator = f"{first['record']}:{first['line']}"
        rows.append(
            ConversationRow(
                f"- 委譲先への依頼 {first.get('timestamp') or '時刻なし'} {locator}: ",
                str(first.get("text", "")),
                _RANK_DELEGATION,
                locator,
                first.get("timestamp") or events[0].get("timestamp"),
                first.get("source_index"),
            )
        )
    if assistants:
        last = assistants[-1]
        label = f"委譲先の最終観測発話（アシスタント{len(assistants)}発話; {_activity_summary(events)}）"
        text = str(last.get("text", ""))
    else:
        last = events[-1]
        if users and last is users[0]:
            return rows
        label = f"委譲先の発話未観測（{_activity_summary(events)}）"
        text = ""
    locator = f"{last['record']}:{last['line']}"
    rows.append(
        ConversationRow(
            f"- {label} {last.get('timestamp') or '時刻なし'} {locator}: ",
            text,
            _RANK_DELEGATION,
            locator,
            last.get("timestamp") or events[-1].get("timestamp"),
            last.get("source_index"),
        )
    )
    return rows


def _codex_failed(result: str) -> bool:
    """Codexのシェル実行の結果が0以外の終了コードを報告しているかを返す。 pyfltr: allow-comment

    結果は`Chunk ID:`・`Wall time:`・`Process exited with code`などの行と`Output:`の行で始まる。
    抽出はこの形の失敗を失敗の標識にしないため、結果の行の成否はここで判定する。
    """
    header = result.split("\nOutput:\n", 1)[0]
    match = _CODEX_EXIT_CODE.search(header)
    return match is not None and match[1] != "0"
