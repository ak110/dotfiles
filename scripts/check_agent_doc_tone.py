#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""コーディングエージェント向け文書の文体の密度を測る。

読み手は文脈にある文体を再生産するため、否定形の宣言や法令調の語が多い文書は、
それを読んだ主体の成果物へ同じ文体と範囲外の追加要素を持ち込む。
本スクリプトは当該密度をファイル単位で数え、閾値を超えたファイルを報告する。

指標は次の3つとする。

- 文数
- 否定形で終わる文の数
- 「当該」の出現数

閾値は、Codexを使う前のClaude由来commitが追加した文の水準に置く。
否定形終端率は8%、「当該」の出現率（出現数÷文数）は3%とし、文数が20以上のファイルへ適用する。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

_FRONTMATTER_DELIMITER = "---"
_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s")
_TABLE_ROW_PATTERN = re.compile(r"^\s*\|")
_HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
_LIST_MARKER_PATTERN = re.compile(r"^\s*(?:[-*+]|[0-9]+\.)\s+")
_INLINE_CODE_PATTERN = re.compile(r"`[^`]*`")
_INLINE_CODE_PLACEHOLDER = "コード"

_NEGATIVE_ENDING_PATTERN = re.compile(r"(ない|せず|ず|しません|ません)[。）」]*\s*$")
_SUBJECT_WORD = "当該"

_MIN_SENTENCES_FOR_RATIO = 20
_MAX_NEGATIVE_ENDING_RATIO = 0.08
_MAX_SUBJECT_WORD_RATIO = 0.03

EXCLUDED_PATHS = (
    "agent-toolkit/skills/writing-standards/references/tone-examples.md",
    "agent-toolkit/skills/writing-standards/references/tone-examples-llm-tone.md",
    "agent-toolkit/skills/writing-standards/references/textlint-violations.md",
)
"""意図的な違反例を収録するため検査から除くファイル。"""


def _strip_frontmatter(lines: list[str]) -> list[str]:
    """先頭のYAML frontmatterを除いた行を返す。"""
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return lines
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_DELIMITER:
            return lines[index + 1 :]
    return lines


def _prose_lines(text: str) -> list[str]:
    """本文として数える行だけを返す。

    フェンス付きコードブロック、見出し行、表の行及びHTMLコメントを除き、
    箇条書きの記号と番号を取ってからインラインコードを固定の置換語へ置き換える。
    """
    without_comments = _HTML_COMMENT_PATTERN.sub("", text)
    lines = _strip_frontmatter(without_comments.splitlines())
    prose: list[str] = []
    fence: str | None = None
    for line in lines:
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match is not None:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        if _HEADING_PATTERN.match(line) or _TABLE_ROW_PATTERN.match(line):
            continue
        stripped = _LIST_MARKER_PATTERN.sub("", line).strip()
        if not stripped:
            continue
        prose.append(_INLINE_CODE_PATTERN.sub(_INLINE_CODE_PLACEHOLDER, stripped))
    return prose


def split_sentences(text: str) -> list[str]:
    """本文を「。」で分割した文の一覧を返す。"""
    joined = "".join(_prose_lines(text))
    sentences = [sentence.strip() for sentence in re.split(r"(?<=。)", joined)]
    return [sentence for sentence in sentences if sentence]


class Metrics:
    """1ファイルの文体指標。"""

    def __init__(self, path: pathlib.Path, text: str) -> None:
        sentences = split_sentences(text)
        self.path = path
        self.sentences = len(sentences)
        self.negative_endings = sum(1 for sentence in sentences if _NEGATIVE_ENDING_PATTERN.search(sentence))
        self.subject_words = text.count(_SUBJECT_WORD)

    @property
    def negative_ending_ratio(self) -> float:
        """否定形で終わる文の割合を返す。"""
        return self.negative_endings / self.sentences if self.sentences else 0.0

    @property
    def subject_word_ratio(self) -> float:
        """文数に対する「当該」の出現率を返す。"""
        return self.subject_words / self.sentences if self.sentences else 0.0

    def violations(self) -> list[str]:
        """閾値を満たさない指標の説明を返す。"""
        problems: list[str] = []
        if self.sentences >= _MIN_SENTENCES_FOR_RATIO:
            if self.negative_ending_ratio > _MAX_NEGATIVE_ENDING_RATIO:
                problems.append(
                    f"否定形終端率 {self.negative_ending_ratio:.1%}（上限 {_MAX_NEGATIVE_ENDING_RATIO:.0%}、"
                    f"{self.negative_endings}/{self.sentences}文）"
                )
            if self.subject_word_ratio > _MAX_SUBJECT_WORD_RATIO:
                problems.append(
                    f"「当該」の出現率 {self.subject_word_ratio:.1%}（上限 {_MAX_SUBJECT_WORD_RATIO:.0%}、"
                    f"{self.subject_words}回/{self.sentences}文）"
                )
        return problems


def _is_excluded(path: pathlib.Path) -> bool:
    """検査から除く固定のファイルかを返す。"""
    posix = path.as_posix()
    return any(posix.endswith(excluded) for excluded in EXCLUDED_PATHS)


def _print_report(metrics: list[Metrics]) -> None:
    """全ファイルの指標を表で標準出力へ書く。"""
    header = ("文数", "否定形終端", "当該", "ファイル")
    print("\t".join(header))
    for metric in metrics:
        print(
            "\t".join(
                (
                    str(metric.sentences),
                    str(metric.negative_endings),
                    str(metric.subject_words),
                    str(metric.path),
                )
            )
        )


def main(argv: list[str] | None = None) -> int:
    """指定ファイルの文体指標を測り、閾値を超えた場合に1を返す。"""
    parser = argparse.ArgumentParser(description="コーディングエージェント向け文書の文体の密度を測る。")
    parser.add_argument("paths", metavar="PATH", nargs="+", type=pathlib.Path, help="検査対象のMarkdownファイル。")
    parser.add_argument("--report", action="store_true", help="判定を行わず、全ファイルの指標を表で出力する。")
    args = parser.parse_args(argv)

    targets = [path for path in args.paths if not _is_excluded(path)]
    metrics = [Metrics(path, path.read_text(encoding="utf-8")) for path in targets]
    if args.report:
        _print_report(metrics)
        return 0

    failed = False
    for metric in metrics:
        problems = metric.violations()
        if problems:
            failed = True
            print(f"{metric.path}: {'、'.join(problems)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
