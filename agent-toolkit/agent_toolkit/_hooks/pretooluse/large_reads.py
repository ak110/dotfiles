"""CodexのBashによる大量の全文取得を分割読取又は軽量委譲へ誘導する。

Codexではシェル出力の上限を超えた取得が返却本文の欠落を招き、欠落した範囲を回復できないため遮断する。
Claude Codeはホストが上限超過を`PARTIAL view`又は退避ファイルとして返し、残りを続けて取得できるため対象外とする。
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass

from agent_toolkit._hooks import bash_command_parser
from agent_toolkit._hooks.notice import block_formatter

_DEFAULT_LINE_THRESHOLD = 350
# 1回の応答へ収まる実効上限を超える前に、バイト数でも分割へ誘導する。
_DEFAULT_BYTE_THRESHOLD = 16 * 1024
_LINE_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_LINES"
_BYTE_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_BYTES"
_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
_block_notice = block_formatter("agent-toolkit/pretooluse")


def _positive_threshold(environment_name: str, default: int) -> int:
    """環境指定が正の整数なら採用し、それ以外は既定値を返す。"""
    try:
        threshold = int(os.environ.get(environment_name, str(default)))
    except ValueError:
        return default
    return threshold if threshold > 0 else default


def _line_threshold() -> int:
    return _positive_threshold(_LINE_THRESHOLD_ENV, _DEFAULT_LINE_THRESHOLD)


def _byte_threshold() -> int:
    return _positive_threshold(_BYTE_THRESHOLD_ENV, _DEFAULT_BYTE_THRESHOLD)


@dataclass(frozen=True)
class _ReadPlan:
    line_count: int
    byte_count: int
    ranges: tuple[tuple[int, int], ...]
    oversized_lines: tuple[tuple[int, int], ...]


def _measure_and_plan(path: pathlib.Path) -> _ReadPlan | None:
    """同じ走査で両閾値を測り、成立する連続行範囲を組み立てる。"""
    if not path.is_file():
        return None
    line_threshold = _line_threshold()
    byte_threshold = _byte_threshold()
    line_count = byte_count = start = size = count = 0
    ranges: list[tuple[int, int]] = []
    oversized: list[tuple[int, int]] = []
    try:
        with path.open("rb") as source:
            for line in source:
                line_count += 1
                length = len(line)
                byte_count += length
                if length > byte_threshold:
                    if count:
                        ranges.append((start, count))
                        count = size = 0
                    oversized.append((line_count, length))
                    continue
                if count and (count == line_threshold or size + length > byte_threshold):
                    ranges.append((start, count))
                    count = size = 0
                if not count:
                    start = line_count
                count += 1
                size += length
    except OSError:
        return None
    if count:
        ranges.append((start, count))
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


def _offset_limit_plan(plan: _ReadPlan) -> str:
    """両閾値に収まるRead範囲と、行単位の取得が成立しない行を示す。"""
    parts = [f"`offset={offset}, limit={limit}`" for offset, limit in plan.ranges]
    if plan.oversized_lines:
        lines = "、".join(f"{line}行目（{size}バイト）" for line, size in plan.oversized_lines)
        parts.append(f"{lines}は単一行がバイト閾値を超えるため、バイト単位又は構造化抽出で読む")
    return "。".join(parts)


def _large_read_notice(path: pathlib.Path, plan: _ReadPlan, cwd: str) -> str:
    line_threshold = _line_threshold()
    return _block_notice(
        f"{plan.line_count}行、{plan.byte_count}バイトのファイルの全文取得を遮断した"
        f"（閾値: {line_threshold}行又は{_byte_threshold()}バイト）: {path}",
        fix=(
            f"取得案: {_offset_limit_plan(plan)}。"
            "agents_serverのstart_exploreへ"
            f"質問とcwd={cwd}を渡して読み取り専用調査を委譲してもよい。"
        ),
    )


def _large_multi_read_notice(path_counts: Sequence[tuple[pathlib.Path, _ReadPlan]]) -> str:
    line_threshold = _line_threshold()
    byte_threshold = _byte_threshold()
    total_lines = sum(plan.line_count for _path, plan in path_counts)
    total_bytes = sum(plan.byte_count for _path, plan in path_counts)
    details = "、".join(
        f"`{path}`: {plan.line_count}行、{plan.byte_count}バイト、{_offset_limit_plan(plan)}" for path, plan in path_counts
    )
    return _block_notice(
        f"複数ファイルの全文取得を遮断した（合計: {total_lines}行、{total_bytes}バイト、"
        f"閾値: {line_threshold}行又は{byte_threshold}バイト）: {details}",
        fix="ファイルごとに個別取得するか、各ファイルを示した連続した行範囲へ分割して取得する。",
    )


def check_large_bash_read(command: str, cwd: str, *, is_codex: bool = False) -> str | None:
    """Codexでパイプ・リダイレクトを持たない単純なBash全文取得だけを遮断する。"""
    if not is_codex:
        return None
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
            measurement = _measure_and_plan(path)
            if measurement is not None:
                path_counts.append((path, measurement))
        if not path_counts:
            continue
        line_threshold = _line_threshold()
        byte_threshold = _byte_threshold()
        if (
            any(plan.line_count > line_threshold or plan.byte_count > byte_threshold for _path, plan in path_counts)
            or sum(plan.line_count for _path, plan in path_counts) > line_threshold
            or sum(plan.byte_count for _path, plan in path_counts) > byte_threshold
        ):
            if len(path_counts) == 1:
                path, plan = path_counts[0]
                return _large_read_notice(path, plan, cwd)
            return _large_multi_read_notice(path_counts)
    return None
