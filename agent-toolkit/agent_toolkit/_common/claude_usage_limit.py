"""Claude Codeの利用上限を、解除まで待つ対象かどうかで分類する。

agents_server、process-loopの可用性判定と`atk commit`は同じ分類を使う。
Weekly limit（`seven_day`、`seven_day_opus`、`seven_day_sonnet`）と5時間の利用上限（`five_hour`）で
拒否された場合は、待てば解除されるため別のengineへ切り替えず、解除まで待って同じClaudeで続ける（ユーザー指示）。
HTTP状態の429・529だけからは種類を判別できないため推定せず、Claudeが出力する利用枠情報だけを根拠にする。
"""

from __future__ import annotations

import dataclasses
import datetime
import json
from collections.abc import Iterable
from typing import Any

WAIT_LIMIT_TYPES = frozenset({"seven_day", "seven_day_opus", "seven_day_sonnet", "five_hour"})
# 解除予定時刻が無いか過ぎた場合に、同じClaudeの可用性を確かめ直す間隔。
# 解除まで待ち続けるため回数と総時間の上限は置かない。間隔は確認の起動費用と解除後の再開の遅れの釣り合いで選ぶ。
RECHECK_SECONDS = 300.0
_REJECTED = "rejected"


@dataclasses.dataclass(frozen=True)
class UsageLimitState:
    """Claudeが報告した利用枠の状態。"""

    status: str
    limit_type: str | None
    resets_at: int | None

    @property
    def is_wait_target(self) -> bool:
        """解除まで待つ対象の拒否かを返す。"""
        return self.status == _REJECTED and self.limit_type in WAIT_LIMIT_TYPES

    def delay_seconds(self, now: float) -> float:
        """次に可用性を確かめるまでの秒数を返す。`now`はUNIX時刻。"""
        if self.resets_at is not None and self.resets_at > now:
            return float(self.resets_at) - now
        return RECHECK_SECONDS

    def resets_at_iso(self) -> str | None:
        """解除予定時刻をISO 8601のUTCで返す。"""
        if self.resets_at is None:
            return None
        return datetime.datetime.fromtimestamp(self.resets_at, datetime.UTC).isoformat()

    def describe(self) -> str:
        """ユーザーへ示す1行の説明を返す。"""
        resets = self.resets_at_iso() or "不明（一定間隔で確認する）"
        return f"Claude Codeの利用上限（{self.limit_type}）の解除待ち。解除予定: {resets}"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def from_info(info: Any) -> UsageLimitState | None:
    """SDKの`RateLimitInfo`か、CLIの`rate_limit_info`の辞書から状態を得る。"""
    if info is None:
        return None
    if isinstance(info, dict):
        status = info.get("status")
        limit_type = info.get("rateLimitType", info.get("rate_limit_type"))
        resets_at = info.get("resetsAt", info.get("resets_at"))
    else:
        status = getattr(info, "status", None)
        limit_type = getattr(info, "rate_limit_type", None)
        resets_at = getattr(info, "resets_at", None)
    if not isinstance(status, str) or not status:
        return None
    return UsageLimitState(status, limit_type if isinstance(limit_type, str) else None, _int_or_none(resets_at))


def from_event(event: Any) -> UsageLimitState | None:
    """SDKの`RateLimitEvent`か、CLIの`stream-json`の1イベント（辞書）から状態を得る。"""
    if isinstance(event, dict):
        if event.get("type") != "rate_limit_event":
            return None
        return from_info(event.get("rate_limit_info"))
    return from_info(getattr(event, "rate_limit_info", None))


def from_stream_lines(lines: Iterable[str]) -> UsageLimitState | None:
    """CLIの`--output-format stream-json`の出力から、最後に報告された利用枠の状態を返す。"""
    latest: UsageLimitState | None = None
    for line in lines:
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except ValueError:
            continue
        state = from_event(event) if isinstance(event, dict) else None
        if state is not None:
            latest = state
    return latest


def session_id_from_stream_lines(lines: Iterable[str]) -> str | None:
    """CLIの`stream-json`の出力から会話のsession識別子を返す。"""
    for line in lines:
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("session_id"), str) and event["session_id"]:
            return event["session_id"]
    return None


def result_from_stream_lines(lines: Iterable[str]) -> dict[str, Any] | None:
    """CLIの`stream-json`の出力から最後の`result`イベントを返す。"""
    latest: dict[str, Any] | None = None
    for line in lines:
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            event = json.loads(text)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            latest = event
    return latest
