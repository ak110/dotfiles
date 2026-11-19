"""入口のスキル`atk-mode`の本文が、圧縮後に付け直される範囲に収まり、作業の名前がhookの読み替えと一致することを確かめる。"""

import re
from pathlib import Path

from agent_toolkit._common import skill_modes

_ENTRY = Path(__file__).resolve().parent / skill_modes.ENTRY_SKILL / "SKILL.md"

# Claude Codeは自動圧縮の後、起動済みのスキルの内容を先頭の5,000トークンまでしか付け直さない（公式資料のskillsの頁）。
# 付け直す内容は「Base directory for this skill: <導入先の絶対パス>」の1行と、frontmatterを除いた本文である。
# 2026年11月17〜18日のClaude Code 2.1.326・2.1.327のtranscriptで、compact後の`invoked_skills`の内容から観測した。
# トークナイザーを試験で使えないため本文の文字数で確かめる。
# 2026年11月19日の実測は3,590文字で2,727トークン（1文字0.76トークン）である。
# 1文字1.1トークンまで増えても5,000トークンに収まる約4,545文字から、
# 先頭の1行の分（約150文字）を引いた本文の文字数を上限にする。
_MAX_BODY_CHARACTERS = 4_400


# test-copy-contract: 圧縮後に付け直される範囲へ入口の本文の全体が収まることを、本文の量で保証する。
def test_entry_body_fits_reattached_range() -> None:
    """上限を超える追記は、作業ごとの手順書か原則の本文へ移す。"""
    text = _ENTRY.read_text(encoding="utf-8")
    frontmatter = re.match(r"---\n.*?\n---\n", text, flags=re.DOTALL)
    assert frontmatter is not None
    assert len(text) - frontmatter.end() <= _MAX_BODY_CHARACTERS


def test_entry_table_names_every_work_the_hooks_resolve() -> None:
    """表の作業の名前とhookが読み替える作業の名前がずれると、名指しした作業の状態が設定されない。"""
    rows = re.findall(r"^\| `([a-z-]+)[ `]", _ENTRY.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert set(rows) == {*skill_modes.ENTRY_WORKS, "subagent"}


def test_argument_hint_lists_every_named_work() -> None:
    """補完が示す作業と入口が受け付ける作業がずれると、ユーザーは名指しできる作業を補完から知れない。"""
    hint = re.search(r'^argument-hint: "\[([a-z|-]+)\]', _ENTRY.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert hint is not None
    assert set(hint[1].split("|")) == set(skill_modes.ENTRY_WORKS)
