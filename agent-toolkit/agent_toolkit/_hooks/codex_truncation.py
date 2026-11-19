"""Codexが出力を省略した時の包絡の識別と、省略を回収させる通知。"""

from __future__ import annotations

import re

_ENVELOPE = re.compile(r"Warning: truncated output \(original token count: (\d+)\)")

# 省略を実行全体の限界と取り違え、分割取得で補える不足を理由に続行不能を返した委譲先が複数あったため、
# 省略が担当内で回収できる不足であることを次の操作とともに示す。
TRUNCATED_OUTPUT_NOTICE = (
    "この出力は省略された。判断に必要な未取得部分を補え。"
    "対象や条件を限定するか、収まる範囲に分けて再実行するか、"
    "内側の`exec_command`と外側の`functions.exec`の`max_output_tokens`をともに省略前の量以上へ上げて1回だけ再実行せよ。"
    "省略は担当内で回収できる不足であり、続行できない理由にしない。"
)


def truncation_notice(response: object) -> str | None:
    """省略された出力へ、観測した元の量と回収の操作を示す本文を返す。省略が無ければ`None`を返す。"""
    if not isinstance(response, str) or (match := _ENVELOPE.match(response)) is None:
        return None
    return f"観測した元の出力量は{match[1]}トークン。{TRUNCATED_OUTPUT_NOTICE}"
