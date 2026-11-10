"""removal_registryのテスト。"""

import datetime
from pathlib import Path

import pytest

from pytools._internal import cleanup_paths, removal_registry, removal_registry_report


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


def test_due_report_names_expired_and_near_entries_with_action() -> None:
    """期限切れと期限まで14日以内の項目を、表名・登録日・表から外す対処とともに報告する。"""
    today = datetime.date(2026, 11, 7)
    current = removal_registry.Registered("current", datetime.date(2026, 5, 22))
    near = removal_registry.Registered("near", datetime.date(2026, 5, 21))
    expired = cleanup_paths.RemovedPath(Path("old"), datetime.date(2026, 5, 6))

    report = removal_registry.due_report({"_TABLE": [current, near, expired]}, today)

    assert len(report) == 2
    assert all("_TABLE" in line and "表から外して" in line for line in report)
    assert "near" in report[0] and "2026-11-21 が近い" in report[0]
    assert "old" in report[1] and "2026-11-06 の期限を過ぎた" in report[1]


def test_due_report_is_empty_when_all_entries_are_before_notice() -> None:
    """期限の15日以上前の項目だけなら報告は空である。"""
    today = datetime.date(2026, 10, 7)
    entries = [removal_registry.Registered("a", datetime.date(2026, 4, 22)), removal_registry.Registered("b", today)]
    assert not removal_registry.due_report({"_TABLE": entries}, today)


@pytest.mark.parametrize(
    ("today", "expected_code"),
    [(datetime.date(2026, 11, 7), 0), (datetime.date(2099, 1, 1), 1)],
)
def test_report_cli_fails_only_when_entries_are_due(
    today: datetime.date, expected_code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """報告CLIは基準日に期限切れ・期限間近の項目があれば対処を示して1、無ければ0を返す。

    2026-11-07は処理時点で期限の近い項目を外した後の基準日、2099-01-01は全項目が期限切れになる基準日である。
    """
    assert removal_registry_report.main(["--today", today.isoformat()]) == expected_code
    captured = capsys.readouterr()
    if expected_code:
        assert "表から外してください" in captured.err
        assert "次の操作:" in captured.err
    else:
        assert not captured.err


def test_values_extracts_registered_values() -> None:
    """登録日付きの列から値だけを順に取り出す。"""
    entries = (
        removal_registry.Registered("a", datetime.date(2026, 9, 1)),
        removal_registry.Registered("b", datetime.date(2026, 9, 2)),
    )
    assert removal_registry.values(entries) == ("a", "b")
