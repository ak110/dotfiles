"""計画の検証表からshell再評価を必要としないコマンドを順序付きで取得する。"""

from __future__ import annotations

import re
import shlex

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan.structure import constants, markdown

_CODE = re.compile(r"`([^`]+)`")
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)


def commands_from_cell(cell: str) -> list[tuple[str, list[str]]]:
    """各code spanを完全なargvへ変換する。説明と曖昧なshell構文は起動前に拒否する。"""
    failure = "検証コマンドは1 code spanに1つの完全なコマンドを書き、複数は<br>で分ける。shell構文はscriptへ移す"
    spans = list(_CODE.finditer(cell))
    if not spans or _BREAK.sub("", _CODE.sub("", cell)).strip():
        raise ActionableError("検証行が実行可能なコマンドだけで構成されていません", next_action=failure)
    commands = []
    for span in spans:
        command = span.group(1).replace(r"\|", "|")
        unquoted = _QUOTED.sub("", command)
        if re.search(r"[;&|<>`\n]|\$", unquoted) or re.match(r"\s*[A-Za-z_][A-Za-z0-9_]*=", command):
            raise ActionableError(f"shellの再評価を要する検証コマンドです: {command}", next_action=failure)
        try:
            argv = shlex.split(command)
        except ValueError as error:
            raise ActionableError(f"検証コマンドの引用が不正です: {command}: {error}", next_action=failure) from error
        if not argv:
            raise ActionableError("検証コマンドが空です", next_action=failure)
        commands.append((command, argv))
    return commands


def plan_commands(content: str) -> list[tuple[str, list[str]]]:
    """現行・読取互換の検証節から変更範囲の検証を1件だけ取り出す。"""
    headings = markdown.extract_headings(content)
    sections = [
        index
        for index, heading in enumerate(headings)
        if heading.level == 2 and heading.text in {constants.PLAN_H2_CURRENT_VERIFICATION, constants.PLAN_H2_VERIFICATION}
    ]
    if len(sections) == 1:
        start, end = markdown.heading_subtree_range(headings, sections[0])
        tables = markdown.extract_tables(markdown.lines_within(list(markdown.iter_markdown_body_lines(content)), start, end))
        names = constants.PLAN_CURRENT_VERIFICATION_TABLE_ROWS + constants.PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS
        cells = [row[1] for table in tables for row in table.rows if len(row) == 2 and row[0] in names]
        if len(cells) == 1:
            return commands_from_cell(cells[0])
    raise ActionableError(
        "計画の変更範囲の検証行を一意に取得できません",
        next_action="計画の## 検証へ変更範囲の検証行を1つ置き、plan-check後に同じ引数で再実行する",
    )
