"""`<役割名>.subagent.md`の実行命令を持つ委譲プロンプトを、宣言済みの入力だけへ限る。

`share/<役割名>.subagent.md`を持つ委譲では、委譲先の手順、権限、検証方法、返却形式は
`<役割名>.subagent.md`と委譲先が読む規範が定める。委譲プロンプトへそれらを書き足すと、委譲の費用が増え、指示の重複や
委譲元の先入観が委譲先へ混入する。本モジュールは次の2つを遮断する。

- `agents_server`の`start`のうち、自由本文を渡すmode（`delegate`・`explore`・`write`）の本文が
  `<役割名>.subagent.md`の実行を命じる起動。
  `<役割名>.subagent.md`を指定する起動は`start`のtaskへ`subagent_md_path`（役割名）と`extra_params`で渡す
- `Agent`ツールの本文が`<役割名>.subagent.md`の実行を命じ、1行目の`<.subagent.mdの絶対パス>の手順を実行せよ。`と
  宣言済みの入力名の行（字下げした続きの行を含む）以外を含む起動

実行の命令は、引用とコードの外で文書の絶対パスの直後に実行を求める述語が続く文として判定する。
定型の`<.subagent.mdの絶対パス>の手順を実行せよ。`に限らず、パスの後の空白、`に従って作業せよ`・`に従い作業せよ`などの言い回し、
`を読み、その手順を実行せよ`のように文書を読む指示へ実行を続ける文、同じ行に続く指示を持つ命令も対象とする。

遮断の根拠: 委譲の起動は委譲プロンプトを委譲先のコンテキストへ取り込ませるため、通した後に結果を復元できない。
実行主体は同じターンで、通知が示す`start`の呼び出しまたは宣言済みの行だけの本文へ組み直して再実行できる。
宣言を読めない`<役割名>.subagent.md`は入力との一致を確かめられないため遮断しない（`agents_server`の`start`も警告だけで起動を続ける）。
宣言の解析は`agents_server`の`start`と`agent_toolkit._agents_server.task_documents`を共有する。
読解・比較の参照と、コードや引用に載せた命令の例は通す。命令を確定できない参照も通し、会話の意味を推定しない。
"""

from __future__ import annotations

import pathlib
import re

import markdown_it
from markdown_it.token import Token

from agent_toolkit._agents_server import task_documents
from agent_toolkit._agents_server import tool_names as _tool_names
from agent_toolkit._hooks.notice import block_formatter

_block_notice = block_formatter("pretooluse")

START_TOOLS: frozenset[str] = frozenset(
    f"{namespace}{operation}" for namespace in _tool_names.MCP_NAMESPACES for operation in _tool_names.START_OPERATIONS
)
"""`agents_server`の起動ツールの完全修飾名。"""

FREE_TEXT_START_MODES: frozenset[str] = frozenset({"delegate", "explore", "write"})
"""`start`のうち、本文を自由に書くmode。"""

AGENT_TOOL_NAMES: frozenset[str] = frozenset({"Agent", "Task"})
"""Claude Codeのサブエージェント起動ツール名（旧名`Task`を含む）。"""

_INPUT_LINE_PATTERN = re.compile(r"^(?P<name>[^\s:：][^:：]*?):(?: (?P<value>.*))?$")
_DOCUMENT_STEPS = r"(?:の(?:手順|工程|指示)|を(?:読み|読んで)\s*[、,]?\s*(?:(?:その|同書の)?(?:手順|工程|指示)|それ))"
"""パスの直後で文書の手順を指す句。文書を読む指示に続けて「その手順」「それ」で手順を指す形を含める。"""
_EXECUTION_AFTER_PATH = re.compile(
    rf"""`?\s*(?:
        (?:{_DOCUMENT_STEPS}\s*)?に従(?:え|うこと)
      | (?:
            {_DOCUMENT_STEPS}\s*(?:を|に(?:従って|従い|沿って|沿い)|どおりに?|で)?
          | に(?:従って|従い|沿って|沿い)
          | を
          | どおりに?
        )?\s*
        (?:
            (?:実行|実施|遂行|作業|処理)(?:せよ|しろ|しなさい|して|すること|し[、,]|する(?:[。.]|$))
          | 進め(?:よ|て|ること|[、,]|る(?:[。.]|$))
        )
    )""",
    re.VERBOSE,
)
"""文書の絶対パスの直後に続き、その文書の手順の実行を求める述語。読解・引用・比較の述語は含めない。"""
_QUOTE_OPENERS = ("「", "『", "“", '"', "'")
"""直前にあると、続く命令を引用した文として扱う開き括弧と引用符。"""
_MASKED_CODE = "\x00"
"""文書の絶対パス以外を内容とするインラインコードの置き換え。コード中の命令例を実行の命令と区別する。"""
_CONTINUATION_PREFIX = "  "
_MARKDOWN = markdown_it.MarkdownIt("commonmark")


def check_task_document_launch(tool_name: str, tool_input: dict) -> str | None:
    """遮断する起動なら遮断通知の本文を返し、通す場合はNoneを返す。"""
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None
    if tool_name in START_TOOLS:
        operation = tool_name.rsplit("__", 1)[-1]
        mode = _tool_names.start_mode(operation, tool_input)
        return _check_free_text_start(mode, prompt) if mode in FREE_TEXT_START_MODES else None
    if tool_name in AGENT_TOOL_NAMES:
        return _check_agent_prompt(prompt)
    return None


def _check_free_text_start(mode: str, prompt: str) -> str | None:
    document = _execution_document(prompt)
    if document is None:
        return None
    role_name = document.name.removesuffix(task_documents.TASK_DOCUMENT_SUFFIX)
    return _block_notice(
        f"blocked: `start`の`{mode}`で渡した`prompt`が`{document}`の手順を実行する命令を持つ。"
        "`<役割名>.subagent.md`を持つ委譲は自由本文の起動の対象外である。",
        fix=(
            f"`agents_server`の`start`へ`mode`を指定せず、役割名の`subagent_md_path={role_name}`と、"
            "`<役割名>.subagent.md`が宣言した入力名だけを持つ`extra_params`を渡して起動する。"
            f"サーバー自身のプラグインルートと別の版の文書を使う場合だけ、`subagent_md_path`へ`{document}`を渡す。"
        ),
    )


def _check_agent_prompt(prompt: str) -> str | None:
    document = _execution_document(prompt)
    if document is None:
        return None
    lines = prompt.rstrip().splitlines()
    expected_first = f"{document}の手順を実行せよ。"
    declaration = task_documents.read_declaration(document)
    if isinstance(declaration, str):
        return None
    accepted = declaration.accepted
    violations: list[str] = []
    if not lines or not _is_canonical_first_line(lines[0], document):
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
        f"blocked: `Agent`の本文が`{document}`を指しながら、1行目の命令と宣言済みの入力以外の行を含む。\n宣言外の行:\n{shown}",
        fix=(
            f"1行目を`{expected_first}`とし、2行目以降は`<入力名>: <値>`の行と半角空白2字で字下げした続きの行だけにする。"
            f"受理する入力名: {', '.join(sorted(accepted))}。手順、権限、返却形式は`<役割名>.subagent.md`が定めるため書かない。"
        ),
    )


def _is_canonical_first_line(line: str, document: pathlib.Path) -> bool:
    """1行目が定型の実行命令だけからなるかを返す。パスの前後の空白とパスを囲むバッククォートの有無は区別しない。"""
    pattern = rf"`?{re.escape(str(document))}`?\s*の手順を実行せよ。?"
    return re.fullmatch(pattern, line.strip()) is not None


def _execution_document(prompt: str) -> pathlib.Path | None:
    """引用とコードの外にある実行命令から、最初に命令の対象となった文書を求める。"""
    documents = task_documents.find_task_documents(prompt)
    if not documents:
        return None
    paths = {str(document): document for document in documents}
    quote_depth = 0
    for token in _MARKDOWN.parse(prompt):
        if token.type == "blockquote_open":
            quote_depth += 1
        elif token.type == "blockquote_close":
            quote_depth -= 1
        elif token.type == "inline" and not quote_depth:
            for line in _plain_text(token, paths).splitlines():
                document = _commanded_document(line, paths)
                if document is not None:
                    return document
    return None


def _plain_text(token: Token, paths: dict[str, pathlib.Path]) -> str:
    """インライン要素の本文を、改行を保ち、文書パス以外のインラインコードを伏せて返す。"""
    parts: list[str] = []
    for child in token.children or []:
        if child.type == "text":
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif child.type == "code_inline":
            parts.append(f"`{child.content}`" if child.content.strip() in paths else _MASKED_CODE)
    return "".join(parts)


def _commanded_document(line: str, paths: dict[str, pathlib.Path]) -> pathlib.Path | None:
    """行の中で、引用符の外にあり直後に実行の述語が続く文書パスを返す。"""
    for text, document in paths.items():
        start = line.find(text)
        while start >= 0:
            before = line[:start].rstrip("`").rstrip()
            quoted = before.endswith(_QUOTE_OPENERS)
            if not quoted and _EXECUTION_AFTER_PATH.match(line, start + len(text)) is not None:
                return document
            start = line.find(text, start + 1)
    return None
