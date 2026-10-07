"""Claude Code・Codex・Antigravityの保存済みセッション記録（JSON Lines）の形式の解釈。

記録の保存先の規約（接尾辞、Codexのロールアウトの接頭辞、ファイル名から取り出す識別子、サブエージェント記録の配置）と、
記録の行の解析、表示用のイベント列への変換を1か所に持つ。セッション画面、`atk agents`のログ出力、
process-wiの判定（`session_records`）、振り返りの証拠抽出とSSH先のリモートヘルパーがこのモジュールを使う。
保存先の規約は`agent-toolkit/skills/writing-standards/references/session-records.md`が定める。
保存先を指定しない場合に使う各ホストのホームは`agent_toolkit._common.host_homes`が解決する。
リモートヘルパーはSSH先で標準ライブラリと`platformdirs`だけを与えて起動するため、本モジュールは
それ以外のパッケージと`_atk`の他のモジュールに依存しない。
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import typing
from collections.abc import Iterator

from agent_toolkit._common.runtime_inserted import is_runtime_generated, is_runtime_inserted_text

RECORD_SUFFIX = ".jsonl"
CODEX_ROLLOUT_PREFIX = "rollout-"


def claude_session_records(claude_home: pathlib.Path) -> Iterator[pathlib.Path]:
    """Claude Codeのセッション本体の記録を返す。

    記録階層は深さ2（`<claude_home>/projects/<project>/<session-uuid>.jsonl`）をセッション本体とする。
    サブエージェント記録は深さ4に置かれ、本体とは別に`claude_subagents`で解決する。
    """
    projects = claude_home / "projects"
    if not projects.is_dir():
        return
    for project_dir in projects.iterdir():
        if not project_dir.is_dir():
            continue
        for path in project_dir.glob(f"*{RECORD_SUFFIX}"):
            if path.is_file():
                yield path


def codex_session_records(codex_home: pathlib.Path) -> Iterator[pathlib.Path]:
    """Codexのロールアウト記録（`<codex_home>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`）を返す。"""
    sessions = codex_home / "sessions"
    if not sessions.is_dir():
        return
    for path in sessions.glob(f"*/*/*/{CODEX_ROLLOUT_PREFIX}*{RECORD_SUFFIX}"):
        if path.is_file():
            yield path


def parsed_records(path: pathlib.Path) -> Iterator[dict[str, typing.Any]]:
    """記録ファイルを1行ずつ読み、JSONとして解釈できる辞書の行だけを返す。"""
    with path.open(encoding="utf-8") as record_file:
        for line in record_file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


@dataclasses.dataclass(frozen=True, slots=True)
class SessionEvent:
    """詳細画面が時系列に並べる1件の発話・操作。"""

    kind: str
    timestamp: str | None
    text: str | None = None
    name: str | None = None
    usage: dict[str, typing.Any] | None = None
    detail: dict[str, typing.Any] | None = None
    call_id: str | None = None
    message_id: str | None = None

    def to_json(self) -> dict[str, typing.Any]:
        """JSON応答向けの辞書へ変換する。"""
        result = dataclasses.asdict(self)
        result.pop("call_id")
        result.pop("message_id")
        return result


def as_text(value: typing.Any) -> str | None:
    """記録の本文欄を表示用の文字列へ正規化する。取り出せない場合は`None`を返す。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        return "\n".join(parts) if parts else None
    return None


def claude_events(records: typing.Iterable[dict[str, typing.Any]]) -> tuple[list[SessionEvent], dict[str, typing.Any]]:
    """Claude Codeの記録を表示モデルのイベント列とトークン集計へ変換する。"""
    events: list[SessionEvent] = []
    totals: dict[str, typing.Any] = {"input_tokens": None, "output_tokens": None}
    for record in records:
        kind = record.get("type")
        timestamp = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
        if kind == "system" and record.get("subtype") == "compact_boundary":
            metadata = record.get("compactMetadata")
            events.append(
                SessionEvent(
                    kind="compact_boundary",
                    timestamp=timestamp,
                    text=as_text(record.get("content")),
                    detail=dict(metadata) if isinstance(metadata, dict) else None,
                )
            )
            continue
        message = record.get("message")
        if kind not in {"user", "assistant"} or not isinstance(message, dict):
            continue
        message_id = message.get("id") if kind == "assistant" and isinstance(message.get("id"), str) else None
        runtime_generated = kind == "user" and is_runtime_generated(record)
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        if usage is not None:
            for key in ("input_tokens", "output_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    totals[key] = (totals[key] or 0) + value
        content = message.get("content")
        if not isinstance(content, list):
            text = as_text(content)
            event_kind = (
                "injected" if kind == "user" and (runtime_generated or text and is_runtime_inserted_text(text)) else kind
            )
            events.append(SessionEvent(kind=event_kind, timestamp=timestamp, text=text, usage=usage, message_id=message_id))
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            events.append(
                _claude_block_event(block, kind, timestamp, usage, runtime_generated=runtime_generated, message_id=message_id)
            )
    return events, totals


def _claude_block_event(
    block: dict[str, typing.Any],
    kind: str,
    timestamp: str | None,
    usage: dict[str, typing.Any] | None,
    *,
    runtime_generated: bool = False,
    message_id: str | None = None,
) -> SessionEvent:
    """メッセージの1ブロックを表示モデルのイベントへ変換する。"""
    block_type = block.get("type")
    if block_type == "thinking":
        return SessionEvent(kind="thinking", timestamp=timestamp, text=as_text(block.get("thinking")), message_id=message_id)
    if block_type == "tool_use":
        return SessionEvent(
            kind="tool_call",
            timestamp=timestamp,
            name=block.get("name") if isinstance(block.get("name"), str) else None,
            detail={"input": block.get("input")},
            call_id=block.get("id") if isinstance(block.get("id"), str) else None,
            message_id=message_id,
        )
    if block_type == "tool_result":
        return SessionEvent(
            kind="tool_result",
            timestamp=timestamp,
            text=as_text(block.get("content")),
            detail={"is_error": block.get("is_error")} if "is_error" in block else None,
            call_id=block.get("tool_use_id") if isinstance(block.get("tool_use_id"), str) else None,
        )
    text = as_text(block.get("text"))
    event_kind = (
        "injected"
        if kind == "user" and block_type == "text" and (runtime_generated or text and is_runtime_inserted_text(text))
        else kind
    )
    return SessionEvent(kind=event_kind, timestamp=timestamp, text=text, usage=usage, message_id=message_id)


def claude_subagents(record_path: pathlib.Path) -> list[dict[str, typing.Any]] | None:
    """セッション本体に属するサブエージェント記録の親子関係を返す。

    記録が無い場合は`None`を返し、取得不能であることを表す。
    `path`はそのサブエージェントの記録本体であり、閲覧要求の対象として使う。記録が残っていない場合は`None`とする。
    `parent_agent_id`は深さが2以上の記録にだけ現れるため、階層の復元は`spawn_depth`を典拠とする。
    """
    directory = record_path.with_suffix("") / "subagents"
    if not directory.is_dir():
        return None
    found: list[dict[str, typing.Any]] = []
    for meta_path in sorted(directory.glob("*.meta.json")):
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        # `agent_id`は`agent-<16進数>`の形であり接頭辞を含むため、記録本体の名前へ重ねて付けない。
        agent_id = meta_path.name.removesuffix(".meta.json")
        agent_record = meta_path.with_name(f"{agent_id}{RECORD_SUFFIX}")
        found.append(
            {
                "agent_id": agent_id,
                "agent_type": metadata.get("agentType"),
                "description": metadata.get("description"),
                "spawn_depth": metadata.get("spawnDepth"),
                "parent_agent_id": metadata.get("parentAgentId"),
                "model": metadata.get("model"),
                "path": str(agent_record) if agent_record.is_file() else None,
            }
        )
    return found or None


def claude_subagent_records(record_path: pathlib.Path) -> list[tuple[str, pathlib.Path, str]]:
    """セッション本体に属するサブエージェント記録を、エージェント識別子・記録のパス・親の記録のパスの組で返す。

    親は`parent_agent_id`が指す兄弟の記録とし、深さ1の記録はセッション本体を親とする。
    記録本体が残っていないものと、親を確定できないものは除く。
    """
    subagents = claude_subagents(record_path) or []
    agent_paths = {item["agent_id"]: item["path"] for item in subagents if item["path"]}
    found: list[tuple[str, pathlib.Path, str]] = []
    for item in subagents:
        child_path = item["path"]
        if not child_path:
            continue
        parent_id = item.get("parent_agent_id")
        parent_path = (
            agent_paths.get(parent_id) or agent_paths.get(f"agent-{parent_id}") if isinstance(parent_id, str) else None
        )
        if parent_path is None and item.get("spawn_depth") == 1:
            parent_path = str(record_path)
        if parent_path is None:
            continue
        found.append((item["agent_id"], pathlib.Path(child_path), parent_path))
    return found


def codex_events(records: typing.Iterable[dict[str, typing.Any]]) -> tuple[list[SessionEvent], dict[str, typing.Any]]:
    """Codexのロールアウトを表示モデルのイベント列とトークン集計へ変換する。"""
    events: list[SessionEvent] = []
    totals: dict[str, typing.Any] = {"input_tokens": None, "output_tokens": None}
    for record in records:
        timestamp = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        record_type = record.get("type")
        if record_type == "event_msg":
            _codex_apply_token_count(payload, totals)
            continue
        if record_type == "compacted":
            events.append(
                SessionEvent(
                    kind="compact_boundary",
                    timestamp=timestamp,
                    text=as_text(payload.get("message")) or None,
                    detail={"window_number": payload.get("window_number")},
                )
            )
            continue
        if record_type != "response_item":
            continue
        events.append(_codex_payload_event(payload, timestamp))
    return [event for event in events if event is not None], totals


def _codex_apply_token_count(payload: dict[str, typing.Any], totals: dict[str, typing.Any]) -> None:
    """`token_count`イベントの累計値をトークン集計へ反映する。

    Codexは累計値を通知するため、加算せず最後に観測した値で置き換える。
    """
    if payload.get("type") != "token_count":
        return
    info = payload.get("info")
    usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(usage, dict):
        return
    for key, source in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens")):
        value = usage.get(source)
        if isinstance(value, int):
            totals[key] = value


def _codex_payload_event(payload: dict[str, typing.Any], timestamp: str | None) -> SessionEvent:
    """`response_item`の1件を表示モデルのイベントへ変換する。"""
    payload_type = payload.get("type")
    if payload_type == "reasoning":
        summary = payload.get("summary")
        return SessionEvent(kind="thinking", timestamp=timestamp, text=as_text(summary))
    if payload_type in {"function_call", "custom_tool_call"}:
        return SessionEvent(
            kind="tool_call",
            timestamp=timestamp,
            name=payload.get("name") if isinstance(payload.get("name"), str) else None,
            detail={"input": payload.get("arguments", payload.get("input"))},
            call_id=payload.get("call_id") if isinstance(payload.get("call_id"), str) else None,
        )
    if payload_type in {"function_call_output", "custom_tool_call_output"}:
        output = payload.get("output")
        return SessionEvent(
            kind="tool_result",
            timestamp=timestamp,
            text=as_text(output) or _stringify(output),
            call_id=payload.get("call_id") if isinstance(payload.get("call_id"), str) else None,
        )
    role = payload.get("role")
    kind = (
        {
            "user": "user",
            "developer": "developer",
            "assistant": "assistant",
        }.get(role, "assistant")
        if isinstance(role, str)
        else "assistant"
    )
    text = as_text(payload.get("content"))
    if kind in {"user", "developer"} and text and is_runtime_inserted_text(text):
        kind = "injected"
    return SessionEvent(kind=kind, timestamp=timestamp, text=text)


def _stringify(value: typing.Any) -> str | None:
    """辞書などの構造化された値を表示用の文字列へ変換する。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def codex_metadata(records: typing.Iterable[dict[str, typing.Any]]) -> dict[str, typing.Any]:
    """`session_meta`から作業ディレクトリと開始時刻を取り出す。"""
    for record in records:
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if isinstance(payload, dict):
            return {"cwd": payload.get("cwd"), "started_at": payload.get("timestamp")}
        break
    return {"cwd": None, "started_at": None}


def codex_session_id(path: pathlib.Path) -> str:
    """ロールアウトのファイル名からthread IDを取り出す。

    ファイル名は`rollout-<日時>-<thread-id>`の形であり、thread IDはUUIDの5区画で末尾に置かれる。
    """
    stem = path.name[len(CODEX_ROLLOUT_PREFIX) : -len(RECORD_SUFFIX)]
    parts = stem.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else stem


def parse_records(text: str) -> tuple[list[dict[str, typing.Any]], int]:
    """JSON Linesを解析し、解析できた行と解析できなかった行数を返す。

    書き込み途中の行が含まれていても他の行を失わせないため、行単位で解析する。
    """
    records: list[dict[str, typing.Any]] = []
    broken = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            broken += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            broken += 1
    return records, broken


def record_events(engine: str, records: typing.Iterable[dict[str, typing.Any]]) -> list[SessionEvent]:
    """保存済み記録を件数制限のない表示イベントへ変換する。"""
    if engine == "claude":
        return claude_events(records)[0]
    if engine == "codex":
        return codex_events(records)[0]
    if engine == "agy":
        events = []
        for record in records:
            kind = record.get("type") or record.get("event")
            if not isinstance(kind, str):
                continue
            body = record.get(kind)
            if not isinstance(body, dict):
                body = record
            detail = next(
                (
                    body[key]
                    for key in ("response", "text", "text_delta", "message", "summary")
                    if isinstance(body.get(key), str)
                ),
                None,
            )
            if kind == "init" and detail is None:
                detail = record.get("conversation_id")
            events.append(SessionEvent(kind=kind, timestamp=None, text=detail))
        return events
    raise ValueError(f"unsupported engine: {engine}")
