"""Codexの保存済みロールアウトから、利用上限による停止と解除予定を読む。"""

from __future__ import annotations

import dataclasses
import datetime
import itertools
import json
import math
import pathlib
from collections.abc import Iterable, Iterator
from typing import Any

from agent_toolkit._common import host_homes, session_record_paths
from agent_toolkit._common.claude_usage_limit import RECHECK_SECONDS

_MTIME_SLACK_SECONDS = 60.0
"""起動時刻より前の更新時刻でも開いて判定する幅。

記録内の時刻と更新時刻のずれ（更新時刻の粒度、ネットワークファイルシステムの時計）を吸収する。
"""


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
    # マルチバイト文字の途中で終わる行も、JSONとして解析できない行と同じく解析の対象から除く。
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


def used_percent(window: dict[str, Any]) -> float | None:
    """rolloutとApp Serverの窓から使用率を返す。有限の数値でなければ確認できない値としてNoneを返す。"""
    used = window.get("used_percent", window.get("usedPercent"))
    if isinstance(used, bool) or not isinstance(used, int | float) or not math.isfinite(used):
        return None
    return float(used)


def from_rate_limits(rate_limits: Any) -> UsageLimitState:
    """rolloutとApp Serverの利用枠から、枯渇した窓の種類と解除予定を返す。"""
    windows: list[tuple[float, str | None, int | None]] = []
    if isinstance(rate_limits, dict):
        for name in ("primary", "secondary"):
            window = rate_limits.get(name)
            if not isinstance(window, dict):
                continue
            used = used_percent(window)
            if used is None or used < 100:
                continue
            reset = window.get("resets_at", window.get("resetsAt"))
            reset = reset if isinstance(reset, int) and not isinstance(reset, bool) else None
            minutes = window.get("window_minutes", window.get("windowDurationMins"))
            limit_type = (
                {300: "five_hour", 10080: "seven_day"}.get(minutes)
                if isinstance(minutes, int) and not isinstance(minutes, bool)
                else None
            )
            windows.append((used, limit_type, reset))
    # 同時に枯渇した窓は、全てが解除される時刻まで待つ。
    if not windows:
        return UsageLimitState(None)
    kinds = {window[1] for window in windows}
    limit_type = "seven_day" if "seven_day" in kinds else None if None in kinds else "five_hour"
    resets = [window[2] for window in windows]
    resets_at = max(value for value in resets if value is not None) if all(value is not None for value in resets) else None
    return UsageLimitState(resets_at, limit_type)


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
                    from_rate_limits(rate_limits)
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
    sessions = (host_homes.codex_home() if codex_home is None else codex_home) / "sessions"
    # 作成日の日付ディレクトリを先に探し、そこに無い記録とUUIDv7でないthread IDは全期間から探す。
    candidates = itertools.chain(
        (
            path
            for directory in session_record_paths.codex_thread_directories(sessions, thread_id) or ()
            for path in directory.glob("rollout-*.jsonl")
        ),
        sessions.glob("*/*/*/rollout-*.jsonl"),
    )
    found = next((path for path in candidates if path.name.endswith(f"-{thread_id}.jsonl")), None)
    return from_rollout(found) if found is not None else None


def find_session_rollout(
    cwd: pathlib.Path, started_at: float, *, codex_home: pathlib.Path | None = None
) -> pathlib.Path | None:
    """子のcwdと起動時刻に一致する最上位のTUI記録を返す。曖昧な対応は保留する。"""
    root = host_homes.codex_home() if codex_home is None else codex_home
    candidates: list[pathlib.Path] = []
    for path in (root / "sessions").glob("*/*/*/rollout-*.jsonl"):
        try:
            # 起動より前から書き込みの無い記録は、起動後に始まった会話でも再開した会話でもないため開かない。
            if path.stat().st_mtime < started_at - _MTIME_SLACK_SECONDS:
                continue
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
