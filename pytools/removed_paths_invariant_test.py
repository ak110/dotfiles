"""撤去表の全項目が登録日を持つことを確かめる不変条件テスト。

対象はpost-applyの撤去表2件と`update_claude_settings`の除去表5件。
期限の判定は日付で結果が変わるため、ここでは行わず定期実行のAuditへ置く（規則は`pytools/_internal/removal_registry.py`）。
"""

import datetime

from pytools._internal import removal_registry_report


def test_all_entries_have_registration_date() -> None:
    """7つの表の全項目が日付型の登録日を持つ。"""
    missing = [
        f"{name}: {entry!r}"
        for name, entries in removal_registry_report.tables().items()
        for entry in entries
        if not isinstance(getattr(entry, "registered", None), datetime.date)
    ]
    assert not missing, "登録日の無い撤去表の項目:\n" + "\n".join(missing)
