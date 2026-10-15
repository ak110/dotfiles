"""Codexの保存済みロールアウトから、利用上限による停止と解除予定を読む。"""

from __future__ import annotations

import dataclasses
import datetime
import json
import math
import pathlib
from collections.abc import Iterable, Iterator
from typing import Any

from agent_toolkit._common import host_homes
from agent_toolkit._common.claude_usage_limit import RECHECK_SECONDS


@dataclasses.dataclass(frozen=True)
class UsageLimitState:
    """新しいturnによって解除されていない上限失敗。"""

    resets_at: int | None
    limit_type: str | None = None

    def delay_seconds(self, now: float) -> float:
        """解除予定までの秒数か、予定が不明・経過済みの場合の再確認間隔を返す。"""
        return self.resets_at - now if self.resets_at is not None and self.resets_at > now else RECHECK_SECONDS

    def resets_at_iso(self) -> str | None:
        """解除予定をUTCのISO 8601で返す。"""
        return datetime.datetime.fromtimestamp(self.resets_at, datetime.UTC).isoformat() if self.resets_at is not None else None

    def describe(self) -> str:
        """待機する利用枠と解除予定を説明する。"""
        resets = self.resets_at_iso() or "不明（一定間隔で確認する）"
        return f"Codexの利用上限（{self.limit_type or '不明'}）の解除待ち。解除予定: {resets}"


def thread_id_from_stream_lines(lines: Iterable[str]) -> str | None:
    """`codex exec --json`のthread.startedからthread IDを得る。"""
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


def _records(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def _limit(rate_limits: Any) -> UsageLimitState:
    windows: list[tuple[float, str, int | None]] = []
    if isinstance(rate_limits, dict):
        for name in ("primary", "secondary"):
            window = rate_limits.get(name)
            if not isinstance(window, dict):
                continue
            used = window.get("used_percent")
            if isinstance(used, bool) or not isinstance(used, int | float) or not math.isfinite(used) or used < 100:
                continue
            reset = window.get("resets_at")
            reset = reset if isinstance(reset, int) and not isinstance(reset, bool) else None
            windows.append((used, name, reset))
    # 同時に枯渇した窓は、全てが解除される時刻まで待つ。
    known = [window for window in windows if window[2] is not None]
    selected = max(known, key=lambda window: window[2] or 0) if known else (max(windows) if windows else None)
    return UsageLimitState(selected[2], selected[1]) if selected is not None else UsageLimitState(None)


def from_rollout(path: pathlib.Path) -> UsageLimitState | None:
    """上限のtask_completeを読み、後続turn開始・終了があれば停止状態を解く。"""
    latest: UsageLimitState | None = None
    rate_limits: Any = None
    try:
        for record in _records(path):
            payload = record.get("payload")
            if record.get("type") != "event_msg" or not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if kind == "token_count":
                rate_limits = payload.get("rate_limits")
            elif kind == "task_started":
                latest = None
            elif kind == "task_complete":
                error = payload.get("error")
                latest = (
                    _limit(rate_limits)
                    if isinstance(error, dict) and error.get("codex_error_info") == "usage_limit_exceeded"
                    else None
                )
    except OSError:
        return None
    return latest


def from_thread(thread_id: str | None, *, codex_home: pathlib.Path | None = None) -> UsageLimitState | None:
    """execが報告したthread IDと一致する保存記録を判定する。"""
    if not thread_id:
        return None
    root = host_homes.codex_home() if codex_home is None else codex_home
    for path in (root / "sessions").glob("*/*/*/rollout-*.jsonl"):
        if path.name.endswith(f"-{thread_id}.jsonl"):
            return from_rollout(path)
    return None


def find_session_rollout(
    cwd: pathlib.Path, started_at: float, *, codex_home: pathlib.Path | None = None
) -> pathlib.Path | None:
    """子のcwdと起動時刻に一致する最上位のTUI記録を返す。曖昧な対応は保留する。"""
    root = host_homes.codex_home() if codex_home is None else codex_home
    candidates: list[pathlib.Path] = []
    for path in (root / "sessions").glob("*/*/*/rollout-*.jsonl"):
        try:
            records = _records(path)
            meta = next(records, {})
            payload = meta.get("payload")
            if meta.get("type") != "session_meta" or not isinstance(payload, dict):
                continue
            if payload.get("cwd") != str(cwd) or payload.get("originator") != "codex-tui":
                continue
            if isinstance(payload.get("source"), dict):
                continue
            stamp = datetime.datetime.fromisoformat(payload["timestamp"]).timestamp()
            # resumeではsession_metaが元の開始時刻を保つ。再開したturnの開始でも対応付ける。
            resumed = stamp < started_at and any(
                record.get("type") == "event_msg"
                and isinstance(record.get("payload"), dict)
                and record["payload"].get("type") == "task_started"
                and datetime.datetime.fromisoformat(record["timestamp"]).timestamp() >= started_at
                for record in records
            )
            if stamp >= started_at or resumed:
                candidates.append(path)
        except OSError, ValueError, KeyError, TypeError:
            continue
    return candidates[0] if len(candidates) == 1 else None
