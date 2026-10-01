"""CodexのBashによる大量の全文取得を分割読取または軽量委譲へ誘導する。

Codexではシェル出力の上限を超えた取得が返却本文の欠落を招き、欠落した範囲を回復できないため遮断する。
Claude Codeはホストが上限超過を`PARTIAL view`または退避ファイルとして返し、残りを続けて取得できるため対象外とする。

遮断後に対処する型のhookとする。規範は全文取得の前に容量を測る工程を求めず、
遮断した場合だけ通知が示す連続した行範囲で読ませる。閾値を出力上限から導いておけば、
遮断は実際に上限を超え得るファイルだけで発火し、事前計測の呼び出しを操作のたびに払うより費用が小さい。
"""

from __future__ import annotations

import os
import pathlib
import shlex
from collections.abc import Sequence
from dataclasses import dataclass

from agent_toolkit._hooks import bash_command_parser
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
    ranges: tuple[tuple[int, int], ...]
    oversized_lines: tuple[tuple[int, int], ...]


def _measure_and_plan(path: pathlib.Path, byte_threshold: int) -> _ReadPlan | None:
    """容量を測り、各範囲が閾値以下になる連続行範囲を組み立てる。

    範囲は`(開始行, 終了行)`とし、重複も欠落もなく全行を覆う。単一行で閾値を超える行は範囲から外して別に返す。
    """
    if not path.is_file():
        return None
    line_count = byte_count = start = size = 0
    ranges: list[tuple[int, int]] = []
    oversized: list[tuple[int, int]] = []
    try:
        with path.open("rb") as source:
            for line in source:
                line_count += 1
                length = len(line)
                byte_count += length
                if length > byte_threshold:
                    if start:
                        ranges.append((start, line_count - 1))
                        start = size = 0
                    oversized.append((line_count, length))
                    continue
                if start and size + length > byte_threshold:
                    ranges.append((start, line_count - 1))
                    start = size = 0
                if not start:
                    start = line_count
                size += length
    except OSError:
        return None
    if start:
        ranges.append((start, line_count))
    return _ReadPlan(line_count, byte_count, tuple(ranges), tuple(oversized))


def _resolve_path(value: str, cwd: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else pathlib.Path(cwd) / path


def _full_read_operands(tokens: Sequence[str]) -> tuple[str, ...]:
    """静的に全文取得と確定できる単純コマンドからファイル群を返す。"""
    if (
        len(tokens) >= 2
        and pathlib.PurePath(tokens[0]).name in _FULL_READ_COMMANDS
        and all(
            not operand.startswith("-") and not any(marker in operand for marker in ("<", ">", "$(", "`"))
            for operand in tokens[1:]
        )
    ):
        return tuple(tokens[1:])
    if len(tokens) == 4 and pathlib.PurePath(tokens[0]).name == "sed" and tuple(tokens[1:3]) in {("-n", "p"), ("-n", "1,$p")}:
        return (tokens[3],)
    if len(tokens) == 3 and pathlib.PurePath(tokens[0]).name == "awk":
        program = "".join(tokens[1].split())
        if program in {"{print}", "{print$0}"}:
            return (tokens[2],)
    return ()


def _range_plan(path: pathlib.Path, plan: _ReadPlan) -> str:
    """閾値以下の連続行範囲を取得するコマンドと、行単位の取得が成立しない行を示す。

    範囲の取得コマンドは`_full_read_operands`の全文取得の判定に一致しないため、通知どおりの取得で同じ通知は反復しない。
    """
    quoted = shlex.quote(str(path))
    parts = [f"`sed -n '{first},{last}p' {quoted}`" for first, last in plan.ranges]
    if plan.oversized_lines:
        lines = "、".join(f"{line}行目（{size}バイト）" for line, size in plan.oversized_lines)
        parts.append(f"{lines}は単一行がバイト閾値を超えるため、バイト単位または構造化抽出で読む")
    return "、".join(parts)


def _large_read_notice(path: pathlib.Path, plan: _ReadPlan, cwd: str, byte_threshold: int) -> str:
    return _block_notice(
        f"{plan.line_count}行、{plan.byte_count}バイトのファイルの全文取得を遮断した（閾値: {byte_threshold}バイト）: {path}",
        fix=(
            f"次の連続した行範囲を全て取得すると全文を読了したものとする: {_range_plan(path, plan)}。"
            "`agents_server`の`start_explore`へ"
            f"質問と`cwd={cwd}`を渡して読み取り専用調査を委譲してもよい。"
        ),
    )


def _large_multi_read_notice(path_counts: Sequence[tuple[pathlib.Path, _ReadPlan]], byte_threshold: int) -> str:
    total_bytes = sum(plan.byte_count for _path, plan in path_counts)
    details = "、".join(
        f"`{path}`: {plan.line_count}行、{plan.byte_count}バイト、{_range_plan(path, plan)}" for path, plan in path_counts
    )
    return _block_notice(
        f"複数ファイルの全文取得を遮断した（合計: {total_bytes}バイト、閾値: {byte_threshold}バイト）: {details}",
        fix="ファイルごとに別々の取得で読むか、各ファイルに示した連続した行範囲を全て取得する。",
    )


def check_large_bash_read(command: str, cwd: str, *, is_codex: bool = False) -> str | None:
    """Codexでパイプ・リダイレクトを持たない単純なBash全文取得のうち、閾値を超えるものだけを遮断する。"""
    if not is_codex:
        return None
    byte_threshold = _byte_threshold()
    current = bash_command_parser.CwdResolution(cwd, bool(cwd))
    for pipeline in bash_command_parser.extract_execution_pipelines(command):
        if len(pipeline) != 1 or not pipeline[0].resolved:
            continue
        tokens = pipeline[0].tokens
        cwd_change = bash_command_parser.resolve_cwd_change(list(tokens), current)
        if cwd_change is not None:
            current = cwd_change
            continue
        operands = _full_read_operands(tokens)
        if not operands:
            continue
        base = current.path if current.resolved and current.path else cwd
        path_counts: list[tuple[pathlib.Path, _ReadPlan]] = []
        for operand in operands:
            path = _resolve_path(operand, base)
            measurement = _measure_and_plan(path, byte_threshold)
            if measurement is not None:
                path_counts.append((path, measurement))
        if not path_counts:
            continue
        if sum(plan.byte_count for _path, plan in path_counts) > byte_threshold:
            if len(path_counts) == 1:
                path, plan = path_counts[0]
                return _large_read_notice(path, plan, cwd, byte_threshold)
            return _large_multi_read_notice(path_counts, byte_threshold)
    return None
