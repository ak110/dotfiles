"""大量の全文取得を分割読取又は軽量委譲へ誘導する。"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Sequence

from agent_toolkit._hooks import bash_command_parser
from agent_toolkit._hooks.notice import block_formatter

_DEFAULT_LINE_THRESHOLD = 350
_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_LINES"
_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
_MANDATORY_DOCUMENT_NAMES = frozenset({"AGENTS.md", "CLAUDE.md", "SKILL.md"})
_MANDATORY_PATH_PARTS = frozenset({"rules", "skills"})
_block_notice = block_formatter("agent-toolkit/pretooluse")


def _line_threshold() -> int:
    """環境指定が正の整数なら採用し、それ以外は既定値を返す。"""
    try:
        threshold = int(os.environ.get(_THRESHOLD_ENV, str(_DEFAULT_LINE_THRESHOLD)))
    except ValueError:
        return _DEFAULT_LINE_THRESHOLD
    return threshold if threshold > 0 else _DEFAULT_LINE_THRESHOLD


def _is_mandatory_document(path: pathlib.Path) -> bool:
    """分割すると契約の読了を損なう必須指示文書であるかを返す。"""
    if path.name in _MANDATORY_DOCUMENT_NAMES:
        return True
    parts = set(path.parts)
    return "agent-toolkit" in parts and bool(parts & _MANDATORY_PATH_PARTS)


def _line_count_if_large(path: pathlib.Path) -> int | None:
    """通常ファイルが閾値を超える場合に実測行数を返す。"""
    threshold = _line_threshold()
    try:
        if not path.is_file() or _is_mandatory_document(path):
            return None
        with path.open("rb") as source:
            line_count = sum(1 for _line in source)
            return line_count if line_count > threshold else None
    except OSError:
        return None


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


def _large_read_notice(path: pathlib.Path, line_count: int, cwd: str) -> str:
    return _block_notice(
        f"{line_count}行のファイルの全文取得を遮断した（閾値: {_line_threshold()}行）: {path}",
        fix=(
            "Readのoffset/limitで必要な範囲へ分割するか、"
            "agents_serverのstart_exploreへ"
            f"質問とcwd={cwd}を渡して読み取り専用調査を委譲する。"
        ),
    )


def check_large_read(tool_input: dict, cwd: str) -> str | None:
    """範囲指定のないReadが大容量ファイルを対象とする場合に遮断理由を返す。"""
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return None
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    path = _resolve_path(file_path, cwd)
    line_count = _line_count_if_large(path)
    return _large_read_notice(path, line_count, cwd) if line_count is not None else None


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
