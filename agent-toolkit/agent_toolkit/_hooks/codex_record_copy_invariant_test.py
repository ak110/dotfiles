"""取得済みの判定がCodexの保存用写しへ依存しないことを検証する。"""

import pathlib

_HOOKS = pathlib.Path(__file__).resolve().parent


def test_record_copy_is_not_used_for_read_acquisition():
    offenders = [
        str(path.relative_to(_HOOKS))
        for path in sorted(_HOOKS.rglob("*.py"))
        if not path.name.endswith("_test.py") and "aggregated_output" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"記録の写しを取得判定に使っている: {offenders}。公開読取の成功を使え"
