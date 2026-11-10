"""生成側と受信側が共有する、実行レビュー条件表の構造契約。"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping, Set

from markdown_it import MarkdownIt


def required_counts(
    *, kind: str | None, source: object, sections: Set[str], supplements: int, reflections: int
) -> tuple[int, int] | None:
    """呼出側が既存の節抽出で得た値から、自動条件表の対象と必要件数を返す。"""
    if kind != "awi" or not source or sections & {"適用範囲", "実現性"}:
        return None
    return supplements, reflections


def read_wi_originals(path: pathlib.Path, names: Set[str]) -> dict[str, str]:
    """WI名から保存本文の絶対パスへのJSON辞書を読み、要求集合の全本文を返す。"""
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
        return {name: pathlib.Path(data[name]).read_text(encoding="utf-8") for name in sorted(names)}
    except (OSError, ValueError) as error:
        raise ValueError(f"評価用WI原本を読み込めない: {path}: {error}。一覧と保存本文を直して再準備する") from error


def validate(text: str, required: Mapping[str, tuple[int, int]]) -> None:
    """表が同じ原本の必要件数を満たすか確認する。意味の検収はレビュー担当が行う。"""
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
            row.append(token.content.strip().strip("`"))
        elif token.type == "tr_close" and row is not None:
            cells = list(row)
            if not header:
                header = cells
            elif set(columns).issubset(header) and len(cells) == len(header):
                rows.append([cells[header.index(column)] for column in columns])
            row = None
    for name, (count, reflection_count) in required.items():
        own = [row for row in rows if row[0] == name]
        major = sum(row[2] == "主要完成状態" for row in own)
        supplements = sum(row[2] == "補足条件" for row in own)
        if major < 1 or supplements < count:
            raise ValueError(f"{name}: 主要完成状態 {major}/1件、補足条件 {supplements}/{count}件")
        numbers = [row[1] for row in own]
        if any(not number for number in numbers) or len(set(numbers)) != len(numbers):
            raise ValueError(f"{name}: 条件番号が不正または重複")
        covered = {int(number) for row in own for number in re.findall(r"反映内容([1-9][0-9]*)(?![0-9])", row[4])}
        uncovered = [f"反映内容{number}" for number in range(1, reflection_count + 1) if number not in covered]
        if uncovered:
            raise ValueError(
                f"{name}: 出典に対応する行が無い項目: {'、'.join(uncovered)}。"
                "各項目の主要完成状態または除外理由を記した行を追加し、出典へ`反映内容N`を記す"
            )
