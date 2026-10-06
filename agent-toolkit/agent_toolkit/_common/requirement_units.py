"""要求原文を箇条書きの項目と文の単位へ分ける規則を提供する。

`exec-review-evidence-check`がユーザーコメントなどの原文要求を期待行へ分ける処理と、
`wi-quote-check`が逐語引用の要求単位ごとの位置を出力する処理が同じ分け方を使う。
分け方を別々に持つと、起草者が位置で示す単位と`exec-review-evidence-check`が期待行にする単位が一致しなくなるため、本モジュールへ集約する。
"""

import re

HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
# 全角の終止記号は位置によらず文末とする。ASCIIの終止記号は直後が空白か段落末の場合だけ文末とし、
# ドメイン名・ファイル名・版番号など語の内部のピリオドで文を分けない。
FULLWIDTH_TERMINATORS = "。．！？"
ASCII_TERMINATORS = ".!?"
# 文末記号の直後に続く閉じ括弧類は同じ文へ含め、閉じ括弧だけの単位が残る分割を避ける。
CLOSING_BRACKETS = ")）」』]】"
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")


def fenced_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """閉じたフェンス付きコードブロックの開始行・終了行の位置と情報文字列を出現順に返す。

    閉じるフェンスは開始と同じ文字で同じ長さ以上とし、長いフェンスの内側にある短いフェンスは内容として扱う。
    閉じていないフェンスはブロックとして扱わない。後続の要求を補足資料として失わないためである。
    """
    blocks: list[tuple[int, int, str]] = []
    index = 0
    while index < len(lines):
        opening = FENCE.match(lines[index])
        if opening is None or (opening["fence"][0] == "`" and "`" in opening["info"]):
            index += 1
            continue
        fence = opening["fence"]
        end = next(
            (
                position
                for position in range(index + 1, len(lines))
                if (closing := FENCE.match(lines[position])) is not None
                and closing["fence"][0] == fence[0]
                and len(closing["fence"]) >= len(fence)
                and not closing["info"].strip()
            ),
            None,
        )
        if end is None:
            index += 1
            continue
        blocks.append((index, end, opening["info"].strip()))
        index = end + 1
    return blocks


def without_fenced_blocks(lines: list[str]) -> list[str]:
    """補足資料のフェンス付きコードブロックを空行へ置き換え、前後の地の文を別の段落に保つ。"""
    remaining = list(lines)
    for start, end, _info in fenced_blocks(lines):
        remaining[start : end + 1] = [""] * (end + 1 - start)
    return remaining


def sentences(text: str) -> list[str]:
    """段落の文字列を文へ分ける。インラインコードの内側では分割しない。"""
    protected = [False] * len(text)
    for match in INLINE_CODE.finditer(text):
        protected[match.start() : match.end()] = [True] * (match.end() - match.start())
    terminators = FULLWIDTH_TERMINATORS + ASCII_TERMINATORS
    found: list[str] = []
    start = index = 0
    while index < len(text):
        if protected[index] or text[index] not in terminators:
            index += 1
            continue
        end = index
        while end < len(text) and text[end] in terminators and not protected[end]:
            end += 1
        fullwidth = any(char in FULLWIDTH_TERMINATORS for char in text[index:end])
        while end < len(text) and text[end] in CLOSING_BRACKETS and not protected[end]:
            end += 1
        if fullwidth or end == len(text) or text[end].isspace():
            found.append(text[start:end])
            start = end
        index = end
    found.append(text[start:])
    return [sentence.strip() for sentence in found if sentence.strip()]


def requirement_units(content: list[str]) -> list[str]:
    """要求原文を、箇条書きの項目と文の単位へ分ける。

    補足のフェンス付きコードブロック（ログ、設定断片、コマンド出力など）は分割の前に除く。
    句点やピリオドを含むログの断片を要求として数えないためである。資料として読む責務はレビュー担当に残る。
    """
    units: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            units.extend(sentences(" ".join(paragraph)))
            paragraph.clear()

    cleaned = without_fenced_blocks(HTML_COMMENT.sub("", "\n".join(content)).splitlines())
    for line in cleaned:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        item = LIST_ITEM.match(stripped)
        if item:
            flush()
            units.extend(sentences(stripped[item.end() :]))
            continue
        paragraph.append(stripped)
    flush()
    return units
