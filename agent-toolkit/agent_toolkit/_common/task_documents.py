"""agent-toolkitの`share/<役割名>.subagent.md`が宣言する起動設定を読む。

現行形式は先頭のYAML frontmatterで、mode・モデル段位・用途・入力・継続・書込を宣言する。
旧版の`## 入力`直後の`text`コードブロックにある宣言行は、保持された役割文書とsessionの読取互換として扱う。
`agents_server`の`start`、`Agent`ツール・自由本文の起動を確認するPreToolUseフック、`atk lane integrate`の稼働判定が
同じ宣言を読むため、解析を本モジュールへ集約する。解析が複数箇所へ分かれると、一方だけが新しい行を受理し、
同じ委譲プロンプトがサーバーでは拒否されフックでは受理される（またはその逆の）不一致が生じる。
`atk lane integrate`（`_atk`）からも読むため、共通層へ置く。
"""

import dataclasses
import json
import pathlib
import re
import typing

import yaml

from agent_toolkit._common.markdown_headings import top_level_atx_headings

REQUIRED_INPUT_PREFIX = "必須入力名: "
OPTIONAL_INPUT_PREFIX = "任意入力名: "
LAUNCH_KIND_PREFIX = "mode: "
# `mode:`へ改める前の宣言行。旧形式の`<役割名>.subagent.md`を同じ意味で読む読み取り互換に限って使う。
_LEGACY_LAUNCH_KIND_PREFIX = "起動種別: "
CONTINUATION_PREFIX = "継続入力: "
WORKTREE_WRITE_PREFIX = "作業ツリーへの書込: "
_WORKTREE_WRITE_VALUES = {"あり": True, "なし": False}
# `agents_server`の`start`の`task`が委譲プロンプトの1行目へ置く見出し。session登録簿の起動本文から役割文書を特定する側も使う。
_TASK_PROMPT_HEADER_PREFIX = "次の文書の手順を実行せよ（出所: "
_TASK_PROMPT_HEADER_SUFFIX = "）。"
INPUT_NAME_PATTERN = re.compile(r"^[^`\s:，、](?:[^`\s，、]*[^`\s:，、])?$")
TASK_DOCUMENT_SUFFIX = ".subagent.md"

LaunchKind = typing.Literal["delegate", "explore", "shell", "write"]
LAUNCH_KINDS: tuple[LaunchKind, ...] = typing.get_args(LaunchKind)

# 委譲プロンプトの中から`<役割名>.subagent.md`の絶対パスを抽出する。空白、バッククォートおよび引用符を区切りとし、
# Windowsの区切り文字を含むパスも抽出する。実在とagent-toolkitのshare配下であることは抽出した後に確かめる。
_TASK_DOCUMENT_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:)?[/\\][^\s`'\"<>|（）「」]*?\.subagent\.md")


@dataclasses.dataclass(frozen=True)
class ContinuationDeclaration:
    """役割文書が所有する定型継続の入力契約。"""

    instruction: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    path_lists: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    condition: str = ""

    @property
    def accepted(self) -> frozenset[str]:
        """受理する名前付き入力。"""
        return frozenset((*self.required, *self.optional))


@dataclasses.dataclass(frozen=True)
class TaskDocumentDeclaration:
    """役割文書が宣言する入力名・mode・モデル段位・用途・書込の値。

    `writes_worktree`は起動時のcwdが属する作業ツリーへ書き込む役割かを表す。
    """

    required: tuple[str, ...]
    optional: tuple[str, ...]
    launch_kind: LaunchKind
    writes_worktree: bool
    continuations: tuple[ContinuationDeclaration, ...] = ()
    model_type: str | None = None

    @property
    def accepted(self) -> frozenset[str]:
        """委譲プロンプトで受理する全入力名（必須・任意入力名）。

        待機と再開の方針は採用したbackendの能力に従ってサーバーが`share/agents-server-*.md`で伝えるため、委譲元が渡す共通入力は持たない。
        """
        return frozenset(self.required) | frozenset(self.optional)


def is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginのshare直下にある`<役割名>.subagent.md`だけを受理する。"""
    if path.parent.name != "share":
        return False
    try:
        manifest = json.loads((path.parent.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "agent-toolkit"


def read_declaration(task_document: pathlib.Path, document_text: str | None = None) -> TaskDocumentDeclaration | str:
    """`<役割名>.subagent.md`の宣言を返す。宣言を読めない場合は理由を示す警告文を返す。

    警告文を返す場合、呼び出し元は必須入力と宣言外入力を確認できないことを示して起動を続ける。
    宣言行の書式違反（`mode:`の未知の値を含む）も警告文として返す。
    """
    if not is_agent_toolkit_task_document(task_document):
        return f"必須入力を確認できません: `<役割名>.subagent.md`がshare配下ではありません: {task_document}"
    return read_declaration_unchecked(task_document, document_text)


def read_declaration_unchecked(
    task_document: pathlib.Path,
    document_text: str | None = None,
) -> TaskDocumentDeclaration | str:
    """所在の確認を済ませた`<役割名>.subagent.md`の宣言を返す。戻り値の意味は`read_declaration`と同じ。"""
    if document_text is None:
        try:
            document_text = task_document.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return f"必須入力を確認できません: `<役割名>.subagent.md`をUTF-8で読めません: {task_document}: {error}"
    if document_text.startswith("---\n") or document_text.startswith("---\r\n"):
        return _frontmatter_declaration(task_document, document_text)
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
    writes_worktree: bool | None = None
    continuations: list[ContinuationDeclaration] = []
    for line in block[1:]:
        if line.startswith(OPTIONAL_INPUT_PREFIX):
            parsed = _parse_names(line.removeprefix(OPTIONAL_INPUT_PREFIX))
            if parsed is None:
                return f"必須入力を確認できません: 任意入力名の書式が不正です: {task_document}"
            optional = parsed
        elif line.startswith((LAUNCH_KIND_PREFIX, _LEGACY_LAUNCH_KIND_PREFIX)):
            prefix = LAUNCH_KIND_PREFIX if line.startswith(LAUNCH_KIND_PREFIX) else _LEGACY_LAUNCH_KIND_PREFIX
            value = line.removeprefix(prefix).strip()
            if value not in LAUNCH_KINDS:
                return f"必須入力を確認できません: `mode:`の値が不正です: {value}: {task_document}"
            launch_kind = value
        elif line.startswith(WORKTREE_WRITE_PREFIX):
            value = line.removeprefix(WORKTREE_WRITE_PREFIX).strip()
            if value not in _WORKTREE_WRITE_VALUES:
                return (
                    f"必須入力を確認できません: `作業ツリーへの書込:`の値が不正です: {value}: {task_document}。"
                    "受理する値は`あり`か`なし`で、`作業ツリーへの書込: <あり|なし>`の形に直す"
                )
            writes_worktree = _WORKTREE_WRITE_VALUES[value]
        elif line.startswith(CONTINUATION_PREFIX):
            try:
                entry = json.loads(line.removeprefix(CONTINUATION_PREFIX))
                continuation = ContinuationDeclaration(**entry)
                if (
                    not isinstance(continuation.instruction, str)
                    or not isinstance(continuation.condition, str)
                    or any(
                        not isinstance(value, (list, tuple))
                        for value in (
                            continuation.required,
                            continuation.optional,
                            continuation.paths,
                            continuation.path_lists,
                            continuation.references,
                        )
                    )
                ):
                    raise ValueError("継続指示・条件は文字列、入力名は配列が必要")
                names = (
                    *continuation.required,
                    *continuation.optional,
                    *continuation.paths,
                    *continuation.path_lists,
                    *continuation.references,
                )
                if (
                    not continuation.instruction
                    or any(not INPUT_NAME_PATTERN.fullmatch(name) for name in names)
                    or not set(continuation.paths) <= continuation.accepted
                    or not set(continuation.path_lists) <= continuation.accepted
                    or bool(set(continuation.paths) & set(continuation.path_lists))
                    or not set(continuation.references) <= continuation.accepted
                    or any(item.instruction == continuation.instruction for item in continuations)
                ):
                    raise ValueError("継続入力の指示または入力名が不正")
                continuations.append(
                    dataclasses.replace(
                        continuation,
                        required=tuple(continuation.required),
                        optional=tuple(continuation.optional),
                        paths=tuple(continuation.paths),
                        path_lists=tuple(continuation.path_lists),
                        references=tuple(continuation.references),
                    )
                )
            except (TypeError, ValueError) as error:
                return f"必須入力を確認できません: 継続入力の書式が不正です: {task_document}: {error}"
    if writes_worktree is None:
        # 新しい役割の作成者に書込先をその時点で決めさせるため、省略時の値を置かない。
        return (
            f"必須入力を確認できません: `作業ツリーへの書込:`の行がありません: {task_document}。"
            "`## 入力`のtextコードブロックへ`作業ツリーへの書込: <あり|なし>`の行を追加する"
        )
    return TaskDocumentDeclaration(
        required=required,
        optional=optional,
        launch_kind=launch_kind,
        writes_worktree=writes_worktree,
        continuations=tuple(continuations),
    )


def _frontmatter_declaration(task_document: pathlib.Path, document_text: str) -> TaskDocumentDeclaration | str:
    """実行に必要な設定を読み取る。定義の静的整合性は配布前の試験で保証する。"""
    try:
        lines = document_text.splitlines()
        end = lines.index("---", 1)
        values = yaml.safe_load("\n".join(lines[1:end]))
        # 起動条件と書込主体を決める値は、既知の値を明示して受け取る。
        mode = values["mode"]
        writes = values["writes_worktree"]
        if mode not in LAUNCH_KINDS or not isinstance(writes, bool):
            raise ValueError("modeまたはwrites_worktreeの値が不正です")
        return TaskDocumentDeclaration(
            required=tuple(values["required_inputs"]),
            optional=tuple(values.get("optional_inputs", [])),
            launch_kind=mode,
            writes_worktree=writes,
            model_type=values["model_type"],
            continuations=tuple(
                ContinuationDeclaration(
                    instruction=entry["instruction"],
                    required=tuple(entry.get("required", [])),
                    optional=tuple(entry.get("optional", [])),
                    paths=tuple(entry.get("paths", [])),
                    path_lists=tuple(entry.get("path_lists", [])),
                    references=tuple(entry.get("references", [])),
                    condition=entry.get("condition", ""),
                )
                for entry in values.get("continuations", [])
            ),
        )
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        return f"必須入力を確認できません: frontmatterが不正です: {task_document}: {error}"


def task_prompt_header(task_document: pathlib.Path) -> str:
    """`start`の`task`が委譲プロンプトの1行目へ置く見出しを返す。"""
    return f"{_TASK_PROMPT_HEADER_PREFIX}{task_document}{_TASK_PROMPT_HEADER_SUFFIX}"


def launched_task_document(launch_prompt: str) -> pathlib.Path | None:
    """起動本文の1行目が`task`の見出しなら、その`<役割名>.subagent.md`の絶対パスを返す。

    文書を参照するだけの自由本文を役割の起動と取り違えないよう、見出しの完全一致に限る。
    """
    first = launch_prompt.split("\n", 1)[0]
    if not first.startswith(_TASK_PROMPT_HEADER_PREFIX) or not first.endswith(_TASK_PROMPT_HEADER_SUFFIX):
        return None
    path = pathlib.Path(first.removeprefix(_TASK_PROMPT_HEADER_PREFIX).removesuffix(_TASK_PROMPT_HEADER_SUFFIX))
    return path if path.is_absolute() and path.name.endswith(TASK_DOCUMENT_SUFFIX) else None


def find_task_documents(text: str) -> list[pathlib.Path]:
    """本文が絶対パスで指すagent-toolkitの`<役割名>.subagent.md`を出現順に重複なく返す。"""
    found: list[pathlib.Path] = []
    candidates = [match.group(0) for match in _TASK_DOCUMENT_PATH_PATTERN.finditer(text)]
    candidates.extend(re.findall(r"出所: (.+?\.subagent\.md)", text))
    candidates.extend(re.findall(r"^`?((?:[A-Za-z]:)?[/\\].+?\.subagent\.md)`?\s*の手順", text, re.MULTILINE))
    for candidate in candidates:
        path = pathlib.Path(candidate)
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
        return "`<役割名>.subagent.md`に## 入力がありません"
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
