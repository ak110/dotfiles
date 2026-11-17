"""保存した会話イベントの選択と、文書全体のUTF-8予算を持つ表示。"""

from __future__ import annotations

import argparse
import datetime
import json
import shlex
from typing import Any


def positive_integer(value: str) -> int:
    """公開表示オプションの正整数を検証する。"""
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("正整数を指定する")
    return number


def nonnegative_integer(value: str) -> int:
    """同一bundle内のイベント位置を検証する。"""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("非負整数を指定する")
    return number


def timestamp(value: str) -> datetime.datetime:
    """期間の境界をtimezone付きISO8601として受け付ける。"""
    try:
        parsed = datetime.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezoneが無い")
    except ValueError as error:
        raise argparse.ArgumentTypeError("timezone付きのISO8601日時を指定する") from error
    return parsed


def _event_time(event: dict[str, Any]) -> datetime.datetime | None:
    try:
        return timestamp(str(event.get("timestamp") or ""))
    except argparse.ArgumentTypeError:
        return None


def _clip(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    if limit < 3:
        return ""
    return encoded[: limit - 3].decode("utf-8", errors="ignore") + "…"


def window_document(
    events: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    observed_at: str,
    main_record: str | None,
    unresolved_count: int,
    recovered_count: int,
    detail_command: str,
    continue_command: str,
    max_bytes: int,
) -> tuple[str, dict[str, Any]]:
    """保存bundleから時系列順を定め、固定部も含めた有限表示と選択メタ情報を返す。

    位置は既知時刻の昇順と、その後の未知時刻区分を連結した1始まりの番号であり、結果も含む。
    同時刻と未知時刻区分内は元の抽出順を保つ。recentは期間・記録・afterを適用した後の既知時刻だけへ適用する。
    """
    after = args.conversation_after or 0
    if after > len(events):
        raise ValueError("--conversation-afterがbundleの範囲外。次の操作: 同じwork-dirの前回位置を指定する")
    records = args.conversation_record
    since, until = args.conversation_since, args.conversation_until
    # 記録別の連結順は全担当の直近を表さない。同じ保存bundleから毎回同じ位置を再構成する。
    ordered = sorted(
        ((_event_time(event), event) for event in events),
        key=lambda item: (item[0] is None, item[0] or datetime.datetime.min.replace(tzinfo=datetime.UTC)),
    )
    eligible: list[tuple[int, dict[str, Any]]] = []
    unknown = 0
    for position, (event_time, event) in enumerate(ordered, 1):
        if event.get("kind") not in {"utterance", "tool-call", "delegation-request"} or position <= after:
            continue
        if records and event["record"] not in records:
            continue
        if event_time is None:
            unknown += 1
            if since is not None or until is not None or args.conversation_recent is not None:
                continue
        if event_time is not None and (since is not None and event_time < since or until is not None and event_time > until):
            continue
        eligible.append((position, event))
    selected = eligible[-args.conversation_recent :] if args.conversation_recent else eligible
    known_records = {event["record"] for event in events}
    unknown_records = len(set(records or []) - known_records)
    conditions = {
        "record": records,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "recent": args.conversation_recent,
        "after": after,
    }
    continue_options = ["--conversation-window", "--conversation-max-bytes", str(max_bytes)]
    for record_id in records or []:
        continue_options.extend(["--conversation-record", record_id])
    for option, value in (("--conversation-since", since), ("--conversation-until", until)):
        if value is not None:
            continue_options.extend([option, value.isoformat()])
    if args.conversation_recent is not None:
        continue_options.extend(["--conversation-recent", str(args.conversation_recent)])
    continue_command += " " + shlex.join(continue_options)
    header = (
        "# 会話の流れ（少量表示）\n\n"
        f"観測時刻: {observed_at}\n"
        f"対象セッション・メイン担当: {main_record or '未確認'}。各行に担当記録を示す。\n"
        f"条件: {json.dumps(conditions, ensure_ascii=False)}\n"
        "順序: 既知時刻の昇順、同時刻は元の抽出順。番号は結果イベントも含む1始まりの位置。"
        "未知時刻は末尾の別区分に元の抽出順で示し、時系列順は未確定。recentは全担当の既知時刻だけから直近を選ぶ。"
        "発話は観測した途中経過であり、担当の完了・操作成功は実行状態と詳細で確認する。\n"
        f"未知時刻: {unknown}件（期間またはrecent指定時は選択から除外）。指定記録未確認: {unknown_records}件。"
        f"未解決: {unresolved_count}件、回復行: {recovered_count}件。"
        "全件はbundle/unconfirmed.jsonlとbundle/recovered.jsonl。\n\n"
    )

    def footer(count: int, cursor: int, clipped: int) -> str:
        remaining = len(selected) - count
        omitted_range = f"{selected[count][0]}〜{selected[-1][0]}" if remaining else "なし"
        return (
            f"\n対象{len(eligible)}件、件数条件後{len(selected)}件、表示{count}件。"
            f"件数条件の省略{len(eligible) - len(selected)}件、予算による省略{remaining}件（位置{omitted_range}）、"
            f"本文抜粋{clipped}件。\n"
            f"継続位置: {cursor}。同じbundle・条件の続き: `{continue_command} --conversation-after {cursor}`\n"
            "継続時は同じwork-dirの保存bundleを使う。件数条件の省略分はrecentを外してafter 0で取得する。\n"
            f"全文は元記録から取得する。詳細: `{detail_command} --detail <記録>:<行番号>`。\n"
        )

    # 案内が長い入力でも文書全体の予算を守る。固定部を短縮すると対象や再取得操作が曖昧になるため拒否する。
    cursor = after
    if len((header + footer(0, cursor, 0)).encode("utf-8")) > max_bytes:
        raise ValueError("会話表示の最小固定部が予算を超えた。次の操作: --conversation-max-bytesを増やして再実行する")
    rows: list[str] = []
    used = len(header.encode("utf-8"))
    clipped = 0
    for position, event in selected:
        label = event.get("role") if event["kind"] == "utterance" else event["kind"]
        if event["kind"] == "tool-call":
            result_line = event.get("result_line")
            label = (
                f"tool-call {event.get('tool') or 'unknown'}（結果位置{result_line if result_line is not None else '未確認'}）"
            )
        shown_time = event["timestamp"] if _event_time(event) is not None else "時刻不明（時系列順未確定）"
        prefix = f"- [{position}] {label} {shown_time} {event['record']}:{event['line']}: "
        full_body = " ".join(str(event.get("text", "")).split())
        available = max_bytes - used - len(footer(len(rows) + 1, position, clipped + 1).encode("utf-8"))
        body_budget = min(1200, available - len(prefix.encode("utf-8")) - 1)
        if body_budget < 3:
            if not rows:
                raise ValueError("会話表示の記録位置を含む最小行が予算を超えた。次の操作: --conversation-max-bytesを増やす")
            break
        body = _clip(full_body, body_budget)
        clipped += body != full_body or bool(event.get("text_truncated"))
        row = prefix + body + "\n"
        rows.append(row)
        used += len(row.encode("utf-8"))
        cursor = position
    document = header + "".join(rows) + footer(len(rows), cursor, clipped)
    return document, {
        "mode": "window",
        "observed_at": observed_at,
        "conditions": conditions,
        "target_count": len(eligible),
        "selected_count": len(selected),
        "displayed_count": len(rows),
        "omitted_count": len(eligible) - len(rows),
        "excerpt_count": clipped,
        "unknown_timestamp_count": unknown,
        "unknown_timestamp_excluded_count": (
            unknown if since is not None or until is not None or args.conversation_recent is not None else 0
        ),
        "unknown_record_count": unknown_records,
        "unresolved_count": unresolved_count,
        "next_after": cursor,
        "has_more": len(rows) < len(selected),
    }


_DIGEST_RECENT_ASSISTANT = 8
"""長く示す直近のエージェント発話の件数。圧縮直前の報告と判断を読み取れる量にする。"""


_DIGEST_RECENT_BYTES = 1800


_DIGEST_OLDER_BYTES = 300


_DIGEST_ELIDED_MIN_BYTES = 600
"""中ほどを省くユーザー発話と委譲元の依頼に残す、下限のUTF-8バイト数。"""


def _omission_note(omitted: int) -> str:
    """省いたエージェント発話の件数を示す行を返す。省いていなければ空文字列を返す。"""
    return f"- （古いエージェント発話{omitted}件を省いた。残した行は元の順である）\n" if omitted else ""


def _elide_middle(text: str, limit: int) -> str:
    """先頭と末尾を残して中ほどを省き、省いた字数と原文の照会先を本文の中に示す。

    依頼の制約は末尾に置かれやすいため、末尾を省く`_clip`を使わない。
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker = "…（中ほど{}字を省いた。原文は冒頭のコマンドで照会する）…"
    keep = limit - len(marker.format(len(text)).encode("utf-8"))
    if keep < 2:
        return _clip(text, limit)
    head = encoded[: keep * 3 // 5].decode("utf-8", errors="ignore")
    tail = encoded[len(encoded) - (keep - keep * 3 // 5) :].decode("utf-8", errors="ignore")
    return head + marker.format(len(text) - len(head) - len(tail)) + tail


def digest_document(
    events: list[dict[str, Any]],
    *,
    main_record: str | None,
    detail_command: str,
    max_bytes: int,
) -> tuple[str, dict[str, Any]]:
    """1つの会話の発話と委譲元の依頼を、話者と本文の並びで文書全体のUTF-8予算内に示す。

    会話圧縮後に、依頼・回答・進捗の流れを1回の読取で取り戻すために使う。
    ユーザー発話と委譲元の依頼は全文を保ち、エージェント発話は直近を長く、古いものを短く示す。
    予算を超える分は古いエージェント発話から省き、なお超える場合は直近のエージェント発話を短くする。
    それでも超える場合だけ、ユーザー発話と委譲元の依頼を長いものから順に、中ほどを省いて字数を行内へ示す。
    記録位置と時刻は行へ付けない。必要になった時に冒頭の照会コマンドで本文から調べる。
    """
    if main_record is None:
        main_record = next((str(event["record"]) for event in events if event.get("record")), None)
    rows = [
        event
        for event in events
        if event.get("kind") in {"utterance", "delegation-request"}
        and str(event.get("record")) == main_record
        and str(event.get("text", "")).strip()
    ]
    header = (
        "# 会話の要約（発話のみ）\n\n"
        "この会話の発話を古い順に並べる。ユーザー発話と委譲元の依頼は全文を示す。"
        "文書全体が上限を超える場合だけ、長いものから中ほどを省き、省いた字数をその行に示す。"
        f"エージェントの発話は直近{_DIGEST_RECENT_ASSISTANT}件を長く、それより前を短く示す。ツール呼び出しと結果は含まない。\n"
        f"原文と前後の操作が必要になったら、`{detail_command}`へ`--fixed-string <本文の一部>`を続けて"
        "一致する記録位置（recordとline）を調べ、`--detail <record>:<line>`を続けて原文を取得する。\n\n"
    )
    requests = [index for index, event in enumerate(rows) if event.get("kind") == "delegation-request"]
    users = [index for index, event in enumerate(rows) if event.get("kind") == "utterance" and event.get("role") == "user"]
    assistants = [index for index, event in enumerate(rows) if event.get("kind") == "utterance" and event.get("role") != "user"]
    recent = assistants[-_DIGEST_RECENT_ASSISTANT:]
    older = assistants[: len(assistants) - len(recent)]
    bodies = [" ".join(str(event.get("text", "")).split()) for event in rows]

    def line(index: int, body: str) -> str:
        if rows[index].get("kind") == "delegation-request":
            label = "委譲元の依頼"
        else:
            label = "ユーザー" if rows[index].get("role") == "user" else "エージェント"
        return f"- {label}: {body}\n"

    lines = {index: line(index, bodies[index]) for index in (*requests, *users)}
    lines |= {index: line(index, _clip(bodies[index], _DIGEST_RECENT_BYTES)) for index in recent}
    lines |= {index: line(index, _clip(bodies[index], _DIGEST_OLDER_BYTES)) for index in older}
    total = len(header.encode("utf-8")) + sum(len(text.encode("utf-8")) for text in lines.values())
    omitted = 0
    for index in older:
        if total + len(_omission_note(omitted).encode("utf-8")) <= max_bytes:
            break
        total -= len(lines.pop(index).encode("utf-8"))
        omitted += 1
    # 実際に出す件数の行の分だけを、本文の予算から除く。
    note = _omission_note(omitted)
    budget = max_bytes - len(note.encode("utf-8"))
    for index in recent:
        if total <= budget:
            break
        shorter = line(index, _clip(bodies[index], _DIGEST_OLDER_BYTES))
        total -= len(lines[index].encode("utf-8")) - len(shorter.encode("utf-8"))
        lines[index] = shorter
    elided = 0
    for index in sorted((*requests, *users), key=lambda item: len(bodies[item].encode("utf-8")), reverse=True):
        if total <= budget:
            break
        size = len(bodies[index].encode("utf-8"))
        shorter = line(index, _elide_middle(bodies[index], max(_DIGEST_ELIDED_MIN_BYTES, size - (total - budget))))
        if shorter == lines[index]:
            continue
        total -= len(lines[index].encode("utf-8")) - len(shorter.encode("utf-8"))
        lines[index] = shorter
        elided += 1
    if total > budget:
        raise ValueError("会話の要約が予算を超えた。次の操作: --conversation-max-bytesを増やして再実行する")
    excerpts = sum(
        1 for index, text in lines.items() if not text.endswith(bodies[index] + "\n") or bool(rows[index].get("text_truncated"))
    )
    document = header + note + "".join(lines[index] for index in sorted(lines))
    return document, {
        "mode": "digest",
        "user_count": len(users),
        "assistant_count": len(assistants),
        "request_count": len(requests),
        "omitted_assistant_count": omitted,
        "elided_count": elided,
        "excerpt_count": excerpts,
    }
