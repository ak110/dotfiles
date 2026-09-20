"""大量の全文取得を分割読取又は軽量委譲へ誘導する。"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Sequence

from agent_toolkit._hooks import bash_command_parser
from agent_toolkit._hooks.notice import _WARN_TAG, block_formatter, formatter

_DEFAULT_LINE_THRESHOLD = 350
_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_LINES"
_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
# `Read`が行の列ではない形（画像の視覚提示、PDFのページ単位）で提示する形式。
# 当該形式では改行バイトの個数が取得量に対応せず、`offset`と`limit`も取得量を変えない。
_NON_LINE_ORIENTED_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".pdf", ".png", ".webp"})
_block_notice = block_formatter("agent-toolkit/pretooluse")
_llm_notice = formatter("agent-toolkit/pretooluse")


def _line_threshold() -> int:
    """環境指定が正の整数なら採用し、それ以外は既定値を返す。"""
    try:
        threshold = int(os.environ.get(_THRESHOLD_ENV, str(_DEFAULT_LINE_THRESHOLD)))
    except ValueError:
        return _DEFAULT_LINE_THRESHOLD
    return threshold if threshold > 0 else _DEFAULT_LINE_THRESHOLD


def _line_count_if_large(path: pathlib.Path) -> int | None:
    """行指向ファイルが閾値を超える場合に実測行数を返す。"""
    threshold = _line_threshold()
    try:
        if not path.is_file():
            return None
        with path.open("rb") as source:
            line_count = sum(1 for _line in source)
            return line_count if line_count > threshold else None
    except OSError:
        return None


def _is_non_line_oriented(path: pathlib.Path) -> bool:
    """`Read`が行の列として提示しない形式であるかを返す。"""
    return path.suffix.lower() in _NON_LINE_ORIENTED_SUFFIXES


def _resolve_path(value: str, cwd: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else pathlib.Path(cwd) / path


def _full_read_operand(tokens: Sequence[str]) -> str | None:
    """静的に全文取得と確定できる単純コマンドから単一ファイルを返す。"""
    if len(tokens) == 2 and pathlib.PurePath(tokens[0]).name in _FULL_READ_COMMANDS and not tokens[1].startswith("-"):
        return tokens[1]
    if len(tokens) == 4 and pathlib.PurePath(tokens[0]).name == "sed" and tuple(tokens[1:3]) in {("-n", "p"), ("-n", "1,$p")}:
        return tokens[3]
    if len(tokens) == 3 and pathlib.PurePath(tokens[0]).name == "awk":
        program = "".join(tokens[1].split())
        if program in {"{print}", "{print$0}"}:
            return tokens[2]
    return None


def _offset_limit_plan(line_count: int, threshold: int) -> str:
    """実測行数と閾値から、全行を覆う`offset`と`limit`の組を先頭から順に返す。

    判定時点で確定している2つの値だけから代替形を導けるため、通知の受領側が自ら算出せずに済む形で示す。
    """
    return "、".join(
        f"`offset={offset}, limit={min(threshold, line_count - offset + 1)}`" for offset in range(1, line_count + 1, threshold)
    )


def _large_read_notice(path: pathlib.Path, line_count: int, cwd: str) -> str:
    threshold = _line_threshold()
    return _block_notice(
        f"{line_count}行のファイルの全文取得を遮断した（閾値: {threshold}行）: {path}",
        fix=(
            f"Readへ次の組を順に渡して分割する: {_offset_limit_plan(line_count, threshold)}。"
            "agents_serverのstart_exploreへ"
            f"質問とcwd={cwd}を渡して読み取り専用調査を委譲してもよい。"
        ),
    )


def check_large_read(tool_input: dict, cwd: str) -> tuple[dict, str] | None:
    """範囲指定のないReadが大容量ファイルを対象とする場合に、補正後の入力と通知を返す。

    代替の入力は判定の時点で一意に算出できるため、遮断して同じ操作の再発行を求めず、
    先頭の閾値行へ補正して通す。残りの範囲は通知本文が示す。
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
    line_count = _line_count_if_large(path)
    if line_count is None:
        return None
    threshold = _line_threshold()
    corrected = dict(tool_input)
    corrected["offset"] = 1
    corrected["limit"] = threshold
    notice = _llm_notice(
        f"{line_count}行のファイルの全文取得を先頭{threshold}行へ補正した: {path}\n"
        f"残りの範囲は次の組で取得する: {_offset_limit_plan(line_count, threshold)}。\n"
        f"agents_serverのstart_exploreへ質問とcwd={cwd}を渡して読み取り専用調査を委譲してもよい。",
        tag=_WARN_TAG,
        removable_cause=True,
    )
    return corrected, notice


def check_large_bash_read(command: str, cwd: str) -> str | None:
    """パイプ・リダイレクトを持たない単純なBash全文取得だけを遮断する。"""
    for pipeline in bash_command_parser.extract_execution_pipelines(command):
        if len(pipeline) != 1 or not pipeline[0].resolved:
            continue
        operand = _full_read_operand(pipeline[0].tokens)
        if operand is None:
            continue
        path = _resolve_path(operand, cwd)
        line_count = _line_count_if_large(path)
        if line_count is not None:
            return _large_read_notice(path, line_count, cwd)
    return None
