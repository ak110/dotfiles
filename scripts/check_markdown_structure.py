"""Markdownの文単位の改行と全角丸括弧の入れ子を検査する。

表記の回帰をCIで拒否する明示要件に従い、通常の体裁warningと異なりerrorを返す。
--fixは原文へ改行・リスト継続字下げだけを追加する。
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import io
import pathlib
import re
import sys

import markdown_it
import markdown_it.rules_inline

_COMMENT = re.compile(r"<!--[\s\S]*?(?:-->|$)")
_LIST = re.compile(r"^(\s*)(?:[-+*]|\d+[.)])(\s+)")
_EXTERNAL = ".chezmoi-source/dot_gemini/antigravity-cli/skills/japanese-tech-writing/SKILL.md"


@dataclasses.dataclass(frozen=True)
class _Issue:
    offset: int
    message: str


def _mask(text: str) -> str:
    # 同じ文字数で伏せ、原文への位置対応と行跨ぎの括弧状態を保持する。
    masked = list(text)

    def record_code(state: markdown_it.rules_inline.StateInline, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        accepted = markdown_it.rules_inline.backtick(state, silent)
        if not silent and len(state.tokens) > count and state.tokens[-1].type == "code_inline" and state.src == text:
            masked[start : state.pos] = re.sub(r"[^\r\n]", " ", text[start : state.pos])
        return accepted

    parser = markdown_it.MarkdownIt("commonmark")
    # backtickのエスケープ・区切り長は解析器に任せ、元の位置だけを記録する。
    parser.inline.ruler.at("backticks", record_code)
    parser.inline.parse(text, parser, {}, [])
    text = "".join(masked)
    for pattern in (_COMMENT, re.compile(r"<[^>\n]+>")):
        text = pattern.sub(lambda m: re.sub(r"[^\r\n]", " ", m[0]), text)
    return text


def _excluded_source(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip("\ufeff\r\n") == "---":
        for end in range(1, len(lines)):
            if lines[end].strip() in {"---", "..."}:
                lines[: end + 1] = [re.sub(r"[^\r\n]", " ", line) for line in lines[: end + 1]]
                break
    source = "".join(lines)
    if source.startswith("\ufeff"):
        source = " " + source[1:]
    return _COMMENT.sub(lambda m: re.sub(r"[^\r\n]", " ", m[0]), source)


def _sentence_breaks(text: str) -> list[int]:
    masked = _mask(text)
    stack: list[str] = []
    pairs = {"（": "）", "(": ")", "「": "」", "『": "』"}
    breaks = []
    for index, char in enumerate(masked):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == "。" and not stack:
            end = index + 1
            # 文末の強調記法とリンクの閉じ記法は同じ行へ残す。
            while end < len(text):
                closing = re.match(r"\*\*|__|~~|\*|_", text[end:])
                if closing and text[:end].count(closing[0]) % 2:
                    end += len(closing[0])
                    continue
                if text[end:].startswith("]("):
                    link_end = text.find(")", end + 2)
                    if link_end >= 0:
                        end = link_end + 1
                        continue
                break
            line_end = masked.find("\n", end)
            if line_end < 0:
                line_end = len(masked)
            if text[end:line_end].strip():
                breaks.append(end)
    return breaks


def _inspect(text: str, *, sentences: bool) -> tuple[list[_Issue], dict[int, str]]:
    source = _excluded_source(text)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    tokens = markdown_it.MarkdownIt("commonmark").enable("table").parse(source)
    issues: list[_Issue] = []
    insertions: dict[int, str] = {}
    quote_depth = 0
    paragraph = False
    in_table = False
    lists: list[str] = []
    depth = 0
    for token in tokens:
        if token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        elif token.type == "list_item_open":
            assert token.map is not None
            first = lines[token.map[0]].rstrip("\r\n")
            match = _LIST.match(first)
            lists.append(" " * match.end() if match else "")
        elif token.type == "list_item_close":
            lists.pop()
        elif token.type == "paragraph_open":
            paragraph = True
        elif token.type == "paragraph_close":
            paragraph = False
        elif token.type == "table_open":
            in_table = True
        elif token.type == "table_close":
            in_table = False
        # cellのinline.mapは同じ物理行を共有するため、括弧の深さは表全体で一度だけ更新する。
        if token.type == "inline" and in_table:
            continue
        if token.type not in {"inline", "table_open"} or token.map is None or quote_depth:
            continue
        start, stop = token.map
        raw = "".join(lines[start:stop])
        masked = _mask(raw)
        for index, char in enumerate(masked):
            if char == "（":
                if depth:
                    issues.append(_Issue(offsets[start] + index, "全角丸括弧の入れ子を解消する"))
                depth += 1
            elif char == "）":
                depth = max(0, depth - 1)
        if token.type != "inline" or not sentences or not paragraph or token.content.lstrip().startswith("逐語引用:"):
            continue
        first_content = token.content.split("\n", maxsplit=1)[0]
        content_column = lines[start].find(first_content)
        indent = " " * content_column if lists and content_column >= 0 else (lists[-1] if lists else "")
        for end in _sentence_breaks(raw):
            position = offsets[start] + end
            issues.append(_Issue(position - 1, "1文1行へ改行する（--fixで修正可能）"))
            # 空白は原文のまま次行へ送り、リストの継続に必要な字下げだけを追加する。
            insertions[position] = indent
    return sorted(issues, key=lambda issue: issue.offset), insertions


def main(argv: list[str] | None = None) -> int:
    """指定ファイルを検査し、成功0・違反1・入力エラー2を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="文境界の改行とリスト継続字下げを追加する")
    parser.add_argument("files", nargs="*", type=pathlib.Path)
    args = parser.parse_args(argv)
    result = 0
    prepared: list[tuple[pathlib.Path, str, list[_Issue]]] = []
    changes: dict[pathlib.Path, bytes] = {}
    root = pathlib.Path.cwd().resolve()
    for path in args.files:
        try:
            text = path.read_bytes().decode("utf-8")
            resolved = path.resolve()
            # 作業領域の上位にあるfixturesという名前で通常入力まで除外しない。
            if resolved.is_relative_to(root) and "fixtures" in resolved.relative_to(root).parts:
                continue
            sentences = not path.as_posix().endswith(_EXTERNAL)
            issues, insertions = _inspect(text, sentences=sentences)
            if args.fix and insertions:
                newline = "\r\n" if "\r\n" in text else "\n"
                for position, indent in sorted(insertions.items(), reverse=True):
                    text = text[:position] + newline + indent + text[position:]
                changes[path] = text.encode("utf-8")
                issues, _ = _inspect(text, sentences=sentences)
            prepared.append((path, text, issues))
            result = max(result, int(bool(issues)))
        except (OSError, UnicodeError) as exc:
            print(f"{path}: 入力エラー: {exc}。UTF-8の読書き可能なファイルを指定する", file=sys.stderr)
            result = 2
    # 後続入力の失敗で一部だけが修正されないよう、全入力の解析・出力生成を先に終える。
    if args.fix and result == 2:
        return result
    for path, content in changes.items():
        try:
            path.write_bytes(content)
        except OSError as exc:
            print(f"{path}: 入力エラー: {exc}。UTF-8の読書き可能なファイルを指定する", file=sys.stderr)
            result = 2
    for path, text, issues in prepared:
        starts = [0, *(m.end() for m in re.finditer("\n", text))]
        for issue in issues:
            line = bisect.bisect_right(starts, issue.offset)
            column = issue.offset - starts[line - 1] + 1
            print(f"{path}:{line}:{column}: error: {issue.message}", file=sys.stderr)
    return result


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
