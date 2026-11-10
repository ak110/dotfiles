"""過去の記録に含まれるWI本文の表記警告を識別する。"""

import re

_DETAIL_PREFIX = "本文:"
_SUMMARY_PREFIX = "WI本文の表記診断: "
_BODY_WARNING = re.compile(r"^警告: " + re.escape(_DETAIL_PREFIX) + r"\d+:\d+: (?:口語表現|ダッシュ) ")
_BODY_WARNING_CONTEXT = re.compile(
    r'^警告: WI本文の表記診断: 入力元="(?:[^"\\]|\\.)*" 論理本文:\d+:\d+: (?:口語表現|ダッシュ) '
)
_BODY_WARNING_SUMMARY = re.compile(r"^警告: " + re.escape(_SUMMARY_PREFIX) + r"[1-9]\d*件(?:\s|$)")
"""件数だけの要約行。現行版は書かないが、過去の記録を読むreview-sessionが同じ診断として識別する。"""


def is_body_style_diagnostic_warning(text: str) -> bool:
    """WI本文の表記診断だった行かを過去の表示文言から判定する。"""
    return bool(_BODY_WARNING.match(text) or _BODY_WARNING_CONTEXT.match(text) or _BODY_WARNING_SUMMARY.match(text))
