"""大量の全文取得を分割読取又は軽量委譲へ誘導する。

上限超過の取得は返却本文の欠落を招くため遮断する。補正後も反復する警告は
母集団欠落を再発させるため遮断へ昇格する。
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Sequence
from dataclasses import dataclass

from agent_toolkit._hooks import bash_command_parser
from agent_toolkit._hooks.notice import _WARN_TAG, block_formatter, formatter

_DEFAULT_LINE_THRESHOLD = 350
# 1回の応答へ収まる実効上限を超える前に、バイト数でも分割へ誘導する。
_DEFAULT_BYTE_THRESHOLD = 16 * 1024
_LINE_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_LINES"
_BYTE_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_BYTES"
_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
# `Read`が行の列ではない形（画像の視覚提示、PDFのページ単位）で提示する形式。
# 当該形式では改行バイトの個数が取得量に対応せず、`offset`と`limit`も取得量を変えない。
_NON_LINE_ORIENTED_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".pdf", ".png", ".webp"})
_block_notice = block_formatter("agent-toolkit/pretooluse")
_llm_notice = formatter("agent-toolkit/pretooluse")


@dataclass(frozen=True)
class LargeReadResult:
    """Read入力の補正結果、又は補正では上限内へ収まらない場合の遮断理由。"""

    updated_input: dict | None
    notice: str


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


def _is_non_line_oriented(path: pathlib.Path) -> bool:
    """`Read`が行の列として提示しない形式であるかを返す。"""
    return path.suffix.lower() in _NON_LINE_ORIENTED_SUFFIXES


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


def check_large_read(tool_input: dict, cwd: str) -> LargeReadResult | None:
    """範囲指定のないReadが大容量ファイルを対象とする場合に、補正又は遮断の結果を返す。

    代替の入力は判定の時点で一意に算出できるため、遮断して同じ操作の再発行を求めず、
    先頭の閾値行がバイト閾値以下なら補正して通す。補正範囲もバイト閾値を超える場合は、
    行単位のReadでは上限内へ収まらないため遮断し、Bashでのバイト単位分割又は探索委譲を案内する。
    行数を取得量の指標とする判定は、`Read`が対象を行の列として提示する場合にだけ成立する。
    行の列として提示しない形式では、補正しても取得量が変わらないため判定の対象から外す。
    Bashの全文取得は当該形式も行の列として直列化するため`check_large_bash_read`が扱う。
    """
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    path = _resolve_path(file_path, cwd)
    if _is_non_line_oriented(path):
        return None
    plan = _measure_and_plan(path)
    if plan is None:
        return None
    if plan.line_count <= _line_threshold() and plan.byte_count <= _byte_threshold():
        return None
    if not plan.ranges or plan.ranges[0][0] != 1:
        notice = _block_notice(
            f"先頭1行が{plan.oversized_lines[0][1]}バイトとなり、バイト閾値"
            f"{_byte_threshold()}を超えるためReadを遮断した: {path}",
            fix=(
                f"Bashで当該行をバイト単位又は構造化して取得する。残りの範囲: {_offset_limit_plan(plan)}。"
                "agents_serverのstart_exploreへ"
                f"質問とcwd={cwd}を渡して読み取り専用調査を委譲してもよい。"
            ),
        )
        return LargeReadResult(updated_input=None, notice=notice)
    corrected = dict(tool_input)
    corrected["offset"] = 1
    corrected["limit"] = plan.ranges[0][1]
    notice = _llm_notice(
        f"{plan.line_count}行、{plan.byte_count}バイトのファイルの全文取得を先頭{plan.ranges[0][1]}行へ補正した"
        f"（閾値: {_line_threshold()}行又は{_byte_threshold()}バイト）: {path}\n"
        f"範囲は次の組で取得する: {_offset_limit_plan(plan)}。\n"
        f"agents_serverのstart_exploreへ質問とcwd={cwd}を渡して読み取り専用調査を委譲してもよい。",
        tag=_WARN_TAG,
        removable_cause=True,
        escalate_on_repeat=True,
    )
    return LargeReadResult(updated_input=corrected, notice=notice)


def check_large_bash_read(command: str, cwd: str) -> str | None:
    """パイプ・リダイレクトを持たない単純なBash全文取得だけを遮断する。"""
    for pipeline in bash_command_parser.extract_execution_pipelines(command):
        if len(pipeline) != 1 or not pipeline[0].resolved:
            continue
        operands = _full_read_operands(pipeline[0].tokens)
        if not operands:
            continue
        path_counts = tuple(
            (path, measurement)
            for operand in operands
            if (measurement := _measure_and_plan(path := _resolve_path(operand, cwd))) is not None
        )
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
