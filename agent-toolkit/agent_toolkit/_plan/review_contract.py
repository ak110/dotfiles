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


def _numbered_conditions(original: str) -> dict[str, tuple[str, str]]:
    conditions: dict[str, tuple[str, str]] = {}
    for number, item in enumerate(last_h2_section_items(original, "完成条件"), 1):
        match = re.fullmatch(r"([0-9]+)[.)][ \t]+(.+)", item, re.DOTALL)
        if match:
            conditions[str(number)] = (" ".join(match[2].split()), " ".join(item.split()))
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
