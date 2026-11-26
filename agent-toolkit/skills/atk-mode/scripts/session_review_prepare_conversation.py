from __future__ import annotations

import collections
import datetime
from typing import Any

from agent_toolkit._common.session_conversation_rows import (
    ConversationRow as _ConversationRow,
)
from agent_toolkit._common.session_conversation_rows import (
    delegate_rows as _delegate_rows,
)
from agent_toolkit._common.session_conversation_rows import (
    main_turn_rows as _main_turn_rows,
)
from agent_toolkit._common.session_conversation_rows import (
    main_turns as _main_turns,
)
from session_conversation import compact_assistant_reports
from session_review_prepare_text import _clip_utf8

_CONVERSATION_MAX_BYTES = 100_000
"""1エージェントがセッション全体の流れを保持して通読する文書の原則上限。"""


_CONVERSATION_EXCERPT_MIN_BYTES = 120
"""本文を持つ行へ予約する抜粋の最小量。行数を優先して抜粋が数バイトまで縮むと、判断の内容を読めなくなる。"""


def _conversation_excerpt(text: str, limit: int) -> str:
    return _clip_utf8(" ".join(text.split()), limit)


def _spread(items: list[int], count: int) -> list[int]:
    if count >= len(items):
        return list(items)
    if count <= 0:
        return []
    if count == 1:
        return [items[len(items) // 2]]
    return sorted({items[round(step * (len(items) - 1) / (count - 1))] for step in range(count)})


def _omission_row(rows: list[_ConversationRow]) -> str:
    first = rows[0].locator
    last = rows[-1].locator
    span = first if first == last else f"{first}〜{last}"
    return f"- （省略: {len(rows)}行、記録位置{span}。詳細は記録位置から取得する）"


def _rows_cost(rows: list[tuple[str, str]]) -> int:
    """固定部と、本文を持つ行へ予約する抜粋の最小量を合わせたUTF-8バイト数を返す。"""
    return sum(len(prefix.encode("utf-8")) + 1 + (_CONVERSATION_EXCERPT_MIN_BYTES if text else 0) for prefix, text in rows)


def _select_rows(rows: list[_ConversationRow], budget: int) -> list[tuple[str, str]]:
    by_rank: dict[int, list[int]] = collections.defaultdict(list)
    for position, row in enumerate(rows):
        by_rank[row.rank].append(position)

    def chosen(count: int) -> list[int]:
        picked: list[int] = []
        for rank in sorted(by_rank):
            remaining = count - len(picked)
            if remaining <= 0:
                break
            picked.extend(_spread(by_rank[rank], remaining))
        return sorted(picked)

    def render(selected: list[int]) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        previous = -1
        for position in selected:
            if position - previous > 1:
                lines.append((_omission_row(rows[previous + 1 : position]), ""))
            lines.append((rows[position].prefix, rows[position].text))
            previous = position
        if previous < len(rows) - 1:
            lines.append((_omission_row(rows[previous + 1 :]), ""))
        return lines

    def size(lines: list[tuple[str, str]]) -> int:
        return _rows_cost(lines)

    low, high = 0, len(rows)
    while low < high:
        middle = (low + high + 1) // 2
        if size(render(chosen(middle))) <= budget:
            low = middle
        else:
            high = middle - 1
    return render(chosen(low))


def _sort_key_time(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def _merge_by_time(groups: list[list[_ConversationRow]]) -> list[_ConversationRow]:
    keyed: list[tuple[datetime.datetime, int, int, _ConversationRow]] = []
    floor = datetime.datetime.min.replace(tzinfo=datetime.UTC)
    for group_index, group in enumerate(groups):
        current = floor
        for row_index, row in enumerate(group):
            moment = _sort_key_time(row.timestamp)
            if moment is not None:
                current = max(current, moment)
            keyed.append((current, group_index, row_index, row))
    keyed.sort(key=lambda item: (item[0], item[1], item[2]))
    return [row for _moment, _group, _index, row in keyed]


def _conversation_document(events: list[dict[str, Any]], detail_command: str, main_record: str | None) -> str:
    events, _ = compact_assistant_reports(events)
    by_record: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in events:
        if event.get("kind") != "record-provenance":
            by_record[str(event["record"])].append(event)
    if main_record is None and by_record:
        main_record = next(iter(by_record))
    header = [
        "# 会話の流れ",
        "",
        "セッション全体の目的、判断、主要な操作、分担、待機、再読、失敗と手戻りを、"
        "1エージェントが一続きで見直すための概要である。メインの発話・操作と、委譲先への依頼・最終観測発話を時刻順に並べる。"
        "委譲元の依頼本文の抜粋を含み、機械生成の内包資料と結果本文は除く。"
        "表示予算を超える会話では、ツール呼出しを区間ごとの操作要約へ集約し、それでも収まらない場合は"
        "ユーザー発話、委譲の依頼・返却、アシスタント発話の順に優先して全期間へ均等に行を残し、省略した区間の行数と記録位置を示す。"
        f"原文は`{detail_command} --detail <記録位置>`で照会し、省略があれば由来が示す元記録の該当位置を読む。"
        "区間を限定した通読は同じ準備コマンドへ`--conversation-window`と`--conversation-since`・`--conversation-until`を加えて行う。",
        "",
        "## 時系列",
        "",
    ]
    fork_records = [event for event in events if event.get("kind") == "record-provenance" and event.get("fork_lines")]
    if fork_records:
        context = ["## 親から引き継いだ記録", ""]
        for event in fork_records[:10]:
            first, last = event["fork_lines"]
            note = "親の記録と一致" if event["fork_attribution_known"] else "帰属不明のため操作数から除外"
            context.append(f"- `{event['record']}:{first}〜{last}`: {note}。親の識別子: `{event['fork_parent']}`")
        if len(fork_records) > 10:
            context.append(f"- 残り{len(fork_records) - 10}記録の所在はbundle/timeline.jsonlのrecord-provenanceを参照する。")
        header[-2:-2] = [*context, ""]
    main_events = by_record.pop(str(main_record), []) if main_record is not None else []
    turns = _main_turns(main_events)
    delegate_groups = [_delegate_rows(record_events) for record_events in by_record.values() if record_events]
    if not turns and not delegate_groups:
        return "\n".join([*header, "- 発話と操作は0件である。"]) + "\n"

    budget = _CONVERSATION_MAX_BYTES - len(("\n".join(header) + "\n").encode("utf-8"))

    rows: list[_ConversationRow] = []
    selected: list[tuple[str, str]] = []
    for level in (0, 1, 2):
        main_rows = [row for turn in turns for row in _main_turn_rows(turn, compact_calls=level)]
        rows = _merge_by_time([main_rows, *delegate_groups])
        selected = [(row.prefix, row.text) for row in rows]
        if _rows_cost(selected) <= budget:
            break
    else:
        selected = _select_rows(rows, budget)
    minimum = sum(len(prefix.encode("utf-8")) + 1 for prefix, _ in selected)
    excerpt_bytes = (budget - minimum) // max(1, sum(1 for _, text in selected if text))
    body = [
        prefix + (_conversation_excerpt(text, excerpt_bytes) if text and excerpt_bytes >= 3 else "")
        for prefix, text in selected
    ]
    return "\n".join([*header, *body]) + "\n"
