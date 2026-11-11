"""資料の全文・見出し・Makefileの行範囲を解決する。"""

from __future__ import annotations

import dataclasses
import functools
import pathlib
import re
import urllib.parse
from collections.abc import Iterator

from agent_toolkit._common.read_triggers_model import _scope_cached


def _scope_selector(text: str) -> str:
    """節を指す自然文から、見出し・範囲・冒頭の選択子だけを保つ。"""
    ranges = re.findall(r"「([^」]+)」から「([^」]+)」まで", text)
    rest = re.sub(r"「([^」]+)」から「([^」]+)」まで", "", text)
    result = "冒頭" if "冒頭" in re.sub(r"「[^」]+」", "", rest) else ""
    result += "".join(f"「{start}」から「{end}」まで" for start, end in ranges)
    result += "".join(f"「{name}」" for name in re.findall(r"「([^」]+)」", rest))
    return result


@_scope_cached
def is_normative_document(path: pathlib.Path) -> bool:
    """規範原本とホスト向けの写しを、記録類のMarkdownから区別する。"""
    path = path.resolve()
    if path.name in {"AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md"}:
        return True
    # ホスト側のリンクをresolveするとchezmoiの原本へ戻るため、配布元も同じ範囲で識別する。
    if path.name.endswith((".md", ".md.tmpl")):
        for parent in path.parents:
            if parent.name not in {"skills", "rules"}:
                continue
            host = parent.parent
            if host.name == "antigravity-cli" and host.parent.name == "dot_gemini":
                host = host.parent
            if host.name in {"dot_claude", "dot_codex", "dot_agents", "dot_gemini"} and host.parent.name == ".chezmoi-source":
                return True
    if path.suffix != ".md":
        return False
    if any(
        parent.name in {"skills", "rules"} and parent.parent.name in {".claude", ".codex", ".agents", ".gemini"}
        for parent in path.parents
    ):
        return True
    owner = next((parent for parent in path.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
    return owner is not None and path.relative_to(owner).parts[0] in {"skills", "share", "rules"}


def document_lines(text: str) -> Iterator[tuple[int, str, bool]]:
    """物理行と、長さの異なる内側フェンスを除いた本文の可視性を返す。"""
    fence: tuple[str, int] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        mark = re.match(r"(`{3,}|~{3,})", stripped)
        if mark:
            if not fence:
                fence = (mark[1][0], len(mark[1]))
            elif mark[1][0] == fence[0] and len(mark[1]) >= fence[1] and not stripped[len(mark[1]) :].strip():
                fence = None
            yield number, line, False
            continue
        yield number, line, fence is None


@functools.lru_cache(maxsize=1)
def _headings(text: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """節の範囲解決で直前の本文の索引を共有する。本文をキーにし、同サイズ更新も再解析する。"""
    lines = text.splitlines(keepends=True)
    headings = []
    for number, line, visible in document_lines(text):
        matched = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if visible and matched:
            headings.append((number - 1, len(matched[1]), matched[2]))
    return lines, headings


def _make_targets(text: str, name: str, path: pathlib.Path) -> tuple[tuple[int, int], ...]:
    """指定targetと静的に名指しされた子targetを、共有変数の定義とともに読む。"""
    lines = text.splitlines(keepends=True)
    starts = [
        (index, match[1], match[2])
        for index, line in enumerate(lines)
        if (match := re.match(r"^([\w.-]+):\s*(.*?)\s*$", line)) and not line.startswith(".")
    ]
    blocks = {
        target: (index, starts[n + 1][0] if n + 1 < len(starts) else len(lines), dependencies)
        for n, (index, target, dependencies) in enumerate(starts)
    }
    selected: set[str] = set()

    def visit(target: str) -> None:
        if target in selected:
            return
        if target not in blocks:
            raise ValueError(f"{path}: Makefileのtargetが存在しない: {target}")
        selected.add(target)
        start, end, dependencies = blocks[target]
        children = dependencies.split() + re.findall(r"\$\(MAKE\)\s+([\w.-]+)", "".join(lines[start:end]))
        for child in children:
            visit(child)

    visit(name)
    indices = set(range(starts[0][0])) if starts else set()
    for target in selected:
        start, end, _dependencies = blocks[target]
        indices.update(range(start, end))
    ranges: list[tuple[int, int]] = []
    for index in sorted(indices):
        if ranges and ranges[-1][1] == index:
            ranges[-1] = (ranges[-1][0], index + 1)
        else:
            ranges.append((index + 1, index + 1))
    return tuple(ranges)


@dataclasses.dataclass(frozen=True)
class ReadTarget:
    """資料全体か、明示指定された見出しの範囲。"""

    path: pathlib.Path
    heading: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.expanduser().resolve())

    def __str__(self) -> str:
        return str(self.path) + ("#" + self.heading if self.heading else "")

    def body(self, text: str | None = None) -> str:
        """指定範囲だけを返す。見出しの曖昧さと不在は宣言エラーにする。"""
        text = self.path.read_text(encoding="utf-8") if text is None else text
        lines = text.splitlines(keepends=True)
        return "".join("".join(lines[start - 1 : end]) for start, end in self.line_ranges(text))

    def line_ranges(self, text: str | None = None) -> tuple[tuple[int, int], ...]:
        """節の解決を共有し、案内するnative読取へ1始まりの行範囲を渡す。"""
        text = self.path.read_text(encoding="utf-8") if text is None else text
        if self.path.name == "Makefile" and self.heading.startswith("make:"):
            return _make_targets(text, self.heading.removeprefix("make:"), self.path)
        lines, headings = _headings(text)
        if not self.heading:
            return ((1, len(lines)),) if lines else ()
        selector = urllib.parse.unquote(self.heading)

        def find(value: str) -> tuple[int, int, str]:
            value = urllib.parse.unquote(value)
            found = [
                heading
                for heading in headings
                if heading[2] == value or re.sub(r"[^\w\s-]", "", heading[2]).lower().replace(" ", "-") == value
            ]
            if len(found) != 1:
                raise ValueError(f"{self.path}: 見出しを一意に解決できない: {value}")
            return found[0]

        def end_of(heading: tuple[int, int, str]) -> int:
            return next((index for index, level, _name in headings if index > heading[0] and level <= heading[1]), len(lines))

        selections: list[tuple[int, int]] = []
        unquoted = re.sub(r"「[^」]+」から「[^」]+」まで", "", selector)
        preamble = bool(re.fullmatch(r"冒頭[と、・\s]*", re.sub(r"「[^」]+」", "", unquoted)))
        if preamble:
            selections.append((0, next((index for index, level, _name in headings if level == 2), len(lines))))
        ranges = re.findall(r"「([^」]+)」から「([^」]+)」まで", selector)
        rest = re.sub(r"「([^」]+)」から「([^」]+)」まで", "", selector)
        for first, last in ranges:
            start, finish = find(first), find(last)
            if start[0] > finish[0]:
                raise ValueError(f"{self.path}: 節範囲の順序が逆: {self.heading}")
            selections.append((start[0], end_of(finish)))
        names = re.findall(r"「([^」]+)」", rest)
        if not names and not ranges and not preamble:
            names = [selector]
        for name in names:
            heading = find(name)
            selections.append((heading[0], end_of(heading)))
        indices = {index for start, end in selections for index in range(start, end)}
        result: list[tuple[int, int]] = []
        for index in sorted(indices):
            if result and index == result[-1][1]:
                result[-1] = (result[-1][0], index + 1)
            else:
                result.append((index + 1, index + 1))
        return tuple(result)
