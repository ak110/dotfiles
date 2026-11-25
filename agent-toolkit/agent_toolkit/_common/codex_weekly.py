from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Boundary(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)


class _LimitsEnvelope(_Boundary):
    rateLimits: dict[str, Any] | None = None
    rateLimitsByLimitId: dict[str, Any] | None = None
    rateLimitResetCredits: dict[str, Any] | None = None


class _BucketShape(_Boundary):
    limitId: str | None = None
    primary: dict[str, Any] | None = None
    secondary: dict[str, Any] | None = None


class _WindowDuration(_Boundary):
    windowDurationMins: int = Field(gt=0)


class _Window(_Boundary):
    usedPercent: float = Field(ge=0, le=100)
    windowDurationMins: int = Field(gt=0)
    resetsAt: int = Field(gt=0)


@dataclass(frozen=True)
class Weekly:
    window: _Window | None
    reset_credits: dict[str, Any] | None
    diagnostic: str = ""
    window_id: str | None = None


def select_weekly(raw: dict[str, Any]) -> Weekly:
    limits = _LimitsEnvelope.model_validate(raw)
    by_id = limits.rateLimitsByLimitId
    candidate = by_id.get("codex") if by_id is not None else limits.rateLimits
    if candidate is None:
        return Weekly(None, limits.rateLimitResetCredits, "codex利用枠を識別できない")
    bucket = _BucketShape.model_validate(candidate)
    if bucket.limitId not in (None, "codex") or (by_id is None and bucket.limitId != "codex"):
        return Weekly(None, limits.rateLimitResetCredits, "codex利用枠を識別できない")
    for label, window in (("primary", bucket.primary), ("secondary", bucket.secondary)):
        if window is not None and _WindowDuration.model_validate(window).windowDurationMins == 10080:
            return Weekly(_Window.model_validate(window), limits.rateLimitResetCredits, window_id=f"codex:{label}")
    return Weekly(None, limits.rateLimitResetCredits, "weekly利用枠がない")
