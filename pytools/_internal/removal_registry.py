"""撤去表（配布元から廃止した項目を配布先から除去するための表）の登録日を扱う。

撤去表の各項目は登録日を持ち、登録日から`RETENTION_MONTHS`か月を過ぎた項目は表から外す。
配布先の旧項目は、通常の更新を続けている環境ならその期間内の`post-apply`で除去済みとなるためである。

登録日は、その項目を撤去表へ最初に加えたcommitのauthor dateとする。
表のあるファイルが改名・移設された項目は、`git log -S '<値>' --reverse`を移設前のファイルも含めて実行し、
最古の追加を採用する。`git blame`の日付は最終変更日のため登録日に使わない。
期限は登録日に暦の月を`RETENTION_MONTHS`だけ加えた日（その月に同じ日が無い場合は月末）とし、
今日が期限より後になった項目を期限切れとする。

期限の検出は、日付で結果が変わるためcommit hookやpush時のCIでは行わない。
期限は配布先の旧項目が除去済みになる期間の目安で、超過しても無関係な変更を止める必要は無いためである。
定期実行のAudit workflowが`removal_registry_report`を毎日実行し、期限の`NOTICE_DAYS`日前から項目と対処を報告して失敗する。
報告された項目は、期限前に表から外す。
"""

import calendar
import dataclasses
import datetime
from collections.abc import Iterable, Mapping
from typing import Protocol

RETENTION_MONTHS = 6
# 期限の何日前から報告するか。定期実行の失敗に気付いて表から外すまでの猶予として置く。
NOTICE_DAYS = 14


class HasRegistered(Protocol):
    """登録日を持つ撤去表の項目。"""

    @property
    def registered(self) -> datetime.date:
        """撤去表へ登録した日。"""
        ...


@dataclasses.dataclass(frozen=True)
class Registered[T]:
    """登録日付きの撤去表の値。"""

    value: T
    registered: datetime.date


def values[T](entries: Iterable[Registered[T]]) -> tuple[T, ...]:
    """登録日付きの値の列から値だけを取り出す。"""
    return tuple(entry.value for entry in entries)


def expiry_date(registered: datetime.date, months: int = RETENTION_MONTHS) -> datetime.date:
    """登録日に暦の月を加えた期限日を返す。同じ日が無い月では月末とする。"""
    month_index = registered.month - 1 + months
    year = registered.year + month_index // 12
    month = month_index % 12 + 1
    day = min(registered.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def is_expired(entry: HasRegistered, today: datetime.date) -> bool:
    """今日が登録日からの期限を過ぎていれば真を返す。"""
    return today > expiry_date(entry.registered)


def is_due(entry: HasRegistered, today: datetime.date, notice_days: int = NOTICE_DAYS) -> bool:
    """今日が期限の`notice_days`日前以降なら真を返す。"""
    return today >= expiry_date(entry.registered) - datetime.timedelta(days=notice_days)


def due_report(
    tables: Mapping[str, Iterable[HasRegistered]], today: datetime.date, notice_days: int = NOTICE_DAYS
) -> list[str]:
    """表名ごとの項目から期限切れと期限間近の項目を探し、項目と対処を示す行を返す。該当が無ければ空。"""
    lines: list[str] = []
    for table_name, entries in tables.items():
        for entry in entries:
            if not is_due(entry, today, notice_days):
                continue
            expiry = expiry_date(entry.registered)
            state = "の期限を過ぎた" if is_expired(entry, today) else "が近い"
            lines.append(
                f"{table_name}: {entry!r} は登録日 {entry.registered} から{RETENTION_MONTHS}か月の期限"
                f" {expiry} {state}ため、表から外してください"
            )
    return lines
