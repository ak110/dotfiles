"""保存済みセッションの共通イベント列をMarkdownへ描画する。"""

from __future__ import annotations

import dataclasses
import datetime
import html
import json
import pathlib
import re
import typing

from agent_toolkit._atk.serve import sessions as session_records

_TOOL_RESULT_MAX_CHARS = 2000


@dataclasses.dataclass(frozen=True)
class RecordMetadata:
    """Markdownの見出し、表、出力ファイル名へ用いる記録内の値。"""

    session_id: str
    title: str
    cwd: str | None
    branch: str | None
    started_at: str | None
    ended_at: str | None


def metadata_from_records(
    engine: str,
    records: list[dict[str, typing.Any]],
    session_id: str,
) -> RecordMetadata:
    """実行系の記録形式から表示用メタデータを取り出す。"""
    cwd: str | None = None
    branch: str | None = None
    slug: str | None = None
    custom_title: str | None = None
    timestamps: list[str] = []
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str) and _parse_timestamp(timestamp) is not None:
            timestamps.append(timestamp)
        if engine == "claude":
            cwd = cwd or _optional_text(record.get("cwd"))
            branch = branch or _optional_text(record.get("gitBranch"))
            slug = slug or _optional_text(record.get("slug"))
            if record.get("type") == "custom-title":
                custom_title = _optional_text(record.get("customTitle")) or custom_title
        elif engine == "codex":
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "session_meta":
                cwd = cwd or _optional_text(payload.get("cwd"))
                custom_title = custom_title or _optional_text(payload.get("title"))
                git = payload.get("git")
                if isinstance(git, dict):
                    branch = branch or _optional_text(git.get("branch"))
            elif record.get("type") == "turn_context":
                cwd = cwd or _optional_text(payload.get("cwd"))
    ordered = sorted(
        timestamps, key=lambda value: _parse_timestamp(value) or datetime.datetime.min.replace(tzinfo=datetime.UTC)
    )
    return RecordMetadata(
        session_id=session_id,
        title=custom_title or slug or session_id[:8],
        cwd=cwd,
        branch=branch,
        started_at=ordered[0] if ordered else None,
        ended_at=ordered[-1] if ordered else None,
    )


def render_session(
    engine: str,
    records: list[dict[str, typing.Any]],
    session_id: str,
    record_path: pathlib.Path,
    *,
    include_thinking: bool = False,
    include_subagents: bool = False,
    tool_details: bool = True,
) -> str:
    """既存の記録解析から人とアシスタントの会話をMarkdownへまとめる。"""
    metadata = metadata_from_records(engine, records, session_id)
    lines = [f"# Session: {metadata.title}", "", "| 項目 | 値 |", "| --- | --- |"]
    for label, value in (
        ("セッションID", metadata.session_id),
        ("プロジェクト", metadata.cwd),
        ("ブランチ", metadata.branch),
        ("タイトル", metadata.title),
        ("期間", _period(metadata.started_at, metadata.ended_at)),
    ):
        lines.append(f"| {label} | {_escape_cell(value or '記録なし')} |")
    lines.append("")
    _render_events(
        lines,
        session_records.record_events(engine, _display_records(engine, records)),
        heading_level=2,
        include_thinking=include_thinking,
        tool_details=tool_details,
    )
    if include_subagents and engine == "claude":
        for item in session_records.claude_subagents(record_path) or []:
            raw_path = item.get("path")
            if not isinstance(raw_path, str):
                continue
            child_path = pathlib.Path(raw_path)
            try:
                child_records, _ = session_records.parse_records(child_path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            description = _optional_text(item.get("description")) or _optional_text(item.get("agent_id")) or "Subagent"
            lines.extend([f"## Subagent: {description}", ""])
            agent_type = _optional_text(item.get("agent_type"))
            if agent_type:
                lines.extend([f"Type: {agent_type}", ""])
            _render_events(
                lines,
                session_records.record_events("claude", _display_records("claude", child_records, is_subagent=True)),
                heading_level=3,
                include_thinking=include_thinking,
                tool_details=tool_details,
            )
    return "\n".join(lines).rstrip() + "\n"


def _display_records(
    engine: str,
    records: list[dict[str, typing.Any]],
    *,
    is_subagent: bool = False,
) -> list[dict[str, typing.Any]]:
    """Markdownの読者に見せるClaude Code記録を選ぶ。"""
    if engine != "claude":
        return records
    selected: list[dict[str, typing.Any]] = []
    for record in records:
        if record.get("isSidechain") and not is_subagent:
            continue
        if record.get("type") == "user" and record.get("isMeta"):
            continue
        if record.get("type") == "queue-operation":
            content = record.get("content")
            if record.get("operation") != "enqueue" or not isinstance(content, str) or content.startswith("<"):
                continue
            selected.append({"type": "user", "timestamp": record.get("timestamp"), "message": {"content": content}})
            continue
        selected.append(record)
    selected.sort(key=lambda item: _optional_text(item.get("timestamp")) or "")
    return selected


def _render_events(
    lines: list[str],
    events: list[session_records.SessionEvent],
    *,
    heading_level: int,
    include_thinking: bool,
    tool_details: bool,
) -> None:
    """tool結果を呼出しへ結び、人とアシスタントのターンを描画する。"""
    results: dict[str, list[str]] = {}
    for event in events:
        if event.kind == "tool_result" and event.call_id and event.text:
            results.setdefault(event.call_id, []).append(event.text)
    role: str | None = None
    message_id: str | None = None
    turn: list[session_records.SessionEvent] = []
    for event in events:
        if event.kind == "user":
            next_role = "Human"
        elif event.kind in {"assistant", "thinking", "tool_call"}:
            if event.kind == "thinking" and not include_thinking:
                continue
            next_role = "Assistant"
        else:
            continue
        if role is not None and (next_role != role or next_role == "Assistant" and event.message_id != message_id):
            _render_turn(lines, role, turn, results, heading_level=heading_level, tool_details=tool_details)
            turn = []
        role = next_role
        message_id = event.message_id
        turn.append(event)
    if role is not None:
        _render_turn(lines, role, turn, results, heading_level=heading_level, tool_details=tool_details)


def _render_turn(
    lines: list[str],
    role: str,
    events: list[session_records.SessionEvent],
    results: dict[str, list[str]],
    *,
    heading_level: int,
    tool_details: bool,
) -> None:
    """1ターンの本文を元のイベント順に出す。"""
    lines.extend(["---", "", f"{'#' * heading_level} {role}", ""])
    for event in events:
        if event.kind in {"user", "assistant"} and event.text:
            lines.extend([event.text, ""])
        elif event.kind == "thinking" and event.text:
            lines.extend(["<details>", "<summary>Thinking</summary>", "", event.text, "", "</details>", ""])
        elif event.kind == "tool_call":
            _render_tool_call(lines, event, results.get(event.call_id or "", []), tool_details=tool_details)


def _render_tool_call(
    lines: list[str],
    event: session_records.SessionEvent,
    results: list[str],
    *,
    tool_details: bool,
) -> None:
    """ツール入力と対応する結果を折り畳み可能な表示へまとめる。"""
    raw_input = event.detail.get("input") if event.detail else None
    summary = _tool_summary(event.name or "Unknown", raw_input)
    if not tool_details:
        lines.extend([f"> Tool: {summary}", ""])
        return
    lines.extend(["<details>", f"<summary>Tool: {html.escape(summary)}</summary>", ""])
    if raw_input is not None:
        input_text = json.dumps(raw_input, ensure_ascii=False, indent=2) if not isinstance(raw_input, str) else raw_input
        lines.extend([*_fenced(input_text, "json"), ""])
    if results:
        result_text = "\n".join(results)
        if len(result_text) > _TOOL_RESULT_MAX_CHARS:
            result_text = result_text[:_TOOL_RESULT_MAX_CHARS] + "\n\n...（以下省略）"
        lines.extend(["**Result:**", "", *_fenced(result_text), ""])
    lines.extend(["</details>", ""])


def _tool_summary(name: str, raw_input: typing.Any) -> str:
    """ツール名と利用者が識別する入力の短い要約を返す。"""
    parsed = raw_input
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            parsed = {}
    inputs = parsed if isinstance(parsed, dict) else {}
    keys = {
        "Bash": "command",
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "Grep": "pattern",
        "Glob": "pattern",
        "Agent": "description",
        "Skill": "skill",
        "TaskCreate": "subject",
        "TaskUpdate": "taskId",
    }
    value = inputs.get(keys.get(name, ""))
    if not isinstance(value, str):
        return name
    if name == "Bash" and len(value) > 80:
        value = value[:77] + "..."
    if name in {"Agent", "TaskCreate"}:
        return f"{name} — {value}"
    prefix = "#" if name == "TaskUpdate" else ""
    return f"{name} — `{prefix}{value}`"


def _fenced(value: str, language: str = "") -> list[str]:
    """入力に含まれるバッククォートより長いコードフェンスを選ぶ。"""
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", value, fence]


def _period(started_at: str | None, ended_at: str | None) -> str | None:
    """開始と終了の時刻を表の1行にまとめる。"""
    if started_at is None:
        return None
    start = _format_timestamp(started_at)
    end = _format_timestamp(ended_at) if ended_at else start
    return f"{start} 〜 {end}"


def _format_timestamp(value: str) -> str:
    """ISO 8601を人間が読みやすい時刻へ変換する。"""
    parsed = _parse_timestamp(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S %Z") if parsed is not None else value


def _parse_timestamp(value: str) -> datetime.datetime | None:
    """日付順と表示に使えるタイムゾーン付き時刻へ変換する。"""
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def _optional_text(value: typing.Any) -> str | None:
    """空でない文字列だけを受け取る。"""
    return value if isinstance(value, str) and value else None


def _escape_cell(value: str) -> str:
    """メタデータの区切り文字と改行を表のセル内に保つ。"""
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
