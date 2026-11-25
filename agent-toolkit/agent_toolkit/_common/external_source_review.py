from __future__ import annotations

import pathlib
import re
import unicodedata
from collections.abc import Iterator

from markdown_it import MarkdownIt

from agent_toolkit._common.markdown_headings import without_literal_blocks

EXTERNAL_SOURCE_ENTRY_KEY = "external_source_entry"
_SOURCE_URL = re.compile(r"https?://(?:[A-Za-z0-9._~*/:?#\[\]@!$&'()+,;=\-]|%[0-9A-Fa-f]{2})+")
_SOURCE_URL_EMPHASIS = re.compile(r"(?<!\*)\*{1,3}$|(?<!_)_{1,3}$|(?<!~)~~$")
_SOURCE_URL_TRAILING_PUNCTUATION = ".,;:!?"
_SOURCE_URL_BRACKETS = {")": "(", "]": "["}


def _source_url_end(candidate: str) -> int:
    opening: list[str] = []
    for match in re.finditer(r"[()\[\]]", candidate):
        char = match[0]
        if char in _SOURCE_URL_BRACKETS.values():
            opening.append(char)
        elif not opening or opening.pop() != _SOURCE_URL_BRACKETS[char]:
            return match.start()
    return len(candidate)


def source_urls(line: str) -> Iterator[str]:
    offset = 0
    while match := _SOURCE_URL.search(line, offset):
        start = match.start()
        url = match[0][: _source_url_end(match[0])]
        offset = start + len(url)
        if line[start - 1 : start] == "[" and line.startswith("](", offset):
            continue
        before = line[start - 1 : start]
        after = line[offset : offset + 1]
        if (before, after) in {("<", ">"), ("`", "`")} or (line[:start].endswith("](") and after == ")"):
            yield url
        elif marker := _SOURCE_URL_EMPHASIS.search(line, 0, start):
            yield url.removesuffix(marker[0])
        else:
            yield url.rstrip(_SOURCE_URL_TRAILING_PUNCTUATION)


TABLE_NAME = "external-source-review.md"
COLUMNS = (
    "資料URL",
    "資料の主張（原文の抜粋）",
    "既存の対応箇所",
    "既存との差分",
    "削る・まとめる・置き換える案",
    "採否と理由",
)
_ABSENT = {"なし", "無し"}
_REFERENCE = re.compile(r"(?<![\w./\\:-])([\w./-]+):([1-9][0-9]*)(?![0-9])")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+)$")


def contract_urls(body: str, recorded: object = None) -> set[str]:
    urls = {url for url in recorded if isinstance(url, str)} if isinstance(recorded, list) else set()
    within_sources = False
    for line in without_literal_blocks(body).splitlines():
        if heading := _HEADING.match(line):
            if len(heading[1]) <= 2:
                within_sources = heading[2].strip() in {"原本", "入力"}
            continue
        if within_sources or re.match(r"\s*-?\s*(?:外部資料|入力)[:：]", line):
            urls.update(source_urls(line))
    return urls


def check_table(contract: pathlib.Path | None, urls: set[str], root: pathlib.Path) -> str | None:
    if not urls:
        return None
    if contract is not None:
        path = contract.with_name(TABLE_NAME)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeError:
            return f"`{path}`を読み取れません。"
        return table_error(text, urls, root)
    return f"`{TABLE_NAME}`を読み取るための作業契約がありません。"


def _meaningful(cell: str) -> str:
    inline = MarkdownIt("commonmark").parseInline(unicodedata.normalize("NFKC", cell))[0]
    value = "".join(token.content for token in inline.children or () if token.type in {"text", "code_inline"})
    return re.sub(r"[^\w]|_", "", value)


def _valid_reference(cell: str, root: pathlib.Path) -> bool:
    for path_value, line_value in _REFERENCE.findall(cell):
        relative = pathlib.PurePosixPath(path_value)
        if relative.is_absolute() or ".." in relative.parts or len(line_value) > 12:
            continue
        try:
            path = (root / relative).resolve()
            if path.is_relative_to(root) and path.is_file():
                with path.open(encoding="utf-8") as stream:
                    if any(number == int(line_value) for number, _ in enumerate(stream, 1)):
                        return True
        except OSError, UnicodeError:
            continue
    return False


def table_error(text: str, urls: set[str], root: pathlib.Path) -> str | None:
    root = root.resolve()
    columns: dict[str, int] = {}
    covered: set[str] = set()
    lines = without_literal_blocks(text).splitlines()
    for line_number, line in enumerate(lines, 1):
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            columns = {}
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line[1:-1])]
        if COLUMNS[0] in cells:
            bad_columns = [column for column in COLUMNS if cells.count(column) != 1]
            if bad_columns:
                columns_text = "、".join(f"`{column}`" for column in bad_columns)
                diagnostics = [f"{line_number}行目の見出しに{columns_text}を各1列書いてください。"]
                for affected, following in enumerate(lines[line_number:], line_number + 1):
                    if not following.strip().startswith("|"):
                        break
                    if urls.intersection(source_urls(following)):
                        diagnostics.append(f"{affected}行目は必須列が揃っていないため比較できません。")
                diagnostics.append(
                    "差分が「なし」の行には `path:行` が必要です。既存の箇所が同じ行動を促すか独立に照合するためです。"
                )
                return "\n".join(diagnostics)
            columns = {column: cells.index(column) for column in COLUMNS}
            continue
        if not columns or columns[COLUMNS[0]] >= len(cells):
            continue
        url = next(source_urls(cells[columns[COLUMNS[0]]]), "")
        if url not in urls:
            continue
        values = {column: cells[index] if index < len(cells) else "" for column, index in columns.items()}
        for column in COLUMNS[1:]:
            content = _meaningful(values[column])
            if not content or (column == COLUMNS[4] and content in _ABSENT):
                return f"{line_number}行目の`{column}`に記入がありません。"
        if _meaningful(values[COLUMNS[3]]) in _ABSENT and not _valid_reference(values[COLUMNS[2]], root):
            return f"{line_number}行目で既存との差分がない場合は、実在する相対パスと範囲内の行を `path:行` で示してください。"
        covered.add(url)
    if missing := urls - covered:
        return f"`{COLUMNS[0]}`に対応する行がありません: " + "、".join(sorted(missing))
    return None
