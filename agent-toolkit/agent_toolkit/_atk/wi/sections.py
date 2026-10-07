"""保存済みWIの本文からH1タイトルとH2の節を取り出す。

振り返りの候補抽出（WIの記入欄の是正、過去の同種記録）が、private-notesの全WIを短時間で読むために使う。
見出しはフェンス付きコードブロックの外にある行だけとし、逐語引用のフェンス内の`#`行で節を分けない。
"""

import collections.abc
import re

from agent_toolkit._atk.wi.frontmatter import parse_frontmatter

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _lines(text: str) -> collections.abc.Iterator[tuple[str, bool]]:
    """frontmatterを除いた本文の各行と、その行がフェンスの外にあるかを返す。"""
    parsed = parse_frontmatter(text)
    body = parsed[1] if parsed is not None else text
    fence: str | None = None
    for line in body.replace("\r\n", "\n").split("\n"):
        match = _FENCE.match(line)
        if fence is None and match is not None:
            fence = match.group(1)
            yield line, False
        elif fence is not None:
            closing = _FENCE.match(line)
            if closing is not None and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
                fence = None
            yield line, False
        else:
            yield line, True


def h2_sections(text: str) -> dict[str, str]:
    """H2見出し名ごとの節の本文を返す。同じ名前の節が複数ある場合は後の節を返す。"""
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line, outside in _lines(text):
        if outside and line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip("\n")
            current, lines = line[3:].strip(), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip("\n")
    return sections


def title(text: str) -> str:
    """AWIのH1タイトル、H1を持たないUWIでは`## 質問`の最初の空でない行を返す。取得できない場合は空文字列を返す。"""
    for line, outside in _lines(text):
        if outside and line.startswith("# "):
            return line[2:].strip()
    question = h2_sections(text).get("質問", "")
    return next((line.strip() for line in question.splitlines() if line.strip()), "")
