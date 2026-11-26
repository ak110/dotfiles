"""契約・接続・提供期間からCodexの自動速度を判定する。  pyfltr: allow-comment"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from agent_toolkit._common import atomic_file, state_paths

_LOG = logging.getLogger(__name__)


_FREE_FAST_MODE_PERIODS = (
    (
        frozenset({"business", "team"}),
        datetime.datetime.fromisoformat("2027-01-01T00:00:00+09:00"),
        "https://x.com/thsottiaux/status/2125299248782778588",
    ),
)
"""提供条件を変える場合は、対象のplanType、時差付き終端、根拠URLを同じ行に記録する。  pyfltr: allow-comment"""


@dataclasses.dataclass(frozen=True)
class FastTier:
    tier: str
    explanation: str


def record_start_decision(instant: datetime.datetime, decision: FastTier) -> None:
    path = state_paths.state_dir() / "codex-fast-start.json"
    try:
        atomic_file.atomic_write(path, json.dumps({"at": instant.isoformat(), **dataclasses.asdict(decision)}))
    except OSError:
        _LOG.debug("Codexの起動時判定を保存できません", exc_info=True)


def latest_start_decision() -> tuple[str, FastTier] | None:
    path = state_paths.state_dir() / "codex-fast-start.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("tier") not in ("priority", "default"):
            return None
        instant, explanation = record.get("at"), record.get("explanation")
        if not isinstance(instant, str) or not isinstance(explanation, str):
            return None
        if datetime.datetime.fromisoformat(instant).tzinfo is None:
            return None
        return instant, FastTier(record["tier"], explanation)
    except OSError, ValueError:
        return None


async def auto_service_tier(
    request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    cwd: str,
    *,
    subscription: bool | None,
    now: datetime.datetime | None = None,
) -> FastTier:
    instant = now if now is not None else datetime.datetime.now(datetime.UTC)
    active = tuple((plans, expires) for plans, expires, _source in _FREE_FAST_MODE_PERIODS if instant < expires)
    if not active:
        return FastTier("default", "現在は提供期間外")
    if subscription is False:
        return FastTier("default", "サブスクリプション接続ではない")
    try:
        async with asyncio.timeout(3):
            if subscription is None:
                response = await request("config/read", {"cwd": cwd, "includeLayers": False})
                config = response.get("config")
                if not isinstance(config, dict) or config.get("model_provider") != "openai":
                    return FastTier("default", "サブスクリプション接続を確認できない")
            response = await request("account/read", {"refreshToken": False})
    except Exception:  # pylint: disable=broad-exception-caught
        _LOG.debug("Codexの自動速度判定に必要な接続情報を取得できません", exc_info=True)
        return FastTier("default", "接続情報を取得できない")
    account = response.get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        return FastTier("default", "ChatGPTアカウントを確認できない")
    plan = account.get("planType")
    if not isinstance(plan, str) or not re.fullmatch(r"[a-z_]{1,64}", plan):
        return FastTier("default", "planTypeを確認できない")
    for plans, expires in active:
        if plan in plans:
            return FastTier("priority", f"planType={plan}、提供終端={expires.isoformat()}")
    return FastTier("default", f"planType={plan}は提供対象外")
