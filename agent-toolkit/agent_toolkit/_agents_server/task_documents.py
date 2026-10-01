"""agent-toolkitのタスク文書（`share/<役割名>.subagent.md`）が宣言する入力と起動種別を読む。

タスク文書は`## 入力`直後の`text`コードブロックへ、1行目の`必須入力名:`に続けて任意の`任意入力名:`と
`起動種別:`を置く。`agents_server`の`start`と、`Agent`ツール・自由本文の起動を確認するPreToolUseフックが
同じ宣言を読むため、解析を本モジュールへ集約する。解析が2箇所へ分かれると、一方だけが新しい行を受理し、
同じ起動文がサーバーでは拒否されフックでは通る（またはその逆の）不一致が生じる。
"""

import dataclasses
import json
import pathlib
import re
import typing

from agent_toolkit._common.markdown_headings import top_level_atx_headings

REQUIRED_INPUT_PREFIX = "必須入力名: "
OPTIONAL_INPUT_PREFIX = "任意入力名: "
LAUNCH_KIND_PREFIX = "起動種別: "
INPUT_NAME_PATTERN = re.compile(r"^[^`\s:，、](?:[^`\s，、]*[^`\s:，、])?$")
TASK_DOCUMENT_SUFFIX = ".subagent.md"

LaunchKind = typing.Literal["delegate", "explore", "shell", "write"]
LAUNCH_KINDS: tuple[LaunchKind, ...] = typing.get_args(LaunchKind)

# 起動文の中からタスク文書の絶対パスを拾う。空白、バッククォートおよび引用符を区切りとし、
# Windowsの区切り文字を含むパスも拾う。実在とagent-toolkitのshare配下であることは拾った後に確かめる。
_TASK_DOCUMENT_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:)?[/\\][^\s`'\"<>|（）「」]*?\.subagent\.md")


@dataclasses.dataclass(frozen=True)
class TaskDocumentDeclaration:
    """タスク文書が`## 入力`で宣言した入力名と起動種別。"""

    required: tuple[str, ...]
    optional: tuple[str, ...]
    launch_kind: LaunchKind

    @property
    def accepted(self) -> frozenset[str]:
        """起動文で受理する全入力名（必須・任意入力名）。

        待機と再開の方針は採用したbackendの能力に従ってサーバーが固定指示で伝えるため、呼び出し元が渡す共通入力は持たない。
        """
        return frozenset(self.required) | frozenset(self.optional)


def is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginのshare直下にあるタスク文書だけを受理する。"""
    if path.parent.name != "share":
        return False
    try:
        manifest = json.loads((path.parent.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "agent-toolkit"


def read_declaration(task_document: pathlib.Path, document_text: str | None = None) -> TaskDocumentDeclaration | str:
    """タスク文書の宣言を返す。宣言を読めない場合は理由を示す警告文を返す。

    警告文を返す場合、呼び出し元は必須入力と宣言外入力を確認できないことを示して起動を続ける。
    宣言行の書式違反（未知の起動種別を含む）も警告文として返す。
    """
    if not is_agent_toolkit_task_document(task_document):
        return f"必須入力を確認できません: タスク文書がshare配下ではありません: {task_document}"
    return read_declaration_unchecked(task_document, document_text)


def read_declaration_unchecked(
    task_document: pathlib.Path,
    document_text: str | None = None,
) -> TaskDocumentDeclaration | str:
    """所在の確認を済ませたタスク文書の宣言を返す。戻り値の意味は`read_declaration`と同じ。"""
    if document_text is None:
        try:
            document_text = task_document.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return f"必須入力を確認できません: タスク文書をUTF-8で読めません: {task_document}: {error}"
    block = _input_code_block(document_text)
    if isinstance(block, str):
        return f"必須入力を確認できません: {block}: {task_document}"
    if not block or not block[0].startswith(REQUIRED_INPUT_PREFIX):
        return f"必須入力を確認できません: 必須入力名を取得できません: {task_document}"
    required = _parse_names(block[0].removeprefix(REQUIRED_INPUT_PREFIX))
    if required is None:
        return f"必須入力を確認できません: 必須入力名の書式が不正です: {task_document}"
    optional: tuple[str, ...] = ()
    launch_kind: LaunchKind = "delegate"
    for line in block[1:]:
        if line.startswith(OPTIONAL_INPUT_PREFIX):
            parsed = _parse_names(line.removeprefix(OPTIONAL_INPUT_PREFIX))
            if parsed is None:
                return f"必須入力を確認できません: 任意入力名の書式が不正です: {task_document}"
            optional = parsed
        elif line.startswith(LAUNCH_KIND_PREFIX):
            value = line.removeprefix(LAUNCH_KIND_PREFIX).strip()
            if value not in LAUNCH_KINDS:
                return f"必須入力を確認できません: 起動種別が不正です: {value}: {task_document}"
            launch_kind = value
    return TaskDocumentDeclaration(required=required, optional=optional, launch_kind=launch_kind)


def find_task_documents(text: str) -> list[pathlib.Path]:
    """本文が絶対パスで指すagent-toolkitのタスク文書を出現順に重複なく返す。"""
    found: list[pathlib.Path] = []
    for match in _TASK_DOCUMENT_PATH_PATTERN.finditer(text):
        path = pathlib.Path(match.group(0))
        if path in found or not path.is_file() or not is_agent_toolkit_task_document(path):
            continue
        found.append(path)
    return found


def _input_code_block(document_text: str) -> list[str] | str:
    """`## 入力`直後の`text`コードブロックの行を返す。見つからない場合は理由を返す。"""
    document_lines = document_text.splitlines()
    headings = top_level_atx_headings("\n".join(document_lines), 2)
    positions = [index for index, (_, title) in enumerate(headings) if title == "入力"]
    if not positions:
        return "タスク文書に## 入力がありません"
    position = positions[0]
    token = headings[position][0]
    assert token.map is not None
    following = headings[position + 1][0] if position + 1 < len(headings) else None
    end = following.map[0] if following is not None and following.map is not None else len(document_lines)
    section = document_lines[token.map[1] : end]
    try:
        fence = section.index("```text")
        closing = section.index("```", fence + 1)
    except ValueError:
        return "## 入力にtextコードブロックがありません"
    return section[fence + 1 : closing]


def _parse_names(value: str) -> tuple[str, ...] | None:
    """ASCIIカンマ区切りの入力名を返す。書式に合わない名前を含む場合はNoneを返す。"""
    names = tuple(value.split(","))
    if not names or any(not INPUT_NAME_PATTERN.fullmatch(name) for name in names):
        return None
    return names
