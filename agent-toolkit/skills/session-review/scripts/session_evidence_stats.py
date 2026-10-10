"""証拠抽出の`--stats`照会。トークン使用量、ツール呼び出しの内訳、コンパクションの集計を返す。"""

from __future__ import annotations

import collections
import datetime
import json
from pathlib import Path
from typing import Any

from session_evidence_extract import (
    _clip,
    _CollectedRecord,
    _fallback,
    _json_object,
    _parse_timestamp,
    _Record,
    _record_timestamp,
    _Runtime,
)
from session_evidence_records import (
    _codex_record_thread_id,
)

from agent_toolkit._common.runtime_identity import distinct_identities as _distinct_identities

_CLAUDE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


_CODEX_TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


_CLAUDE_HINT_KEYS = ("command", "file_path", "path", "pattern", "url", "query")


def _parse_cli_timestamp(value: str) -> datetime.datetime:
    """CLI引数で受け取ったISO 8601の時刻を解析し、タイムゾーン無しの値を実行ホストのローカル時刻として返す。

    人やエージェントが手で書く境界は同じホストの`date`などで得たローカル時刻から書かれるため、
    記録向けの`_parse_timestamp`と同じくUTCとみなすと、ローカルタイムゾーンとUTCの差だけ境界がずれ、
    該当0件の結果と区別できない。日付だけの値はその日のローカル時刻0時とする。
    """
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _apply_observation_boundary(records: list[_Record], boundary: datetime.datetime) -> list[_Record]:
    """時刻無しと境界以前のメイン記録を、元の行番号を保って返す。"""
    return [record for record in records if (timestamp := _record_timestamp(record)) is None or timestamp <= boundary]


def _elapsed_until_event(records: list[_Record], until_text: str) -> dict[str, Any] | str:
    """最初の記録から指定時刻までの経過時間イベントまたはエラー文を返す。"""
    try:
        until = _parse_cli_timestamp(until_text)
    except ValueError:
        return f"経過時間の終端が不正: {until_text}"
    timestamps = [
        (timestamp, record.entry["timestamp"]) for record in records if (timestamp := _record_timestamp(record)) is not None
    ]
    if not timestamps:
        return ""
    start, start_text = min(timestamps, key=lambda item: item[0])
    if until < start:
        return f"経過時間の終端が最初のレコードより前: {until_text}"
    return {
        "kind": "session-elapsed",
        "start": start_text,
        "until": until_text,
        "elapsed_seconds": int((until - start).total_seconds()),
    }


def _token_value(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _claude_tokens(usage: dict[str, Any]) -> dict[str, int]:
    return {key: _token_value(usage.get(key)) for key in _CLAUDE_TOKEN_KEYS}


def _token_total(tokens: dict[str, int]) -> int:
    return sum(value for value in tokens.values())


def _add_tokens(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def _codex_normalized_tokens(tokens: dict[str, int]) -> dict[str, int]:
    """Codexの内訳をClaude形式の4成分へ意味的に変換する。

    Codexの`input_tokens`はキャッシュ済み入力（`cached_input_tokens`）を内包する総入力であり、
    非キャッシュ入力だけを表すClaude形式の同名キーとは同義ではない。同名のまま合算すると
    キャッシュ済み入力が非キャッシュ入力の欄へ混入する。`total_tokens`は入力と出力の合計であり、
    加算すれば他成分の再合算となる。そのため次の対応で変換した値だけを合算へ用いる。

    - `cache_read_input_tokens` ← `cached_input_tokens`
    - `input_tokens` ← `input_tokens - cached_input_tokens`（内包関係は実際の記録で確認済み）
    - `output_tokens` ← `output_tokens`（`reasoning_output_tokens`は内包されるため加算しない）
    - `cache_creation_input_tokens` ← `cache_write_input_tokens`
    """
    cached = tokens.get("cached_input_tokens", 0)
    return {
        "input_tokens": max(tokens.get("input_tokens", 0) - cached, 0),
        "output_tokens": tokens.get("output_tokens", 0),
        "cache_creation_input_tokens": tokens.get("cache_write_input_tokens", 0),
        "cache_read_input_tokens": cached,
    }


def _latest_claude_usages(records: list[_Record]) -> list[tuple[_Record, dict[str, int]]]:
    """同一`message.id`の重複エントリを最後のusageだけへ畳み込む。

    Claude Code transcriptでは同一`message.id`のエントリが複数現れ、各エントリのusageを合算すると
    トークン消費量が数倍になる。実際の記録で確認した重複形状に合わせ、最後に現れたusageを採用する。
    """
    latest: dict[str, tuple[_Record, dict[str, int]]] = {}
    for record in records:
        message = record.entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        message_id = message.get("id")
        key = f"message:{message_id}" if isinstance(message_id, str) else f"line:{record.line}"
        latest[key] = (record, _claude_tokens(usage))
    return list(latest.values())


def _agy_token_usages(records: list[_Record]) -> list[tuple[_Record, dict[str, int]]]:
    """Antigravityの応答ステップの`usage`を、Claude形式の4成分へ正規化して返す。

    agyの`usage`は`input_tokens`へキャッシュ読取を含めず、`output_tokens`へ思考分を含める
    （記録の確認結果: `total_tokens`は`input_tokens`と`output_tokens`の和に一致する）。
    キャッシュ作成の成分は持たないため0とする。
    同じ応答の`usage`は完了した`agent_response`ステップだけが持つため、各ステップを1回ずつ数える。
    """
    usages: list[tuple[_Record, dict[str, int]]] = []
    for record in records:
        step = record.entry.get("step_update")
        if not isinstance(step, dict) or step.get("step_type") != "agent_response":
            continue
        usage = step.get("usage")
        if not isinstance(usage, dict):
            continue
        usages.append(
            (
                record,
                {
                    "input_tokens": _token_value(usage.get("input_tokens")),
                    "output_tokens": _token_value(usage.get("output_tokens")),
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": _token_value(usage.get("cache_read_tokens")),
                },
            )
        )
    return usages


def _codex_token_usages(records: list[_Record]) -> list[tuple[_Record, dict[str, int]]]:
    """各`token_count`レコードの`info.last_token_usage`（1リクエストの実消費）を返す。

    同じレコードの`info.total_token_usage`はセッション内の累積値だが、Codexは過去のチェックポイントへ
    巻き戻すと累積器を巻き戻し先の値へ戻して再累積する。巻き戻し後の値には巻き戻し先までの
    消費が既に含まれるため、減少を境界とみなして減少前の値を加算するとそのプレフィックスを二重計上する
    （記録の確認結果: 累積が`1246611`から`579472`へ減少した記録で、減少後の値から同レコードの
    `last_token_usage.total_tokens`を引いた`490803`が8レコード前の累積値と一致した。
    区間合算方式では実消費`3086405`に対し`3577208`を報告していた）。
    `last_token_usage`は1リクエスト当たりの実消費であり、巻き戻しの有無にかかわらず単純加算で
    セッション全体の消費量が得られる（記録の確認結果: 走査した4398 rolloutの全`token_count`レコードに存在する）。

    ただしCodexは同一リクエストの`token_count`を複数回記録する（ターン終了時の再送、compact直後の
    記録など。後者は`last_token_usage`の6成分が全て0となる）。重複記録では`total_token_usage`が
    直前の採用レコードと完全に一致するため、一致するレコードを加算対象から除外する
    （記録の確認結果: `~/.codex/sessions/2026/08/`配下1942セッションのうち707セッションで無条件加算が実消費を
    上回り、最大54.8%の過大計上となった。除外方式を実rollout 476件へ適用すると474件で加算値が
    セッション内の最終`total_token_usage`と一致した）。巻き戻しでは`total_token_usage`が直前と
    異なる値へ変わるため、減少後のレコードは加算対象へ残る。
    `total_token_usage`がdictでないか`total_tokens`を欠くレコードは判別条件を適用できないため、
    安全側として常に加算対象へ含める。
    """
    usages: list[tuple[_Record, dict[str, int]]] = []
    previous_total: dict[str, Any] | None = None
    for record in records:
        payload = record.entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        last_usage = info.get("last_token_usage")
        if not isinstance(last_usage, dict):
            continue
        total_usage = info.get("total_token_usage")
        comparable = isinstance(total_usage, dict) and "total_tokens" in total_usage
        if comparable and previous_total is not None and total_usage == previous_total:
            continue
        previous_total = total_usage if comparable else None
        usages.append((record, {key: _token_value(last_usage.get(key)) for key in _CODEX_TOKEN_KEYS}))
    return usages


def _turn_completion_data(
    records: list[_Record],
    runtime: _Runtime,
    first_timestamp: datetime.datetime,
    last_timestamp: datetime.datetime,
) -> dict[str, Any]:
    """最初のturnの開始から最後のturnの完了までの区間と、完了後に続く記録の区間を返す。

    記録の最初と最後の差（`elapsed_seconds`）は、turnの完了後に残るプロセスや待機の記録を含み、
    委譲先が結果を返すまでの時間より長くなる（記録の確認結果: Codexのユーザビリティレビュー担当で、
    最初の`task_started`から最後の`task_complete`までは約17分、記録の終端までは8408秒）。
    Codexは`event_msg`の`task_started`と`task_complete`、Claude Codeは`stop_reason`が`end_turn`の
    assistantレコードをturnの境界とし、Claude Codeは記録の最初のレコードを開始とする。
    turnの完了を持たない記録は区間を定められないため、空の辞書を返す。
    """
    started: datetime.datetime | None = first_timestamp if runtime == "claude" else None
    completed: _Record | None = None
    completed_timestamp: datetime.datetime | None = None
    for record in records:
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        entry = record.entry
        if runtime == "codex" and entry.get("type") == "event_msg":
            payload = entry.get("payload")
            payload_type = payload.get("type") if isinstance(payload, dict) else None
            if payload_type == "task_started" and started is None:
                started = timestamp
            elif payload_type == "task_complete":
                completed, completed_timestamp = record, timestamp
        elif runtime == "claude" and entry.get("type") == "assistant":
            message = entry.get("message")
            if isinstance(message, dict) and message.get("stop_reason") == "end_turn":
                completed, completed_timestamp = record, timestamp
    if started is None or completed is None or completed_timestamp is None:
        return {}
    return {
        "turn_elapsed_seconds": int((completed_timestamp - started).total_seconds()),
        "last_turn_completed_at": completed.entry["timestamp"],
        "after_last_turn_seconds": int((last_timestamp - completed_timestamp).total_seconds()),
    }


def _stats_summary_data(records: list[_Record], runtime: _Runtime) -> dict[str, Any]:
    timestamps = [(record, timestamp) for record in records if (timestamp := _record_timestamp(record)) is not None]
    summary: dict[str, Any] = {}
    identities = [identity.public() for identity in _distinct_identities(records, runtime)]
    if identities:
        summary["observed_identities"] = identities
    if timestamps:
        first_record, first_timestamp = min(timestamps, key=lambda item: item[1])
        last_record, last_timestamp = max(timestamps, key=lambda item: item[1])
        summary["start"] = first_record.entry["timestamp"]
        summary["end"] = last_record.entry["timestamp"]
        summary["elapsed_seconds"] = int((last_timestamp - first_timestamp).total_seconds())
        summary.update(_turn_completion_data(records, runtime, first_timestamp, last_timestamp))

    if runtime in {"claude", "agy"}:
        usages = _latest_claude_usages(records) if runtime == "claude" else _agy_token_usages(records)
        if not usages:
            return summary
        tokens: dict[str, int] = {key: 0 for key in _CLAUDE_TOKEN_KEYS}
        max_context = 0
        for _, usage in usages:
            _add_tokens(tokens, usage)
            max_context = max(
                max_context,
                usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"] + usage["input_tokens"],
            )
        summary.update(tokens=tokens, max_context_tokens=max_context, api_messages=len(usages))
        return summary

    usages = _codex_token_usages(records)
    if not usages:
        return summary
    tokens = {key: 0 for key in _CODEX_TOKEN_KEYS}
    for _, usage in usages:
        _add_tokens(tokens, usage)
    summary.update(
        tokens=tokens,
        max_context_tokens=max(usage["input_tokens"] for _, usage in usages),
        api_messages=len(usages),
    )
    return summary


def _claude_call_hint(block_input: Any) -> str | None:
    """tool_use入力から反復照会の識別に用いる代表的な対象値を取得する。

    `command`を持たないツール（`Read`・`Edit`・`Grep`など）では、対象を表す入力キーを
    定義順に探す。列挙は全ツール種別の網羅を目的とせず、取得できた値だけをヒントとする。
    いずれのキーも持たない呼び出しはヒントなしとし、反復集計の対象から外れる。

    値は先頭行への切り詰めも文字数の切り詰めも行わず全体を返す。複数行のコマンドは先頭行が
    `cd <ディレクトリ>`・変数代入・ヒアドキュメント開始行などで一致しやすく、
    先頭行だけをヒントにすると内容の異なる呼び出しが同じ反復組へ集まるためである。
    同じ理由で文字数の切り詰めも反復判定より後段（表示時）へ置く。
    """
    if not isinstance(block_input, dict):
        return None
    for key in _CLAUDE_HINT_KEYS:
        value = block_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _codex_call_hint(payload: dict[str, Any]) -> str | None:
    """Codexのツール呼び出しpayloadから反復照会の識別に用いる対象値を取得する。

    実行内容の格納先は呼び出しの種類で異なり、`arguments`のJSONへ`command`または`cmd`を持つ
    呼び出しと、`arguments`を持たず自由形式の`input`へ実行内容を埋め込む呼び出し（`exec`など）が
    実在する。前者から取得できない場合は`input`の文字列をそのままヒントとする。
    値は`_claude_call_hint`と同じ理由で先頭行へも文字数へも切り詰めず全体を返す。
    """
    arguments = _json_object(payload.get("arguments"))
    if arguments is not None:
        for key in ("command", "cmd"):
            command = arguments.get(key)
            if isinstance(command, list):
                joined = " ".join(part for part in command if isinstance(part, str))
                if joined.strip():
                    return joined.strip()
            elif isinstance(command, str) and command.strip():
                return command.strip()
    value = payload.get("input")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _stats_call_entries(records: list[_Record], runtime: _Runtime) -> list[dict[str, Any]]:
    """ツール呼び出しと結果を対応付け、所要時間と入力ヒントを持つ呼び出しエントリを返す。

    エントリは表示用の`hint`（`_clip`で切り詰めた値）と、反復判定用の`hint_key`（切り詰め前の原文）を
    分けて持つ。切り詰め後の値で反復を判定すると、上限まで前方一致するだけの別内容の呼び出しが
    同じ反復組へ集約されるためである（記録の確認結果: 上限2000文字の一致で内容の異なる組が実記録に存在する）。
    """
    calls: dict[str, tuple[str, str | None, int, datetime.datetime]] = {}
    results: dict[str, list[datetime.datetime]] = {}
    for record in records:
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        if runtime == "claude":
            message = record.entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "tool_use" and isinstance(block.get("id"), str):
                    hint = _claude_call_hint(block.get("input"))
                    calls.setdefault(block["id"], (str(block.get("name", "")), hint, record.line, timestamp))
                elif block_type == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    results.setdefault(block["tool_use_id"], []).append(timestamp)
            continue

        payload = record.entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if payload_type in {"custom_tool_call", "function_call"} and isinstance(payload.get("call_id"), str):
            hint = _codex_call_hint(payload)
            calls.setdefault(payload["call_id"], (str(payload.get("name", "")), hint, record.line, timestamp))
        elif payload_type in {"custom_tool_call_output", "function_call_output"} and isinstance(payload.get("call_id"), str):
            results.setdefault(payload["call_id"], []).append(timestamp)

    paired: list[dict[str, Any]] = []
    for call_id, (name, hint, line, started) in calls.items():
        finished = next((value for value in results.get(call_id, []) if value >= started), None)
        if finished is None:
            continue
        item: dict[str, Any] = {
            "tool": name,
            "seconds": (finished - started).total_seconds(),
            "line": line,
        }
        if hint:
            item["hint"] = _clip(hint)
            item["hint_key"] = hint
        paired.append(item)
    return paired


def _stats_token_peaks(records: list[_Record], runtime: _Runtime) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if runtime in {"claude", "agy"}:
        usages = _latest_claude_usages(records) if runtime == "claude" else _agy_token_usages(records)
        for record, tokens in usages:
            candidates.append(
                {
                    "total_tokens": _token_total(tokens),
                    **tokens,
                    "line": record.line,
                    "new_tokens": tokens["output_tokens"] + tokens["cache_creation_input_tokens"],
                }
            )
    else:
        for record, raw in _codex_token_usages(records):
            # `cached_input_tokens`が`input_tokens`へ内包される関係は`last_token_usage`でも同じであり、
            # 変換しないとキャッシュ済み入力が非キャッシュ入力の欄へ混入し、
            # 同じ走行の`stats-total`（変換済み）と数値が矛盾する。
            normalized = _codex_normalized_tokens(raw)
            candidates.append(
                {
                    "total_tokens": _token_total(normalized),
                    **normalized,
                    "line": record.line,
                    "new_tokens": normalized["output_tokens"] + normalized["cache_creation_input_tokens"],
                }
            )
    by_total = sorted(candidates, key=lambda item: (-item["total_tokens"], item["line"]))[:10]
    by_new = sorted(candidates, key=lambda item: (-item["new_tokens"], item["line"]))[:10]
    selected = {item["line"]: item for item in by_total}
    selected.update({item["line"]: item for item in by_new})
    return [
        {key: value for key, value in item.items() if key != "new_tokens"}
        for item in sorted(selected.values(), key=lambda value: (-value["total_tokens"], value["line"]))
    ]


def _claude_compaction_fields(metadata: Any) -> dict[str, Any]:
    """Claude Codeの`compactMetadata`から契機・前後トークン・所要時間を取り出す。

    所要時間はミリ秒で記録されるため秒へ換算する。欄を持たない記録もあるため、
    取得できた欄だけを返す。
    """
    if not isinstance(metadata, dict):
        return {}
    fields: dict[str, Any] = {}
    trigger = metadata.get("trigger")
    if isinstance(trigger, str):
        fields["trigger"] = trigger
    for key, source_key in (("pre_tokens", "preTokens"), ("post_tokens", "postTokens")):
        value = metadata.get(source_key)
        if isinstance(value, int) and not isinstance(value, bool):
            fields[key] = value
    duration_ms = metadata.get("durationMs")
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
        fields["duration_seconds"] = round(duration_ms / 1000, 1)
    return fields


def _compaction_event(record: _Record, record_id: str) -> dict[str, Any] | None:
    """コンパクション1回分のイベントを返す。該当しないレコードでは`None`を返す。

    Claude Codeは`subtype`が`compact_boundary`のsystemレコード、Codexは`type`が`compacted`の
    レコードとして1回の発生を記録する。Codexの所要時間は呼び出し側が計測記録から付与する。
    """
    entry = record.entry
    entry_type = entry.get("type")
    if entry_type == "system" and entry.get("subtype") == "compact_boundary":
        engine: _Runtime = "claude"
    elif entry_type == "compacted":
        engine = "codex"
    else:
        return None
    event: dict[str, Any] = {"kind": "stats-compaction", "record": record_id, "line": record.line, "engine": engine}
    timestamp = entry.get("timestamp")
    if isinstance(timestamp, str):
        event["timestamp"] = timestamp
    if engine == "claude":
        event.update(_claude_compaction_fields(entry.get("compactMetadata")))
    return event


def _compaction_durations(directory: Path, thread_id: str) -> list[float]:
    """threadの有効な計測記録から所要秒数を記録順で返す。"""
    try:
        lines = (directory / f"{thread_id}.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    durations: list[float] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("version") != 1 or record.get("thread_id") != thread_id:
            continue
        duration = record.get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            durations.append(float(duration))
    return durations


def _stats_compaction_events(
    collected: list[_CollectedRecord],
    compaction_record_dir: Path,
) -> list[dict[str, Any]]:
    """全記録のコンパクションの発生位置と件数を返す。

    メイン記録・サブエージェント記録・委譲先セッションのいずれで発生した分も数える。
    発生が無い場合も件数0の集計イベントだけは返し、発生の有無を呼び出し側が判別できるようにする。
    """
    events: list[dict[str, Any]] = []
    for item in collected:
        thread_id = _codex_record_thread_id(item)
        durations = iter(_compaction_durations(compaction_record_dir, thread_id)) if thread_id is not None else iter(())
        for record in item.records:
            event = _compaction_event(record, item.record_id)
            if event is None:
                continue
            if event["engine"] == "codex":
                duration = next(durations, None)
                if duration is not None:
                    event["duration_seconds"] = duration
            events.append(event)
    events.sort(key=lambda event: (event["record"], event["line"]))
    counts = collections.Counter(event["record"] for event in events)
    total = {
        "kind": "stats-compaction-total",
        "count": len(events),
        "by_record": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "total_duration_seconds": round(
            sum((event["duration_seconds"] for event in events if "duration_seconds" in event), 0.0), 1
        ),
        "duration_unknown_count": sum("duration_seconds" not in event for event in events),
    }
    return [*events, total]


def _stats_breakdown_events(records: list[_Record], runtime: _Runtime) -> list[dict[str, Any]]:
    """1つの記録の記録間隔、ツール別、反復および遅い呼び出しの集計イベントを返す。

    メイン記録と各agent threadの記録へ同じ集計を適用する。行番号はその記録の行番号である。
    """
    events: list[dict[str, Any]] = []
    timestamped_records = [(record, timestamp) for record in records if (timestamp := _record_timestamp(record)) is not None]
    gaps = sorted(
        (
            (after_timestamp - before_timestamp).total_seconds(),
            before.line,
            after.line,
        )
        for (before, before_timestamp), (after, after_timestamp) in zip(
            timestamped_records, timestamped_records[1:], strict=False
        )
        if (after_timestamp - before_timestamp).total_seconds() >= 60
    )
    events.extend(
        {"kind": "stats-gap", "seconds": round(seconds, 1), "before_line": before, "after_line": after}
        for seconds, before, after in sorted(gaps, reverse=True)[:10]
    )

    calls = _stats_call_entries(records, runtime)
    tool_groups: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        tool_groups.setdefault(call["tool"], []).append(call)
    events.extend(
        {
            "kind": "stats-tool",
            "tool": tool,
            "count": len(items),
            "total_seconds": round(sum(item["seconds"] for item in items), 1),
        }
        for tool, items in sorted(tool_groups.items(), key=lambda item: (-sum(call["seconds"] for call in item[1]), item[0]))[
            :20
        ]
    )
    # 入力ヒントを取れない呼び出しは対象が異なっても同じ組へ集まり、
    # 反復照会の実態と異なる件数を報告する。集計対象から除く。
    repeats: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for call in calls:
        hint_key = call.get("hint_key")
        if hint_key:
            repeats.setdefault((call["tool"], hint_key), []).append(call)
    events.extend(
        {
            "kind": "stats-repeat",
            "tool": tool,
            "hint": items[0]["hint"],
            "count": len(items),
            "lines": [item["line"] for item in items],
        }
        for (tool, _), items in sorted(
            ((key, items) for key, items in repeats.items() if len(items) >= 2),
            key=lambda item: (-len(item[1]), item[0]),
        )[:10]
    )
    for call in sorted(calls, key=lambda item: (-item["seconds"], item["line"]))[:10]:
        event = {"kind": "stats-slow-call", "tool": call["tool"], "seconds": round(call["seconds"], 1), "line": call["line"]}
        if call.get("hint"):
            event["hint"] = call["hint"]
        events.append(event)
    return events


def _thread_breakdown_events(thread: _CollectedRecord, runtime: _Runtime) -> list[dict[str, Any]]:
    """Agent threadの記録へメイン記録と同じ内訳の集計を適用し、threadの識別子と記録位置を付けて返す。

    律速区間となった委譲先の内部の工程を、振り返りが追加の照会なしで読めるようにする。
    種別はメイン記録の集計と区別するため`stats-thread-`で始め、行番号は`--detail`へそのまま渡せる
    `<記録ID>:<行番号>`の記録位置へ置き換える。
    """
    thread_id = thread.record_id.split(":", 1)[1]

    def locate(line: Any) -> str:
        return f"{thread.record_id}:{line}"

    events: list[dict[str, Any]] = []
    for event in _stats_breakdown_events(thread.records, runtime):
        item = {**event, "kind": "stats-thread-" + event["kind"].removeprefix("stats-"), "thread": thread_id}
        if "before_line" in item:
            item["before"] = locate(item.pop("before_line"))
            item["after"] = locate(item.pop("after_line"))
        if "line" in item:
            item["location"] = locate(item.pop("line"))
        if "lines" in item:
            item["locations"] = [locate(line) for line in item.pop("lines")]
        events.append(item)
    return events


def _stats_events(collected: list[_CollectedRecord], compaction_record_dir: Path) -> list[dict[str, Any]]:
    """セッション全体を対象とした集計イベント列を返す。

    `stats-total`はメイン記録・全サブエージェント記録・全Codexスレッドの3区分の合算とする。
    3区分は記録ファイルが互いに排他であり、トークンが重複しない。
    Codex形式の内訳はClaude形式と成分の意味が異なるため、合算前に`_codex_normalized_tokens`で
    4成分へ変換する。メイン記録自体がCodex形式である場合も同じ変換を適用する。
    変換は成分ごとの加減算だけで構成され、`cached_input_tokens`は各レコードで`input_tokens`へ
    内包されるため、レコード単位で変換してから合算した値と、合算してから変換した値は一致する。
    個別表示の`stats-summary`と`stats-codex-thread`はCodexの全成分をそのまま表示する。
    """
    main_record = collected[0]
    main_records = main_record.records
    runtime = main_record.runtime
    if runtime is None:
        return _fallback()
    summary = _stats_summary_data(main_records, runtime)
    subagents = [item for item in collected if item.role == "subagent" and item.runtime == "claude"]
    thread_summaries: list[tuple[_CollectedRecord, _Runtime, dict[str, Any]]] = []
    for item in collected:
        if item.role == "session" and item.runtime is not None:
            thread_summaries.append((item, item.runtime, _stats_summary_data(item.records, item.runtime)))

    total_tokens: dict[str, int] = {}
    if isinstance(summary.get("tokens"), dict):
        _add_tokens(total_tokens, _codex_normalized_tokens(summary["tokens"]) if runtime == "codex" else summary["tokens"])
    for subagent in subagents:
        sub_summary = _stats_summary_data(subagent.records, "claude")
        if isinstance(sub_summary.get("tokens"), dict):
            _add_tokens(total_tokens, sub_summary["tokens"])
    for _, thread_runtime, thread_summary in thread_summaries:
        if isinstance(thread_summary.get("tokens"), dict):
            _add_tokens(
                total_tokens,
                _codex_normalized_tokens(thread_summary["tokens"]) if thread_runtime == "codex" else thread_summary["tokens"],
            )

    thread_counts: dict[str, int] = collections.Counter(thread_runtime for _, thread_runtime, _ in thread_summaries)

    total_event: dict[str, Any] = {
        "kind": "stats-total",
        "tokens": total_tokens,
        "subagent_count": len(subagents),
        "agent_thread_count": len(thread_summaries),
        "agent_thread_counts": dict(sorted(thread_counts.items())),
    }
    if "elapsed_seconds" in summary:
        total_event["elapsed_seconds"] = summary["elapsed_seconds"]
    events = [total_event]
    events.append(
        {"kind": "stats-summary", "engine": runtime, "record": main_record.record_id, **summary}
        if summary
        else {"kind": "stats-summary", "text": "集計対象なし"}
    )

    events.extend(_stats_breakdown_events(main_records, runtime))
    events.extend({"kind": "stats-token-peak", **peak} for peak in _stats_token_peaks(main_records, runtime))
    events.extend(_stats_compaction_events(collected, compaction_record_dir))

    if subagents:
        subagent_rows: list[tuple[str, str | None, dict[str, Any]]] = []
        subagent_total: dict[str, int] = {key: 0 for key in _CLAUDE_TOKEN_KEYS}
        for subagent in subagents:
            sub_summary = _stats_summary_data(subagent.records, "claude")
            row: dict[str, Any] = {"agent": subagent.record_id, "engine": "claude", **sub_summary}
            if subagent.agent_type:
                row["agent_type"] = subagent.agent_type
            row["elapsed_seconds"] = sub_summary.get("elapsed_seconds", 0)
            row["tokens"] = sub_summary.get("tokens", {key: 0 for key in _CLAUDE_TOKEN_KEYS})
            row["api_messages"] = sub_summary.get("api_messages", 0)
            subagent_rows.append((subagent.record_id, subagent.agent_type, row))
            _add_tokens(subagent_total, row["tokens"])
        for _, _, row in sorted(subagent_rows, key=lambda item: (-_token_total(item[2]["tokens"]), item[0])):
            events.append({"kind": "stats-subagent", **row})
        events.append(
            {
                "kind": "stats-subagent-total",
                "count": len(subagents),
                "tokens": subagent_total,
            }
        )

    for thread, thread_runtime, thread_summary in sorted(
        thread_summaries,
        key=lambda item: (-item[2].get("tokens", {}).get("total_tokens", 0), item[0].record_id),
    ):
        # `line`はメイン記録の`--detail`用であるため、メイン以外から見つけた委譲先では
        # 由来記録IDを`agent`へ付ける。
        thread_event: dict[str, Any] = {
            "kind": "stats-agent-thread",
            "engine": thread_runtime,
            "session_id": thread.record_id.split(":", 1)[1],
            "thread": thread.record_id.split(":", 1)[1],
            **thread_summary,
        }
        if thread.source_record != main_record.record_id:
            thread_event["agent"] = thread.source_record
        elif thread.source_line is not None:
            thread_event["line"] = thread.source_line
        events.append(thread_event)
        events.extend(_thread_breakdown_events(thread, thread_runtime))
    measured_threads = [event for event in events if event.get("kind") == "stats-agent-thread"]
    unmeasured_threads = [event["session_id"] for event in measured_threads if "start" not in event or "end" not in event]
    intervals: list[tuple[datetime.datetime, datetime.datetime, str]] = []
    for event in measured_threads:
        if event["session_id"] in unmeasured_threads:
            continue
        start = _parse_timestamp(event["start"])
        end = _parse_timestamp(event["end"])
        if start is not None and end is not None and start < end:
            intervals.append((start, end, event["session_id"]))
    main_start = _parse_timestamp(summary["start"]) if isinstance(summary.get("start"), str) else None
    main_end = _parse_timestamp(summary["end"]) if isinstance(summary.get("end"), str) else None
    if main_start is not None and main_end is not None and main_start < main_end:
        intervals = [(max(start, main_start), min(end, main_end), thread) for start, end, thread in intervals]
        intervals = [(start, end, thread) for start, end, thread in intervals if start < end]
        boundaries = sorted({main_start, main_end, *(point for start, end, _ in intervals for point in (start, end))})
        main_only = 0.0
        overlap = 0.0
        exclusive: collections.defaultdict[str, float] = collections.defaultdict(float)
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            active = [thread for left, right, thread in intervals if left <= start and end <= right]
            seconds = (end - start).total_seconds()
            if not active:
                main_only += seconds
            elif len(active) == 1:
                exclusive[active[0]] += seconds
            else:
                overlap += seconds
        events.append(
            {
                "kind": "stats-critical-path",
                "elapsed_seconds": round((main_end - main_start).total_seconds(), 1),
                "main_only_seconds": round(main_only, 1),
                "overlap_seconds": round(overlap, 1),
                "segments": [
                    {"owner": owner, "exclusive_seconds": round(seconds, 1)}
                    for owner, seconds in sorted(exclusive.items(), key=lambda item: (-item[1], item[0]))
                ],
                "unmeasured_threads": sorted(unmeasured_threads),
            }
        )
    return events
