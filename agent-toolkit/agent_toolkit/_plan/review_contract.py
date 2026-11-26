"""生成側と受信側が共有する、実行レビュー条件表の構造契約。"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping, Set

from markdown_it import MarkdownIt

from agent_toolkit._common.markdown_headings import last_h2_section_items, last_h2_section_lines


def required_counts(
    *, kind: str | None, source: object, sections: Set[str], supplements: int, reflections: int
) -> tuple[int, int] | None:
    if kind != "awi" or not source or sections & {"適用範囲", "実現性"}:
        return None
    return supplements, reflections


def read_wi_originals(path: pathlib.Path, names: Set[str]) -> dict[str, tuple[pathlib.Path, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("WI名と絶対パスのJSON辞書が必要")
        for name, value in data.items():
            if not isinstance(name, str) or re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md", name) is None:
                raise ValueError(f"WI名が不正: {name!r}")
            if not isinstance(value, str) or not pathlib.Path(value).is_absolute():
                raise ValueError(f"{name}: 保存本文の絶対パスが必要")
        missing = sorted(names - data.keys())
        if missing:
            raise ValueError(f"保存本文の指定が無い: {', '.join(missing)}")
        paths = {name: pathlib.Path(data[name]) for name in sorted(names)}
        return {name: (original, original.read_text(encoding="utf-8")) for name, original in paths.items()}
    except (OSError, ValueError) as error:
        raise ValueError(f"評価用WI原本を読み込めない: {path}: {error}。一覧と保存本文を直して再準備する") from error


def _labelled_conditions(original: str) -> list[tuple[int, str, bool]]:
    lines = last_h2_section_lines(original, "完成条件") or []
    items: list[tuple[int | None, str]] = []
    number: int | None = None
    for token in MarkdownIt().parse("\n".join(lines)):
        if token.level == 0 and token.type in {"ordered_list_open", "bullet_list_open"}:
            number = int(token.attrGet("start") or 1) if token.type == "ordered_list_open" else None
        elif token.level == 1 and token.type == "list_item_open" and token.map is not None:
            item = "\n".join(lines[token.map[0] : token.map[1]]).strip()
            items.append((number, item))
            if number is not None:
                number += 1
    if not items:
        return [(number, item, False) for number, item in enumerate(last_h2_section_items(original, "完成条件"), 1)]

    reserved = [number for number, _ in items if number is not None]
    used = set(reserved)
    if len(used) != len(reserved):
        raise ValueError("完成条件に同じ表示番号がある。順序付きリストの開始番号が重ならないよう原本を見直す")
    conditions = []
    next_number = 1
    for number, item in items:
        if number is not None:
            conditions.append((number, item, True))
        else:
            while next_number in used:
                next_number += 1
            conditions.append((next_number, item, False))
            next_number += 1
    return conditions


def completion_conditions(original: str) -> list[tuple[int, str]]:
    return [(number, item) for number, item, _ in _labelled_conditions(original)]


def _numbered_conditions(original: str) -> dict[str, tuple[str, str]]:
    conditions = {}
    for number, item, ordered in _labelled_conditions(original):
        if not ordered:
            continue
        match = re.fullmatch(r"[0-9]+[.)][ \t]+(.+)", item, re.DOTALL)
        if match is not None:
            conditions[str(number)] = (" ".join(match[1].split()), " ".join(item.split()))
    return conditions


def excluded_reflections(original: str) -> set[int]:
    section = "\n".join(last_h2_section_lines(original, "反映内容と反映先") or [])
    match = re.search(r"(?m)^採らないもの[:：]\s*$", section)
    if match is None:
        return set()
    remaining = section[match.end() :]
    return {index for index, item in enumerate(last_h2_section_items(original, "反映内容と反映先"), 1) if item in remaining}


def _condition_reference(content: str, original_path: pathlib.Path | None, number: str) -> bool:
    if original_path is None:
        return False
    text = re.sub(r"^参照[:：]\s*", "", content).replace("`", "")
    return text == f"{original_path}「完成条件」項目{number}" or any(
        text == f"{original_path}{separator}完成条件{space}{number}{closing}"
        for separator, closing in (("#", ""), ("「", "」"))
        for space in ("", " ")
    )


def validate(
    text: str,
    required: Mapping[str, tuple[int, int]],
    *,
    originals: Mapping[str, str] | None = None,
    original_paths: Mapping[str, pathlib.Path] | None = None,
) -> None:
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    rows: list[list[str]] = []
    row: list[str] | None = None
    header: list[str] = []
    columns = ["WI", "条件番号", "区分", "内容", "出典"]
    for token in tokens:
        if token.type == "table_open":
            header = []
        elif token.type == "tr_open":
            row = []
        elif token.type == "inline" and row is not None:
            row.append(token.content.strip())
        elif token.type == "tr_close" and row is not None:
            cells = list(row)
            if not header:
                header = cells
            elif set(columns).issubset(header) and len(cells) == len(header):
                fields = [cells[header.index(column)] for column in columns]
                rows.append([*(field.strip("`") for field in fields[:3]), *fields[3:]])
            row = None
    errors: list[str] = []
    for name, (count, reflection_count) in required.items():
        own = [row for row in rows if row[0] == name]
        major = sum(row[2] == "主要完成状態" for row in own)
        supplements = sum(row[2] == "補足条件" for row in own)
        if major < 1 or supplements < count:
            errors.append(f"{name}: 主要完成状態 {major}/1件、補足条件 {supplements}/{count}件")
        numbers = [row[1] for row in own]
        if any(not number for number in numbers) or len(set(numbers)) != len(numbers):
            errors.append(f"{name}: 条件番号が不正または重複")
        original = (originals or {}).get(name, "")
        for index, _ in enumerate(quoted_passages(original), 1):
            reference = f"引用{index}"
            if sum(row[2] == "原文照合" and bool(row[3]) and row[4] == reference for row in own) != 1:
                errors.append(f"{name}: {reference} に対応する原文照合行が無いか重複している")
        numbered = _numbered_conditions(original)
        excluded = excluded_reflections(original)
        for row in own:
            reflections = {int(number) for number in re.findall(r"反映内容([1-9][0-9]*)", row[4])}
            if (
                row[2] == "主要完成状態"
                and (
                    (reflections and reflections <= excluded)
                    or (
                        re.search(r"背景|比較候補|調査結果と判断根拠|不実施", row[4])
                        and not re.search(r"反映内容[1-9][0-9]*|完成条件|目的|原因分析", row[4])
                    )
                )
                and not re.search(r"除外|不採用|実施しない|対象外", row[3])
            ):
                errors.append(f"{name}: 条件 {row[1]} は背景・比較候補・不実施の説明だけを実装義務の出典にしている")
            if row[2] != "補足条件" or not numbered:
                continue
            number = row[1]
            if number not in numbered:
                if len(numbered) == count:
                    errors.append(f"{name}: 原本にない補足条件 {number}")
                continue
            content = " ".join(re.sub(r"<br\s*/?>", " ", row[3], flags=re.IGNORECASE).split())
            expected, original_item = numbered[number]
            if content not in {
                expected,
                original_item,
                f"{number}. {expected}",
                f"{number}) {expected}",
            } and not _condition_reference(content, (original_paths or {}).get(name), number):
                errors.append(f"{name}: 補足条件 {number} が原本の全文と一致しない")
        for number in numbered.keys() - {row[1] for row in own if row[2] == "補足条件"}:
            errors.append(f"{name}: 原本の補足条件 {number} が表にない")
        covered = {int(number) for row in own for number in re.findall(r"反映内容([1-9][0-9]*)(?![0-9])", row[4])}
        uncovered = [f"反映内容{number}" for number in range(1, reflection_count + 1) if number not in covered]
        if uncovered:
            errors.append(
                f"{name}: 出典に対応する行が無い項目: {'、'.join(uncovered)}。"
                "各項目の主要完成状態または除外理由を記した行を追加し、出典へ`反映内容N`を記す"
            )
    if errors:
        raise ValueError("\n".join(errors))


def _origin_passages(original: str, heading: str) -> list[str]:
    lines = last_h2_section_lines(original, heading) or []
    return [
        token.content.rstrip("\n") if token.type == "fence" else "\n".join(lines[token.map[0] : token.map[1]])
        for token in MarkdownIt().parse("\n".join(lines))
        if token.level == 0 and token.map is not None
    ]


def quoted_passages(original: str) -> list[str]:
    return _origin_passages(original, "ユーザー指摘の逐語引用")


_CODE_OR_SENTENCE_END = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`)[\s\S]*?(?<!`)(?P=ticks)(?!`)"
    r"|[。！？][」』）】”’]*|[.!?][」』）】”’]*(?=\s|$)"
)


def _requirement_units(passage: str) -> list[str]:
    lines = [re.sub(r"^\s*(?:> *)+", "", line) for line in passage.splitlines()]
    lines = [re.sub(r"^\s*(?:[-+*]|[0-9]+[.)]) +", "\n", line) for line in lines]
    units = []
    for paragraph in re.split(r"\n\s*\n", "\n".join(lines).strip()):
        text = " ".join(paragraph.split())
        start = 0
        for match in _CODE_OR_SENTENCE_END.finditer(text):
            if match["ticks"] is None:
                units.append(text[start : match.end()].strip())
                start = match.end()
        if tail := text[start:].strip():
            units.append(tail)
    return units


_EXCLUSIONS = r"(失効|割当外|投入元で完了|回答済み|背景|重複)"


def _validate_passage(
    label: str,
    passage: str,
    matching: list[tuple[str, str]],
    exclusions: dict[int, list[str]],
    requirements: Mapping[str, tuple[str, str]],
) -> list[str]:
    if len(matching) != 1:
        return [f"{label}: 判定が無いか重複している"]
    verdict, evidence = matching[0]
    if verdict in {"未達", "根拠不足"}:
        return [f"{label}: {verdict}"]

    units = _requirement_units(passage)
    numbers = dict.fromkeys(re.findall(r"原文要求([1-9][0-9]*)", evidence)) if verdict == "達成" else {}
    pending = {index: unit for index, unit in enumerate(units, 1) if index not in exclusions}
    errors = []
    for number in numbers:
        quote, status = requirements.get(number, ("", ""))
        index = next((index for index, unit in pending.items() if unit == quote), None)
        if status != "達成" or index is None:
            errors.append(f"{label}: 原文要求{number}の逐語・達成判定を照合できない")
        else:
            del pending[index]
    if verdict == "達成" and not numbers:
        errors.append(f"{label}: 達成した原文要求行の参照が無い")
    if verdict == "除外" and not exclusions:
        errors.append(f"{label}: 原文単位ごとの除外が無い")
    for index, excluded in exclusions.items():
        if len(excluded) > 1:
            errors.append(f"{label}.{index}: 原文単位の判定が重複している")
    for index, unit in pending.items():
        errors.append(f"{label}.{index}: 原文要求の達成または除外の判定が無い: {unit}")
    for index in exclusions.keys() - set(range(1, len(units) + 1)):
        errors.append(f"{label}.{index}: 原本にない原文単位を除外している")
    return errors


def validate_origin_verdicts(result: str, originals: Mapping[str, str]) -> None:
    quotes: dict[str, dict[int, list[tuple[str, str]]]] = {}
    requirements: dict[str, dict[str, tuple[str, str]]] = {}
    exclusions: dict[str, dict[int, dict[int, list[str]]]] = {}
    errors = []
    for line in result.splitlines():
        if match := re.fullmatch(r"(\S+) 引用照合([1-9][0-9]*): (達成|未達|根拠不足|除外) — 根拠: (\S.*)", line):
            name, index, verdict, evidence = match.groups()
            quotes.setdefault(name, {}).setdefault(int(index), []).append((verdict, evidence))
        if match := re.fullmatch(rf"(\S+) 引用除外([1-9][0-9]*)\.([1-9][0-9]*): {_EXCLUSIONS} — 根拠: (\S.*)", line):
            name, block, unit, _, evidence = match.groups()
            exclusions.setdefault(name, {}).setdefault(int(block), {}).setdefault(int(unit), []).append(evidence)
        if match := re.fullmatch(r"(\S+) 原文要求([1-9][0-9]*)「(.+)」: (達成|未達|根拠不足) — 根拠: \S.*", line):
            name, index, quote, verdict = match.groups()
            rows = requirements.setdefault(name, {})
            if name in originals and (index in rows or verdict != "達成"):
                errors.append(f"{name} 原文要求{index}: 重複または{verdict}")
            rows[index] = (" ".join(quote.split()), verdict)
    for name, original in originals.items():
        passages = (
            quoted_passages(original) + _origin_passages(original, "ユーザーコメント") + _origin_passages(original, "回答")
        )
        for index, passage in enumerate(passages, 1):
            errors.extend(
                _validate_passage(
                    f"{name} 引用照合{index}",
                    passage,
                    quotes.get(name, {}).get(index, []),
                    exclusions.get(name, {}).get(index, {}),
                    requirements.get(name, {}),
                )
            )
    if errors:
        raise ValueError("\n".join(errors))
