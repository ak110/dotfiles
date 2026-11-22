"""agent-toolkitの役割文書（`<役割名>.subagent.md`）の所在を解決し、宣言された起動設定を読む。

役割文書の置き場所は`ROLE_DOCUMENT_DIRECTORY`だけが定める。

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
from collections.abc import Collection, Mapping

import markdown_it
import yaml

from agent_toolkit._common.markdown_headings import h2_section_bodies, top_level_atx_headings
from agent_toolkit._common.next_action import ActionableError

REQUIRED_INPUT_PREFIX = "必須入力名: "
OPTIONAL_INPUT_PREFIX = "任意入力名: "
LAUNCH_KIND_PREFIX = "mode: "
# `mode:`へ改める前の宣言行。旧形式の`<役割名>.subagent.md`を同じ意味で読む読み取り互換に限って使う。
_LEGACY_LAUNCH_KIND_PREFIX = "起動種別: "
CONTINUATION_PREFIX = "継続入力: "
WORKTREE_WRITE_PREFIX = "作業ツリーへの書込: "
_WORKTREE_WRITE_VALUES = {"あり": True, "なし": False}

_TASK_PROMPT_HEADER_PREFIX = "次の文書の手順を実行せよ（出所: "
_TASK_PROMPT_HEADER_SUFFIX = "）。"
INPUT_NAME_PATTERN = re.compile(r"^[^`\s:，、](?:[^`\s，、]*[^`\s:，、])?$")
TASK_DOCUMENT_SUFFIX = ".subagent.md"
TASK_PROMPT_REFERENCES = "\n## 手順・参照資料\n"
INPUT_CONTRACT_NOTICE = (
    "入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。\n"
    "役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。"
)
WHOLE_CONTRACT_PREPARATION = (
    "通常起動（`mode: delegate`）の共通の準備: 起動の前に"
    "`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意し、"
    "`extra_params`とは別の公開入力`whole_contract_path`へ絶対パスを渡す。"
)
"""通常起動だけが前提にする共通の準備。`start`は全体作業契約が解決できない通常起動を拒否する。

役割の入力一覧だけを完全な一覧として示すと、全体作業契約を作らずに起動して拒否され、作り直して起動し直す往復が起きた。
軽量の起動（`explore`・`shell`・`write`）は全体作業契約を検査しないため、この準備を案内しない。
"""


ROLE_DOCUMENT_DIRECTORY = "skills/atk-mode/roles"
"""役割文書と起動・受領契約（`<役割名>.parent.md`）を置く、plugin rootからの相対ディレクトリ。

スキルの配下だけを配置するホストへも役割文書を届けるため、入口スキル`atk-mode`の配下に置く。
"""
_ROLE_DOCUMENT_PARTS = tuple(ROLE_DOCUMENT_DIRECTORY.split("/"))


def role_document_directory(plugin_root: pathlib.Path) -> pathlib.Path:
    return plugin_root.joinpath(*_ROLE_DOCUMENT_PARTS)


def role_document_plugin_root(path: pathlib.Path) -> pathlib.Path | None:
    if path.parent.parts[-len(_ROLE_DOCUMENT_PARTS) :] == _ROLE_DOCUMENT_PARTS:
        return path.parents[len(_ROLE_DOCUMENT_PARTS)]
    return None


def document_plugin_root(path: pathlib.Path) -> pathlib.Path:
    root = role_document_plugin_root(path)
    if root is None:
        raise ValueError(f"役割文書の置き場所（{ROLE_DOCUMENT_DIRECTORY}）の外にある: {path}")
    return root


def find_role_document(plugin_root: pathlib.Path, name: str) -> pathlib.Path | None:
    """ファイル名（`<役割名>.subagent.md`・`<役割名>.parent.md`）に対応する実在の文書を返す。"""
    candidate = role_document_directory(plugin_root) / name
    return candidate if candidate.is_file() else None


def require_role_document(plugin_root: pathlib.Path, role: str) -> pathlib.Path:
    """役割名に対応する`<役割名>.subagent.md`を返す。無ければ探した置き場所を示して失敗する。"""
    document = find_role_document(plugin_root, role + TASK_DOCUMENT_SUFFIX)
    if document is None:
        raise ValueError(f"役割文書が存在しない: {role_document_directory(plugin_root)}に{role}.subagent.mdが無い")
    return document


def role_documents(plugin_root: pathlib.Path, suffix: str = TASK_DOCUMENT_SUFFIX) -> list[pathlib.Path]:
    return sorted(role_document_directory(plugin_root).glob(f"*{suffix}"), key=lambda path: path.name)


def launch_contract_documents(plugin_root: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """起動対象を宣言した全契約と役割名を返す。返却生成の対象表には依存しない。"""
    contracts: list[tuple[pathlib.Path, str]] = []
    markdown = markdown_it.MarkdownIt()
    for path in sorted((plugin_root / "skills").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "起動対象: " not in text:
            continue
        for token in markdown.parse(text):
            if token.type != "fence" or token.level != 0 or token.info != "text":
                continue
            first = token.content.split("\n", 1)[0]
            if not first.startswith("起動対象: "):
                continue
            for name in first.removeprefix("起動対象: ").split(","):
                if not re.fullmatch(r"[a-z][a-z0-9-]*\.subagent\.md", name):
                    raise ValueError(
                        f"{path}: 起動対象の名前が不正: {name}。"
                        "次の操作: 表示された契約の起動対象を、実在する<役割名>.subagent.mdの名前へ修正せよ。"
                    )
                contracts.append((path, name.removesuffix(TASK_DOCUMENT_SUFFIX)))
    return contracts


def input_contract_body(task_document: pathlib.Path) -> str:
    """共通の宣言と入力節から、呼出側へ配置する入力契約を生成する。

    通常起動の役割には、役割固有の入力とは別の共通の準備（全体作業契約）を加える。
    """
    text = task_document.read_text(encoding="utf-8")
    declaration = read_declaration_unchecked(task_document, text)
    if isinstance(declaration, str):
        raise ValueError(declaration)
    lines = [INPUT_CONTRACT_NOTICE]
    if declaration.launch_kind == "delegate":
        lines.append(WHOLE_CONTRACT_PREPARATION)
    lines.append("")
    conditional = [
        ("条件付きの必須入力名（" + "、".join(f"`{name}`が`{value}`" for name, value in entry.when) + "の時）", entry.inputs)
        for entry in declaration.required_when
    ]
    for label, names in (("必須入力名", declaration.required), *conditional, ("任意入力名", declaration.optional)):
        if not names:
            if label == "必須入力名":
                lines.append(f"- {label}: なし")
            continue
        prefix = f"- {label}: "
        line = prefix
        for name in names:
            token = f"`{name}`"
            if len(line) + len(token) > 100 and line != prefix:
                lines.append(line)
                line = prefix
            line += ("、" if line != prefix else "") + token
        lines.append(line)
    start, end = "<!-- caller-input:start -->", "<!-- caller-input:end -->"
    counts = text.splitlines().count(start), text.splitlines().count(end)
    if counts == (0, 0):
        return "\n".join(lines)
    for heading, body in h2_section_bodies(text):
        if heading != "入力" or start not in body or end not in body:
            continue
        if counts == (1, 1) and body.index(start) < body.index(end):
            definitions = "\n".join(body[body.index(start) + 1 : body.index(end)]).strip()
            if definitions:
                return "\n".join([*lines, "", definitions])
    raise ValueError(f"{task_document}: 呼出側の入力定義を「入力」節の一意な caller-input 境界内に置く")


LaunchKind = typing.Literal["delegate", "explore", "shell", "write"]
LAUNCH_KINDS: tuple[LaunchKind, ...] = typing.get_args(LaunchKind)

# 委譲プロンプトの中から`<役割名>.subagent.md`の絶対パスを抽出する。空白、バッククォートおよび引用符を区切りとし、
# Windowsの区切り文字を含むパスも抽出する。実在とagent-toolkitの役割文書の置き場所にあることは抽出した後に確かめる。
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
        return frozenset((*self.required, *self.optional))


type InputCondition = tuple[tuple[str, str], ...]
"""入力名と値の組。全ての入力の値が完全に一致した時に成立する（frontmatterの`when`）。"""


def parse_when(raw: object, declared: Collection[str]) -> InputCondition:
    if (
        not isinstance(raw, dict)
        or not raw
        or any(not isinstance(name, str) or name not in declared for name in raw)
        or any(not isinstance(value, str) or not value for value in raw.values())
    ):
        raise ValueError(f"whenは宣言済みの入力名から空でない文字列の値への対応で指定する: {raw!r}")
    return tuple(raw.items())


def when_matches(condition: InputCondition, params: Mapping[str, str]) -> bool:
    return all(params.get(name) == value for name, value in condition)


@dataclasses.dataclass(frozen=True)
class ConditionalInputs:
    when: InputCondition
    inputs: tuple[str, ...]


def parse_required_inputs_when(raw: object, declared: Collection[str]) -> tuple[ConditionalInputs, ...]:
    if not isinstance(raw, list):
        raise ValueError("required_inputs_whenは配列で指定する")
    entries: list[ConditionalInputs] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"when", "inputs"}:
            raise ValueError(f"required_inputs_whenの要素はwhenとinputsだけを持つ: {entry!r}")
        inputs = entry["inputs"]
        if (
            not isinstance(inputs, list)
            or not inputs
            or any(not isinstance(name, str) or name not in declared for name in inputs)
            or len(set(inputs)) != len(inputs)
        ):
            raise ValueError(f"required_inputs_whenのinputsは宣言済みの入力名を重複なく並べる: {inputs!r}")
        entries.append(ConditionalInputs(when=parse_when(entry["when"], declared), inputs=tuple(inputs)))
    return tuple(entries)


STARTUP_DOCUMENT_REFERENCE = re.compile(r"(?:agent-toolkit|agent-basics):[a-z0-9-]+/(?:[a-z0-9-]+/)*[a-z0-9.-]+\.md")
"""`startup_documents`が受理する資料の参照。`<plugin名>:<スキル名>/<スキルのディレクトリからの相対パス>`の形とする。"""


@dataclasses.dataclass(frozen=True)
class StartupDocument:
    reference: str
    when: InputCondition = ()

    def applies(self, inputs: Mapping[str, str]) -> bool:
        return when_matches(self.when, inputs)


def parse_startup_documents(values: object, accepted: frozenset[str]) -> tuple[StartupDocument, ...]:
    if not isinstance(values, list):
        raise ValueError("startup_documentsは資料の参照の配列で指定する")
    documents: list[StartupDocument] = []
    for value in values:
        entry = {"document": value} if isinstance(value, str) else value
        if not isinstance(entry, dict) or set(entry) - {"document", "when"}:
            raise ValueError(f"startup_documentsの要素は参照の文字列か{{document, when}}で指定する: {value!r}")
        reference = entry.get("document")
        if not isinstance(reference, str) or not STARTUP_DOCUMENT_REFERENCE.fullmatch(reference):
            raise ValueError(f"startup_documentsの参照は`<plugin名>:<スキル名>/<相対パス>.md`で指定する: {reference!r}")
        try:
            when = parse_when(entry["when"], accepted) if "when" in entry else ()
        except ValueError as error:
            raise ValueError(f"startup_documentsの{error}") from error
        documents.append(StartupDocument(reference, when))
    return tuple(documents)


@dataclasses.dataclass(frozen=True)
class TaskDocumentDeclaration:
    required: tuple[str, ...]
    optional: tuple[str, ...]
    launch_kind: LaunchKind
    writes_worktree: bool
    continuations: tuple[ContinuationDeclaration, ...] = ()
    model_type: str | None = None

    result_heads: tuple[str, ...] = ()
    required_when: tuple[ConditionalInputs, ...] = ()
    startup_documents: tuple[StartupDocument, ...] = ()

    @property
    def accepted(self) -> frozenset[str]:
        """委譲プロンプトで受理する全入力名（必須・任意入力名）。

        待機と再開の方針は採用したbackendの能力に従ってサーバーが起動の本文（`launch_prompts.py`）で伝えるため、委譲元が渡す共通入力は持たない。
        """
        return frozenset(self.required) | frozenset(self.optional)

    def required_for(self, params: Mapping[str, str]) -> tuple[str, ...]:
        names = list(self.required)
        for entry in self.required_when:
            if when_matches(entry.when, params):
                names.extend(name for name in entry.inputs if name not in names)
        return tuple(names)


def is_agent_toolkit_plugin_root(root: pathlib.Path) -> bool:
    try:
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "agent-toolkit"


def is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginの役割文書の置き場所の直下にある`<役割名>.subagent.md`だけを受理する。"""
    root = role_document_plugin_root(path)
    return root is not None and is_agent_toolkit_plugin_root(root)


def read_declaration(task_document: pathlib.Path, document_text: str | None = None) -> TaskDocumentDeclaration | str:
    """`<役割名>.subagent.md`の宣言を返す。宣言を読めない場合は理由を示す警告文を返す。

    警告文を返す場合、呼び出し元は必須入力と宣言外入力を確認できないことを示して起動を続ける。
    宣言行の書式違反（`mode:`の未知の値を含む）も警告文として返す。
    """
    if not is_agent_toolkit_task_document(task_document):
        return (
            f"必須入力を確認できません: `<役割名>.subagent.md`がagent-toolkitの役割文書の置き場所にありません: {task_document}"
        )
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
    try:
        lines = document_text.splitlines()
        end = lines.index("---", 1)
        values = yaml.safe_load("\n".join(lines[1:end]))

        mode = values["mode"]
        writes = values["writes_worktree"]
        if mode not in LAUNCH_KINDS or not isinstance(writes, bool):
            raise ValueError("modeまたはwrites_worktreeの値が不正です")
        heads = values.get("result_heads", [])
        if not isinstance(heads, list) or any(
            not isinstance(head, str)
            or not head.strip()
            or "\n" in head
            or head.startswith("状態:")
            and not head.removeprefix("状態:").strip()
            for head in heads
        ):
            raise ValueError("result_headsは値を含む固定行の配列で指定する")
        required = tuple(values["required_inputs"])
        optional = tuple(values.get("optional_inputs", []))
        startup = parse_startup_documents(values.get("startup_documents", []), frozenset((*required, *optional)))
        return TaskDocumentDeclaration(
            required=required,
            optional=optional,
            startup_documents=startup,
            launch_kind=mode,
            writes_worktree=writes,
            model_type=values["model_type"],
            result_heads=tuple(heads),
            required_when=parse_required_inputs_when(values.get("required_inputs_when", []), (*required, *optional)),
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


def parse_named_inputs(lines: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    current: str | None = None
    first_continuation = False
    for number, line in enumerate(lines, 2):
        if current is not None and line.startswith("  "):
            params[current] = line[2:] if first_continuation else params[current] + "\n" + line[2:]
            first_continuation = False
            continue
        if not line.strip():
            continue
        name, separator, value = line.partition(":")
        if not separator or (value and not value.startswith(" ")) or not INPUT_NAME_PATTERN.fullmatch(name) or name in params:
            raise ActionableError(
                f"入力本文の{number}行目を受理できません: {line}",
                next_action="指示と宣言済みの名前付き入力だけで再実行する。本文生成にはdelegation-promptを使う",
            )
        current = name
        params[name] = value.removeprefix(" ")
        first_continuation = not params[name]
    return params


def task_prompt_inputs(launch_prompt: str) -> dict[str, str]:
    instructions, boundary, _references = launch_prompt.partition(TASK_PROMPT_REFERENCES)
    if boundary:
        return parse_named_inputs(instructions.partition("\n入力:\n")[2].splitlines())
    return parse_named_inputs(launch_prompt.rpartition("\n入力:\n")[2].splitlines())


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
    names = tuple(value.split(","))
    if not names or any(not INPUT_NAME_PATTERN.fullmatch(name) for name in names):
        return None
    return names
