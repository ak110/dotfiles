"""契約・接続・提供期間からCodexの自動速度を判定する。  pyfltr: allow-comment"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

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
