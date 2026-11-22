from __future__ import annotations

import re

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.shell_tokens import split_words
from agent_toolkit._plan.structure import constants, markdown

_CODE = re.compile(r"`([^`]+)`")
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)


_CD_REWRITE = (
    "cdで作業ディレクトリを変える行は、作業ディレクトリを引数で渡すargv"
    "（`uv run --directory <dir> <command>`、`git -C <dir> <command>`、`make -C <dir> <target>`など）へ書き換える。"
    "引数で渡せないコマンドはscriptへ移し、そのscriptを実行するargvを書く"
)


def commands_from_cell(cell: str) -> list[tuple[str, list[str]]]:
    failure = "検証コマンドは1 code spanに1つの完全なコマンドを書き、複数は<br>で分ける。shell構文はscriptへ移す"
    spans = list(_CODE.finditer(cell))
    if not spans or _BREAK.sub("", _CODE.sub("", cell)).strip():
        raise ActionableError("検証行が実行可能なコマンドだけで構成されていません", next_action=failure)
    commands = []
    for span in spans:
        command = span.group(1).replace(r"\|", "|")
        try:
            words = split_words(command)
        except ValueError as error:
            raise ActionableError(f"検証コマンドの引用が不正です: {command}: {error}", next_action=failure) from error

        if (
            "\n" in command
            or any(word.expansion or re.search(r"[;&|<>`]", word.unquoted) for word in words)
            or (words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0].unquoted))
        ):
            raise ActionableError(
                f"shellの再評価を要する検証コマンドです: {command}",
                next_action=_CD_REWRITE if words and words[0].unquoted == "cd" else failure,
            )
        argv = [str(word) for word in words]
        if not argv:
            raise ActionableError("検証コマンドが空です", next_action=failure)
        commands.append((command, argv))
    return commands


def plan_commands(content: str, *, current_content: str | None = None) -> list[tuple[str, list[str]]]:
    if current_content is not None:
        content = current_content
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
