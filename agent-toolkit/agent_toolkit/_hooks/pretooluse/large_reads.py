"""CodexのBashによる大量の全文取得を共通の本文読取へ誘導する。

Codexではシェル出力の上限を超えた取得が返却本文の欠落を招き、欠落した範囲を回復できないため遮断する。
Claude Codeはホストが上限超過を`PARTIAL view`または退避ファイルとして返し、残りを続けて取得できるため対象外とする。

遮断後に対処する型のhookとする。規範は全文取得の前に容量を測る工程を求めず、
遮断した場合だけ共通の本文読取コマンドで読ませる。閾値を出力上限から導いておけば、
遮断は実際に上限を超え得るファイルだけで発火し、事前計測の呼び出しを操作のたびに払うより費用が小さい。
"""

from __future__ import annotations

import os
import pathlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from agent_toolkit._atk import read_file
from agent_toolkit._common import shell_cwd, shell_segments
from agent_toolkit._hooks.notice import block_formatter

# Codexの配布設定の出力上限`tool_output_token_limit = 20000`（トークン）と、
# agent-toolkitのMarkdownで測った1トークンあたりバイト数の最小値3.10の積は約62,000バイトである。
# 実行セルが本文へ付加する分の余裕を取り、48KiBを閾値とする。
# 測定の記録と再検証手段は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_hooks/pretooluse/large_reads.py：全文取得の閾値：2026年9月29日」にある。
_DEFAULT_BYTE_THRESHOLD = 48 * 1024
_BYTE_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_BYTES"
_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
_block_notice = block_formatter("pretooluse")


def _byte_threshold() -> int:
    """環境指定が正の整数なら採用し、それ以外は指定を省略した場合の値を返す。"""
    try:
        threshold = int(os.environ.get(_BYTE_THRESHOLD_ENV, str(_DEFAULT_BYTE_THRESHOLD)))
    except ValueError:
        return _DEFAULT_BYTE_THRESHOLD
    return threshold if threshold > 0 else _DEFAULT_BYTE_THRESHOLD


@dataclass(frozen=True)
class _ReadPlan:
    line_count: int
    byte_count: int


def _measure_and_plan(path: pathlib.Path) -> _ReadPlan | None:
    """遮断の判定と対象の説明に必要な容量だけを数える。"""
    if not path.is_file():
        return None
    line_count = byte_count = 0
    try:
        with path.open("rb") as source:
            for line in source:
                line_count += 1
                byte_count += len(line)
    except OSError:
        return None
    return _ReadPlan(line_count, byte_count)


def _resolve_path(value: str, cwd: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else pathlib.Path(cwd) / path


def _read_operands(tokens: Sequence[str], *, include_partial: bool) -> tuple[str, ...]:
    """全文取得と、配送だけが対象とする範囲取得のファイル引数を返す。"""
    if not tokens:
        return ()
    name = pathlib.PurePath(tokens[0]).name
    operands = tokens[1:]
    if include_partial and operands[:1] == ("--",):
        operands = operands[1:]
    if (
        len(tokens) >= 2
        and name in _FULL_READ_COMMANDS
        and all(
            not operand.startswith("-") and not any(marker in operand for marker in ("<", ">", "$(", "`"))
            for operand in operands
        )
    ):
        return tuple(operands)
    if (
        len(tokens) == 4
        and name == "sed"
        and tokens[1] == "-n"
        and (tokens[2] in {"p", "1,$p"} or include_partial and re.fullmatch(r"\d+(?:,[\d$]+)?p|p", tokens[2]))
    ):
        return (tokens[3],)
    if len(tokens) == 3 and name == "awk":
        program = "".join(tokens[1].split())
        if program in {"{print}", "{print$0}"}:
            return (tokens[2],)
    if include_partial and name == "atk" and tokens[1:2] == ("read-file",) and "--" in tokens:
        separator = tokens.index("--")
        options = tokens[2:separator]
        if not options or (len(options) == 2 and options[0] == "--start" and options[1].isdigit()):
            return tuple(tokens[separator + 1 :]) if len(tokens) == separator + 2 else ()
    return ()


def bash_read_paths(command: str, cwd: str, *, include_partial: bool = False) -> Iterator[tuple[pathlib.Path, ...]]:
    """読取の解析とcwd・パスの解決を共有し、コマンドごとにファイル群を返す。"""
    current = shell_cwd.CwdResolution(cwd, bool(cwd))
    for pipeline in shell_segments.extract_execution_pipelines(command):
        if len(pipeline) != 1 or not pipeline[0].resolved:
            continue
        tokens = tuple(pipeline[0].tokens)
        change = shell_cwd.resolve_cwd_change(list(tokens), current)
        if change is not None:
            current = change
            continue
        paths = []
        for operand in _read_operands(tokens, include_partial=include_partial):
            if include_partial and any(marker in operand for marker in ("$", "`", "<", ">", "*", "?")):
                continue
            if include_partial and not pathlib.Path(operand).expanduser().is_absolute() and not current.resolved:
                continue
            base = current.path if current.resolved and current.path else cwd
            paths.append(_resolve_path(operand, base))
        if paths:
            yield tuple(paths)


def _large_read_notice(path: pathlib.Path, plan: _ReadPlan, byte_threshold: int) -> str:
    return _block_notice(
        f"{plan.line_count}行、{plan.byte_count}バイトのファイルの全文取得を遮断した（閾値: {byte_threshold}バイト）: {path}",
        fix=(
            f"`{read_file.first_read_command(path)}`で取得し、`text`を連結する。"
            "`next`を`--start`へ渡し、`eof`が`true`になるまで続ける。"
        ),
    )


def _large_multi_read_notice(path_counts: Sequence[tuple[pathlib.Path, _ReadPlan]], byte_threshold: int) -> str:
    total_bytes = sum(plan.byte_count for _path, plan in path_counts)
    details = "、".join(
        f"`{path}`: {plan.line_count}行、{plan.byte_count}バイト、`{read_file.first_read_command(path)}`"
        for path, plan in path_counts
    )
    return _block_notice(
        f"複数ファイルの全文取得を遮断した（合計: {total_bytes}バイト、閾値: {byte_threshold}バイト）: {details}",
        fix="ファイルごとに示したコマンドで取得し、`text`を連結する。`next`を`--start`へ渡し、`eof`が`true`になるまで続ける。",
    )


def check_large_bash_read(command: str, cwd: str, *, is_codex: bool = False) -> str | None:
    """Codexでパイプ・リダイレクトを持たない単純なBash全文取得のうち、閾値を超えるものだけを遮断する。"""
    if not is_codex:
        return None
    byte_threshold = _byte_threshold()
    for paths in bash_read_paths(command, cwd):
        path_counts: list[tuple[pathlib.Path, _ReadPlan]] = []
        for path in paths:
            measurement = _measure_and_plan(path)
            if measurement is not None:
                path_counts.append((path, measurement))
        if not path_counts:
            continue
        if sum(plan.byte_count for _path, plan in path_counts) > byte_threshold:
            if len(path_counts) == 1:
                path, plan = path_counts[0]
                return _large_read_notice(path, plan, byte_threshold)
            return _large_multi_read_notice(path_counts, byte_threshold)
    return None
