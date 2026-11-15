"""利用枠の一時指定・許可の保存形式を、設定CLIと観測処理で共有する。"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import pathlib
import time
from typing import Any

from agent_toolkit._common import state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock


def context_id() -> str:
    """認証内容を複製せず、同じホスト設定の観測だけを再利用する。"""
    values = [os.environ.get(key) for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HOME", "USERPROFILE")]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def read_metadata(engine: str) -> dict[str, Any]:
    return read_settings().get("metadata", {}).get(f"{context_id()}:{engine}", {})


def settings_path() -> pathlib.Path:
    return state_paths.state_dir() / "usage-settings.json"


def read_settings() -> dict[str, Any]:
    try:
        value = json.loads(settings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise ValueError("利用枠の一時設定はJSONオブジェクトで保存する")
    return value


def deadline(value: str) -> float:
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("期限はタイムゾーンを含む日時で指定する")
    return parsed.timestamp()


def temporary_settings() -> dict[str, str]:
    """期限切れは現在の通常設定へ戻し、過去の保存値を書き戻さない。"""
    state = read_settings()
    until = state.get("until")
    if until is not None and (not isinstance(until, str) or deadline(until) <= time.time()):
        return {}
    values = state.get("values", {})
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()
    ):
        raise ValueError("一時設定のキーと値は文字列で保存する")
    return values


def record_metadata(engine: str, metadata: dict[str, Any]) -> None:
    """契約から読めた課金・reset情報を残量と分けて表示する。"""
    try:
        _save_metadata(engine, metadata)
    except (OSError, ValueError, KeyError, TypeError) as error:
        logging.getLogger("agent-toolkit.usage").warning("利用枠の付随情報を保存できません: %s", error)


def _save_metadata(engine: str, metadata: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+", encoding="utf-8") as lock:
        acquire_lock(lock)
        try:
            state = read_settings()
            entries = state.setdefault("metadata", {})
            key = f"{context_id()}:{engine}"
            entries[key] = {**entries.get(key, {}), **metadata, "observed_at": time.time()}
            if len(entries) > 256:
                state["metadata"] = dict(sorted(entries.items(), key=lambda item: item[1]["observed_at"])[-256:])
            atomic_write(path, json.dumps(state, ensure_ascii=False, allow_nan=False) + "\n")
        finally:
            release_lock(lock)


def validate_permissions(value: str) -> None:
    entries = json.loads(value)
    if not isinstance(entries, list):
        raise ValueError("reset許可は期間・対象・回数を持つJSON配列で指定する")
    identifiers = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("engine") not in {"claude", "codex"}:
            raise ValueError("reset許可にはengineを指定する")
        for field in ("id", "account", "scope"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValueError(f"reset許可には{field}を指定する")
        if entry["id"] in identifiers:
            raise ValueError("reset許可のidは重複させない")
        identifiers.add(entry["id"])
        count = entry.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("reset許可のcountは0以上の整数で指定する")
        if not isinstance(entry.get("from"), str) or not isinstance(entry.get("until"), str):
            raise ValueError("reset許可にはfromとuntilの日時を指定する")
        if deadline(entry["from"]) >= deadline(entry["until"]):
            raise ValueError("reset許可の期間はfromよりuntilを後にする")
