"""入口のスキル`atk-mode`の本文が、圧縮後に付け直される範囲に収まり、作業の名前がhookの読み替えと一致することを確かめる。"""

import re
from pathlib import Path

from agent_toolkit._common import skill_modes

_ENTRY = Path(__file__).resolve().parent / skill_modes.ENTRY_SKILL / "SKILL.md"

# Claude Codeは自動圧縮の後、起動済みのスキルの本文を先頭の5,000トークンまでしか付け直さない。
# トークナイザーを試験で使えないため文字数で確かめる。
# 2026年11月19日の実測は3,590文字で2,727トークン（1文字0.76トークン）であり、
# 1文字1.1トークンまで増えても5,000トークンに収まる文字数を上限にする。
_MAX_CHARACTERS = 4_500


# test-copy-contract: 圧縮後に付け直される範囲へ入口の本文の全体が収まることを、本文の量で保証する。
def test_entry_body_fits_reattached_range() -> None:
    """上限を超える追記は、作業ごとの手順書か原則の本文へ移す。"""
    assert len(_ENTRY.read_text(encoding="utf-8")) <= _MAX_CHARACTERS


def test_entry_table_names_every_work_the_hooks_resolve() -> None:
    """表の作業の名前とhookが読み替える作業の名前がずれると、名指しした作業の状態が設定されない。"""
    rows = re.findall(r"^\| `([a-z-]+)[ `]", _ENTRY.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert set(rows) == {*skill_modes.ENTRY_WORKS, "subagent"}
