"""操作を観測できる条件の読込表から、配送宣言の付け忘れを検出する。"""

from __future__ import annotations

import re
from collections.abc import Iterable

from agent_toolkit._common.read_triggers_model import ReadTrigger

_OPERATION_CONDITIONS = (
    re.compile("受領|検収|返却|受け取"),
    re.compile("委譲先.*起動|担当.*起動|委譲.*開始|委譲.*前"),
    re.compile("ユーザー.*確認|確認.*発行|質問.*発行|AskUserQuestion|構造化質問"),
    re.compile("WI.*保存|WI.*投入|WI.*登録|原稿.*保存"),
)
_REFERENCE_CONDITIONS = ("基準を適用", "資料を選択して取得")
_EXEMPTION = re.compile(r"（操作対象外: ([^\n）]+)）")


def missing_operation_rows(rows: Iterable[ReadTrigger]) -> list[str]:
    """配送操作か理由付き対象外標識を要する行の所在を返す。

    条件語は観測できる操作の候補を広く選ぶ。操作に当たるかの意味判定は書き手が行い、
    語が一致しただけの判断条件には行ごとの理由を残す。
    """
    issues = []
    for row in rows:
        if not row.derived_read or any(term in row.condition for term in _REFERENCE_CONDITIONS):
            continue
        if not any(pattern.search(row.condition) for pattern in _OPERATION_CONDITIONS):
            continue
        exemption = _EXEMPTION.search(row.condition)
        if exemption is not None and exemption[1].strip():
            continue
        issues.append(
            f"{row.source}:{row.line}: 操作に対応する読込条件の配送操作が無い: {row.condition}。"
            "配送操作を付けるか、操作に当たらない理由を操作対象外の標識へ書く。"
        )
    return issues
