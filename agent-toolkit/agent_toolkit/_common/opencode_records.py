"""OpenCodeのSQLite記録を読取専用スナップショットから証拠抽出へ渡す。

対象契約はOpenCode v1.19.3のpackages/core/src/session/sql.tsと
packages/schema/src/v1/session.ts（session/message/partとJSON data）である。
各記録位置のlineはセッション内のmessage・part順から得た論理位置であり、
元のテーブル名と行IDをopencode_sourceへ保持する。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from agent_toolkit._common.session_family import _CollectedRecord, _Record, _UnresolvedRecord


def database_path() -> Path:
    """OpenCodeが両OSで使うXDG形式のデータ保存先を返す。"""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "opencode" / "opencode.db"


def _object(value: str, source: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"OpenCode記録がJSON objectでない: {source}")
    return parsed


def _timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.datetime.fromtimestamp(value / 1000, datetime.UTC).isoformat()
    return None


def _message_records(connection: sqlite3.Connection, session: sqlite3.Row) -> list[_Record]:
    records: list[_Record] = []
    messages = connection.execute(
        "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created, id", (session["id"],)
    ).fetchall()

    def add(message_id: str, entry: dict[str, Any], source: dict[str, Any]) -> None:
        entry.update(sessionID=session["id"], _opencode_sqlite=True, opencode_source=source)
        entry.setdefault("cwd", session["directory"])
        message = entry.get("message")
        if isinstance(message, dict):
            message.setdefault("id", message_id)
        records.append(_Record(len(records) + 1, json.dumps(entry, ensure_ascii=False), entry))

    for message_row in messages:
        message_id = message_row["id"]
        message = _object(message_row["data"], f"message:{message_id}")
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise ValueError(f"OpenCode messageのroleを解釈できない: {message_id}: {role}")
        parts = connection.execute(
            "SELECT id, time_created, data FROM part WHERE session_id = ? AND message_id = ? ORDER BY id",
            (session["id"], message_id),
        ).fetchall()
        message_time = message.get("time", {})
        model = message.get("model", {}) if role == "user" else message
        identity = {"model": model.get("modelID"), "provider": model.get("providerID")}
        for part_row in parts:
            part = _object(part_row["data"], f"part:{part_row['id']}")
            source = {"table": "part", "id": part_row["id"], "message_id": message_id, "data": part}
            kind = part.get("type")
            part_time = part.get("time", {})
            timestamp = _timestamp(part_time.get("start", message_time.get("created", message_row["time_created"])))
            entry: dict[str, Any] = {"type": role, "timestamp": timestamp}
            content: list[dict[str, Any]] = []
            if kind == "text":
                content = [{"type": "text", "text": part.get("text", "")}]
                entry["isMeta"] = part.get("synthetic") is True or part.get("ignored") is True
            elif kind == "reasoning":
                content = [{"type": "thinking", "thinking": part.get("text", "")}]
            elif kind == "tool":
                state = part.get("state", {})
                tool_time = state.get("time", {})
                entry.update(type="assistant", timestamp=_timestamp(tool_time.get("start", part_row["time_created"])))
                content = [
                    {"type": "tool_use", "id": part.get("callID"), "name": part.get("tool"), "input": state.get("input")}
                ]
                entry["message"] = {"role": "assistant", "content": content, **identity}
                add(message_id, entry, source)
                if state.get("status") not in {"completed", "error"}:
                    continue
                entry = {"type": "user", "timestamp": _timestamp(tool_time.get("end", part_row["time_created"]))}
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": part.get("callID"),
                        "content": state.get("output", state.get("error", "")),
                        "is_error": state.get("status") == "error",
                    }
                ]
            elif kind == "compaction":
                entry.update(type="system", subtype="compact_boundary")
            else:
                # step-finishの使用量はmessageの合計と重複するため、原文だけを保持する。
                entry["type"] = "opencode-part"
            entry["message"] = {"role": entry["type"], "content": content, **identity}
            add(message_id, entry, source)
        if role == "assistant":
            if message.get("error") is not None:
                add(
                    message_id,
                    {
                        "type": "user",
                        "timestamp": _timestamp(message_time.get("completed", message_row["time_created"])),
                        "_opencode_error": True,
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": f"{message_id}:error",
                                    "is_error": True,
                                    "content": json.dumps(message["error"], ensure_ascii=False),
                                }
                            ],
                        },
                    },
                    {"table": "message", "id": message_id, "data": message},
                )
            tokens = message.get("tokens", {})
            cache = tokens.get("cache", {})
            usage = {
                "input_tokens": tokens.get("input"),
                "output_tokens": tokens.get("output"),
                "cache_read_input_tokens": cache.get("read"),
                "cache_creation_input_tokens": cache.get("write"),
            }
            add(
                message_id,
                {
                    "type": "assistant",
                    "timestamp": _timestamp(message_time.get("completed", message_row["time_created"])),
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "usage": usage,
                        **identity,
                        "stop_reason": "end_turn" if message.get("finish") == "stop" else message.get("finish"),
                    },
                },
                {"table": "message", "id": message_id, "data": message},
            )
    if not records:
        raise ValueError(f"OpenCodeセッションに読み取れるmessage・partが無い: {session['id']}")
    return records


def collect_records(
    session_id: str,
    database: Path | None = None,
    *,
    main_only: bool = False,
) -> tuple[list[_CollectedRecord], list[_UnresolvedRecord]]:
    """指定セッションとparent_idでつながる子孫を、同じ読取transactionから取得する。

    不在・未対応schema・破損JSONはValueErrorで返し、SQLite原本を作成・変更しない。
    """
    if re.fullmatch(r"ses_[A-Za-z0-9]+", session_id) is None:
        raise ValueError(f"OpenCodeセッション識別子が不正: {session_id}")
    path = (database if database is not None else database_path()).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"OpenCodeのSQLite記録が見つからない: {path}")
    try:
        with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            sessions = connection.execute(
                "WITH RECURSIVE family(id) AS (SELECT id FROM session WHERE id = ? "
                "UNION SELECT s.id FROM session s JOIN family f ON s.parent_id = f.id) "
                "SELECT id, parent_id, directory, time_created FROM session WHERE id IN (SELECT id FROM family) "
                "ORDER BY time_created, id",
                (session_id,),
            ).fetchall()
            main = next((row for row in sessions if row["id"] == session_id), None)
            if main is None:
                raise ValueError(f"OpenCodeセッションがSQLiteに無い: {session_id}: {path}")
            selected = [main] if main_only else [main, *(row for row in sessions if row["id"] != session_id)]
            collected: list[_CollectedRecord] = []
            unresolved: list[_UnresolvedRecord] = []
            for row in selected:
                try:
                    records = _message_records(connection, row)
                except ValueError:
                    if row["id"] == session_id:
                        raise
                    unresolved.append(_UnresolvedRecord(f"opencode:{row['id']}", 0))
                    continue
                collected.append(
                    _CollectedRecord(
                        f"opencode:{row['id']}",
                        path,
                        records,
                        "opencode",
                        None if row["id"] == session_id else f"opencode:{row['parent_id']}",
                        None,
                        None,
                        "main" if row["id"] == session_id else "session",
                        ("main",) if row["id"] == session_id else (),
                    )
                )
            return collected, unresolved
    except (sqlite3.Error, json.JSONDecodeError, TypeError, AttributeError) as error:
        raise ValueError(f"OpenCode SQLite記録を取得できない: {path}: {error}") from error
