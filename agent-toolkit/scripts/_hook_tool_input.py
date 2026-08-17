"""Claude Code・Codexの編集ツール入力を共通の操作記録へ正規化する。

Claude Codeの`Write` / `Edit` / `MultiEdit`と、Codexの`apply_patch`は、
同じ「ファイルをどう変えるか」を別々の入力形式で表す。
本モジュールは両者を1つの操作記録（`EditOperation`）へ変換し、
PreToolUseとPostToolUseの双方が同じ単位で検査・記録できるようにする。

層は2つに分ける。

- 解析層（`parse_operations`）: 入力だけから操作種別・対象パス・順序付き編集断片を求める純粋処理。
  ファイルを読まないためPostToolUse（適用後）からも安全に呼べる
- 具体化層（`materialize`）: 対象ファイルの現在内容を読み、変更前後の全文像を組み立てる。
  PreToolUse（適用前）だけが呼ぶ

Codexのpatch構文はシェル構文として評価せず、公式の`*** Begin Patch`〜`*** End Patch`形式だけを対象とする。
"""

from __future__ import annotations

import dataclasses
import pathlib

# Codexの編集ツール名。matcher上は`Edit`・`Write`の別名に一致するが、payloadの`tool_name`は本名で届く。
CODEX_APPLY_PATCH_TOOL = "apply_patch"

# Claude Codeの編集ツール名。
CLAUDE_EDIT_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "MultiEdit"})

# 操作種別。`write`はClaudeの`Write`（全文書き込み）、残りはCodex patchの4形式に対応する。
KIND_WRITE = "write"
KIND_ADD = "add"
KIND_UPDATE = "update"
KIND_DELETE = "delete"
KIND_MOVE = "move"

# 全文を書き込む操作種別。検査によっては断片ごとではなく変更後全文で判定する。
_WHOLE_WRITE_KINDS: frozenset[str] = frozenset({KIND_WRITE, KIND_ADD})

_PATCH_BEGIN = "*** Begin Patch"
_PATCH_END = "*** End Patch"
_ADD_PREFIX = "*** Add File: "
_UPDATE_PREFIX = "*** Update File: "
_DELETE_PREFIX = "*** Delete File: "
_MOVE_PREFIX = "*** Move to: "


def is_codex_payload(payload: object) -> bool:
    """Codexのターン単位hook入力であるかを返す。

    Codexはターン単位のhookへ非空文字列の`turn_id`を付加する。
    `model`の有無やツール名の推測を別のホスト判定として併設しない。
    """
    if not isinstance(payload, dict):
        return False
    turn_id = payload.get("turn_id")
    return isinstance(turn_id, str) and turn_id != ""


@dataclasses.dataclass(frozen=True)
class EditFragment:
    """1つの編集単位に対応する変更前断片と変更後断片。

    `label`は入力中の位置を識別する安定した名前とし、警告文へそのまま載せる。
    `replace_all`はClaudeの同名引数に対応し、行単位断片では使わない。
    """

    label: str
    before: str
    after: str
    replace_all: bool = False


@dataclasses.dataclass(frozen=True)
class EditOperation:
    """1ファイルに対する1操作の記録。ファイル内容は読まない。

    `line_based`はCodex patchのhunkのように断片が行単位で完結することを示す。
    Claudeの`old_string`は行内の部分文字列を取り得るため偽とし、文字列置換で適用する。
    """

    kind: str
    path: str
    display_path: str
    fragments: tuple[EditFragment, ...]
    whole_after_text: str | None = None
    source_path: str | None = None
    source_display_path: str | None = None
    line_based: bool = False

    @property
    def is_whole_write(self) -> bool:
        """変更後全文が入力だけで確定する操作であるかを返す。"""
        return self.kind in _WHOLE_WRITE_KINDS

    @property
    def exists_after_apply(self) -> bool:
        """適用後に対象パスのファイルが存在する操作であるかを返す。"""
        return self.kind != KIND_DELETE

    @property
    def display_paths(self) -> list[str]:
        """パス判定と状態記録の対象となる入力表記を返す。移動では移動先と移動元の双方を含む。"""
        paths = [self.display_path, self.source_display_path]
        return [path for path in paths if path]


@dataclasses.dataclass(frozen=True)
class MaterializedEdit:
    """操作記録へ対象ファイルの現在内容を適用した変更前後の全文像。"""

    operation: EditOperation
    before_image: str
    after_image: str


def parse_operations(tool_name: str, tool_input: object, cwd: str) -> list[EditOperation] | None:
    """編集ツール入力を操作記録の一覧へ変換する。

    対象外のツールではNoneを返す。
    Codexのpatch構文を認識できない場合は空リストを返し、呼び出し側は検査を行わずに通過させる
    （patchの妥当性判定は`apply_patch`本体へ委ねる）。
    """
    if not isinstance(tool_input, dict):
        return None
    if tool_name in CLAUDE_EDIT_TOOLS:
        return _parse_claude_operations(tool_name, tool_input, cwd)
    if tool_name == CODEX_APPLY_PATCH_TOOL:
        command = tool_input.get("command")
        if not isinstance(command, str):
            return []
        return _parse_codex_operations(command, cwd)
    return None


def new_content_fields(tool_name: str, tool_input: object, cwd: str = "") -> list[tuple[str, str]] | None:
    """編集ツール入力の変更後断片を（ラベル, 値）の一覧で返す。

    新規に書き込まれる側だけを検査するhookの共通入口とする。対象外のツールではNoneを返す。
    """
    operations = parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        return None
    return [(fragment.label, fragment.after) for operation in operations for fragment in operation.fragments]


def materialize(operation: EditOperation) -> MaterializedEdit | None:
    """操作記録へ現在のファイル内容を適用し、変更前後の全文像を返す。

    現在内容の取得に失敗した場合、又はpatchのhunkを現在内容へ適用できない場合はNoneを返す。
    """
    if operation.kind == KIND_ADD:
        return MaterializedEdit(operation, "", operation.whole_after_text or "")
    read_path = operation.source_path if operation.kind == KIND_MOVE else operation.path
    if operation.kind == KIND_WRITE:
        # Claudeの`Write`は変更後全文が入力で確定するが、変更前像は既存ファイルの読み取りに依存する。
        # 対象が存在しないと確定した場合だけ空の変更前像とし、他の読み取り失敗は判定不能として返す。
        before_text, missing = _read_text_for_overwrite(read_path)
        if before_text is None and not missing:
            return None
        return MaterializedEdit(operation, before_text or "", operation.whole_after_text or "")
    before = _read_text(read_path)
    if before is None:
        return None
    if operation.kind == KIND_DELETE:
        return MaterializedEdit(operation, before, "")
    after = _apply_fragments(before, operation.fragments, line_based=operation.line_based)
    if after is None:
        return None
    return MaterializedEdit(operation, before, after)


def _read_text(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _read_text_for_overwrite(path: str | None) -> tuple[str | None, bool]:
    """全文上書き前の内容と、対象が存在しないと確定できたかを返す。

    変更後全文が入力で確定することと、変更前像を復元できることは独立した条件である。
    `FileNotFoundError`だけを対象不在の確定として扱う。
    権限不足・文字コードエラー・パス種別の不一致などは、対象の有無を確定できない読み取り失敗とする。
    `pathlib.Path.exists()`はOSErrorとValueErrorを偽として扱い、親ディレクトリへ到達できない場合に
    既存ファイルと未作成パスを区別できないため、存在確認関数は用いない。
    """
    if not path:
        return None, True
    try:
        return pathlib.Path(path).read_text(encoding="utf-8"), False
    except FileNotFoundError:
        return None, True
    except (OSError, UnicodeDecodeError, ValueError):
        return None, False


# --- Claude Code入力の解析 ---


def _parse_claude_operations(tool_name: str, tool_input: dict, cwd: str) -> list[EditOperation]:
    file_path_raw = tool_input.get("file_path")
    display_path = file_path_raw if isinstance(file_path_raw, str) else ""
    path = _resolve(display_path, cwd)
    if tool_name == "Write":
        content = tool_input.get("content")
        content = content if isinstance(content, str) else ""
        return [
            EditOperation(
                kind=KIND_WRITE,
                path=path,
                display_path=display_path,
                fragments=(EditFragment("content", "", content),),
                whole_after_text=content,
            )
        ]
    # Edit・MultiEditは断片を解釈できない入力でも操作記録自体は返す
    # （PostToolUseの編集ファイル記録が入力欄の有無に左右されないようにするため）。
    fragments: list[EditFragment] = []
    if tool_name == "Edit":
        new_string = tool_input.get("new_string")
        if isinstance(new_string, str):
            old_string = tool_input.get("old_string")
            fragments.append(
                EditFragment(
                    "new_string",
                    old_string if isinstance(old_string, str) else "",
                    new_string,
                    replace_all=bool(tool_input.get("replace_all")),
                )
            )
    else:
        edits = tool_input.get("edits")
        for index, edit in enumerate(edits if isinstance(edits, list) else []):
            if not isinstance(edit, dict):
                continue
            new_string = edit.get("new_string")
            if not isinstance(new_string, str):
                continue
            old_string = edit.get("old_string")
            fragments.append(
                EditFragment(
                    f"edits[{index}].new_string",
                    old_string if isinstance(old_string, str) else "",
                    new_string,
                    replace_all=bool(edit.get("replace_all")),
                )
            )
    return [
        EditOperation(
            kind=KIND_UPDATE,
            path=path,
            display_path=display_path,
            fragments=tuple(fragments),
        )
    ]


# --- Codex patchの解析 ---


def _parse_codex_operations(patch_text: str, cwd: str) -> list[EditOperation]:
    sections = _parse_patch_sections(patch_text)
    if sections is None:
        return []
    operations: list[EditOperation] = []
    for section in sections:
        operations.extend(_section_to_operations(section, cwd))
    return operations


@dataclasses.dataclass
class _PatchSection:
    kind: str
    path: str
    body: list[str] = dataclasses.field(default_factory=list)
    move_to: str | None = None


def _parse_patch_sections(patch_text: str) -> list[_PatchSection] | None:
    """`*** Begin Patch`〜`*** End Patch`をファイル単位の区間へ分解する。

    開始標識・終了標識・ファイル標識のいずれかを認識できない場合はNoneを返す。
    """
    lines = patch_text.splitlines()
    try:
        start = lines.index(_PATCH_BEGIN)
    except ValueError:
        return None
    sections: list[_PatchSection] = []
    current: _PatchSection | None = None
    terminated = False
    for line in lines[start + 1 :]:
        if line == _PATCH_END:
            terminated = True
            break
        if line.startswith(_ADD_PREFIX):
            current = _PatchSection(KIND_ADD, line[len(_ADD_PREFIX) :].strip())
            sections.append(current)
            continue
        if line.startswith(_UPDATE_PREFIX):
            current = _PatchSection(KIND_UPDATE, line[len(_UPDATE_PREFIX) :].strip())
            sections.append(current)
            continue
        if line.startswith(_DELETE_PREFIX):
            current = _PatchSection(KIND_DELETE, line[len(_DELETE_PREFIX) :].strip())
            sections.append(current)
            continue
        if line.startswith(_MOVE_PREFIX):
            if current is None or current.kind != KIND_UPDATE:
                return None
            current.move_to = line[len(_MOVE_PREFIX) :].strip()
            continue
        if current is not None:
            current.body.append(line)
    if not terminated or not sections:
        return None
    if any(not section.path for section in sections):
        return None
    return sections


def _section_to_operations(section: _PatchSection, cwd: str) -> list[EditOperation]:
    if section.kind == KIND_ADD:
        content = _added_content(section.body)
        return [
            EditOperation(
                kind=KIND_ADD,
                path=_resolve(section.path, cwd),
                display_path=section.path,
                fragments=(EditFragment("*** Add File", "", content),),
                whole_after_text=content,
            )
        ]
    if section.kind == KIND_DELETE:
        return [
            EditOperation(
                kind=KIND_DELETE,
                path=_resolve(section.path, cwd),
                display_path=section.path,
                fragments=(),
            )
        ]
    fragments = _hunk_fragments(section.body)
    if section.move_to is None:
        return [
            EditOperation(
                kind=KIND_UPDATE,
                path=_resolve(section.path, cwd),
                display_path=section.path,
                fragments=fragments,
                line_based=True,
            )
        ]
    # 移動は移動先への変更と移動元の消滅の2操作として扱う。
    return [
        EditOperation(
            kind=KIND_MOVE,
            path=_resolve(section.move_to, cwd),
            display_path=section.move_to,
            fragments=fragments,
            source_path=_resolve(section.path, cwd),
            source_display_path=section.path,
            line_based=True,
        ),
        EditOperation(
            kind=KIND_DELETE,
            path=_resolve(section.path, cwd),
            display_path=section.path,
            fragments=(),
        ),
    ]


def _added_content(body: list[str]) -> str:
    lines = [line[1:] if line.startswith("+") else line for line in body if not line.startswith("***")]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _hunk_fragments(body: list[str]) -> tuple[EditFragment, ...]:
    fragments: list[EditFragment] = []
    for index, hunk in enumerate(_split_hunks(body)):
        before_lines: list[str] = []
        after_lines: list[str] = []
        for line in hunk:
            if line.startswith("-"):
                before_lines.append(line[1:])
            elif line.startswith("+"):
                after_lines.append(line[1:])
            else:
                text = line[1:] if line.startswith(" ") else line
                before_lines.append(text)
                after_lines.append(text)
        fragments.append(EditFragment(f"hunk[{index}]", "\n".join(before_lines), "\n".join(after_lines)))
    return tuple(fragments)


def _split_hunks(body: list[str]) -> list[list[str]]:
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in body:
        if line.startswith("@@"):
            current = []
            hunks.append(current)
            continue
        if line.startswith("***"):
            continue
        if current is None:
            current = []
            hunks.append(current)
        current.append(line)
    return [hunk for hunk in hunks if hunk]


def _apply_fragments(before: str, fragments: tuple[EditFragment, ...], *, line_based: bool) -> str | None:
    """順序付き編集断片を現在内容へ順に適用する。

    行単位断片では変更前断片を行の並びとして照合し、見つからない場合はNoneを返す。
    文字列断片では先頭からの置換で適用する。
    """
    if not fragments:
        return before
    if not line_based:
        result = before
        for fragment in fragments:
            if fragment.replace_all:
                result = result.replace(fragment.before, fragment.after)
            else:
                result = result.replace(fragment.before, fragment.after, 1)
        return result
    lines = before.split("\n")
    position = 0
    for fragment in fragments:
        before_lines = fragment.before.split("\n")
        if fragment.before == "":
            return None
        index = _find_sublist(lines, before_lines, position)
        if index is None:
            return None
        after_lines = fragment.after.split("\n") if fragment.after != "" else []
        lines = lines[:index] + after_lines + lines[index + len(before_lines) :]
        position = index + len(after_lines)
    return "\n".join(lines)


def _find_sublist(lines: list[str], target: list[str], start: int) -> int | None:
    if not target or len(target) > len(lines):
        return None
    for index in range(start, len(lines) - len(target) + 1):
        if lines[index : index + len(target)] == target:
            return index
    return None


def _resolve(path: str, cwd: str) -> str:
    """相対パスをpayloadの`cwd`起点で解決した表記を返す。"""
    if not path:
        return ""
    candidate = pathlib.PurePath(path)
    if candidate.is_absolute() or not cwd:
        return str(candidate)
    return str(pathlib.PurePath(cwd) / candidate)
