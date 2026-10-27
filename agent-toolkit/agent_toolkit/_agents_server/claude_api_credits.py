"""Claude APIの残高不足の観測を、agents_serverのプロセス内で一定時間だけ共有する。

委譲先の接続順がAPIを先頭に置く設定では、残高不足の間に起動する新しい委譲先が毎回APIで失敗し、
サブスクリプションへの切替と再開の往復を繰り返す。残高不足を観測した時刻をプロセス内に保持し、
`CREDIT_SHORTAGE_TTL_SECONDS`以内に起動する委譲先ではAPIの候補を後ろへ回す。

API残高は無料クレジットの付与などで外部から回復するため、観測は永続化しない。
期限を過ぎるか、agents_serverのプロセスを再起動した後は、通常の接続順でAPIを再び試す。
利用上限など残高不足以外の理由の失敗は対象にしない。
"""

from __future__ import annotations

import time

from agent_toolkit._atk import claude_connection

CREDIT_SHORTAGE_TTL_SECONDS = 60 * 60
"""残高不足の観測を新しい委譲先の接続順へ反映する期間（秒）。"""

_CREDIT_SHORTAGE_MARKERS = (
    "credit balance is too low",
    "credit balance too low",
    "billing_error",
    "insufficient credits",
)
"""残高不足を示すAPIエラーの文言（小文字）。利用上限の文言は含めない。"""

_OBSERVED_AT_KEY = "observed_at"
_observations: dict[str, float] = {}
"""プロセス内でだけ保持する観測時刻（`time.monotonic()`の値）。"""


def is_credit_shortage(text: str) -> bool:
    """エラーの本文が残高不足を示すかを返す。"""
    folded = text.casefold()
    return any(marker in folded for marker in _CREDIT_SHORTAGE_MARKERS)


def record_shortage(*, now: float | None = None) -> None:
    """残高不足を観測した時刻を記録する。"""
    _observations[_OBSERVED_AT_KEY] = time.monotonic() if now is None else now


def clear() -> None:
    """記録した観測を削除する。"""
    _observations.pop(_OBSERVED_AT_KEY, None)


def shortage_active(*, now: float | None = None) -> bool:
    """残高不足の観測から`CREDIT_SHORTAGE_TTL_SECONDS`未満であるかを返す。"""
    observed_at = _observations.get(_OBSERVED_AT_KEY)
    if observed_at is None:
        return False
    current = time.monotonic() if now is None else now
    return current - observed_at < CREDIT_SHORTAGE_TTL_SECONDS


def order_connections(
    connections: tuple[claude_connection.Connection, ...], *, now: float | None = None
) -> tuple[claude_connection.Connection, ...]:
    """残高不足の観測が有効な間は、APIの候補を他の候補の後ろへ回した接続順を返す。

    APIの候補は外さずに残し、サブスクリプションが使えない場合の後続候補にする。
    """
    if not shortage_active(now=now):
        return connections
    return tuple(c for c in connections if c.kind != "api") + tuple(c for c in connections if c.kind == "api")
