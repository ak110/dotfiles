"""SDKが省く利用枠情報を、同じ会話・同じ再試行のCLI記録から解決する。"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from agent_toolkit._common import claude_usage_limit, session_record_paths

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")


def retry_usage_limit(session_id: str, retry: dict[str, Any], *, started_at: str) -> claude_usage_limit.UsageLimitState | None:
    """CLIの現在の再試行と対応した429の利用枠だけを返す。対応不能なら推定しない。

    Claude Code 2.1.309の`u2`は`system/api_error`へ`error.rateLimits`を記録し、SDK向けの
    `api_retry`への変換で利用枠情報を除く。attempt・上限回数・待機時間・HTTP状態を同じ変換で対応付ける。
    再起動前の拒否と、正常応答より前に記録された古い拒否は回復の根拠へ使わない。
    """
    if retry.get("error_status") != 429:
        return None
    latest: dict[str, Any] | None = None
    try:
        record = session_record_paths.find_session_record(session_id)
        if record is None or record.engine != "claude" or len(record.paths) != 1:
            return None
        with record.paths[0].open(encoding="utf-8") as lines:
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "assistant":
                    latest = None
                if event.get("type") == "system" and event.get("subtype") == "api_error":
                    latest = event
    except (OSError, UnicodeError) as exc:
        _LOG.debug("claude_retry_record_unavailable session_id=%s error_type=%s", session_id, type(exc).__name__)
        return None
    if latest is None:
        return None
    timestamp = latest.get("timestamp")
    if not isinstance(timestamp, str):
        return None
    try:
        observed_at = datetime.datetime.fromisoformat(timestamp)
        lower_bound = datetime.datetime.fromisoformat(started_at)
        if observed_at.tzinfo is None or observed_at < lower_bound:
            return None
    except ValueError:
        return None
    error = latest.get("error")
    if not isinstance(error, dict):
        return None
    if any(
        actual != retry.get(name)
        for actual, name in (
            (latest.get("retryAttempt"), "attempt"),
            (latest.get("maxRetries"), "max_retries"),
            (latest.get("retryInMs"), "retry_delay_ms"),
            (error.get("status"), "error_status"),
        )
    ):
        return None
    limits = error.get("rateLimits")
    if not isinstance(limits, dict):
        return None
    return claude_usage_limit.from_info({**limits, "status": "rejected"})
