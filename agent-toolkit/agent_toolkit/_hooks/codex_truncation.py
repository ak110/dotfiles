"""Codexが出力を省略した時の包絡の識別と、省略を回収させる通知。"""

from __future__ import annotations

import re

_ENVELOPE = re.compile(r"Warning: truncated output \(original token count: (\d+)\)")


TRUNCATED_OUTPUT_NOTICE = (
    "この出力は省略された。判断に必要な未取得部分を補ってください。"
    "対象や条件を限定するか、収まる範囲に分けて再実行するか、"
    "内側の`exec_command`と外側の`functions.exec`の`max_output_tokens`をともに省略前の量以上へ上げて1回だけ再実行してください。"
    "省略は担当内で回収できる不足であり、続行できない理由にしない。"
)


def truncation_notice(response: object) -> str | None:
    if not isinstance(response, str) or (match := _ENVELOPE.match(response)) is None:
        return None
    return f"観測した元の出力量は{match[1]}トークン。{TRUNCATED_OUTPUT_NOTICE}"
