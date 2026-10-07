"""engineの可用性を理由に除外した起動候補の記録（`unavailable-candidates.json`）の読み書き。"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
from collections.abc import Callable
from typing import Any

from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock

UNAVAILABLE_CANDIDATES_RETENTION_SECONDS = 1800.0
"""可用性を理由に除外した候補を、最終除外時刻から保持し続ける秒数。

上限の回復を検知する遅れと、枯渇した候補を先頭から試す待機の発生頻度との兼ね合いで選んだ値である。
"""


def unavailable_candidates_path(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """可用性を理由に除外した候補の記録ファイルを返す。

    `list_root_session_ids`は`agents-server`直下のディレクトリをルートsession識別子として列挙するため、
    この階層へはディレクトリではなく単一のファイルとして置く。
    """
    root = _state_paths.state_dir() if state_root is None else state_root
    return root / "agents-server" / "unavailable-candidates.json"


def _read_unavailable_entries(path: pathlib.Path) -> list[dict[str, Any]]:
    """記録ファイルのうち、書式に適合する項目だけを返す。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _unavailable_entry_is_live(entry: dict[str, Any], now: datetime.datetime) -> bool:
    """最終除外時刻から保持期間内の項目かを返す。"""
    try:
        excluded_at = datetime.datetime.fromisoformat(str(entry.get("excluded_at")))
    except (TypeError, ValueError):
        return False
    if excluded_at.tzinfo is None:
        return False
    return (now - excluded_at).total_seconds() < UNAVAILABLE_CANDIDATES_RETENTION_SECONDS


def load_unavailable_candidates(
    model_type: str,
    launch_kind: str,
    *,
    now: datetime.datetime,
    state_root: pathlib.Path | None = None,
) -> dict[tuple[str, str | None, str | None], str]:
    """指定した起動条件で除外中の候補を、候補ごとの除外理由とともに返す。

    保持期間を過ぎた項目は返さない。呼び出し元は残った候補だけを除外集合として扱う。
    """
    result: dict[tuple[str, str | None, str | None], str] = {}
    for entry in _read_unavailable_entries(unavailable_candidates_path(state_root)):
        if entry.get("model_type") != model_type or entry.get("launch_kind") != launch_kind:
            continue
        if not _unavailable_entry_is_live(entry, now):
            continue
        engine = entry.get("engine")
        if not isinstance(engine, str):
            continue
        model = entry.get("model")
        effort = entry.get("effort")
        result[(engine, model if isinstance(model, str) else None, effort if isinstance(effort, str) else None)] = str(
            entry.get("reason", "")
        )
    return result


def _update_unavailable_candidates(
    path: pathlib.Path,
    *,
    now: datetime.datetime,
    replace: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> None:
    """記録ファイルを排他区間で読み書きし、保持期間を過ぎた項目を取り除く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / ".unavailable-candidates.lock"
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=True)
        try:
            entries = [entry for entry in _read_unavailable_entries(path) if _unavailable_entry_is_live(entry, now)]
            atomic_write(
                path,
                json.dumps({"version": 1, "entries": replace(entries)}, ensure_ascii=False) + "\n",
            )
        finally:
            release_lock(lock_file)


def record_unavailable_candidate(
    model_type: str,
    launch_kind: str,
    candidate: tuple[str, str | None, str | None],
    reason: str,
    *,
    now: datetime.datetime,
    state_root: pathlib.Path | None = None,
) -> None:
    """可用性を理由に除外した候補の最終除外時刻を記録する。"""
    engine, model, effort = candidate

    def replace(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept = [entry for entry in entries if not _matches_candidate(entry, model_type, launch_kind, engine, model, effort)]
        kept.append(
            {
                "model_type": model_type,
                "launch_kind": launch_kind,
                "engine": engine,
                "model": model,
                "effort": effort,
                "reason": reason,
                "excluded_at": now.isoformat(),
            }
        )
        return kept

    _update_unavailable_candidates(unavailable_candidates_path(state_root), now=now, replace=replace)


def clear_unavailable_candidate(
    model_type: str,
    launch_kind: str,
    candidate: tuple[str, str | None, str | None],
    *,
    now: datetime.datetime,
    state_root: pathlib.Path | None = None,
) -> None:
    """起動が成立した候補の除外記録を取り除く。

    その候補の記録が無い場合は書き込まない。起動のたびに記録ファイルを作成しないためである。
    """
    engine, model, effort = candidate
    if candidate not in load_unavailable_candidates(model_type, launch_kind, now=now, state_root=state_root):
        return

    def replace(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [entry for entry in entries if not _matches_candidate(entry, model_type, launch_kind, engine, model, effort)]

    _update_unavailable_candidates(unavailable_candidates_path(state_root), now=now, replace=replace)


def _matches_candidate(
    entry: dict[str, Any],
    model_type: str,
    launch_kind: str,
    engine: str,
    model: str | None,
    effort: str | None,
) -> bool:
    """記録した項目が指定した起動条件と候補の組に一致するかを返す。"""
    return (
        entry.get("model_type") == model_type
        and entry.get("launch_kind") == launch_kind
        and entry.get("engine") == engine
        and entry.get("model") == model
        and entry.get("effort") == effort
    )


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")
