#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py>=4"]
# ///
r"""文体の密度と、説明に使用しない語の再使用を確認する。

読み手は文脈にある文体を再生産するため、否定形の宣言や法令調の語が多い文書は、
それを読んだ主体の成果物へ同じ文体と範囲外の追加要素を持ち込む。
本スクリプトはこの密度をファイル単位で数える。閾値を超えた指標は、ファイルの1行目へ比率、件数と
上限以下へ戻すのに必要な最少の書き換え件数を報告し、数えた文と「当該」を物理行の位置付きで全件列挙する。

指標は次の3つとする。

- 文数
- 否定形で終わる文の数
- 「当該」の出現数

閾値は、Codexを使う前のClaude由来commitが追加した文の水準に置く。
否定形終端率は8%、「当該」の出現率（出現数÷文数）は3%とし、文数が20以上のファイルへ適用する。

説明文の語は文数によらず判定する。`notation-rules.md`が説明に使わないと定める語は、
未対応なら要求を満たしていないため終了コード1を返す。文脈によって対象と動作が伝わりにくい語は
位置付きの警告として報告し、その語だけでは終了コードを変えない。
密度の判定はMarkdownだけに適用する。引用・悪い例・検出データと構造名は文章と区別する。
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import enum
import io
import json
import pathlib
import re
import sys
import tokenize
import tomllib
from collections.abc import Iterator

import markdown_it

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
_MAX_EXCERPT_LENGTH = 40
_SUBJECT_WORD_CONTEXT = 10

EXCLUDED_PATHS = (
    "agent-toolkit/skills/writing-standards/references/tone-examples.md",
    "agent-toolkit/skills/writing-standards/references/tone-examples-llm-tone.md",
    "agent-toolkit/skills/writing-standards/references/textlint-violations.md",
)
"""意図的な違反例を収録するため密度の判定から除くファイル。"""

# 語そのものを扱う判定用データ。説明文の同義語をここでは定めない。
# 失敗させる語は`notation-rules.md`「日本語の表記ルール」が説明に使わないと定める語に限る。
_DENIED_PATTERNS = {
    "正本": r"(?<!是)正本|正本(?!文)",
    "既定": r"既定",
    "照合": r"照合",
}
# 文脈によって対象と動作が伝わりにくい語。正確な専門語や承認済みの呼称として使う場合もあるため警告に留める。
_METAPHORICAL = "metaphorical"
_ABSTRACT = "abstract"
_CAUTION_PATTERNS = {
    "経路": (r"経路", _ABSTRACT),
    "入口": (r"入口", _METAPHORICAL),
    "検査": (r"検査", _ABSTRACT),
    "実測": (r"実測", _ABSTRACT),
    "黙って": (r"黙って", _METAPHORICAL),
    "漏れ": (r"漏[れら]", _ABSTRACT),
    "帰結": (r"帰結", _ABSTRACT),
    "部品": (r"部品", _METAPHORICAL),
    "見落とす": (r"見落と", _METAPHORICAL),
    "断定": (r"断定", _ABSTRACT),
    "事故": (r"事故", _ABSTRACT),
    "混ざる": (r"混ざ", _METAPHORICAL),
    "素通り": (r"素通り", _METAPHORICAL),
    "切り分ける": (r"切[り]分", _ABSTRACT),
    "焼く": (r"焼(?:く|か|き|け|い)", _METAPHORICAL),
    "崩す": (r"崩(?:す|さ|し|せ|そ)", _METAPHORICAL),
    "疑う": (r"疑(?:う|わ|い|え|っ)", _ABSTRACT),
    "線引き": (r"線引き", _METAPHORICAL),
    "破綻": (r"破綻", _ABSTRACT),
    "ゲート": (r"ゲート", _METAPHORICAL),
}
_CAUTION_MESSAGES = {
    _METAPHORICAL: (
        "比喩や擬人化として使うと、実際の対象、状態または動作が曖昧になる。"
        "字義どおりの用法または定義済みの名称でなければ、別の比喩へ置き換えず、"
        "指している実体、状態または動作を直接書く"
    ),
    _ABSTRACT: (
        "文脈によって、実際の操作、判定対象、条件または結果が変わり得る。"
        "正確な専門語または定義済みの名称でなければ、実際の操作、判定対象、条件または結果を書き分ける"
    ),
}
_TERM_PATTERNS = tuple((name, re.compile(pattern)) for name, pattern in _DENIED_PATTERNS.items())
_CAUTION_TERM_PATTERNS = tuple((name, re.compile(pattern)) for name, (pattern, _category) in _CAUTION_PATTERNS.items())

# 計画の列・メタ情報と担当の返却値として保存する名称。周辺の説明も判定する。
# 「前提を疑う観点」はユーザーが確認で選んだ定義済みの名前（`reviewer.md`のレビュー観点）であるため、この完全一致だけを除く。
# 名前の一部ではない同じ動詞の用法は引き続き検出する。
_STRUCTURAL_LABELS = (
    "消費主体と入口",
    # 改名前の受入シナリオ表の列名。保存済み計画の読み取りと同じく構造の名前として除く。
    "利用者と入口",
    "起動経路",
    "計画検査完了",
    "書込対象の検査",
    "選択肢と帰結",
    "前提を疑う観点",
)
_QUOTED_DATA_PATTERN = re.compile(r"「[^」]*」|`[^`]*`")
# 選定結果の欄`プロジェクト固有の公開後の操作の順序`（旧欄名`terminal_order`）の値`既定`は保存形式の値として扱う。
_TERMINAL_ORDER_VALUE_PATTERN = re.compile(
    r'((?:プロジェクト固有の公開後の操作の順序|terminal_order)[^\n]*?)(?:`既定`|「既定」|"既定")'
)


def _without_structural_labels(text: str) -> str:
    """保存形式の名称だけを空白へ置き換え、説明文を残す。"""
    for label in _STRUCTURAL_LABELS:
        text = text.replace(label, " " * len(label))
    return _TERMINAL_ORDER_VALUE_PATTERN.sub(lambda match: match.group(1), text)


def _example_lines(text: str, start: int) -> Iterator[tuple[int, str]]:
    """例の役割に応じて良い例と解説を返し、原文を示す行を保つ。"""
    role = ""
    for offset, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith(("悪い例:", "冗長な例:", "対象語:", "逐語引用:")):
            role = "data"
        elif stripped.startswith(("書き換え:", "良い例:", "例文:")):
            role = "example"
        elif stripped.startswith("解説:"):
            role = "explanation"
        if role == "data":
            continue
        if role == "explanation":
            line = _QUOTED_DATA_PATTERN.sub(lambda match: " " * len(match.group()), line)
        yield start + offset, line


def _markdown_fragments(text: str) -> Iterator[tuple[int, str]]:
    """Markdownの構造から本文・見出し・表・自作の文を行番号付きで返す。"""
    quote_depth = 0
    for token in markdown_it.MarkdownIt("commonmark").enable("table").parse(text):
        if token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        if quote_depth or token.map is None:
            continue
        if token.type in {"inline", "html_block"}:
            yield from _example_lines(token.content, token.map[0] + 1)
        elif token.type in {"fence", "code_block"}:
            start = token.map[0] + (2 if token.type == "fence" else 1)
            if token.info.strip() == "python":
                try:
                    fragments = list(_python_fragments(token.content))
                except (SyntaxError, tokenize.TokenError):
                    # 部分的なコード例は単独のPythonとして成立しないため、記載された文を確認する。
                    fragments = list(_example_lines(token.content, 1))
                yield from ((start + line - 1, fragment) for line, fragment in fragments)
            else:
                yield from _example_lines(token.content, start)


def _literal_text(node: ast.AST) -> str | None:
    """実行せずに読める文字列の連結を返す。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}" for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left)
        right = _literal_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _call_name(node: ast.AST) -> str:
    """呼び出す関数またはメソッドの末尾の名前を返す。"""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _python_fragments(text: str, *, detector_data: bool = False) -> Iterator[tuple[int, str]]:
    """コメント、docstringと表示用の文字列を返し、識別子と判定データを区別する。"""
    tree = ast.parse(text)
    test_data = any(
        isinstance(node, ast.Import)
        and any(alias.name == "pytest" for alias in node.names)
        or isinstance(node, ast.ImportFrom)
        and node.module == "pytest"
        for node in tree.body
    )
    comments = list(tokenize.generate_tokens(io.StringIO(text).readline))
    test_data = test_data or any(
        token.type == tokenize.COMMENT and token.string == "# agent-doc-tone: test-data" for token in comments
    )
    for token in comments:
        if token.type == tokenize.COMMENT:
            yield token.start[0], token.string
    standalone_strings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    }
    consumed: set[int] = set()
    displayed: set[int] = set()
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def scope(node: ast.AST) -> ast.AST:
        while id(node) in parents:
            node = parents[id(node)]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                return node
        return tree

    assignments: dict[tuple[int, str], list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None:
                for target in targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault((id(scope(node)), target.id), []).append(node.value)

    def mark_usage(node: ast.AST, destination: set[int]) -> None:
        if id(node) in destination:
            return
        destination.add(id(node))
        if isinstance(node, ast.Name):
            owner = scope(node)
            while True:
                if (id(owner), node.id) in assignments:
                    for value in assignments[(id(owner), node.id)]:
                        mark_usage(value, destination)
                    break
                if owner is tree:
                    break
                owner = scope(owner)
        for child in ast.iter_child_nodes(node):
            mark_usage(child, destination)

    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            mark_usage(node.exc, displayed)
        if isinstance(node, ast.Assert):
            consumed.update(id(child) for child in ast.walk(node.test))
            if node.msg is not None:
                mark_usage(node.msg, displayed)
        if isinstance(node, ast.Call) and _call_name(node.func) in {
            "print",
            "write",
            "debug",
            "info",
            "warning",
            "error",
            "exception",
            "critical",
            "fail",
        }:
            for argument in node.args:
                mark_usage(argument, displayed)
        if isinstance(node, ast.Dict):
            consumed.update(id(child) for key in node.keys if key is not None for child in ast.walk(key))
        if isinstance(node, ast.Call) and _call_name(node.func) in {
            "Path",
            "PurePath",
            "PosixPath",
            "WindowsPath",
            "PurePosixPath",
            "PureWindowsPath",
            "open",
            "with_name",
            "with_suffix",
            "joinpath",
        }:
            arguments = node.args[:1] if _call_name(node.func) == "open" else node.args
            for argument in arguments:
                mark_usage(argument, consumed)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
            and node.func.attr == "compile"
            and node.args
        ):
            # 一致させる語を定めるパターンは、説明文とは別の検出用データである。
            consumed.update(id(child) for child in ast.walk(node.args[0]))
    if detector_data:
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in {"_DENIED_PATTERNS", "_CAUTION_PATTERNS"}
                for target in node.targets
            ):
                consumed.update(id(child) for child in ast.walk(node.value))
    for node in ast.walk(tree):
        if id(node) in consumed or not isinstance(node, ast.expr):
            continue
        if test_data and id(node) not in standalone_strings and id(node) not in displayed:
            continue
        value = _literal_text(node)
        if value is not None:
            consumed.update(id(child) for child in ast.walk(node))
            span = (node.end_lineno or node.lineno) - node.lineno
            yield from ((node.lineno + min(index, span), line) for index, line in enumerate(value.splitlines()))


def _json_fragments(text: str) -> Iterator[tuple[int, str]]:
    """JSONのキーを保持し、文字列の値をデコードして返す。"""

    def values(value: object, key: str = "") -> Iterator[bool]:
        if isinstance(value, dict):
            for name, item in value.items():
                yield from values(item, name)
        elif isinstance(value, list):
            for item in value:
                yield from values(item, key)
        elif isinstance(value, str):
            yield _path_field(key)

    matches = (match for match in re.finditer(r'"(?:[^"\\]|\\.)*"', text) if not text[match.end() :].lstrip().startswith(":"))
    for match, path_value in zip(matches, values(json.loads(text)), strict=True):
        if not path_value:
            yield text.count("\n", 0, match.start()) + 1, json.loads(match.group())


def _path_field(key: str) -> bool:
    """保存するパスと宣言された欄を、説明の欄と区別する。"""
    return key.lower().split("_")[-1] in {"path", "paths", "filename", "filenames", "directory", "directories", "dir"}


def _toml_fragments(text: str) -> Iterator[tuple[int, str]]:
    """TOMLのキーを保持し、説明のコメントと文字列の値を返す。"""
    tomllib.loads(text)
    value = False
    key = ""
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    for index, token in enumerate(tokens):
        if token.type == tokenize.NEWLINE:
            value = False
            key = ""
        elif token.type == tokenize.OP and token.string == "=":
            value = True
        elif token.type == tokenize.COMMENT:
            yield token.start[0], token.string
        elif token.type in {tokenize.NAME, tokenize.STRING} and tokens[index + 1].string == "=":
            key = token.string.strip("\"'")
        elif token.type == tokenize.STRING and value and not _path_field(key):
            yield token.start[0], tomllib.loads("value=" + token.string)["value"]


def term_violations(path: pathlib.Path, text: str) -> list[tuple[int, str]]:
    """説明文へ戻った失敗対象の語を行番号とともに返す。"""
    return _term_matches(path, text, _TERM_PATTERNS)


def term_cautions(path: pathlib.Path, text: str) -> list[tuple[int, str]]:
    """説明文にある警告対象の語を行番号とともに返す。"""
    return _term_matches(path, text, _CAUTION_TERM_PATTERNS)


def _term_matches(path: pathlib.Path, text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> list[tuple[int, str]]:
    """説明文の断片から指定した語に一致する箇所を行番号とともに返す。"""
    if path.suffix == ".md":
        fragments = _markdown_fragments(text)
    elif path.suffix == ".py":
        fragments = _python_fragments(
            text,
            detector_data=path.resolve() == pathlib.Path(__file__).resolve(),
        )
    elif path.suffix == ".json":
        fragments = _json_fragments(text)
    elif path.suffix == ".toml":
        fragments = _toml_fragments(text)
    else:
        fragments = ((index, line) for index, line in enumerate(text.splitlines(), start=1))
    return sorted(
        {
            (line, name)
            for line, fragment in fragments
            for name, pattern in patterns
            if pattern.search(_without_structural_labels(fragment))
        }
    )


def _term_target(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    """指定された説明文の範囲へ属するファイルかを返す。"""
    return not roots or any(path.resolve().is_relative_to(root.resolve()) for root in roots)


def _frontmatter_end(lines: list[str]) -> int:
    """先頭のYAML frontmatterの次の行の添字を返し、frontmatterが無ければ0を返す。"""
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_DELIMITER:
            return index + 1
    return 0


@dataclasses.dataclass(frozen=True)
class _ProseLine:
    """本文として数える1行と、入力ファイルの物理行での位置。

    `spans`は`text`の各文字が由来する`source`の範囲を持つ。
    インラインコードを置換語へ置き換えた文字は、コード全体の範囲を指す。
    """

    line: int
    text: str
    source: str
    spans: tuple[tuple[int, int], ...]


def _prose_line(line: int, source: str) -> _ProseLine:
    """インラインコードを固定の置換語へ置き換え、各文字の由来を保持した行を返す。"""
    text: list[str] = []
    spans: list[tuple[int, int]] = []
    position = 0
    for match in _INLINE_CODE_PATTERN.finditer(source):
        for index in range(position, match.start()):
            text.append(source[index])
            spans.append((index, index + 1))
        text.append(_INLINE_CODE_PLACEHOLDER)
        spans.extend([match.span()] * len(_INLINE_CODE_PLACEHOLDER))
        position = match.end()
    for index in range(position, len(source)):
        text.append(source[index])
        spans.append((index, index + 1))
    return _ProseLine(line, "".join(text), source, tuple(spans))


def _prose_lines(text: str) -> list[_ProseLine]:
    """本文として数える行だけを、入力ファイルの物理行の行番号とともに返す。

    フェンス付きコードブロック、見出し行、表の行およびHTMLコメントを除き、
    箇条書きの記号と番号を取ってからインラインコードを固定の置換語へ置き換える。
    HTMLコメントは含んでいた改行だけを残して除き、後続の行番号を保つ。
    """
    without_comments = _HTML_COMMENT_PATTERN.sub(lambda match: "\n" * match.group().count("\n"), text)
    lines = without_comments.splitlines()
    prose: list[_ProseLine] = []
    fence: str | None = None
    for index in range(_frontmatter_end(lines), len(lines)):
        line = lines[index]
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
        prose.append(_prose_line(index + 1, stripped))
    return prose


@dataclasses.dataclass(frozen=True)
class Sentence:
    """本文の1文と、その文が終わる物理行での文末の抜粋。"""

    text: str
    line: int
    excerpt: str


def _excerpt(source: str, start: int, end: int) -> str:
    """行の`start`から`end`までを、長ければ末尾側を残して返す。"""
    fragment = source[start:end].strip()
    if len(fragment) > _MAX_EXCERPT_LENGTH:
        return "…" + fragment[-_MAX_EXCERPT_LENGTH:]
    return fragment


def sentences_with_positions(text: str) -> list[Sentence]:
    """本文を「。」で分割し、各文が終わる物理行と文末の抜粋を付けて返す。

    句点の無い行は次の行と1文に結合されるため、文の終わる行は最後の文字を含む行とする。
    """
    prose = _prose_lines(text)
    joined = "".join(item.text for item in prose)
    # 連結した本文の各文字が由来する行の添字と、その行の中の位置。
    origins = [(row, column) for row, item in enumerate(prose) for column in range(len(item.text))]
    sentences: list[Sentence] = []
    position = 0
    for chunk in re.split(r"(?<=。)", joined):
        start, end = position, position + len(chunk)
        position = end
        while start < end and joined[start].isspace():
            start += 1
        while end > start and joined[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        row, last_column = origins[end - 1]
        first_column = origins[start][1] if origins[start][0] == row else 0
        item = prose[row]
        excerpt = _excerpt(item.source, item.spans[first_column][0], item.spans[last_column][1])
        sentences.append(Sentence(joined[start:end], item.line, excerpt))
    return sentences


def split_sentences(text: str) -> list[str]:
    """本文を「。」で分割した文の一覧を返す。"""
    return [sentence.text for sentence in sentences_with_positions(text)]


@dataclasses.dataclass(frozen=True)
class Occurrence:
    """密度の指標が数えた1要素の物理行と、行の中で要素を区別する抜粋。"""

    line: int
    excerpt: str


@dataclasses.dataclass(frozen=True)
class DensityMetric:
    """数えた要素の位置の一覧から件数と比率を導く密度の指標。

    件数は`occurrences`の長さだけから導くため、位置を持たない指標は作成できない。
    閾値超過の診断も同じ一覧から生成する。
    """

    name: str
    unit: str
    limit: float
    action: str
    element_label: str
    occurrences: tuple[Occurrence, ...]
    sentences: int

    @property
    def count(self) -> int:
        """数えた要素の件数を返す。"""
        return len(self.occurrences)

    @property
    def ratio(self) -> float:
        """文数に対する要素の比率を返す。"""
        return self.count / self.sentences if self.sentences else 0.0

    def exceeded(self) -> bool:
        """文数が判定の下限以上で、比率が上限を超えるかを返す。"""
        return self.sentences >= _MIN_SENTENCES_FOR_RATIO and self.ratio > self.limit

    def required_rewrites(self) -> int:
        """文数を変えずに比率を上限以下へ戻すのに必要な最少の書き換え件数を返す。"""
        remaining = self.count
        while remaining > 0 and remaining / self.sentences > self.limit:
            remaining -= 1
        return self.count - remaining

    def summary(self) -> str:
        """比率、上限、件数と次の操作を1行で返す。"""
        return (
            f"{self.name} {self.ratio:.1%}（上限 {self.limit:.0%}、{self.count}{self.unit}/{self.sentences}文）。"
            f"位置を列挙した{self.element_label}を{self.action}。"
            f"文数を変えずに上限以下へ戻すには、少なくとも{self.required_rewrites()}件を書き換える。"
        )

    def element_messages(self) -> list[tuple[int, str]]:
        """数えた要素ごとの行番号と診断の本文を返す。"""
        return [
            (
                occurrence.line,
                f"{self.name}の超過で数えた{self.element_label}: 「{occurrence.excerpt}」。{self.action}候補。",
            )
            for occurrence in self.occurrences
        ]


def _subject_word_occurrences(text: str) -> tuple[Occurrence, ...]:
    """入力全体の「当該」の出現を、物理行と行の中の前後の抜粋とともに返す。"""
    occurrences: list[Occurrence] = []
    for match in re.finditer(re.escape(_SUBJECT_WORD), text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line_end = len(text) if line_end == -1 else line_end
        start = max(line_start, match.start() - _SUBJECT_WORD_CONTEXT)
        end = min(line_end, match.end() + _SUBJECT_WORD_CONTEXT)
        occurrences.append(Occurrence(text.count("\n", 0, match.start()) + 1, text[start:end].strip()))
    return tuple(occurrences)


class Metrics:
    """1ファイルの文体指標。"""

    def __init__(self, path: pathlib.Path, text: str) -> None:
        sentences = sentences_with_positions(text)
        self.path = path
        self.sentences = len(sentences)
        self.negative_ending = DensityMetric(
            name="否定形終端率",
            unit="",
            limit=_MAX_NEGATIVE_ENDING_RATIO,
            action="肯定形へ書き換える",
            element_label="否定形で終わる文",
            occurrences=tuple(
                Occurrence(sentence.line, sentence.excerpt)
                for sentence in sentences
                if _NEGATIVE_ENDING_PATTERN.search(sentence.text)
            ),
            sentences=self.sentences,
        )
        self.subject_word = DensityMetric(
            name="「当該」の出現率",
            unit="回",
            limit=_MAX_SUBJECT_WORD_RATIO,
            action="指す対象を直接書く平易な語へ書き換える",
            element_label="「当該」",
            occurrences=_subject_word_occurrences(text),
            sentences=self.sentences,
        )

    @property
    def density_metrics(self) -> tuple[DensityMetric, ...]:
        """閾値で判定する全ての密度の指標を返す。"""
        return (self.negative_ending, self.subject_word)

    @property
    def negative_endings(self) -> int:
        """否定形で終わる文の数を返す。"""
        return self.negative_ending.count

    @property
    def subject_words(self) -> int:
        """「当該」の出現数を返す。"""
        return self.subject_word.count

    @property
    def negative_ending_ratio(self) -> float:
        """否定形で終わる文の割合を返す。"""
        return self.negative_ending.ratio

    @property
    def subject_word_ratio(self) -> float:
        """文数に対する「当該」の出現率を返す。"""
        return self.subject_word.ratio

    def exceeded_metrics(self) -> list[DensityMetric]:
        """閾値を超えた密度の指標を返す。"""
        return [metric for metric in self.density_metrics if metric.exceeded()]

    def violations(self) -> list[str]:
        """閾値を満たさない指標の要約を返す。"""
        return [metric.summary() for metric in self.exceeded_metrics()]


def _is_excluded(path: pathlib.Path) -> bool:
    """密度の判定から除く固定のファイルかを返す。"""
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


class Severity(enum.StrEnum):
    """pyfltrが`error-pattern`の`severity`グループとして解釈する診断の重大度。"""

    ERROR = "error"
    WARNING = "warning"


class _Diagnostics:
    """位置付き診断を標準エラーへ書き、書いた診断の重大度から終了コードを導く。"""

    def __init__(self) -> None:
        self._has_error = False

    def emit(self, path: pathlib.Path, line: int, severity: Severity, message: str) -> None:
        """pyfltrが解析する`<パス>:<行番号>: <重大度>: <本文>`の診断を1行書く。"""
        if severity is Severity.ERROR:
            self._has_error = True
        print(f"{path}:{max(1, line)}: {severity}: {message}", file=sys.stderr)

    @property
    def returncode(self) -> int:
        """`error`の診断を1件でも書いていれば1、無ければ0を返す。"""
        return 1 if self._has_error else 0


def main(argv: list[str] | None = None) -> int:
    """説明文の語と文体指標を確認し、失敗対象の語か閾値超過があれば1を返す。"""
    parser = argparse.ArgumentParser(description="説明文の語と文体の密度を確認する。")
    parser.add_argument("paths", metavar="PATH", nargs="+", type=pathlib.Path, help="確認する文書またはコードのファイル。")
    parser.add_argument("--report", action="store_true", help="判定を行わず、全ファイルの指標を表で出力する。")
    parser.add_argument(
        "--term-root",
        action="append",
        type=pathlib.Path,
        default=None,
        help="語の判定を適用するディレクトリまたはファイル。省略時は全入力へ適用する。",
    )
    args = parser.parse_args(argv)

    diagnostics = _Diagnostics()
    contents: list[tuple[pathlib.Path, str]] = []
    for path in args.paths:
        try:
            contents.append((path, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError) as error:
            diagnostics.emit(path, 1, Severity.ERROR, f"入力を読み取れない: {error}。指定したファイルと文字コードを確認する。")
            return 2
    metrics = [Metrics(path, text) for path, text in contents if path.suffix == ".md" and not _is_excluded(path)]
    if args.report:
        _print_report(metrics)
        return 0

    for path, text in contents:
        try:
            if _term_target(path, args.term_root or []):
                for line, term in term_violations(path, text):
                    diagnostics.emit(
                        path, line, Severity.ERROR, f"「{term}」を説明に使用している。対象と動作が分かる文へ書き直す。"
                    )
                for line, term in term_cautions(path, text):
                    diagnostics.emit(
                        path,
                        line,
                        Severity.WARNING,
                        f"警告: 「{term}」を説明に使用している。{_CAUTION_MESSAGES[_CAUTION_PATTERNS[term][1]]}"
                        "（終了コードには影響しない）。",
                    )
        except (SyntaxError, tokenize.TokenError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
            diagnostics.emit(
                path,
                getattr(error, "lineno", 1) or 1,
                Severity.ERROR,
                f"入力の構文を読み取れない: {error}。入力の構文を修正して再実行する。",
            )
            return 2
    for metric in metrics:
        for density in metric.exceeded_metrics():
            diagnostics.emit(metric.path, 1, Severity.ERROR, density.summary())
            for line, message in density.element_messages():
                diagnostics.emit(metric.path, line, Severity.ERROR, message)
    return diagnostics.returncode


if __name__ == "__main__":
    sys.exit(main())
