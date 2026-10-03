#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py>=4"]
# ///
r"""文体の密度と、説明に使用しない語の再使用を確認する。

読み手は文脈にある文体を再生産するため、否定形の宣言や法令調の語が多い文書は、
それを読んだ主体の成果物へ同じ文体と範囲外の追加要素を持ち込む。
本スクリプトは当該密度をファイル単位で数え、閾値を超えたファイルを報告する。

指標は次の3つとする。

- 文数
- 否定形で終わる文の数
- 「当該」の出現数

閾値は、Codexを使う前のClaude由来commitが追加した文の水準に置く。
否定形終端率は8%、「当該」の出現率（出現数÷文数）は3%とし、文数が20以上のファイルへ適用する。

説明文の語は文数によらず判定し、未対応なら要求を満たしていないため終了コード1を返す。
密度の判定はMarkdownだけに適用する。引用・悪い例・検出データと構造名は文章と区別する。
"""

from __future__ import annotations

import argparse
import ast
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

EXCLUDED_PATHS = (
    "agent-toolkit/skills/writing-standards/references/tone-examples.md",
    "agent-toolkit/skills/writing-standards/references/tone-examples-llm-tone.md",
    "agent-toolkit/skills/writing-standards/references/textlint-violations.md",
)
"""意図的な違反例を収録するため密度の判定から除くファイル。"""

# 語そのものを扱う判定用データ。説明文の同義語をここでは定めない。
_DENIED_PATTERNS = {
    "正本": r"(?<!是)正本|正本(?!文)",
    "既定": r"既定",
    "照合": r"照合",
    "経路": r"経路",
    "入口": r"入口",
    "検査": r"検査",
    "実測": r"実測",
    "黙って": r"黙って",
    "漏れ": r"漏[れら]",
    "帰結": r"帰結",
    "部品": r"部品",
    "見落とす": r"見落と",
    "突き合わせる": r"突き合わ",
    "断定": r"断定",
    "事故": r"事故",
    "混ざる": r"混ざ",
    "素通り": r"素通り",
    "切り分ける": r"切[り]分",
    "焼く": r"焼(?:く|か|き|け|い)",
    "崩す": r"崩(?:す|さ|し|せ|そ)",
    "疑う": r"疑(?:う|わ|い|え|っ)",
    "線引き": r"線引き",
    "破綻": r"破綻",
    "ゲート": r"ゲート",
}
_TERM_PATTERNS = tuple((name, re.compile(pattern)) for name, pattern in _DENIED_PATTERNS.items())

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
    "正本ファイル名",
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
                isinstance(target, ast.Name) and target.id == "_DENIED_PATTERNS" for target in node.targets
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
    """説明文へ戻った語を行番号とともに返す。"""
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
            for name, pattern in _TERM_PATTERNS
            if pattern.search(_without_structural_labels(fragment))
        }
    )


def _term_target(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    """指定された説明文の範囲へ属するファイルかを返す。"""
    return not roots or any(path.resolve().is_relative_to(root.resolve()) for root in roots)


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

    フェンス付きコードブロック、見出し行、表の行およびHTMLコメントを除き、
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


def main(argv: list[str] | None = None) -> int:
    """説明文の語と文体指標を確認し、未対応があれば1を返す。"""
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

    try:
        contents = [(path, path.read_text(encoding="utf-8")) for path in args.paths]
        metrics = [Metrics(path, text) for path, text in contents if path.suffix == ".md" and not _is_excluded(path)]
    except (OSError, UnicodeError) as error:
        print(f"入力を読み取れない: {error}。指定したファイルと文字コードを確認する。", file=sys.stderr)
        return 2
    if args.report:
        _print_report(metrics)
        return 0

    failed = False
    try:
        for path, text in contents:
            if _term_target(path, args.term_root or []):
                for line, term in term_violations(path, text):
                    failed = True
                    print(f"{path}:{line}: 「{term}」を説明に使用している。対象と動作が分かる文へ書き直す。", file=sys.stderr)
    except (SyntaxError, tokenize.TokenError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        print(f"入力の構文を読み取れない: {error}。入力の構文を修正して再実行する。", file=sys.stderr)
        return 2
    for metric in metrics:
        problems = metric.violations()
        if problems:
            failed = True
            print(f"{metric.path}: {'、'.join(problems)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
