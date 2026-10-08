"""可視発話の報告見出しと、completion-reportが定める報告本文の判定を扱う。"""

import re
from typing import Any

import markdown_it

from agent_toolkit._common.markdown_headings import top_level_atx_headings

_STAGES = {
    "作業完了報告": "work-complete",
    "振り返り結果の予告": "review-preview",
    "振り返り結果報告": "review-result",
    "AWI投入結果報告": "review-submission",
}
_WI_FILENAME = re.compile(r"\b\d{8}-\d{6}-\d{3}\.md\b")
_CATEGORIES = ("AWI登録予定", "AWI登録済み", "AWI登録", "実装済み")
"""`### 確定した問題と対策`の行の先頭に置く区分。前方一致で判定するため、長い区分名を先に並べる。"""
_SCHEDULED_MARKERS = ("AWI登録予定:", "投入予定:")
"""投入前の予告を示す標識。`投入予定:`は区分を先頭に置く書式へ移る前の書式であり、稼働中のセッションが
旧書式の`agent-toolkit:completion-report`を読んだまま報告しても予告と最終報告の判定が働くよう受理し続ける。"""
_LEGACY_IMPLEMENTED = re.compile(r"（同一セッションで実装済み:\s*[^）\s][^）]*）")
_LEGACY_SCHEDULED = re.compile(r"（投入予定:\s*[^）\s][^）]*）")
_IMPLEMENTED = re.compile(r"- 実装済み: (.+?); 根拠: (.+)")
_NO_PREVENTION = re.compile(r"- 再発防止策なし: (.+?); 評価した案: (.+?); 採らない理由: (.+?); 反復: (.+)")
_CANDIDATE_ID = re.compile(r"(?<![0-9A-Za-z])c\d{4}(?![0-9])")
_TRAILING_IDS = re.compile(r"[（(][^（）()]*(?<![0-9A-Za-z])c\d{4}[^（）()]*[）)]\s*$")
_EXCLUSION_CATEGORIES = ("single-inquiry", "non-changing-request", "unexplained-refusal", "necessary-confirmation")
"""再発防止策が必須の候補を`判定済み`で見送るときに根拠の先頭へ書く除外区分。

定義は`agent-toolkit:session-review`の`references/analysis.md`「ユーザー介入の判定規則」が持つ。
"""
_UNRESEARCHED = ("照会していない", "未照会", "未調査", "未確定", "確定できない")


def has_scheduled(text: str) -> bool:
    """報告本文が投入前の予告（AWI登録予定の行）を含むかを返す。"""
    return any(marker in text for marker in _SCHEDULED_MARKERS)


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


def _measure_error(item: str) -> str | None:
    """`### 確定した問題と対策`の1行が、区分に応じた対策の根拠を持つかを判定する。"""
    if item == "- なし":
        return None
    body = _TRAILING_IDS.sub("", item).rstrip()
    category = next((name for name in _CATEGORIES if body.startswith(f"- {name}: ")), None)
    if category in {"AWI登録", "AWI登録済み"}:
        return None if _WI_FILENAME.search(body) else f"{category}の行へ対策のAWIのファイル名とH1タイトルを書く: {item}"
    if category == "AWI登録予定":
        return (
            None if body.removeprefix(f"- {category}: ").strip() else f"AWI登録予定の行へ投入するAWIのH1タイトルを書く: {item}"
        )
    if category == "実装済み":
        fields = _IMPLEMENTED.fullmatch(body)
        if fields is None or not all(value.strip() for value in fields.groups()):
            return f"実装済みの行へ対策の要旨と根拠（commitの短縮OIDか変更した成果物）を`; 根拠: `で区切って書く: {item}"
        return None
    if _WI_FILENAME.search(item) or _LEGACY_IMPLEMENTED.search(item) or _LEGACY_SCHEDULED.search(item):
        return None
    return f"行の先頭へ区分（AWI登録予定・AWI登録・AWI登録済み・実装済み）を書き、区分に応じた根拠を添える: {item}"


def _mandatory_errors(measures: list[str], skipped: list[str], prepare: dict[str, Any]) -> list[str]:
    """再発防止策が必須の候補を、確定した対策の行と許される見送りの行のいずれかで扱ったかを判定する。"""
    mandatory = prepare.get("mandatory_candidates")
    if not isinstance(mandatory, list):
        return []
    raw_similar = prepare.get("similar_records")
    similar: dict[str, Any] = raw_similar if isinstance(raw_similar, dict) else {}
    errors: list[str] = []
    for candidate_id in (item for item in mandatory if isinstance(item, str)):

        def mentions(line: str, target: str = candidate_id) -> bool:
            return target in _CANDIDATE_ID.findall(line)

        if not any(mentions(line) for line in (*measures, *skipped)):
            errors.append(
                f"再発防止策が必須の候補{candidate_id}を、`### 確定した問題と対策`の行か、"
                "根拠の先頭へ除外区分を書いた`判定済み`の行、`再発防止策なし`の行へ候補IDとともに書く"
            )
            continue
        for line in (item for item in skipped if mentions(item)):
            if line.startswith("- 判定済み: "):
                reason = line.split("; 根拠: ", maxsplit=1)[1] if "; 根拠: " in line else ""
                if not reason.strip().startswith(_EXCLUSION_CATEGORIES):
                    errors.append(
                        f"再発防止策が必須の候補{candidate_id}を判定済みで見送る場合は、根拠の先頭へ除外区分"
                        f"（{'、'.join(_EXCLUSION_CATEGORIES)}）を書く。当たらない場合は対策を確定するか"
                        f"`再発防止策なし`の行で書く: {line}"
                    )
            elif line.startswith("- 再発防止策なし: "):
                records = [name for name in similar.get(candidate_id, []) if isinstance(name, str)]
                fields = _NO_PREVENTION.fullmatch(_TRAILING_IDS.sub("", line).rstrip())
                repeat = fields.group(4) if fields is not None else ""
                if records and not any(name in repeat for name in records):
                    errors.append(
                        f"再発防止策が必須の候補{candidate_id}の`再発防止策なし`の反復の欄へ、過去の同種記録"
                        f"（{'、'.join(records)}）のうち1件以上のファイル名と、前回の処置で再発を防げなかった条件を書く: {line}"
                    )
    return errors


def validate_report(text: str, stage: str, prepare: dict[str, Any] | None = None) -> list[str]:
    """対策の区分と根拠・見送りの根拠・未確定の観測・必須の候補の扱い・最終報告の確定を判定する。

    `prepare`は同じ作業の最後の`atk run-script session-review-prepare`の1行JSONであり、
    振り返り結果報告では再発防止策が必須の候補の扱いを判定する。必須の候補を持たない場合は判定を加えない。
    """
    errors: list[str] = []
    measures = _section_items(text, "確定した問題と対策")
    skipped = _section_items(text, "対策を見送った問題")
    errors.extend(error for item in measures if (error := _measure_error(item)) is not None)
    for item in skipped:
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
        elif item.startswith("- 再発防止策なし: "):
            fields = _NO_PREVENTION.fullmatch(_TRAILING_IDS.sub("", item).rstrip())
            if fields is None or not all(value.strip() for value in fields.groups()):
                errors.append(f"再発防止策なしの行へ評価した案・採らない理由・反復を書く: {item}")
    if stage in {"review-preview", "review-result"} and prepare is not None:
        errors.extend(_mandatory_errors(measures, skipped, prepare))
    if stage == "review-submission" and has_scheduled(text):
        errors.append("最終報告のAWI登録予定を、投入したAWIのファイル名と実際の結果へ直す")
    return errors
