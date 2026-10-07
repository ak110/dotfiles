"""removal_registryのテスト。"""

import datetime
from pathlib import Path

import pytest

from pytools._internal import cleanup_paths, removal_registry


@pytest.mark.parametrize(
    ("registered", "expected"),
    [
        (datetime.date(2026, 4, 8), datetime.date(2026, 10, 8)),
        (datetime.date(2026, 8, 31), datetime.date(2027, 2, 28)),
        (datetime.date(2027, 8, 31), datetime.date(2028, 2, 29)),
        (datetime.date(2026, 12, 15), datetime.date(2027, 6, 15)),
    ],
)
def test_expiry_date_adds_calendar_months(registered: datetime.date, expected: datetime.date) -> None:
    """期限は暦の月で数え、同じ日が無い月では月末とする。"""
    assert removal_registry.expiry_date(registered) == expected


def test_entry_expires_only_after_expiry_date() -> None:
    """期限日当日は期限内で、翌日から期限切れとなる。"""
    entry = removal_registry.Registered("x", datetime.date(2026, 4, 8))
    assert not removal_registry.is_expired(entry, datetime.date(2026, 10, 8))
    assert removal_registry.is_expired(entry, datetime.date(2026, 10, 9))


def test_expired_report_names_entry_and_action() -> None:
    """6か月より前に登録した項目を含む表は、その項目と表から外す対処を報告する。"""
    today = datetime.date(2026, 10, 7)
    current = removal_registry.Registered("current", datetime.date(2026, 9, 1))
    expired = cleanup_paths.RemovedPath(Path("old"), datetime.date(2026, 3, 1))

    report = removal_registry.expired_report({"_TABLE": [current, expired]}, today)

    assert len(report) == 1
    assert "_TABLE" in report[0]
    assert "old" in report[0]
    assert "2026-03-01" in report[0]
    assert "表から外して" in report[0]


def test_expired_report_is_empty_when_all_entries_are_current() -> None:
    """期限内の項目だけなら報告は空である。"""
    today = datetime.date(2026, 10, 7)
    entries = [removal_registry.Registered("a", datetime.date(2026, 4, 7)), removal_registry.Registered("b", today)]
    assert not removal_registry.expired_report({"_TABLE": entries}, today)


def test_values_extracts_registered_values() -> None:
    """登録日付きの列から値だけを順に取り出す。"""
    entries = (
        removal_registry.Registered("a", datetime.date(2026, 9, 1)),
        removal_registry.Registered("b", datetime.date(2026, 9, 2)),
    )
    assert removal_registry.values(entries) == ("a", "b")
