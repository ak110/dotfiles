"""振り返りの入力準備（session_review_prepare_conversation）。"""

from __future__ import annotations

import collections
import datetime
import re
from typing import Any, NamedTuple

from session_review_prepare_text import _clip_utf8

_CONVERSATION_MAX_BYTES = 100_000
"""1エージェントがセッション全体の流れを保持して通読する文書の原則上限。"""


_CONVERSATION_EXCERPT_MIN_BYTES = 120
"""本文を持つ行へ予約する抜粋の最小量。行数を優先して抜粋が数バイトまで縮むと、判断の内容を読めなくなる。"""


_CODEX_EXIT_CODE = re.compile(r"^Process exited with code (\d+)$", re.MULTILINE)


def _conversation_excerpt(text: str, limit: int) -> str:
    """詳細照会を前提に、発話または入力の概要を1行で返す。"""
    return _clip_utf8(" ".join(text.split()), limit)


def _activity_summary(events: list[dict[str, Any]]) -> str:
    """ツール呼び出しの種類・量・失敗と記録位置を1行へ集約する。"""
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
    input_bytes = sum(len(str(item.get("text", "")).encode("utf-8")) for item in calls)
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


def _main_turns(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """メイン記録を実ユーザー発話から次の実ユーザー発話までの区間へ分ける。"""
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


class _ConversationRow(NamedTuple):
    """会話概要の1行。`rank`は予算不足時に残す優先順位（小さいほど優先）である。"""

    prefix: str
    text: str
    rank: int
    locator: str
    timestamp: str | None = None


_RANK_USER = 0


_RANK_DELEGATION = 1


_RANK_ASSISTANT = 2


_RANK_ACTIVITY = 3


def _main_turn_rows(events: list[dict[str, Any]], *, compact_calls: int = 0) -> list[_ConversationRow]:
    """区間内の表示行を固定部と抜粋本文へ分け、結果本文を通読へ含めない。

    `compact_calls`が1なら2件以上連続するツール呼出し、2なら発話の間にある全てのツール呼出しを操作要約1行へ集約する。
    """
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
    rows: list[_ConversationRow] = []
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
                    _ConversationRow(
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
        rows.append(_ConversationRow(prefix, str(item.get("text", "")), rank, locator, item.get("timestamp")))
        index += 1
    return rows


def _spread(items: list[int], count: int) -> list[int]:
    """順序付きの位置から、全期間へ等間隔に`count`件を選ぶ。"""
    if count >= len(items):
        return list(items)
    if count <= 0:
        return []
    if count == 1:
        return [items[len(items) // 2]]
    return sorted({items[round(step * (len(items) - 1) / (count - 1))] for step in range(count)})


def _omission_row(rows: list[_ConversationRow]) -> str:
    """省略した連続区間の件数と記録位置を1行にする。"""
    first = rows[0].locator
    last = rows[-1].locator
    span = first if first == last else f"{first}〜{last}"
    return f"- （省略: {len(rows)}行、記録位置{span}。詳細は記録位置から取得する）"


def _rows_cost(rows: list[tuple[str, str]]) -> int:
    """固定部と、本文を持つ行へ予約する抜粋の最小量を合わせたUTF-8バイト数を返す。"""
    return sum(len(prefix.encode("utf-8")) + 1 + (_CONVERSATION_EXCERPT_MIN_BYTES if text else 0) for prefix, text in rows)


def _select_rows(rows: list[_ConversationRow], budget: int) -> list[tuple[str, str]]:
    """固定部が予算を超える場合に、優先順位と全期間への等間隔の配分で残す行を選ぶ。

    ユーザー発話、アシスタント発話、操作要約の順に残し、同じ順位の中は全期間へ均等に配分する。
    先頭から詰めると後半の判断が欠けるため、省略は区間ごとの件数と記録位置で示す。
    戻り値は選んだ行と省略行の（固定部、抜粋前の本文）を時系列順に並べた一覧である。省略行の本文は空とする。
    """
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


def _delegate_rows(events: list[dict[str, Any]]) -> list[_ConversationRow]:
    """委譲先1記録の依頼と最終観測発話を、メインの時系列へ並べる行にする。

    記録だけでは途中の発話と返却を区別できないため、最後に観測したアシスタント発話を最終観測発話として示す。
    アシスタント発話が無い記録は返却を示さず、依頼の後に操作などの記録があれば発話未観測の行に操作量を添える。
    委譲先の内部の操作は詳細照会へ委ねる。
    """
    users = [
        item
        for item in events
        if item.get("kind") == "delegation-request" or item.get("kind") == "utterance" and item.get("role") == "user"
    ]
    assistants = [item for item in events if item.get("kind") == "utterance" and item.get("role") == "assistant"]
    rows: list[_ConversationRow] = []
    if users:
        first = users[0]
        locator = f"{first['record']}:{first['line']}"
        rows.append(
            _ConversationRow(
                f"- 委譲先への依頼 {first.get('timestamp') or '時刻なし'} {locator}: ",
                str(first.get("text", "")),
                _RANK_DELEGATION,
                locator,
                first.get("timestamp") or events[0].get("timestamp"),
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
        _ConversationRow(
            f"- {label} {last.get('timestamp') or '時刻なし'} {locator}: ",
            text,
            _RANK_DELEGATION,
            locator,
            last.get("timestamp") or events[-1].get("timestamp"),
        )
    )
    return rows


def _sort_key_time(value: str | None) -> datetime.datetime | None:
    """ISO 8601の時刻を比較用に読み、読めない値は未知とする。"""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def _merge_by_time(groups: list[list[_ConversationRow]]) -> list[_ConversationRow]:
    """記録ごとの行を時刻順へ統合する。

    時刻が未知の行は同じ記録の直前の既知時刻を引き継ぐ。
    記録の時刻は前後しうるため、記録内では直前までの最大時刻を下限にして記録内の順序を保つ。
    同じ時刻では入力の順（メインが先）を保つ。
    """
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
    """セッション全体の目的・判断・操作と手戻りを、担当間の時系列を合わせて1エージェントが通読できる量で示す。"""
    by_record: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in events:
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
        f"全文は`{detail_command} --detail <記録位置>`、全イベントは`bundle/conversation.jsonl`から取得し、"
        "区間を限定した通読は同じ準備コマンドへ`--conversation-window`と`--conversation-since`・`--conversation-until`を加えて行う。",
        "",
        "## 時系列",
        "",
    ]
    main_events = by_record.pop(str(main_record), []) if main_record is not None else []
    turns = _main_turns(main_events)
    delegate_groups = [_delegate_rows(record_events) for record_events in by_record.values() if record_events]
    if not turns and not delegate_groups:
        return "\n".join([*header, "- 発話と操作は0件である。"]) + "\n"
    # 見出しと本文を合わせた文書全体を予算内に収めるため、行へ配分する量から固定部を差し引く。
    budget = _CONVERSATION_MAX_BYTES - len(("\n".join(header) + "\n").encode("utf-8"))
    # 抜粋量、連続する呼出し、発話間の全呼出しの順に縮め、なお収まらなければ全期間へ配分して行を選ぶ。
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


def _codex_failed(result: str) -> bool:
    """Codexのシェル実行の結果が0以外の終了コードを報告しているかを返す。

    結果は`Chunk ID:`・`Wall time:`・`Process exited with code`などの行と`Output:`の行で始まる。
    抽出はこの形の失敗を失敗の標識にしないため、結果の行の成否はここで判定する。
    """
    header = result.split("\nOutput:\n", 1)[0]
    match = _CODEX_EXIT_CODE.search(header)
    return match is not None and match[1] != "0"
