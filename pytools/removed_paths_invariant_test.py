"""撤去表の全項目が登録日を持ち、期限内であることを確かめる不変条件テスト。

対象はpost-applyの撤去表2件と`update_claude_settings`の除去表5件。
期限切れで失敗した場合は、報告された項目を表から外す（規則は`pytools/_internal/removal_registry.py`）。
"""

import datetime

from pytools import post_apply
from pytools._internal import removal_registry
from pytools._internal import update_claude_settings as ucs

# pylint: disable=protected-access
_TABLES: dict[str, list[removal_registry.HasRegistered]] = {
    "post_apply._REMOVED_PATHS": [entry for entries in post_apply._REMOVED_PATHS.values() for entry in entries],  # noqa: SLF001
    "post_apply._REMOVED_PATHS_IF_CONTENT": [
        entry
        for entries in post_apply._REMOVED_PATHS_IF_CONTENT.values()  # noqa: SLF001
        for entry in entries
    ],
    "update_claude_settings._REMOVED_HOOK_COMMAND_SUBSTRINGS": list(ucs._REMOVED_HOOK_COMMAND_SUBSTRINGS),  # noqa: SLF001
    "update_claude_settings._REMOVED_ENV_KEYS": list(ucs._REMOVED_ENV_KEYS),  # noqa: SLF001
    "update_claude_settings._REMOVED_KEYS": list(ucs._REMOVED_KEYS),  # noqa: SLF001
    "update_claude_settings._REMOVED_CONFIG_KEYS": list(ucs._REMOVED_CONFIG_KEYS),  # noqa: SLF001
    "update_claude_settings._REMOVED_LIST_ITEM_SUBSTRINGS": list(ucs._REMOVED_LIST_ITEM_SUBSTRINGS),  # noqa: SLF001
}


def test_all_entries_have_registration_date() -> None:
    """7つの表の全項目が日付型の登録日を持つ。"""
    missing = [
        f"{name}: {entry!r}"
        for name, entries in _TABLES.items()
        for entry in entries
        if not isinstance(getattr(entry, "registered", None), datetime.date)
    ]
    assert not missing, "登録日の無い撤去表の項目:\n" + "\n".join(missing)


def test_no_entry_is_past_retention() -> None:
    """登録日から6か月を過ぎた項目が無い。"""
    report = removal_registry.expired_report(_TABLES, datetime.date.today())
    assert not report, "期限切れの撤去表の項目:\n" + "\n".join(report)
