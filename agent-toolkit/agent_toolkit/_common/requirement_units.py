"""要求原文を箇条書きの項目と文の単位へ分ける規則を提供する。

`verification-record`と`exec-review-evidence-check`がWI・計画の原文要求を期待行へ分ける処理と、
`wi-quote-check`が逐語引用の要求単位ごとの位置を出力する処理が同じ分け方を使う。
分け方を別々に持つと、起草者が位置で示す単位と`exec-review-evidence-check`が期待行にする単位が一致しなくなるため、本モジュールへ集約する。
"""

import re
from collections import Counter
from collections.abc import Mapping

from agent_toolkit._common import markdown_headings

WI_HEADER = re.compile(r"^### (\d{8}-\d{6}-\d{3}\.md) \[[^]]+\]$")

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


def wi_body(output: str, filename: str) -> tuple[dict[str, str], list[str]]:
    lines = output.splitlines()
    starts = [index for index, line in enumerate(lines) if WI_HEADER.fullmatch(line) and line.startswith(f"### {filename} ")]
    if len(starts) != 1:
        raise ValueError(f"{filename}: WI本文の見出しを一意に取得できません")
    start = starts[0] + 1
    end = next(
        (
            index
            for index in range(start, len(lines))
            if WI_HEADER.fullmatch(lines[index]) or lines[index].startswith("## target_repo:")
        ),
        len(lines),
    )
    if start >= end or lines[start] != "---":
        raise ValueError(f"{filename}: frontmatterを取得できません")
    frontmatter_end = next((index for index in range(start + 1, end) if lines[index] == "---"), None)
    if frontmatter_end is None:
        raise ValueError(f"{filename}: frontmatterが閉じられていません")
    frontmatter = dict(line.split(": ", 1) for line in lines[start + 1 : frontmatter_end] if ": " in line)
    return frontmatter, lines[frontmatter_end + 1 : end]


def section(body: list[str], heading: str) -> list[str] | None:
    start = next((index for index, line in enumerate(body) if line == heading), None)
    if start is None:
        return None
    end = next((index for index in range(start + 1, len(body)) if body[index].startswith("## ")), len(body))
    return body[start + 1 : end]


def normalize_condition(text: str) -> str:
    """完成条件の行頭記号と前後の空白を除く。"""
    return LIST_ITEM.sub("", text.strip()).strip()


def condition_units(content: list[str], filename: str) -> list[str]:
    lines = HTML_COMMENT.sub("", "\n".join(content)).splitlines()
    items = [normalize_condition(line) for line in lines if LIST_ITEM.match(line.strip())]
    if items:
        return items
    paragraph = " ".join(line.strip() for line in lines if line.strip())
    if paragraph:
        return [paragraph]
    raise ValueError(f"{filename}: 『完成条件』節が空です")


def expected_rows(output: str, filename: str) -> tuple[list[str], list[tuple[str, str]]]:
    """WI本文から、完成条件の原文と、出所付きの原文要求単位を、証拠の判定と雛形が共有する期待行として返す。

    原文要求単位は、完成条件節を持つAWIでは`## ユーザーコメント`、UWIでは`## 回答`、完成条件節の無いAWIでは本文から取る。
    """
    frontmatter, body = wi_body(output, filename)
    kind = frontmatter.get("type")
    if kind not in {"awi", "uwi"}:
        raise ValueError(f"{filename}: WIのtypeが不正です")
    conditions = section(body, "## 完成条件")
    if kind == "awi" and conditions is not None:
        # `## ユーザー指摘の逐語引用`は投入元のセッションへの発話であり、そのセッションで解決済みとして扱う。
        # 達成を確かめるのは処理側へ宛てた`## ユーザーコメント`だけとし、逐語引用は完成条件を解釈する根拠に留める。
        comment = section(body, "## ユーザーコメント")
        requirements = [(unit, f"{filename}#ユーザーコメント") for unit in requirement_units(comment or [])]
        return condition_units(conditions, filename), requirements
    if kind == "awi" and "source" in frontmatter:
        raise ValueError(f"{filename}: 『完成条件』節がありません")
    if kind == "uwi":
        answer = section(body, "## 回答")
        requirements = [(unit, f"{filename}#回答") for unit in requirement_units(answer or [])]
        if not requirements:
            raise ValueError(f"{filename}: 『回答』節が空です")
        return [], requirements
    result = section(body, "## 処理結果")
    if result is not None:
        body = body[: body.index("## 処理結果")]
    requirements = [(unit, f"{filename}#本文") for unit in requirement_units(body)]
    if not requirements:
        raise ValueError(f"{filename}: 原文本文が空です")
    return [], requirements


def plan_requirements(text: str, source: str) -> list[tuple[str, str]]:
    """計画の変更履歴にあるユーザー発言を、要約へ置換せず所在付きの要求単位へ分ける。"""
    lines = text.splitlines()
    headings = markdown_headings.top_level_atx_headings(text, 2)
    history: list[str] = []
    for index, (token, heading) in enumerate(headings):
        if heading == "変更履歴":
            assert token.map is not None
            following = headings[index + 1][0].map if index + 1 < len(headings) else None
            history = lines[token.map[1] : following[0] if following else len(lines)]
            break
    results: list[tuple[str, str]] = []
    sections = markdown_headings.top_level_atx_headings("\n".join(history), 3)
    for index, (token, heading) in enumerate(sections):
        if re.fullmatch(r"ユーザー発言[1-9]\d*", heading):
            assert token.map is not None
            following = sections[index + 1][0].map if index + 1 < len(sections) else None
            tail = history[token.map[1] : following[0] if following else len(history)]
            blocks = fenced_blocks(tail)
            if not blocks or blocks[0][2] != "text" or any(part.strip() for part in tail[: blocks[0][0]]):
                raise ValueError(f"{source}#{heading}: 直下に逐語発言のtextブロックが必要です")
            start, end, _info = blocks[0]
            units = requirement_units(tail[start + 1 : end])
            if not units:
                raise ValueError(f"{source}#{heading}: 原文要求が空です")
            results.extend((unit, f"{source}#{heading} 要求 {number}") for number, unit in enumerate(units, 1))
    return results


def record_rows(
    outputs: Mapping[str, str], filenames: list[str], plans: list[tuple[str, str]]
) -> dict[str, list[dict[str, str]]]:
    """WIと計画から判定前の記録を作成し、同じ原文でも出所が違う要求を別行に保つ。"""
    rows: dict[str, list[dict[str, str]]] = {"wi_conditions": [], "user_requirements": []}
    for filename in dict.fromkeys(filenames):
        conditions, requirements = expected_rows(outputs[filename], filename)
        rows["wi_conditions"].extend(
            {
                "awi": filename,
                "condition": condition,
                "source": f"{filename}#完成条件 {number}",
                "outcome": "",
                "evidence": "",
                "reviewed_head": "",
            }
            for number, condition in enumerate(conditions, 1)
        )
        rows["user_requirements"].extend(
            {
                "awi": filename,
                "requirement": unit,
                "origin": origin,
                "source": origin,
                "outcome": "",
                "evidence": "",
                "reviewed_head": "",
            }
            for unit, origin in requirements
        )
    for source, text in plans:
        rows["user_requirements"].extend(
            {
                "awi": "",
                "requirement": unit,
                "origin": origin,
                "source": origin,
                "outcome": "",
                "evidence": "",
                "reviewed_head": "",
            }
            for unit, origin in plan_requirements(text, source)
        )
    return rows


def append_missing_rows(payload: dict[str, list[dict[str, str]]], expected: dict[str, list[dict[str, str]]]) -> int:
    """既存行の記入内容と順序を保ち、原文・出所の不足分だけを末尾へ加える。"""

    def key(row: dict[str, str], field: str) -> tuple[str, str, str]:
        original = normalize_condition(row[field]) if field == "condition" else row[field]
        return row["awi"], original, row.get("origin", "") if not row["awi"] else ""

    added = 0
    for name, field in (("wi_conditions", "condition"), ("user_requirements", "requirement")):
        present = Counter(key(row, field) for row in payload[name])
        for row in expected[name]:
            identity = key(row, field)
            if present[identity]:
                present[identity] -= 1
            else:
                payload[name].append(row)
                added += 1
    return added
