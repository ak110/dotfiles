"""先送りの損失を根拠に持つ優先区分（`urgent`）と、選定順の決め方。

自動処理の選定はファイル名の昇順（古い順）を原則とするが、担当の起動を妨げる不良のように修正を遅らせると
具体的な損失が続く項目が、昇順の後方で選定の下限から漏れていた。
優先区分は損失の根拠（観測した影響・根拠の所在・解除条件）を伴う場合だけ付け、選定では最古の1件と
優先区分を持つ最古の1件を交互に選ぶ。優先項目が続いても古い項目が毎回前進し、滞留を防ぐ。
点数による多段階の順位付けは損失の精密な比較を装い根拠と調整の負担が増すため、2段階に限る。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

PRIORITY_KEY = "priority"
URGENT = "urgent"
REASON_KEYS = ("priority_impact", "priority_evidence", "priority_release")
"""根拠の各欄。観測した影響、観測条件と根拠の所在、優先を解除する条件の順。"""


def priority_of(metadata: Mapping[str, object]) -> str:
    """frontmatterから優先区分を返す。区分の無い旧項目と不正な値は通常扱いとする。"""
    return URGENT if metadata.get(PRIORITY_KEY) == URGENT else "normal"


def reason_of(metadata: Mapping[str, object]) -> dict[str, str] | None:
    """優先の根拠を返す。urgentでない項目はNoneとする。"""
    if priority_of(metadata) != URGENT:
        return None
    return {key: str(metadata.get(key, "")) for key in REASON_KEYS}


def missing_reason_keys(metadata: Mapping[str, object]) -> list[str]:
    """urgentの項目で空の根拠欄を返す。"""
    reason = reason_of(metadata)
    if reason is None:
        return []
    return [key for key, value in reason.items() if not value.strip()]


def selection_order(awis: Iterable[tuple[str, str]]) -> list[str]:
    """選定可能なAWIの(ファイル名, 優先区分)から選定順を返す。

    最古の1件、urgentの最古の1件を交互に選び、選んだ項目は重複させない。
    urgentが尽きたら最古順で埋める。最古枠が先に来るため、任意の先頭N件の少なくとも半数（端数切上げ）は
    全AWIの最古順から選ばれる。
    """
    ordered = sorted(awis)
    oldest = [name for name, _ in ordered]
    urgent = [name for name, priority in ordered if priority == URGENT]
    chosen: list[str] = []
    taken: set[str] = set()
    take_oldest = True
    while len(chosen) < len(oldest):
        source = oldest if take_oldest or not any(name not in taken for name in urgent) else urgent
        name = next(name for name in source if name not in taken)
        chosen.append(name)
        taken.add(name)
        take_oldest = not take_oldest
    return chosen
