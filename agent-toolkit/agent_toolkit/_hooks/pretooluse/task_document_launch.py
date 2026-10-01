"""タスク文書を指す委譲の起動文を、宣言済みの入力だけへ限る。

専用のタスク文書（`share/<役割名>.subagent.md`）を持つ委譲では、受信者の手順、権限、検証方法、返却形式は
タスク文書と受信者が読む規範が定める。起動文へそれらを書き足すと、委譲の費用が増え、指示の重複や
呼び出し元の先入観が受信者へ混入する。本モジュールは次の2つを遮断する。

- `agents_server`の`start_custom`・`start_explore`・`start_write`の本文がタスク文書を指す起動。
  タスク文書起動は`start`へ`subagent_md_path`と`extra_params`で渡す
- `Agent`ツールの本文がタスク文書を指し、1行目の`<タスク文書の絶対パス>の手順を実行せよ。`と
  宣言済みの入力名の行（字下げした続きの行を含む）以外を含む起動

遮断の根拠: 委譲の起動は起動文を委譲先のコンテキストへ取り込ませるため、通した後に結果を復元できない。
実行主体は同じターンで、通知が示す`start`の呼び出しまたは宣言済みの行だけの本文へ組み直して再実行できる。
宣言を読めないタスク文書は入力との一致を確かめられないため遮断しない（`agents_server`の`start`も警告だけで起動を続ける）。
宣言の解析は`agents_server`の`start`と`agent_toolkit._agents_server.task_documents`を共有する。
"""

from __future__ import annotations

import re

from agent_toolkit._agents_server import task_documents
from agent_toolkit._agents_server import tool_names as _tool_names
from agent_toolkit._hooks.notice import block_formatter

_block_notice = block_formatter("pretooluse")

FREE_TEXT_START_TOOLS: frozenset[str] = frozenset(
    f"{namespace}{operation}"
    for namespace in _tool_names.MCP_NAMESPACES
    for operation in ("start_custom", "start_explore", "start_write")
)
"""本文を自由に書く`agents_server`の起動ツールの完全修飾名。"""

AGENT_TOOL_NAMES: frozenset[str] = frozenset({"Agent", "Task"})
"""Claude Codeのサブエージェント起動ツール名（旧名`Task`を含む）。"""

_INPUT_LINE_PATTERN = re.compile(r"^(?P<name>[^\s:：][^:：]*?):(?: (?P<value>.*))?$")
_CONTINUATION_PREFIX = "  "


def check_task_document_launch(tool_name: str, tool_input: dict) -> str | None:
    """遮断する起動なら遮断通知の本文を返し、通す場合はNoneを返す。"""
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None
    if tool_name in FREE_TEXT_START_TOOLS:
        return _check_free_text_start(tool_name, prompt)
    if tool_name in AGENT_TOOL_NAMES:
        return _check_agent_prompt(prompt)
    return None


def _check_free_text_start(tool_name: str, prompt: str) -> str | None:
    documents = task_documents.find_task_documents(prompt)
    if not documents:
        return None
    display_name = tool_name.rsplit("__", 1)[-1]
    return _block_notice(
        f"blocked: `{display_name}`の本文がタスク文書`{documents[0]}`を指している。"
        "タスク文書を持つ委譲は自由本文の起動の対象外である。",
        fix=(
            f"`agents_server`の`start`へ`subagent_md_path={documents[0]}`と、"
            "タスク文書が宣言した入力名だけを持つ`extra_params`を渡して起動する。"
        ),
    )


def _check_agent_prompt(prompt: str) -> str | None:
    documents = task_documents.find_task_documents(prompt)
    if not documents:
        return None
    lines = prompt.rstrip().splitlines()
    document = documents[0]
    expected_first = f"{document}の手順を実行せよ。"
    declaration = task_documents.read_declaration(document)
    if isinstance(declaration, str):
        return None
    accepted = declaration.accepted
    violations: list[str] = []
    if not lines or lines[0].strip() != expected_first:
        violations.append(lines[0] if lines else "")
    in_value = False
    for line in lines[1:]:
        if in_value and (line.startswith(_CONTINUATION_PREFIX) or not line):
            continue
        match = _INPUT_LINE_PATTERN.match(line)
        if match is not None and match.group("name") in accepted:
            in_value = True
            continue
        in_value = False
        violations.append(line)
    if not violations:
        return None
    shown = "\n".join(f"  {line}" for line in violations[:5])
    return _block_notice(
        f"blocked: `Agent`の本文がタスク文書`{document}`を指しながら、1行目の命令と宣言済みの入力以外の行を含む。\n"
        f"宣言外の行:\n{shown}",
        fix=(
            f"1行目を`{expected_first}`とし、2行目以降は`<入力名>: <値>`の行と半角空白2字で字下げした続きの行だけにする。"
            f"受理する入力名: {', '.join(sorted(accepted))}。手順、権限、返却形式はタスク文書が定めるため書かない。"
        ),
    )
