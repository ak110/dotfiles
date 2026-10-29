"""APIクレジットの残高不足の観測を、agents_serverのプロセス内で一定時間だけエンジン別に共有する。

委譲先の使用順が`credits`を先頭に置く設定では、残高不足の間に起動する新しい委譲先が毎回クレジットで失敗し、
次の接続への切替と再開の往復を繰り返す。残高不足を観測した時刻をエンジンごとにプロセス内へ保持し、
`CREDIT_SHORTAGE_TTL_SECONDS`以内に起動する委譲先では`credits`の候補を後ろへ回す（候補からは外さない）。

APIクレジットは月1回の付与などで外部から回復するため、観測は永続化しない。
期限を過ぎるか、agents_serverのプロセスを再起動した後は、通常の使用順で`credits`を再び試す。
利用上限など残高不足以外の理由の失敗は対象にしない。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

CREDIT_SHORTAGE_TTL_SECONDS = 60 * 60
"""残高不足の観測を新しい委譲先の使用順へ反映する期間（秒）。"""

_CREDIT_SHORTAGE_MARKERS = (
    "credit balance is too low",
    "credit balance too low",
    "billing_error",
    "insufficient credits",
)
"""Claude APIの残高不足を示すエラーの文言（小文字）。利用上限の文言は含めない。"""

_observations: dict[str, float] = {}
"""エンジン名ごとにプロセス内でだけ保持する観測時刻（`time.monotonic()`の値）。"""


def is_credit_shortage(text: str) -> bool:
    """Claude APIのエラーの本文が残高不足を示すかを返す。"""
    folded = text.casefold()
    return any(marker in folded for marker in _CREDIT_SHORTAGE_MARKERS)


def record_shortage(engine: str, *, now: float | None = None) -> None:
    """エンジンの`credits`接続で残高不足を観測した時刻を記録する。"""
    _observations[engine] = time.monotonic() if now is None else now


def clear() -> None:
    """記録した全エンジンの観測を削除する。"""
    _observations.clear()


def shortage_active(engine: str, *, now: float | None = None) -> bool:
    """エンジンの残高不足の観測から`CREDIT_SHORTAGE_TTL_SECONDS`未満であるかを返す。"""
    observed_at = _observations.get(engine)
    if observed_at is None:
        return False
    current = time.monotonic() if now is None else now
    return current - observed_at < CREDIT_SHORTAGE_TTL_SECONDS


def order[Item](
    items: Sequence[Item], kind_of: Callable[[Item], str], *, engine: str, now: float | None = None
) -> tuple[Item, ...]:
    """観測が有効な間は、種別が`credits`の候補を他の候補の後ろへ回した順序を返す。"""
    if not shortage_active(engine, now=now):
        return tuple(items)
    return tuple(item for item in items if kind_of(item) != "credits") + tuple(
        item for item in items if kind_of(item) == "credits"
    )
