"""資料取得操作のパスとスキル実体を解決する。"""

from __future__ import annotations

import pathlib

from agent_toolkit._common import read_triggers, shell_cwd
from agent_toolkit._common.shell_tokens import join_words
from agent_toolkit._hooks import (
    codex_read_observation,
    document_reads,
)
from agent_toolkit._hooks.pretooluse.large_reads import (
    bash_read_paths,
    is_dynamic_read_operand,
)
from agent_toolkit._hooks.pretooluse.read_delivery_cache import _input_cached


def _read_name_hints(tool_name: str, tool_input: dict, cwd: str) -> set[str]:
    """読取対象を資料列の文字列と照らす名前（ファイル名、拡張子を除いた名前、スキルのディレクトリ名）を返す。

    資料列は資料をファイル名かスキル名で名指しする。どの名前も現れない行は読取対象を指さないため、資料を解決せずに外せる。
    """
    hints: set[str] = set()
    for path in _read_path_variants(tool_name, tool_input, cwd):
        hints.update((path.name, path.stem))
        if path.name == "SKILL.md":
            hints.add(path.parent.name)
        if path.name == "antigravity-full-instructions.md":
            hints.update(("SKILL.md", path.parent.parent.name))
    hints.discard("")
    return hints


def _row_operation_kinds(row: read_triggers.ReadTrigger) -> tuple[str, ...]:
    """配送操作の種別の判定に使う値を、導出行の資料を解決せずに返す。"""
    return ("read:",) if row.derived_read else row.operations


def _skill(source: pathlib.Path) -> str | None:
    for parent in [source.parent, *source.parents]:
        if parent.parent.name == "skills":
            return parent.name
    return None


@_input_cached
def _read_paths(tool_name: str, tool_input: dict, cwd: str, *, resolve: bool = True) -> list[pathlib.Path]:
    if tool_name == "Read" and isinstance(tool_input.get("file_path"), str):
        path = pathlib.Path(tool_input["file_path"]).expanduser()
        path = path if path.is_absolute() else pathlib.Path(cwd) / path
        return [path.resolve() if resolve else path]
    command = tool_input.get("command")
    if tool_name == "Bash" and isinstance(command, str):
        # 同一コマンドの前段で生成される候補は、実行後に存在と本文を観測する。
        return [
            path.resolve() if resolve else path
            for group in bash_read_paths(command, cwd, include_partial=True)
            for path in group
            if path.is_file()
        ]
    return []


@_input_cached
def _read_path_variants(tool_name: str, tool_input: dict, cwd: str) -> list[pathlib.Path]:
    """資料の取得を始める読取のパスを、リンク経由の名前でも宣言へ一致させるため解決前後の両方で返す。

    スキルの起動検出には1行目から始まる読取を使う。
    """
    command = tool_input.get("command")
    if tool_name == "Bash" and isinstance(command, str):
        groups = list(bash_read_paths(command, cwd, include_partial=True, first_line_only=True))
        return [
            path.resolve() if resolve else path
            for resolve in (True, False)
            for group in groups
            for path in group
            if path.is_file()
        ]
    return [path for resolve in (True, False) for path in _read_paths(tool_name, tool_input, cwd, resolve=resolve)]


def _operand_paths(read: codex_read_observation.Read, current: shell_cwd.CwdResolution) -> list[pathlib.Path]:
    """読取計画が入力ファイルと判定した位置の引数を、連結中の作業ディレクトリで解決する。

    シェルが展開し得るglobとbrace展開の語、作業ディレクトリを解決できない相対パスは対象に含めない。
    引用された記号はデータとして保持する。
    """
    paths = []
    for index in read.operands:
        operand = read.argv[index]
        if is_dynamic_read_operand(operand):
            continue
        path = pathlib.Path(operand).expanduser()
        if not path.is_absolute():
            if not current.resolved:
                continue
            path = pathlib.Path(current.path) / path
        paths.append(path)
    return paths


def _static_read_paths(command: str, cwd: str) -> list[pathlib.Path] | None:
    """静的読取と解決済みcdだけの連結なら、読取計画のファイル位置から求めた対象パスを返す。

    取得操作に当たらない入力はNoneを返す。受理判定と対象パスの特定は同じ読取計画を使い、
    成功記録の段階でコマンド別の受理条件を別に定めない。
    """
    reads = codex_read_observation.read_plan(command)
    if reads:
        base = shell_cwd.CwdResolution(cwd, bool(cwd))
        return [path for read in reads for path in _operand_paths(read, base)]
    invocations = list(shell_cwd.bash_invocation_cwds(command, cwd))
    if not invocations or any(
        not invocation.arguments_known
        or invocation.output_pipe
        or invocation.output_redirected
        or invocation.captured
        or invocation.background
        or invocation.inline_input
        for invocation, _current in invocations
    ):
        return None
    # 元の演算子を検証し、構文を省いた呼出し列だけから純粋読取と推定しない。
    groups = codex_read_observation.read_command_groups(command)
    if groups is None:
        return None
    current = shell_cwd.CwdResolution(cwd, bool(cwd))
    paths: list[pathlib.Path] = []
    found = False
    for tokens in groups:
        if tokens[0] == "cd":
            changed = shell_cwd.resolve_cwd_change(tokens, current)
            if changed is None or not changed.resolved:
                return None
            current = changed
        elif pathlib.PurePath(tokens[0]).name in {"wc", "ls"}:
            continue
        else:
            plan = codex_read_observation.read_plan(join_words(tokens))
            if len(plan) != 1:
                return None
            found = True
            paths.extend(_operand_paths(plan[0], current))
    return paths if found else None


def _pure_bash_read(command: str, cwd: str) -> bool:
    """既存の静的読取と、解決済みcdだけを連結した取得操作を判定する。"""
    return _static_read_paths(command, cwd) is not None


def _acquiring(tool_name: str, tool_input: dict, cwd: str) -> bool:
    command = tool_input.get("command")
    return tool_name in {"Read", "Skill"} or (
        tool_name == "Bash" and isinstance(command, str) and _pure_bash_read(command, cwd)
    )


def skill_name(path: pathlib.Path) -> str | None:
    """実体のmanifestを使い、配布スキルとローカルスキルを区別する。"""
    return document_reads.skill_name(path, owner=_document_owner)


def requires_skill_invocation(path: pathlib.Path, *, host: str, subject: str, cwd: str = "") -> bool:
    """本文参照と別に`Skill`起動を要する資料かを返す。"""
    return document_reads.requires_skill_invocation(path, host=host, subject=subject, cwd=cwd, owner=_document_owner)


def resolve_skill_document(qualified: object, candidates) -> pathlib.Path | None:
    """`Skill`起動の名前から、ホストが読み込んだ`SKILL.md`を1件に決める。決まらなければNoneを返す。

    プラグイン名付きの名前は、起動中の配布物（agent-toolkitのplugin rootと、その隣に導入された配布物）が所有する候補に限る。
    作業ツリー内の写しなど同名の別実体が候補に含まれても、ホストが読み込んだ実体だけを選ぶ。
    プラグイン名の無い名前に同名の候補が複数あれば、Claude Codeの優先順位に従ってユーザースキル（`~/.claude/skills`）を
    プロジェクトスキルより優先する（https://code.claude.com/docs/en/skills）。プロジェクトスキルの探索範囲は
    `read_triggers.project_skill_dirs`がホストの規則に合わせて決める。PostToolUseの応答は実体のパスを含まないため、
    観測時とセッション記録からの復元の両方でこの規則から実体を導く。
    """
    if not isinstance(qualified, str):
        return None
    matched = list(dict.fromkeys(path for path in candidates if path.is_file() and skill_name(path) == qualified))
    if ":" in qualified:
        active = document_reads.active_skill_root(qualified.split(":", 1)[0])
        if active is not None:
            matched = [path for path in matched if _document_owner(path) == active]
    elif len(matched) > 1:
        user = (pathlib.Path.home() / ".claude" / "skills").resolve()
        preferred = [path for path in matched if path.resolve().is_relative_to(user)]
        matched = preferred or matched
    return matched[0] if len(matched) == 1 else None


@_input_cached
def _document_owner(path: pathlib.Path) -> pathlib.Path | None:
    """同じhook内で同じ実体を繰り返し判定するため、`document_reads.document_owner`の結果を入力単位で共有する。"""
    return document_reads.document_owner(path)


@_input_cached
def _document_plugin(path: str) -> str:
    return document_reads.document_plugin(path)
