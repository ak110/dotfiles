#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""エージェント向け規範の再構築で使う判定台帳の骨格を、指定commitの文書から切り出す。

判定台帳（`docs/development/norm-restructure/`）は、基準commitの対象集合の全条文を1行ずつ持つ。
本スクリプトは条文の切り出し規則を1箇所に置き、台帳の作成と、台帳の行数の再確認の双方で
同じ規則を使えるようにする。切り出し規則と台帳の列は`docs/development/norm-restructure/README.md`が説明する。

`--origin`を付けると、条文ごとにパスを限定しない`git log -S`で最古の導入commitを求め、
`Co-Authored-By`か`Claude-Session`のtrailerの有無から由来列の初期値を埋める。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import io
import pathlib
import re
import subprocess
import sys

# WI「対象集合」の9領域。Git追跡のMarkdown（`.md`と`.md.tmpl`）だけを対象にする
TARGET_PATHSPECS = (
    "agent-toolkit/rules/",
    "agent-toolkit/share/",
    "agent-toolkit/skills/",
    ".claude/skills/",
    "AGENTS.md",
    ".chezmoi-source/dot_claude/rules/",
    ".chezmoi-source/dot_claude/skills/",
    ".chezmoi-source/dot_claude/docs/",
    ".chezmoi-source/dot_gemini/",
)
TARGET_SUFFIXES = (".md", ".md.tmpl")

LEDGER_COLUMNS = (
    "条文キー",
    "先頭抜粋",
    "判定",
    "根拠",
    "削除した場合のQCD",
    "移設先・統合先",
    "保護の根拠",
    "由来",
    "事前承認",
    "連動対象",
)

_EXCERPT_LENGTH = 40
# 導入commitの検索に使う部分文字列の長さ。文面の微修正をまたいで一致させるため、条文の一部だけを使う
_PICKAXE_LENGTH = 24
# これより短い部分文字列は多くの条文に現れ、導入commitを特定できない
_PICKAXE_MIN_LENGTH = 8
_AGENT_TRAILERS = ("Co-Authored-By:", "Claude-Session:")

_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{3,}")
_TOP_LIST_PATTERN = re.compile(r"^ ?(?:[-*+]|[0-9]+\.)\s+")
_MARKUP_PREFIX_PATTERN = re.compile(r"^\s*(?:[-*+]\s+|[0-9]+\.\s+|\|\s*|>\s*)*")


@dataclasses.dataclass
class Clause:
    """切り出した条文1件。"""

    path: str
    headings: tuple[str, ...]
    index: int
    lines: list[str]

    @property
    def key(self) -> str:
        """`<リポジトリ相対パス>#<見出しの経路>#<節内の連番>`の条文キー。"""
        return f"{self.path}#{' > '.join(self.headings)}#{self.index}"

    @property
    def excerpt(self) -> str:
        """条文の先頭40字。表の行の区切り記号と連続する空白は1つの空白へ畳む。"""
        text = " ".join(line.strip() for line in self.lines)
        text = re.sub(r"\s+", " ", text.replace("\t", " ")).strip()
        return text[:_EXCERPT_LENGTH]


def list_target_files(commit: str, cwd: pathlib.Path) -> list[str]:
    """指定commitの対象集合のファイルを、リポジトリ相対パスの昇順で返す。"""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", *TARGET_PATHSPECS],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return sorted(path for path in result.stdout.splitlines() if path.endswith(TARGET_SUFFIXES))


def read_file_at(commit: str, path: str, cwd: pathlib.Path) -> str:
    """指定commitのファイル本文を返す。"""
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


def split_clauses(path: str, text: str) -> list[Clause]:
    """Markdown本文を条文へ切り出す。

    条文の単位は、見出しを除くブロック（frontmatter、段落、最上位の箇条と字下げした子の行、
    表の本体の1行、フェンス付きコードブロック）とする。表の見出し行と区切り行は列名の定義であり、条文に数えない。
    """
    clauses: list[Clause] = []
    headings: list[str] = []
    counters: dict[tuple[str, ...], int] = {}
    current: list[str] = []
    current_kind = ""
    in_table = False
    lines = text.splitlines()

    def flush() -> None:
        nonlocal current, current_kind
        if current:
            section = tuple(headings)
            counters[section] = counters.get(section, 0) + 1
            clauses.append(Clause(path=path, headings=section, index=counters[section], lines=current))
        current = []
        current_kind = ""

    def start(kind: str, line: str) -> None:
        nonlocal current, current_kind
        flush()
        current = [line]
        current_kind = kind

    i = 0
    if lines and lines[0].strip() == "---":
        end = next((j for j in range(1, len(lines)) if lines[j].strip() == "---"), None)
        if end is not None:
            start("frontmatter", lines[0])
            current.extend(lines[1 : end + 1])
            flush()
            i = end + 1

    while i < len(lines):
        line = lines[i]
        fence = _FENCE_PATTERN.match(line)
        if fence:
            marker = fence.group(1)
            block = [line]
            i += 1
            while i < len(lines):
                block.append(lines[i])
                if lines[i].strip().startswith(marker[0] * len(marker)) and lines[i].strip().strip(marker[0]) == "":
                    break
                i += 1
            i += 1
            if current_kind == "list" and line.startswith((" ", "\t")):
                current.extend(block)
            else:
                start("fence", block[0])
                current.extend(block[1:])
                flush()
            in_table = False
            continue
        if not line.strip():
            if current_kind == "list":
                # 空行の後に字下げした行が続く場合は同じ箇条の続きとする
                following = next((lines[j] for j in range(i + 1, len(lines)) if lines[j].strip()), "")
                if following.startswith(("  ", "\t")):
                    current.append(line)
                    i += 1
                    continue
            flush()
            in_table = False
            i += 1
            continue
        heading = _HEADING_PATTERN.match(line)
        if heading and not line.startswith(" "):
            flush()
            level = len(heading.group(1))
            del headings[level - 1 :]
            headings.extend([""] * (level - 1 - len(headings)))
            headings.append(heading.group(2))
            in_table = False
            i += 1
            continue
        if line.lstrip().startswith("|"):
            if not in_table:
                # 表の見出し行と、直後の区切り行は条文に数えない
                flush()
                in_table = True
                i += 1
                if i < len(lines) and _TABLE_SEPARATOR_PATTERN.match(lines[i]):
                    i += 1
                continue
            start("table", line)
            flush()
            i += 1
            continue
        if _TOP_LIST_PATTERN.match(line):
            start("list", line)
            i += 1
            continue
        if current_kind == "list" and line.startswith((" ", "\t")):
            current.append(line)
            i += 1
            continue
        if current_kind != "paragraph":
            start("paragraph", line)
        else:
            current.append(line)
        i += 1
    flush()
    return clauses


def collect_clauses(commit: str, cwd: pathlib.Path) -> list[Clause]:
    """指定commitの対象集合の全条文を返す。"""
    clauses: list[Clause] = []
    for path in list_target_files(commit, cwd):
        clauses.extend(split_clauses(path, read_file_at(commit, path, cwd)))
    return clauses


def pickaxe_text(clause: Clause) -> str:
    """導入commitの検索に使う部分文字列を、条文の最長行の中央付近から選ぶ。"""
    candidates = [_MARKUP_PREFIX_PATTERN.sub("", line).strip() for line in clause.lines]
    longest = max(candidates, key=len, default="")
    if len(longest) <= _PICKAXE_LENGTH:
        return longest
    begin = (len(longest) - _PICKAXE_LENGTH) // 2
    return longest[begin : begin + _PICKAXE_LENGTH]


def find_origin(clause: Clause, commit: str, cwd: pathlib.Path) -> str:
    """条文の最古の導入commitとtrailerから、由来列の初期値を返す。"""
    needle = pickaxe_text(clause)
    if len(needle) < _PICKAXE_MIN_LENGTH:
        return f"未確定: 条文が短く導入commitを特定できない（検索語「{needle}」）"
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H%x1f%B%x1e", "-S", needle, commit],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    records = [record.strip("\n") for record in result.stdout.split("\x1e") if record.strip()]
    if not records:
        return f"未確定: 導入commitを特定できない（検索語「{needle}」）"
    oid, _, body = records[0].partition("\x1f")
    if any(trailer in body for trailer in _AGENT_TRAILERS):
        return f"エージェント由来: 導入commit {oid[:12]}にtrailerあり（検索語「{needle}」）"
    return f"未確定: 導入commit {oid[:12]}にtrailerなし。キュー項目の照合は削除・縮小の行で行う（検索語「{needle}」）"


def ledger_file_name(path: str) -> str:
    """条文のパスから、台帳ファイル名を返す。

    台帳は条文を持つファイルごとに分ける。1ファイルへまとめると容量が大きくなり、
    領域ごとに分担して記入する主体どうしの書込範囲も重なるためである。
    パスの`/`を`__`へ置き換え、先頭の`.`は`dot-`へ置き換えて隠しファイルにしない。
    """
    name = path.replace("/", "__")
    if name.startswith("."):
        name = "dot-" + name[1:]
    return f"{name}.tsv"


def _cell(value: str) -> str:
    return re.sub(r"[\t\r\n]+", " ", value)


def _row(clause: Clause, origin: str) -> str:
    values = {"条文キー": clause.key, "先頭抜粋": clause.excerpt, "由来": origin}
    return "\t".join(_cell(values.get(column, "")) for column in LEDGER_COLUMNS)


def main(argv: list[str] | None = None) -> int:
    """CLIのエントリポイント。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("commit", help="条文を切り出すcommit（台帳の基準commit）")
    parser.add_argument("--count", action="store_true", help="条文の件数だけを出力する")
    parser.add_argument("--origin", action="store_true", help="由来列の初期値を`git log -S`で埋める")
    parser.add_argument("--jobs", type=int, default=8, help="`--origin`の並列数")
    parser.add_argument(
        "--split-dir",
        type=pathlib.Path,
        help="領域ごとの台帳ファイルをこのディレクトリへ書く。省略時は全条文を標準出力へ書く",
    )
    args = parser.parse_args(argv)
    cwd = pathlib.Path.cwd()
    clauses = collect_clauses(args.commit, cwd)
    if args.count:
        print(len(clauses))
        return 0
    origins = [""] * len(clauses)
    if args.origin:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
            origins = list(executor.map(lambda clause: find_origin(clause, args.commit, cwd), clauses))
    header = "\t".join(LEDGER_COLUMNS)
    if args.split_dir is None:
        print(header)
        for clause, origin in zip(clauses, origins, strict=True):
            print(_row(clause, origin))
        return 0
    groups: dict[str, list[str]] = {}
    for clause, origin in zip(clauses, origins, strict=True):
        groups.setdefault(ledger_file_name(clause.path), []).append(_row(clause, origin))
    args.split_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in sorted(groups.items()):
        (args.split_dir / name).write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    print(f"{len(clauses)}件の条文を{len(groups)}ファイルへ書いた: {args.split_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
