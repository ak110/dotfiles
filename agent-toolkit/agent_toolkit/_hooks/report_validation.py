"""可視発話の報告見出しと、人間由来の要件を守る4判定を扱う。"""

import re

import markdown_it

from agent_toolkit._common.markdown_headings import top_level_atx_headings

SCHEDULED_MARKER = "投入予定:"
_STAGES = {"作業完了報告": "work-complete", "振り返り結果報告": "review-result", "AWI投入結果報告": "review-submission"}
_WI_FILENAME = re.compile(r"\b\d{8}-\d{6}-\d{3}\.md\b")
_IMPLEMENTED = re.compile(r"（同一セッションで実装済み:\s*[^）\s][^）]*）")
_SCHEDULED = re.compile(r"（投入予定:\s*[^）\s][^）]*）")
_UNRESEARCHED = ("照会していない", "未照会", "未調査", "未確定", "確定できない")


def reports_from_messages(messages: list[str]) -> dict[str, str]:
    """コード・ツール本文を含めず、実際のH2から各段階の最後の発話を返す。"""
    reports: dict[str, str] = {}
    for message in messages:
        lines = message.splitlines(keepends=True)
        headings = top_level_atx_headings(message, 2)
        for index, (token, title) in enumerate(headings):
            if title not in _STAGES or token.map is None:
                continue
            end = headings[index + 1][0].map if index + 1 < len(headings) else None
            reports[_STAGES[title]] = "".join(lines[token.map[0] : end[0] if end is not None else len(lines)])
    return reports


def _section_items(text: str, title: str) -> list[str]:
    """該当H3の箇条書きだけを取り出す。H3の順序は問わない。"""
    items: list[str] = []
    lines = text.splitlines()
    headings = sorted(
        [*top_level_atx_headings(text, 2), *top_level_atx_headings(text, 3)],
        key=lambda entry: entry[0].map[0] if entry[0].map else 0,
    )
    tokens = markdown_it.MarkdownIt("commonmark").parse(text)
    for index, (heading, name) in enumerate(headings):
        if name != title or heading.map is None:
            continue
        following = headings[index + 1][0].map if index + 1 < len(headings) else None
        end = following[0] if following else len(lines)
        for token in tokens:
            if token.type == "list_item_open" and token.level == 1 and token.map and heading.map[1] <= token.map[0] < end:
                line = lines[token.map[0]].strip()
                if re.match(r"[-+*]\s", line):
                    items.append(re.sub(r"^[-+*]\s+", "- ", line))
    return items


def validate_report(text: str, stage: str) -> list[str]:
    """対策の対応・見送りの根拠・未確定の観測・最終報告の確定を判定する。"""
    errors: list[str] = []
    for item in _section_items(text, "確定した問題と対策"):
        if item != "- なし" and not (_WI_FILENAME.search(item) or _IMPLEMENTED.search(item) or _SCHEDULED.search(item)):
            errors.append(f"対策のAWIファイル名・投入予定のタイトル・同一セッションの実装根拠を書く: {item}")
    for item in _section_items(text, "対策を見送った問題"):
        if item.startswith("- 判定済み: "):
            parts = item.removeprefix("- 判定済み: ").split("; 根拠: ", maxsplit=1)
            if len(parts) != 2 or not all(part.strip() for part in parts):
                errors.append(f"判定済み行へ問題と根拠を書く: {item}")
            elif any(word in parts[1] for word in _UNRESEARCHED):
                errors.append(f"未照会・未確定を判定済みの根拠へ使わず、照会して根拠を確定する: {item}")
        elif item.startswith("- 未確定: "):
            fields = re.fullmatch(r"- 未確定: (.+?); 照会: (.+?); 再現: (.+?); 残る理由: (.+)", item)
            if fields is None or not all(value.strip() for value in fields.groups()):
                errors.append(f"未確定行へ照会・再現・残る理由を書く: {item}")
    if stage == "review-submission" and SCHEDULED_MARKER in text:
        errors.append("最終報告の投入予定を、投入したAWIファイル名と実際の結果へ直す")
    return errors
