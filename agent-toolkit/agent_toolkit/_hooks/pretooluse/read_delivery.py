"""宣言された操作の前に、主体の文脈に未取得の資料を案内する。

初回は資料のパスを案内し、未取得のまま再書込する操作を遮断する。
取得の観測だけを主体別の記録に反映する。
実際に配送された必要範囲を記録し、旧版の受信と現在版の更新を区別する。
軽量委譲は起動契約がスキルを使わないため対象外とする。
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import fnmatch
import functools
import json
import os
import pathlib
import re
import shlex
import tempfile
import tomllib
from collections.abc import Callable, Generator
from typing import cast

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import delegated_session, read_triggers, shell_cwd, transcript
from agent_toolkit._common.plugin_roots import (
    PluginRootError,
    codex_installed_plugins,
    plugin_resolution_scope,
    sibling_plugin_root,
)
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_segments import extract_execution_segments, split_bash_commands
from agent_toolkit._common.shell_tokens import ShellWord, is_atk_command, join_words, split_words
from agent_toolkit._hooks import (
    agent_id,
    codex_read_observation,
    document_reads,
    plugin_resources,
    rules_context,
    work_contract,
)
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import tool_input as tool_input_fields
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.pretooluse import dependency_usage, operation_skills
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths, sed_read_range
from agent_toolkit._hooks.pretooluse.notices import _block_notice, _llm_notice
lazy from agent_toolkit._common import task_documents
lazy from agent_toolkit._hooks.pretooluse import shell_checks

_INPUT_CACHE: contextvars.ContextVar[dict[tuple, object] | None] = contextvars.ContextVar(
    "read_delivery_input_cache", default=None
)


@contextlib.contextmanager
def _input_scope() -> Generator[None]:
    """同じhook内の不変入力だけを共有し、次の操作では資料・cwd・主体を再評価する。"""
    token = _INPUT_CACHE.set({})
    try:
        yield
    finally:
        _INPUT_CACHE.reset(token)


def _input_cached[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(function)
    def cached(*args: P.args, **kwargs: P.kwargs) -> R:
        cache = _INPUT_CACHE.get()
        if cache is None:
            return function(*args, **kwargs)
        key = (function, json.dumps((args, kwargs), sort_keys=True, default=os.fspath))
        if key not in cache:
            cache[key] = function(*args, **kwargs)
        return cast(R, cache[key])

    return cached


@_input_cached
def declarations(cwd: str, *, host: str = "claude", session_id: str = "") -> list[read_triggers.ReadTrigger]:
    """稼働版と対象プロジェクト・ユーザースキルの宣言を読む。"""
    root = plugin_resources.plugin_root()
    sources = read_triggers.document_sources(root, cwd)
    # 外部分離された品質スキルは、導入された実体がある場合に同じ表形式で取り込む。
    sibling = sibling_plugin_root("agent-basics", root)
    if sibling is not None:
        sources.extend(sorted((sibling / "skills").rglob("*.md")))
    # 宣言表ごとの入れ子scopeを、1回のhook要求が共有する一覧へ接続する。
    with plugin_resolution_scope(session_id=session_id), read_triggers.resolution_scope():
        entries = [
            (source.resolve(), read_triggers.parse_tables(source, root, host=host, project=cwd))
            for source in dict.fromkeys(sources)
        ]
    # 参照先の所有plugin全体は有効化しない。選択した実体の宣言は取得時に追加する。
    included = {row.source for source, rows in entries for row in rows if row.source != source}
    return list(dict.fromkeys(row for source, rows in entries if source not in included for row in rows))


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
        return [
            path.resolve() if resolve else path
            for group in bash_read_paths(command, cwd, include_partial=True)
            for path in group
        ]
    return []


@_input_cached
def _read_path_variants(tool_name: str, tool_input: dict, cwd: str) -> list[pathlib.Path]:
    """資料の取得を始める読取のパスを、リンク経由の名前でも宣言へ一致させるため解決前後の両方で返す。

    スキルの起動検出には1行目から始まる読取を使い、節指定の取得はsection宣言で扱う。
    """
    command = tool_input.get("command")
    if tool_name == "Bash" and isinstance(command, str):
        groups = list(bash_read_paths(command, cwd, include_partial=True, first_line_only=True))
        return [path.resolve() if resolve else path for resolve in (True, False) for group in groups for path in group]
    return [path for resolve in (True, False) for path in _read_paths(tool_name, tool_input, cwd, resolve=resolve)]


def _operand_paths(read: codex_read_observation.Read, current: shell_cwd.CwdResolution) -> list[pathlib.Path]:
    """読取計画が入力ファイルと判定した位置の引数を、連結中の作業ディレクトリで解決する。

    シェルが展開し得るglobとbrace展開の語、作業ディレクトリを解決できない相対パスは対象に含めない。
    引用された記号はデータとして保持する。
    """
    paths = []
    for index in read.operands:
        operand = read.argv[index]
        unquoted = operand.unquoted if isinstance(operand, ShellWord) else operand
        if any(marker in unquoted for marker in ("*", "?", "[", "{")):
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


def acquisition_step(target: read_triggers.ReadTarget, subject: str, *, reference: bool = False, cwd: str = "") -> str:
    """主体に合う取得手段と、必要な範囲の容量を示す。"""
    path = target.path
    body = target.body()
    focus = f"。指定節`{target.heading}`と、その成立に必要な前提・例外を読め" if target.heading else ""
    skill = skill_name(path)
    if (
        subject != operation_skills.SUBJECT_CODEX
        and not reference
        and skill
        and document_reads.skill_loadable(path, cwd=cwd, owner=_document_owner)
        and (
            subject == operation_skills.SUBJECT_SUBAGENT
            or requires_skill_invocation(path, host="claude", subject=subject, cwd=cwd)
        )
    ):
        step = f"`Skill`で`{skill}`を起動せよ（資料: `{path}`）"
        if subject == operation_skills.SUBJECT_SUBAGENT:
            step += f"。`Skill`を使えない場合は`{path}`をReadで全文読め"
        return step + focus
    if subject == operation_skills.SUBJECT_CODEX:
        quoted = shlex.quote(str(path))
        commands = (
            [f"sed -n '{start},{end}p' -- {quoted}" for start, end in target.line_ranges()]
            if target.heading and path.suffix == ".md"
            else [f"cat -- {quoted}"]
        )
        size = len(body.encode("utf-8"))
        return (
            "、".join(f"`{command}`" for command in commands)
            + f"で取得せよ（対象{size}バイト、予算目安{(size + 2) // 3}トークン）。"
            "内側exec_commandのmax_output_tokensと外側functions.execの出力枠を確保し、外側は内側の合計以上にせよ。"
            "枠に収まらない範囲は行範囲を分けて取得せよ。" + rules_context.shell_acquisition_conditions() + focus
        )
    return (
        f"Readで`{path}`を取得せよ。Bashでは`cat -- {shlex.quote(str(path))}`、"
        "または`sed -n '<開始行>,<終了行>p' -- <絶対パス>`を単独か`&&`連結で実行せよ" + focus
    )


def _pyproject_dependencies(text: str) -> object:
    """pytest等の設定変更を、依存定義の操作から区別する。

    解析不能な本文はそのまま比較し、壊れた依存宣言の変更を不変と扱わない。
    対象の表はdependency_usageが依存名を抽出する範囲にそろえる。
    """
    try:
        data = tomllib.loads(text)
        project = data.get("project") or {}
        return (
            project.get("dependencies"),
            project.get("optional-dependencies"),
            data.get("dependency-groups"),
            (data.get("tool", {}).get("uv") or {}).get("dev-dependencies"),
        )
    except tomllib.TOMLDecodeError, AttributeError, TypeError:
        return text


_FILE_SCOPED_OPERATIONS = frozenset({"existing-file-edit", "claude-existing-file-edit"})
_PROSE_SUFFIXES = frozenset({".md", ".markdown", ".txt"})


def _temporary_path(path: str) -> bool:
    """成果物ではない一時領域（managed-tempの一時rootと、Claude Codeの一時領域）の配下かを返す。

    一時領域のファイルは使い捨ての集計・下書き用であり、コード規約などの必須読込と再書込の遮断を課すと、
    成果物の品質に寄与しないまま作業だけが止まる。Claude Codeはタスク出力とscratchpadを
    `<一時ディレクトリ>/claude-<uid>/`の下へ置く。一時ディレクトリ全体は作業ツリーの検証用複製なども置かれ得るため含めない。
    """
    candidate = pathlib.Path(path).resolve()
    roots = [managed_temp.temp_root_path()]
    if hasattr(os, "getuid"):
        roots.append(pathlib.Path(tempfile.gettempdir()) / f"claude-{os.getuid()}")
    return any(candidate.is_relative_to(root.resolve()) for root in roots)


def _artifact_written_paths(tool_name: str, tool_input: dict, cwd: str) -> list[str]:
    """確定した書込先のうち、一時領域を除いた成果物の書込先を返す。"""
    return [path for path in operation_skills.written_paths(tool_name, tool_input, cwd) if not _temporary_path(path)]


@_input_cached
def matches(operation: str, tool_name: str, tool_input: dict, cwd: str) -> bool:
    """共通語彙を既存の静的な操作判定へ結びつける。

    書込先で成立する操作（既存ファイルの編集、`path:`・`write:`・`dependency:`）は、一時領域の書込先を除いて判定する。
    本文の作成（`body-writing`）は、一時領域の文章の下書きを除き、スクリプトやデータだけの書込で成立させない。
    """
    if operation in _FILE_SCOPED_OPERATIONS:
        written = operation_skills.written_paths(tool_name, tool_input, cwd)
        if written and all(_temporary_path(path) for path in written):
            return False
    if operation == "body-writing":
        # 一時領域の作業項目の本文・コミットメッセージ・応答などの文章の下書きは、投入・送信される成果物の本文であり、
        # 文章の品質基準を適用する。使い捨てのスクリプトやデータだけを書く場合は成果物の本文に当たらない。
        written = operation_skills.written_paths(tool_name, tool_input, cwd)
        if written and all(
            _temporary_path(path) and pathlib.Path(path).suffix.lower() not in _PROSE_SUFFIXES for path in written
        ):
            return False
    matched = operation_skills.operation_matches(operation, tool_name, tool_input, cwd)
    if matched is not None:
        return matched
    prefix, value = operation.split(":", 1)
    if prefix == "tool":
        return tool_name == value
    if prefix == "write":
        return any(
            fnmatch.fnmatch(path.replace("\\", "/"), value) for path in _artifact_written_paths(tool_name, tool_input, cwd)
        )
    if prefix == "read":
        return any(_matches_document_path(path, value) for path in _read_path_variants(tool_name, tool_input, cwd))
    if prefix == "path":
        paths = _artifact_written_paths(tool_name, tool_input, cwd)
        return any(fnmatch.fnmatch(path.replace("\\", "/"), value) for path in paths)
    if prefix == "section":
        pattern, separator, _heading = value.partition("#")
        return bool(separator) and any(
            _matches_document_path(path, pattern)
            for resolve in (True, False)
            for path in _read_paths(tool_name, tool_input, cwd, resolve=resolve)
        )
    if prefix == "dependency":
        # 名前の出現ではなく、読込記述と依存定義の項目だけを使用として扱う。編集後の本文と既存の本文の両方を見るため、
        # 変更断片にライブラリ名が無い部分編集でも、既存のコードが依存を読み込んでいれば成立する。
        operations = tool_input_fields.parse_operations(tool_name, tool_input, cwd) or []
        for raw in _artifact_written_paths(tool_name, tool_input, cwd):
            candidate = pathlib.Path(raw)
            if not dependency_usage.is_target(candidate):
                continue
            texts = []
            for edit in operations:
                if pathlib.Path(edit.path) != candidate:
                    continue
                materialized = tool_input_fields.materialize(edit)
                if (
                    candidate.name.removesuffix(".tmpl") == "pyproject.toml"
                    and materialized is not None
                    and _pyproject_dependencies(materialized.before_image) == _pyproject_dependencies(materialized.after_image)
                ):
                    continue
                if materialized is not None and materialized.after_image:
                    texts.append(materialized.after_image)
                else:
                    texts.extend(fragment.after for fragment in edit.fragments)
                if candidate.is_file():
                    texts.append(candidate.read_text(encoding="utf-8", errors="replace"))
            if not operations and candidate.is_file():
                texts.append(candidate.read_text(encoding="utf-8", errors="replace"))
            if any(dependency_usage.uses(candidate, text, value) for text in texts):
                return True
        return False
    if prefix == "role":
        # 役割資料は受領した会話へstartup_contextが届ける。起動する親へは配送しない。
        return False
    if prefix == "skill":
        plugin, separator, short = value.partition(":")
        if not separator:
            plugin, short = "agent-toolkit", value
        requested = str(tool_input.get("skill", ""))
        requested = requested if ":" in requested else "agent-toolkit:" + requested
        return (tool_name == "Skill" and requested == plugin + ":" + short) or any(
            document_reads.skill_document(str(path)) == (short, "SKILL.md")
            and document_reads.document_plugin(str(path)) == plugin
            for path in _read_path_variants(tool_name, tool_input, cwd)
        )
    if prefix == "bash" and tool_name == "Bash" and isinstance(tool_input.get("command"), str):
        expected = shlex.split(value)
        for segment in extract_execution_segments(tool_input["command"]):
            if not segment.resolved or not segment.tokens:
                continue
            tokens = [pathlib.Path(segment.tokens[0]).name, *segment.tokens[1:]]
            if expected[0] == "git":
                subcommand = shell_checks.git_subcommand_tokens(segment)
                if subcommand is not None:
                    tokens = ["git", *subcommand]
            if expected[0] == "atk" and is_atk_command(segment.tokens):
                tokens[0] = "atk"
            if tokens[: len(expected)] == expected:
                return True
        return False
    return False


def _matches_document_path(path: pathlib.Path, pattern: str) -> bool:
    """同名skillを持つpluginを、明示した資料の取得操作でも区別する。"""
    candidates = [path]
    if path.name == "antigravity-full-instructions.md" and document_reads.skill_document(str(path)) is not None:
        candidates.append(path.parent.parent / "SKILL.md")
    for plugin in ("agent-toolkit", "agent-basics"):
        if pattern.startswith(plugin + ":"):
            return document_reads.document_plugin(str(path)) == plugin and any(
                fnmatch.fnmatch(candidate.as_posix(), pattern.removeprefix(plugin + ":")) for candidate in candidates
            )
    return any(fnmatch.fnmatch(candidate.as_posix(), pattern) for candidate in candidates)


@_input_cached
def _document_owner(path: pathlib.Path) -> pathlib.Path | None:
    """同じhook内で同じ実体を繰り返し判定するため、`document_reads.document_owner`の結果を入力単位で共有する。"""
    return document_reads.document_owner(path)


@_input_cached
def _document_plugin(path: str) -> str:
    return document_reads.document_plugin(path)


def _bound_operation_matches(
    row: read_triggers.ReadTrigger, operation: str, tool_name: str, tool_input: dict, cwd: str
) -> bool:
    """同名の別rootを読む操作と、宣言が所属する実体を区別する。"""
    if not matches(operation, tool_name, tool_input, cwd):
        return False
    owner = _document_owner(row.source)
    plugin = _document_plugin(str(row.source))
    if owner is not None and operation.startswith((f"read:{plugin}:", f"section:{plugin}:", f"skill:{plugin}:")):
        requests = [tool_input]
        if tool_name == "Bash" and isinstance(tool_input.get("command"), str):
            requests = [
                {"command": join_words(segment.tokens)}
                for segment in extract_execution_segments(tool_input["command"])
                if segment.resolved
            ]
        if any(_read_paths(tool_name, request, cwd) for request in requests):
            prefix, value = operation.split(":", 1)
            pattern = value.partition("#")[0]
            return any(
                matches(operation, tool_name, request, cwd)
                and any(
                    _document_owner(path) == owner
                    and (
                        document_reads.skill_document(str(path)) == (value.rsplit(":", 1)[-1], "SKILL.md")
                        if prefix == "skill"
                        else _matches_document_path(path, pattern)
                    )
                    for path in _read_paths(tool_name, request, cwd)
                )
                for request in requests
            )
        if tool_name == "Skill":
            active = plugin_resources.plugin_root()
            if plugin != "agent-toolkit":
                active = sibling_plugin_root(plugin, active)
            if active is None or active.resolve() != owner:
                return False
    return True


def row_matches(row: read_triggers.ReadTrigger, tool_name: str, tool_input: dict, cwd: str) -> bool:
    plugin = _document_plugin(str(row.source))
    operations = tuple(
        "skill:" + plugin + ":" + operation.removeprefix("skill:")
        if operation.startswith("skill:") and ":" not in operation.removeprefix("skill:")
        else operation
        for operation in row.operations
    )
    if any(_bound_operation_matches(row, operation, tool_name, tool_input, cwd) for operation in operations):
        return True
    if row.kind == "起動時":
        skill = _skill(row.source)
        if skill:
            return _bound_operation_matches(row, f"skill:{plugin}:{skill}", tool_name, tool_input, cwd)
        if row.source.name.endswith(".subagent.md"):
            return False
        return row.source.resolve() in _read_paths(tool_name, tool_input, cwd)
    return False


def condition_confirmed(row: read_triggers.ReadTrigger, tool_name: str, tool_input: dict, cwd: str) -> bool:
    """操作契機と明示的な資料選択だけを確定し、自然文の判断を代行しない。"""
    if row.kind != "判断" or not row.condition:
        return True
    # 確認ツールの呼出しは、確認本文・選択肢を起草する操作が成立した観測になる。
    if "confirmation" in row.operations and matches("confirmation", tool_name, tool_input, cwd):
        return True
    return any(
        operation.startswith(("read:", "section:")) and _bound_operation_matches(row, operation, tool_name, tool_input, cwd)
        for operation in row.operations
    )


def _user_texts(entry: dict):
    """ホストのユーザー本文だけを取り出す。取得した資料・ツール出力の見出しを役割にしない。"""
    message = entry.get("message") if entry.get("type") == "user" else None
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict) and payload.get("role") == "user":
        message = payload
    if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "user_message":
        if isinstance(payload.get("message"), str):
            yield payload["message"]
        return
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        texts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"} and isinstance(block.get("text"), str)
        ]
        if texts:
            yield "\n".join(texts)


def active_roles(payload: dict, session_id: str, records: dict, agent: str) -> set[pathlib.Path]:
    """正式な起動入力と役割を解決し、圧縮をまたぐ実体指定を保持する。"""
    prefix = "read-trigger-role:"
    if agent != "main":
        return set()
    roles = {
        pathlib.Path(key.removeprefix(prefix)).resolve()
        for key, agents in records.items()
        if key.startswith(prefix) and agent in agents
    }
    if agent in records.get("startup-context-observed", []):
        return roles
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return roles
    lines = transcript.read_transcript_lines(path)
    if lines is None:
        return roles
    for raw in lines:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        for text in _user_texts(entry):
            context_seen = False
            startup_methods = ()
            launch_text = text
            opening = '<atk-auto source="agents-server" kind="delivery">\n'
            if text.startswith(opening) and text.rstrip().endswith("</atk-auto>"):
                launch_text = text[len(opening) : text.rfind("</atk-auto>")]
            for line in launch_text.splitlines()[:1]:
                # 正式な役割見出しの候補だけでfrontmatterの処理を読み込む。
                if ".subagent.md" not in line:
                    continue
                role = task_documents.launched_task_document(line)
                role = role.resolve() if role is not None else None
                if role is not None and role.is_file() and role.parent == plugin_resources.plugin_root().resolve() / "share":
                    roles.add(role)
                    update_state(session_id, operation_skills.mark_ready(prefix + str(role), agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        startup_methods = read_triggers.startup_targets(
                            role,
                            plugin_resources.plugin_root(),
                            host="codex" if is_codex_payload(payload) else "claude",
                            project=payload.get("cwd"),
                        )
                    context_seen = True
            for match in re.finditer(r'<atk-auto source="read-delivery" kind="startup-document">\n資料JSON: ([^\n]+)', text):
                metadata = json.loads(match[1])
                selectors = metadata.get("selectors", [metadata.get("selector", "")])
                input_target = any(isinstance(selector, str) and selector.startswith("input:") for selector in selectors)
                if not input_target and not startup_methods:
                    continue
                target = read_triggers.ReadTarget(pathlib.Path(metadata["path"]), metadata["heading"])
                if input_target or target in startup_methods:
                    context_seen = True
                    if input_target:
                        key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                        update_state(session_id, operation_skills.mark_ready(key, agent))
                    with contextlib.suppress(OSError, UnicodeError, ValueError):
                        if agent not in records.get(rules_context.READ_CONTEXT_RESET_KEY, []):
                            delivered = text[match.end() :]
                            owner = _document_owner(target.path)
                            previous = []
                            bodies = []
                            # 資料中のタグの用例を包装の終わりと誤認しないよう、本文digestが一致する境界を使う。
                            for ending in re.finditer(r"(?m)^[ \t]*</atk-auto>[ \t]*(?:\r?\n|$)", delivered):
                                body_lines = delivered[: ending.start()].removeprefix("\n").splitlines(keepends=True)
                                for label in ("資料:", "本文識別子:", "相対参照の基点:"):
                                    if body_lines and body_lines[0].startswith(label):
                                        body_lines.pop(0)
                                body = "".join(body_lines).removesuffix("\n")
                                normalized = body.replace(str(owner), "${CLAUDE_PLUGIN_ROOT}") if owner else body
                                if not (
                                    document_reads.bytes_fingerprint(body.encode("utf-8")) == metadata.get("body_sha256")
                                    or not target.heading
                                    and document_reads.bytes_fingerprint(normalized.encode("utf-8")) == metadata.get("sha256")
                                ):
                                    continue
                                previous = [
                                    item
                                    for item in document_reads.receipt_candidates([target], bodies={target.path: normalized})
                                    if not target.heading or item["heading"]
                                ]
                                for item in previous:
                                    item["version"] = metadata["sha256"]
                                bodies.append(normalized)
                                break
                            if owner is not None:
                                delivered = delivered.replace(str(owner), "${CLAUDE_PLUGIN_ROOT}")
                            _record_receipts(
                                session_id,
                                agent,
                                [*previous, *document_reads.receipt_candidates([target])],
                                [delivered, *bodies],
                            )
            # 保存済みsessionの正式な参照だけの配送も、同じ実体指定として受理する。
            legacy = re.search(
                r'<atk-auto source="agents-server" kind="work-contract-reference">\n全体作業契約: ([^\n]+)\n', text
            )
            if legacy:
                contract = pathlib.Path(legacy[1])
                if contract.is_absolute():
                    context_seen = True
                    metadata = {"path": str(contract.resolve()), "heading": "", "selectors": ["input:作業契約"]}
                    key = "startup-input-target:" + json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
                    update_state(session_id, operation_skills.mark_ready(key, agent))
            if context_seen:
                update_state(session_id, operation_skills.mark_ready("startup-context-observed", agent))
                return roles
    return roles


def _project_skill_rows(
    records: dict,
    agent: str,
    paths: tuple[pathlib.Path, ...] = (),
    *,
    known_sources: set[pathlib.Path] | None = None,
    host: str = "claude",
    project: str = "",
) -> list[read_triggers.ReadTrigger]:
    """選択した資料と、同じ主体が取得したplugin資料の宣言を使う。"""
    rows: list[read_triggers.ReadTrigger] = []
    selected = set(paths)
    origins: dict[pathlib.Path, list[dict]] = {}
    for key, owners in records.items():
        if agent not in owners:
            continue
        if key.startswith("resolved-project-skill:"):
            selected.add(pathlib.Path(key.removeprefix("resolved-project-skill:")))
        elif key.startswith("selected-read-target:"):
            # 未読の起動資料も、選択元の現行宣言を次要求で再解決する。
            # 本文の参照先変更はこの後のparse_tablesへ反映し、保存時のtargetsを復活させない。
            recorded_origins = json.loads(key.removeprefix("selected-read-target:")).get("declarations", [])
            if isinstance(recorded_origins, list):
                for origin in recorded_origins:
                    source = origin.get("source") if isinstance(origin, dict) else None
                    if isinstance(source, str) and pathlib.Path(source).is_absolute():
                        origins.setdefault(pathlib.Path(source).resolve(), []).append(origin)
        elif key.startswith(document_reads.READ_TRIGGER_PREFIX):
            value, separator, fingerprint = key.removeprefix(document_reads.READ_TRIGGER_PREFIX).rpartition("#sha256:")
            if separator and len(fingerprint) == 64:
                path = pathlib.Path(value)
                if (
                    path.is_file()
                    and _document_owner(path) is not None
                    and document_reads.content_fingerprint(path) == fingerprint
                ):
                    selected.add(path)
    selected = {path.resolve() for path in selected}
    for path in sorted(selected | origins.keys()):
        if path.suffix != ".md" or not path.is_file() or path in (known_sources or set()):
            continue
        explicit = "resolved-project-skill:" + str(path)
        if _document_owner(path) is not None or agent in records.get(explicit, []):
            # 未読の宣言元は選択済みの行だけを復元し、別工程の行を有効にしない。
            rows.extend(
                row
                for row in read_triggers.parse_tables(path, plugin_resources.plugin_root(), host=host, project=project)
                if path in selected or _declaration_identity(row) in origins.get(path, [])
            )
    return rows


def document_key(path: pathlib.Path, fingerprint: str = "") -> str:
    return document_reads.read_trigger_key(path, fingerprint or document_reads.content_fingerprint(path))


def dynamic_targets(
    row: read_triggers.ReadTrigger,
    payload: dict,
    tool_name: str,
    tool_input: dict,
    records: dict,
    agent: str,
    session_id: str = "",
) -> list[read_triggers.ReadTarget]:
    """宣言された動的資料を、実行時の実在入力だけから解決する。"""
    if not row.selector:
        return []
    if row.selector == "project-skill":
        targets: list[read_triggers.ReadTarget] = []
        for path in _read_paths(tool_name, tool_input, str(payload.get("cwd", ""))):
            if path.name != "SKILL.md" or not path.is_file():
                continue
            source = read_triggers.resolve_read_source(
                path,
                plugin_resources.plugin_root(),
                "codex" if is_codex_payload(payload) else "claude",
                project=payload.get("cwd"),
            )
            declared = read_triggers.parse_tables(
                source,
                plugin_resources.plugin_root(),
                host="codex" if is_codex_payload(payload) else "claude",
                project=payload.get("cwd"),
            )
            if session_id:
                update_state(session_id, operation_skills.mark_ready("resolved-project-skill:" + str(source), agent))
            targets.extend(target for item in declared if item.kind == "起動時" for target in item.targets)
            targets.append(read_triggers.ReadTarget(source))
        return targets
    if row.selector.startswith("input:"):
        targets = []
        for key, agents in records.items():
            if key.startswith("startup-input-target:") and agent in agents:
                item = json.loads(key.removeprefix("startup-input-target:"))
                if row.selector in item.get("selectors", [item.get("selector", "")]):
                    targets.append(read_triggers.ReadTarget(pathlib.Path(item["path"]), item["heading"]))
        source = read_triggers.INPUT_SELECTOR_RESOLUTION_SOURCES.get(row.selector)
        if not targets and source == "main-work-contract" and agent == "main":
            path = work_contract.record_path(session_id)
            if path is not None and path.is_file():
                targets.append(read_triggers.ReadTarget(path))
            elif path is not None:
                raise ValueError(
                    f"{row.source}:{row.line}: {row.selector}の作業契約が無い: {path}。"
                    "agent-toolkit/share/work-contract.md「作成と所有」に従って作成せよ。"
                )
        if not targets:
            raise ValueError(f"{row.source}:{row.line}: {row.selector}の起動時配送入力が無い")
        return targets
    if row.selector == "same-document":
        return [read_triggers.ReadTarget(row.source)]
    if row.selector == "role-contract":
        role = tool_input.get("subagent_md_path")
        if not isinstance(role, str):
            return []
        return list(
            read_triggers.parent_contract_targets(
                role,
                plugin_resources.plugin_root(),
                host="codex" if is_codex_payload(payload) else "claude",
                project=payload.get("cwd"),
            )
        )
    if row.selector == "review-reference":
        outputs = [
            pathlib.Path(key.removeprefix("review-output:"))
            for key, agents in records.items()
            if key.startswith("review-output:") and agent in agents
        ]
        unread = [path for path in outputs if agent not in records.get("review-output-parsed:" + document_key(path), [])]
        if unread:
            if any(path in _read_paths(tool_name, tool_input, str(payload.get("cwd", ""))) for path in unread):
                return []
            raise ValueError(f"prepareの保存JSONを末尾まで取得せよ: {unread}")
        paths = [
            key.removeprefix("review-reference:")
            for key, agents in records.items()
            if key.startswith("review-reference:") and agent in agents
        ]
        return [read_triggers.ReadTarget(pathlib.Path(path)) for path in paths if pathlib.Path(path).is_file()]
    if row.selector == "artifact-standards":
        cwd = payload.get("cwd", "")
        targets = []
        host = "codex" if is_codex_payload(payload) else "claude"
        provider = document_reads.document_plugin(str(row.source))
        selection = read_triggers.resolve_document(
            f"{provider}:writing-standards", row.source, plugin_resources.plugin_root(), host, project=cwd
        )
        if selection is not None and selection.is_file():
            targets.append(read_triggers.ReadTarget(selection))
        for candidate in declarations(cwd, host=host):
            if candidate.kind == "操作" and any(
                operation.startswith(("path:", "write:", "dependency:")) and matches(operation, tool_name, tool_input, cwd)
                for operation in candidate.operations
            ):
                targets.extend(candidate.targets)
        return list(dict.fromkeys(targets))
    if row.selector == "external-pytilpack-index":
        if is_codex_payload(payload):
            # CodexにはWebFetchの観測契約がない。取得手段は本文の条件に従って選ぶ。
            return []
        index_ready = agent in records.get("external-document:https://ak110.github.io/pytilpack/llms.txt", [])
        pages = [
            key
            for key, agents in records.items()
            if key.startswith("external-document:https://ak110.github.io/pytilpack/")
            and not key.endswith(("/llms.txt", "/llms-full.txt"))
            and agent in agents
        ]
        if index_ready and pages:
            return []
        raise ValueError(f"{row.source}:{row.line}: 外部資料の索引と対象モジュールをWebFetchで取得せよ。ローカル配送の対象外")
    raise ValueError(f"未登録の資料選択子: {row.selector}")


def _mark(session_id: str, path: pathlib.Path, agent: str, fingerprint: str = "", *, record_scopes: bool = True) -> None:
    identifier = document_key(path, fingerprint)
    update_state(session_id, operation_skills.mark_ready(identifier, agent))
    if record_scopes and (not fingerprint or fingerprint == document_reads.content_fingerprint(path)):
        for metadata in document_reads.receipt_candidates([read_triggers.ReadTarget(path)]):
            update_state(session_id, operation_skills.mark_ready(document_reads.receipt_key(metadata), agent))
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    outstanding = [
        target
        for target in selected_targets(records, agent)
        if agent in records.get("read-guidance:" + document_key(target.path), []) and not target_ready(records, target, agent)
    ]
    if not outstanding:
        notice_module.resolve_notice(session_id, "pretooluse", "_deliver_documents", subject=agent)
    if agent in records.get("review-output:" + str(path), []):
        raw = path.read_bytes()
        if fingerprint and document_reads.bytes_fingerprint(raw) != fingerprint:
            return
        _review_reference(session_id, json.loads(raw), agent)
        update_state(session_id, operation_skills.mark_ready("review-output-parsed:" + document_key(path, fingerprint), agent))

        def parsed(state: dict) -> dict:
            rows = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
            key = "review-output:" + str(path)
            if key in rows:
                rows[key] = [owner for owner in rows[key] if owner != agent]
                if not rows[key]:
                    del rows[key]
            return state

        update_state(session_id, parsed)


def _review_reference(session_id: str, prepared: object, agent: str) -> None:
    """prepareの成功JSONが示す実在する資料だけを登録する。"""
    if not isinstance(prepared, dict):
        raise ValueError("session-review-prepareの結果がJSON objectでない")
    value = prepared.get("reference_document")
    if value is None or value == "":
        return
    path = pathlib.Path(value) if isinstance(value, str) else None
    if path is None or not path.is_absolute() or not path.is_file():
        raise ValueError(f"reference_documentの実体を解決できない: {value}")
    update_state(session_id, operation_skills.mark_ready("review-reference:" + str(path), agent))


def target_ready(records: dict, target: read_triggers.ReadTarget, agent: str, *, invocation: bool = False) -> bool:
    if not invocation:
        return document_reads.acquisition_state(records, target, agent) != "missing"
    raw = target.path.read_bytes()
    if target.heading:
        try:
            target.body(raw.decode("utf-8"))
        except ValueError:
            return False
    key = document_reads.skill_invocation_key if invocation else document_reads.read_trigger_key
    owners = records.get(key(target.path, document_reads.bytes_fingerprint(raw)), [])
    return isinstance(owners, list) and agent in owners


def _declaration_identity(row: read_triggers.ReadTrigger) -> dict:
    """行移動や参照見出しの改名をまたいで、選択元の宣言を識別する。"""
    return {"source": str(row.source), "operations": list(row.operations), "kind": row.kind, "selector": row.selector}


def selected_targets(
    records: dict, agent: str, *, rows: list[read_triggers.ReadTrigger] | None = None
) -> list[read_triggers.ReadTarget]:
    targets: list[read_triggers.ReadTarget] = []
    for key, agents in records.items():
        if key.startswith("selected-read-target:") and agent in agents:
            raw = json.loads(key.removeprefix("selected-read-target:"))
            if rows is not None and raw.get("declarations"):
                # 保存時の見出しを再利用せず、現在の宣言が示す対象を解決する。
                targets.extend(
                    target for row in rows if _declaration_identity(row) in raw["declarations"] for target in row.targets
                )
                continue
            path = pathlib.Path(raw["path"])
            if not path.is_file():
                continue
            target = read_triggers.ReadTarget(path, raw["heading"])
            if target.heading:
                try:
                    target.body()
                except ValueError:
                    # 選択後に資料から消えた見出しは全文を読んでも取得済みにならないため、
                    # 保存時の`_select_targets`と同じく選択から外す。
                    continue
            targets.append(target)
    return list(dict.fromkeys(targets))


def _unmark(identifier: str, agent: str) -> Callable[[dict], dict | None]:
    """記録キーから主体を除く状態更新を返す。"""

    def mutate(state: dict) -> dict | None:
        recorded = state.get(rules_context.OPERATION_SKILL_READY_KEY)
        if not isinstance(recorded, dict):
            return None
        agents = recorded.get(identifier)
        if not isinstance(agents, list) or agent not in agents:
            return None
        state[rules_context.OPERATION_SKILL_READY_KEY] = {
            **recorded,
            identifier: [item for item in agents if item != agent],
        }
        return state

    return mutate


def _select_targets(
    session_id: str,
    targets: list[read_triggers.ReadTarget],
    agent: str,
    *,
    rows: list[read_triggers.ReadTrigger] | None = None,
) -> None:
    current = {target.path for target in targets}
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    for key, owners in records.items():
        if key.startswith("selected-read-target:") and agent in owners:
            old = json.loads(key.removeprefix("selected-read-target:"))
            path = pathlib.Path(old["path"])
            if path in current:
                update_state(session_id, _unmark(key, agent))
    for target in targets:
        if target.heading:
            try:
                target.body()
            except ValueError:
                # 不正な宣言を保持すると、宣言を復旧した後にも旧見出しが残る。
                continue
        selected: dict[str, object] = {"path": str(target.path), "heading": target.heading}
        origins = [_declaration_identity(row) for row in rows or () if target in row.targets]
        if origins:
            selected["declarations"] = origins
        value = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
        update_state(session_id, operation_skills.mark_ready("selected-read-target:" + value, agent))


def _codex_dependency_repair(tokens: tuple[str, ...], error: Exception) -> bool:
    """未導入のBasicsだけを、導入済みToolkitと同じmarketplaceから追加できる。"""
    if str(error).casefold() != "codexのagent-basics導入先が一意でない: 0件":
        return False
    if tokens[1:3] != ("plugin", "add") or len(tokens) != 5 or tokens[4] != "--json":
        return False
    if not tokens[3].startswith("agent-basics@"):
        return False
    try:
        installed = codex_installed_plugins(tokens[0])
    except PluginRootError:
        return False
    marketplaces = {
        item["marketplaceName"]
        for item in installed
        if isinstance(item, dict)
        and item.get("name") == "agent-toolkit"
        and item.get("installed") is True
        and item.get("enabled") is True
        and isinstance(item.get("marketplaceName"), str)
        and item["marketplaceName"]
    }
    return len(marketplaces) == 1 and tokens[3] == "agent-basics@" + next(iter(marketplaces))


def _declaration_recovery_operation(tool_name: str, tool_input: dict, error: Exception) -> bool:
    """読取・公開診断と、原因に対応する正規の依存追加を宣言取得から独立させる。"""
    if not isinstance(error, PluginRootError):
        return False
    if tool_name in {"Read", "Glob", "Grep", "mcp__agents_server__list"}:
        return True
    command = tool_input.get("command")
    if tool_name != "Bash" or not isinstance(command, str):
        return False
    # 回復用の例外では、単一の静的な呼出しだけを受理する。
    if any(char in command for char in ("\n", "\r")):
        return False
    commands = split_bash_commands(command)
    if len(commands) != 1:
        return False
    try:
        words = split_words(commands[0][1])
    except ValueError:
        return False
    if any(word.expansion or word.redirection or set("()<>") & set(word.unquoted) for word in words):
        return False
    tokens = tuple(words)
    if not tokens:
        return False
    name = pathlib.Path(tokens[0]).name
    if name in {"cat", "cat.exe"}:
        return len(tokens) > 1
    if sed_read_range(tokens) is not None:
        return True
    if name in {"codex", "codex.exe"}:
        return (
            tokens[1:3] == ("plugin", "list")
            and all(token in {"--json", "--help"} for token in tokens[3:])
            or tokens[1:] == ("plugin", "add", "--help")
            or _codex_dependency_repair(tokens, error)
        )
    if is_atk_command(tokens):
        return tokens[1:2] in {("info",), ("--help",)} or tokens[1:3] in {
            ("agents", "logs"),
            ("agents", "list"),
            ("agents", "status"),
        }
    return False


@_input_scope()
def deliver(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    """現在の操作に関係する宣言資料を配送または案内し、他の担当の手順書の取得へ警告を加え、通常操作の進行を保つ。"""
    handoff = delegated_session.handoff_path(os.environ)
    written = operation_skills.written_paths(tool_name, tool_input, str(payload.get("cwd", "")))
    if handoff and written and all(pathlib.Path(path).resolve() == pathlib.Path(handoff).resolve() for path in written):
        return Decision()
    _refresh_receipts(payload, tool_name, tool_input, session_id)
    decision = _deliver_documents(payload, tool_name, tool_input, session_id)
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    updates = []
    has_receipts = any(
        key.startswith((document_reads.RECEIPT_PREFIX, document_reads.READ_TRIGGER_PREFIX)) and agent in actors
        for key, actors in records.items()
        if isinstance(actors, list)
    )
    current_targets = _receipt_targets(payload, tool_name, tool_input, session_id, active_only=True) if has_receipts else []
    for target in dict.fromkeys(current_targets):
        if document_reads.acquisition_state(records, target, agent) != "updated":
            continue
        metadata = next(
            item
            for item in document_reads.receipt_candidates([target], include_headings=False)
            if item["heading"] == target.heading
        )
        key = "read-update:" + document_reads.receipt_key(metadata)
        if agent in records.get(key, []):
            continue
        text = _llm_notice(
            f"取得済み資料の更新あり: `{target}`。旧版の取得記録を保持している。",
            tag=_WARN_TAG,
            removable_cause=True,
            escalate_on_repeat=False,
            fix=acquisition_step(
                target, operation_skills.resolve_subject(payload), reference=True, cwd=str(payload.get("cwd", ""))
            ),
        )
        notice_module.stage_delivery(text, session_id, rules_context.OPERATION_SKILL_READY_KEY, key, agent)
        updates.append(text)
    decision = dataclasses.replace(decision, notices=(*decision.notices, *updates))
    if decision.block is not None:
        return decision
    candidate_notice = _condition_candidate_notice(payload, tool_name, tool_input, session_id)
    if candidate_notice:
        decision = dataclasses.replace(decision, notices=(*decision.notices, candidate_notice))
    notice = _foreign_task_document_notice(payload, tool_name, tool_input, session_id)
    return dataclasses.replace(decision, notices=(*decision.notices, notice)) if notice else decision


def _condition_candidate_notice(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> str:
    """機械確定できない判断条件を、案内済み遮断の集合へ加えずに提示する。"""
    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return ""
    cwd = str(payload.get("cwd", ""))
    handoff = delegated_session.handoff_path(os.environ)
    written = operation_skills.written_paths(tool_name, tool_input, cwd)
    if handoff and written and all(pathlib.Path(path).resolve() == pathlib.Path(handoff).resolve() for path in written):
        return ""
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    roles = active_roles(payload, session_id, records, agent) | _subagent_task_documents(payload)
    try:
        rows = declarations(cwd, host="codex" if is_codex_payload(payload) else "claude", session_id=session_id)
        candidates = [
            row
            for row in rows
            if (not row.source.name.endswith(".subagent.md") or row.source in roles)
            and row_matches(row, tool_name, tool_input, cwd)
            and not condition_confirmed(row, tool_name, tool_input, cwd)
            and any(not target_ready(records, target, agent) for target in row.targets)
        ]
    except OSError, UnicodeError, ValueError:
        # 宣言の取得失敗は本体の診断が通知する。
        return ""
    if not candidates:
        return ""
    shown = "\n".join(
        f"- 条件: {row.condition}。資料: {'、'.join(str(target) for target in row.targets)}（宣言: {row.source}:{row.line}）"
        for row in candidates
    )
    return _llm_notice(
        "現在の操作に関連する読込条件の候補。条件の成立は未確定である。\n" + shown,
        tag=_WARN_TAG,
        fix="自身の役割と現工程で各条件が成立するか確認し、成立する資料の必要節と前提・例外を判断・操作の前に読め。",
        removable_cause=True,
        escalate_on_repeat=False,
    )


def _subagent_task_documents(payload: dict) -> set[pathlib.Path]:
    """Claude Codeの`Agent`ツールで起動したサブエージェントが受領した役割文書を、自身の記録の最初の依頼本文から返す。

    `transcript_path`はサブエージェント内のhookでもセッション本体の記録を指すため、`agent_id`から自身の記録を求める。
    起動見出しの1行目か、依頼本文が絶対パスで指すagent-toolkitの役割文書を、そのサブエージェントの担当とみなす。
    """
    transcript_path = payload.get("transcript_path")
    agent = payload.get("agent_id")
    session_id = payload.get("session_id")
    if not all(isinstance(value, str) and value for value in (transcript_path, agent, session_id)):
        return set()
    record = pathlib.Path(str(transcript_path)).parent / str(session_id) / "subagents" / f"agent-{agent}.jsonl"
    lines = transcript.read_transcript_lines(str(record)) if record.is_file() else None
    for raw in lines or ():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        for text in _user_texts(entry) if isinstance(entry, dict) else ():
            launched = task_documents.launched_task_document(text)
            documents = [launched] if launched is not None else task_documents.find_task_documents(text)
            return {document.resolve() for document in documents}
    return set()


def _foreign_task_document_notice(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> str | None:
    """Plugin root配下の他の担当の手順書（`share/<役割名>.subagent.md`）を取得する操作に、非遮断の警告を返す。

    `agent-toolkit:delegation`の`SKILL.md`「送信」は委譲元が起動準備のために手順書の本文を読むことを禁じるが、
    事後の振り返りだけでは読んだ時点に作用しないため、読取の直前に起動契約へ誘導する。
    読む主体の担当がその役割である場合は警告しない。plugin root外の同名ファイルは対象外とする。
    編集と返却後の検収を目的の判定なしには区別できないため、遮断せずに続行の条件を示す。
    """
    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return None
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    try:
        root = plugin_resources.plugin_root().resolve()
    except OSError, PluginRootError:
        return None
    share = root / "share"
    command = tool_input.get("command")
    candidates = (
        [path.resolve() for group in bash_read_paths(command, cwd, include_partial=True, include_piped=True) for path in group]
        if tool_name == "Bash" and isinstance(command, str)
        else _read_paths(tool_name, tool_input, cwd)
    )
    paths = [
        path
        for path in dict.fromkeys(candidates)
        if path.parent == share and path.name.endswith(task_documents.TASK_DOCUMENT_SUFFIX) and path.is_file()
    ]
    if not paths:
        return None
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    own = active_roles(payload, session_id, records, agent) if agent == agent_id.MAIN_AGENT_ID else set()
    own |= _subagent_task_documents(payload) if agent != agent_id.MAIN_AGENT_ID else set()
    foreign = [path for path in paths if path not in own]
    if not foreign:
        return None
    contracts: list[str] = []
    for path in foreign:
        try:
            targets = read_triggers.parent_contract_targets(path.name, root)
        except OSError, ValueError:
            targets = ()
        contracts.extend(
            f"`{target.path}`" + (f"「{target.heading}」" if target.heading else "")
            for target in targets
            if f"`{target.path}`" not in contracts
        )
    shown = "、".join(f"`{path}`" for path in foreign)
    guidance = f"起動契約: {'、'.join(contracts)}。" if contracts else ""
    return _llm_notice(
        f"他の担当の手順書{shown}を取得しようとしている。`agent-toolkit:delegation`の`SKILL.md`「送信」は、"
        "委譲元が起動準備のために`<役割名>.subagent.md`の本文を読むことを禁じている。",
        tag=_WARN_TAG,
        fix=(
            "その担当を起動するなら、手順書ではなく起動契約（`起動対象:`でその手順書を宣言する`<役割名>.parent.md`、"
            f"または呼び出し元へ統合した契約）と、その生成済みの返却契約を読め。{guidance}"
            "起動準備なら、手順書の分割読取の続きを行わず呼出側契約へ進め。"
            "手順書自体の編集と、返却を受け取った後の固有手順の検収であれば、このまま続行してよい。"
        ),
        removable_cause=True,
        escalate_on_repeat=False,
    )


def _deliver_documents(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    """現在の操作に関係する宣言資料を配送または案内する。"""
    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return Decision()
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    handoff = delegated_session.handoff_path(os.environ)
    written = operation_skills.written_paths(tool_name, tool_input, cwd)
    temporary_code = bool(written) and all(
        _temporary_path(path) and pathlib.Path(path).suffix.lower() not in _PROSE_SUFFIXES for path in written
    )
    if temporary_code:
        written = []
    if handoff and written and all(pathlib.Path(path).resolve() == pathlib.Path(handoff).resolve() for path in written):
        return Decision()
    diagnostic = operation_skills.is_diagnostic_operation(tool_name, tool_input, cwd)
    warning_only = tool_name in {"AskUserQuestion", "ExitPlanMode", "request_user_input", "request_user_input_async"}
    try:
        rows = declarations(cwd, host="codex" if is_codex_payload(payload) else "claude", session_id=session_id)
    except (OSError, UnicodeError, ValueError) as error:
        if warning_only:
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込宣言を取得できない: {error}",
                        tag=_WARN_TAG,
                        fix="回答に依存する次の操作の前に宣言を復旧し、質問の前提を確認せよ。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        if _declaration_recovery_operation(tool_name, tool_input, error):
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込宣言を取得できない: {error}",
                        tag=_WARN_TAG,
                        fix="読取・導入診断と正規の依存追加で宣言を復旧し、取得結果を確かめてから判断・その他の変更へ進め。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        recovery_paths = operation_skills.written_paths(tool_name, tool_input, cwd) + [
            str(path) for path in _read_paths(tool_name, tool_input, cwd)
        ]
        if any(str(error).startswith(path + ":") for path in recovery_paths):
            return Decision(
                notices=(
                    _llm_notice(
                        f"読込宣言の復旧対象を操作する: {error}",
                        tag=_WARN_TAG,
                        fix="宣言を復旧した後に元の操作を再発行せよ。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        return Decision(
            notices=(
                _llm_notice(
                    f"読込宣言を取得できない: {error}",
                    tag=_WARN_TAG,
                    fix="宣言と資料の所在を診断し、復旧後に必要な資料を読め。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    roles = active_roles(payload, session_id, records, agent) | _subagent_task_documents(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    try:
        rows = list(
            dict.fromkeys(
                [
                    *rows,
                    *_project_skill_rows(
                        records,
                        agent,
                        tuple(_read_paths(tool_name, tool_input, cwd)),
                        known_sources={row.source for row in rows},
                        host="codex" if is_codex_payload(payload) else "claude",
                        project=cwd,
                    ),
                ]
            )
        )
        # 現在の役割で除外した宣言は、選択履歴の保存・再接続にも使用しない。
        rows = [row for row in rows if not row.source.name.endswith(".subagent.md") or row.source in roles]
        active_rows = [
            row
            for row in rows
            if condition_confirmed(row, tool_name, tool_input, cwd)
            and (
                row_matches(row, tool_name, tool_input, cwd)
                or row.selector == "artifact-standards"
                and target_ready(records, read_triggers.ReadTarget(row.source), agent)
                and bool(_artifact_written_paths(tool_name, tool_input, cwd))
                or row.source in roles
                and (row.kind == "起動時" or any(operation.startswith("role:") for operation in row.operations))
            )
        ]
        selected_origins = [
            origin
            for key, owners in records.items()
            if key.startswith("selected-read-target:") and agent in owners
            for origin in json.loads(key.removeprefix("selected-read-target:")).get("declarations", [])
        ]
        selection_rows = list(
            dict.fromkeys([*active_rows, *(row for row in rows if _declaration_identity(row) in selected_origins)])
        )
        host = "codex" if is_codex_payload(payload) else "claude"
        subject = operation_skills.resolve_subject(payload)
        active_targets = {
            row: (*row.targets, *dynamic_targets(row, payload, tool_name, tool_input, records, agent, session_id))
            for row in active_rows
        }
        invocation_paths = {
            target.path
            for row in selection_rows
            if not document_reads.reference_only([row.operations])
            for target in active_targets.get(row, row.targets)
            if not target.heading and requires_skill_invocation(target.path, host=host, subject=subject, cwd=cwd)
        }
        # 動的な起動対象は従来どおりパスで持ち越す。名指し参照の選択元がある場合だけ資料参照として扱う。
        invocation_paths.update(
            target.path
            for target in selected_targets(records, agent, rows=rows)
            if not target.heading
            and requires_skill_invocation(target.path, host=host, subject=subject, cwd=cwd)
            and not document_reads.reference_only([row.operations for row in selection_rows if target in row.targets])
        )

        def ready(target: read_triggers.ReadTarget) -> bool:
            return target_ready(records, target, agent, invocation=target.path in invocation_paths)

        def guidance_key(target: read_triggers.ReadTarget) -> str:
            key = (
                document_reads.skill_invocation_key(target.path, document_reads.content_fingerprint(target.path))
                if target.path in invocation_paths
                else document_key(target.path)
            )
            return "read-guidance:" + key

        pending = list(dict.fromkeys(target for row in active_rows for target in active_targets[row] if not ready(target)))
        # 案内済みで未取得の資料は、取得するまで次の操作でも案内する。
        # 文脈境界（会話圧縮・clear）の前に選んだ資料は候補であり、境界後の操作ごとに再掲しない。
        # 残る工程で必要な資料は、現在の操作の宣言が改めて選んだ時点で案内する。
        carried = [
            target
            for target in selected_targets(records, agent, rows=rows)
            if not temporary_code
            and target not in pending
            and not ready(target)
            and agent not in records.get(rules_context.carried_read_target_key(target.path), [])
        ]
        pending.extend(carried)
        method_sources = dict.fromkeys([*_read_paths(tool_name, tool_input, cwd), *sorted(roles)])
        for path in method_sources:
            if path.suffix == ".md" and path.is_file():
                pending.extend(
                    target
                    for target in read_triggers.reference_method_targets(
                        path, plugin_resources.plugin_root(), host, project=cwd
                    )
                    if target not in pending and not ready(target)
                )
        # スキル本文とともに届く共有の初動資料を、同じ受領単位へ含める。
        cursor = 0
        while cursor < len(pending):
            target = pending[cursor]
            cursor += 1
            pending.extend(
                method
                for method in read_triggers.reference_method_targets(
                    target.path, plugin_resources.plugin_root(), host, project=cwd
                )
                if method not in pending and not ready(method)
            )
            if not target.heading:
                rows = list(
                    dict.fromkeys(
                        [
                            *rows,
                            *_project_skill_rows(
                                records,
                                agent,
                                (target.path,),
                                known_sources={row.source for row in rows},
                                host="codex" if is_codex_payload(payload) else "claude",
                                project=cwd,
                            ),
                        ]
                    )
                )
            target_identity = document_reads.skill_document(str(target.path))
            for row in rows:
                if (
                    row.kind == "起動時"
                    and row.source == target.path
                    and target_identity is not None
                    and target_identity[1] == "SKILL.md"
                    and not target.heading
                ):
                    pending.extend(item for item in row.targets if item not in pending and not ready(item))
        # 選択資料の解析で追加した宣言にも、同じ役割境界を適用する。
        rows = [row for row in rows if not row.source.name.endswith(".subagent.md") or row.source in roles]
    except (OSError, ValueError) as error:
        if isinstance(error, FileNotFoundError) and isinstance(error.filename, str):
            missing = read_triggers.ReadTarget(pathlib.Path(error.filename))
            return Decision(
                notices=(
                    _llm_notice(
                        f"資料`{missing.path}`は存在しない。",
                        tag=_WARN_TAG,
                        fix=(
                            "資料と読込宣言の参照先を復旧せよ。復旧できない場合、委譲先は委譲元へ、それ以外はユーザーへ報告せよ"
                        ),
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        if warning_only:
            return Decision(
                notices=(
                    _llm_notice(
                        f"動的資料を解決できない: {error}",
                        tag=_WARN_TAG,
                        fix="回答に依存する次の操作の前に入力と資料を確定し、質問の前提を確認せよ。",
                        removable_cause=True,
                        escalate_on_repeat=False,
                    ),
                )
            )
        return Decision(
            notices=(
                _llm_notice(
                    f"動的資料を解決できない: {error}",
                    tag=_WARN_TAG,
                    fix="実在する入力と取得対象を確定し、該当する資料を読め。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    if not pending:
        return Decision()
    # 現在の操作が改めて対象にした資料は持ち越しの扱いを外し、圧縮前と同じ限定遮断の対象へ戻す。
    for target in pending:
        carried_key = rules_context.carried_read_target_key(target.path)
        if target not in carried and agent in records.get(carried_key, []):
            update_state(session_id, _unmark(carried_key, agent))
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    command = tool_input.get("command")
    if tool_name in {"Read", "Skill"} or (tool_name == "Bash" and isinstance(command, str) and _pure_bash_read(command, cwd)):
        _select_targets(session_id, pending, agent, rows=selection_rows)
        return Decision()
    acquisition_paths = _read_paths(tool_name, tool_input, cwd)
    if any(row.selector == "project-skill" and row_matches(row, tool_name, tool_input, cwd) for row in rows):
        # 選択したSKILLの初動資料を案内し、取得操作は遮らずPostToolUseで観測する。
        _select_targets(session_id, pending, agent, rows=selection_rows)
        pending = [target for target in pending if target.path not in acquisition_paths]
        if not pending:
            return Decision()
    if any(target.path in acquisition_paths for target in pending):
        _select_targets(session_id, pending, agent, rows=selection_rows)
        pending = [target for target in pending if target.path not in acquisition_paths]
        if not pending:
            # 取得操作を許可し、PostToolUseで対象パスへの読取の成功を観測する。
            # 次の依存操作では取得完了を確かめ、未取得なら関連資料を案内する。
            return Decision()
    if warning_only and tool_name != "ExitPlanMode":
        if not agent_id.is_main_agent_context(payload):
            return Decision()
        history_path = payload.get("transcript_path")
        if isinstance(history_path, str) and history_path and transcript.read_transcript_lines(history_path) is None:
            return Decision()
    # 同じ資料の節違いは取得案内をまとめるため、パスごとに1回だけ示す。
    uninvoked = [target for target in pending if target.path in invocation_paths and target_ready(records, target, agent)]
    label = "未起動スキル" if len(uninvoked) == len(pending) else "未取得資料・未起動スキル" if uninvoked else "未取得資料"
    shown = "、".join(dict.fromkeys(f"`{target.path}`" for target in pending))
    subject = operation_skills.resolve_subject(payload)
    if subject == operation_skills.SUBJECT_CODEX:
        methods = "指定されたシェルコマンド"
    else:
        has_skill = any(target.path in invocation_paths for target in pending)
        has_read = any(target.path not in invocation_paths for target in pending)
        methods = "指定されたSkill・Read" if has_skill and has_read else "指定されたSkill" if has_skill else "指定されたRead"
    followup = "読んだ後に、書いた内容をその基準で見直し、必要なら直せ。" if written and not diagnostic else ""
    instruction = (
        f"次の資料を、この操作の結果を使う前に{methods}で取得せよ。"
        f"必要節と前提・例外への到達は必須手順であり省略するな。{followup}"
    )
    failures: dict[read_triggers.ReadTarget, str] = {}
    for target in pending:
        try:
            acquisition_step(target, subject, reference=target.path not in invocation_paths, cwd=cwd)
        except (OSError, UnicodeError, ValueError) as error:
            repairs = {row.source.resolve() for row in rows if target in row.targets} | {target.path}
            writes = operation_skills.written_paths(tool_name, tool_input, cwd)
            if not warning_only and writes and all(pathlib.Path(path).resolve() in repairs for path in writes):
                return Decision(
                    notices=(
                        _llm_notice(
                            f"読込宣言の範囲を復旧する操作を通す: {error}",
                            tag=_WARN_TAG,
                            fix="宣言と実見出しを整合させた後に元の操作を再発行せよ。",
                            removable_cause=True,
                            escalate_on_repeat=False,
                        ),
                    )
                )
            failures[target] = rules_context.acquisition_failure(
                target,
                error,
                shell=subject == operation_skills.SUBJECT_CODEX,
                agent=subject == operation_skills.SUBJECT_SUBAGENT,
                reference=target.path not in invocation_paths,
                cwd=cwd,
            )
    acquisition = (
        "。".join(_merged_steps(pending, failures, subject, invocation_paths=invocation_paths, cwd=cwd))
        + "。判断に必要な未取得部分は行範囲を分けて補え。"
    )
    guided: list[read_triggers.ReadTarget] = []
    if not warning_only and written and not diagnostic:
        # 文脈境界の前に選んだ資料は、現在の操作が改めて対象にするまで案内済みとせず、遮断の根拠にもしない。
        # 境界後に案内した資料の未取得だけで止め、対象パスの読取成功で解除する契約は保つ。
        guided = [
            target for target in pending if agent not in records.get(rules_context.carried_read_target_key(target.path), [])
        ]
        if any(agent in records.get(guidance_key(target), []) for target in guided):
            # 遮断を解除する取得対象だけを示す。持ち越した資料まで並べると、取得しても解除されない資料を読ませる。
            return Decision(
                block=_block_notice(
                    f"案内済みの取得・起動が未完了のため再書込を停止する。{label}: "
                    + "、".join(dict.fromkeys(f"`{target.path}`" for target in guided))
                    + "。",
                    fix="。".join(_merged_steps(guided, failures, subject, invocation_paths=invocation_paths, cwd=cwd))
                    + "。必要節と前提・例外を取得後に元の書込操作を再発行せよ。",
                )
            )
    if warning_only:
        return Decision(
            notices=(
                _llm_notice(
                    f"{instruction}\n操作前に読む資料が未取得: {shown}。\n質問の前提と選択肢を資料の基準に合わせるため。",
                    tag=_WARN_TAG,
                    fix=acquisition + "質問の前提と選択肢を資料の基準で確かめ直せ。",
                    removable_cause=True,
                    escalate_on_repeat=False,
                ),
            )
        )
    _select_targets(session_id, pending, agent, rows=selection_rows)
    notice = _llm_notice(
        f"{instruction}\n{label}: {shown}。\n判断や記述を資料の基準に合わせるため。",
        tag=_WARN_TAG,
        removable_cause=True,
        escalate_on_repeat=False,
        fix=acquisition + followup,
    )
    if not warning_only and written and not diagnostic:
        for target in guided:
            notice_module.stage_delivery(
                notice,
                session_id,
                rules_context.OPERATION_SKILL_READY_KEY,
                guidance_key(target),
                agent,
            )
    return Decision(notices=(notice,))


def _merged_steps(
    targets: list[read_triggers.ReadTarget],
    failures: dict[read_triggers.ReadTarget, str],
    subject: str,
    *,
    invocation_paths: set[pathlib.Path] | None = None,
    cwd: str = "",
) -> list[str]:
    """同じ資料の取得手段を1件へまとめ、注目する節を併記する。

    節ごとに取得案内を並べると、共通の前提まで繰り返し読ませる。
    読取または見出しの解決に失敗した対象は、復旧の案内を個別に残す。
    """
    groups: dict[pathlib.Path | read_triggers.ReadTarget, list[str]] = {}
    for target in targets:
        if target in failures:
            groups[target] = []
        else:
            groups.setdefault(target.path, []).append(target.heading)
    steps = []
    for key, headings in groups.items():
        if isinstance(key, read_triggers.ReadTarget):
            steps.append(failures[key])
            continue
        named = [heading for heading in dict.fromkeys(headings) if heading]
        heading = (
            "" if "" in headings else "".join(value if "「" in value or value == "冒頭" else f"「{value}」" for value in named)
        )
        step = acquisition_step(
            read_triggers.ReadTarget(key, heading),
            subject,
            reference=invocation_paths is not None and key not in invocation_paths,
            cwd=cwd,
        )
        steps.append(step)
    return steps


_PERSISTED_OUTPUT_OPEN = "<persisted-output>"
_PERSISTED_OUTPUT_CLOSE = "</persisted-output>"


def _receipt_targets(
    payload: dict, tool_name: str, tool_input: dict, session_id: str, *, active_only: bool = False
) -> list[read_triggers.ReadTarget]:
    cwd = str(payload.get("cwd", ""))
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    try:
        rows = declarations(cwd, host="codex" if is_codex_payload(payload) else "claude", session_id=session_id)
    except OSError, UnicodeError, ValueError:
        # 宣言の診断と復旧操作の許可は_deliver_documentsが返す。
        rows = []
    targets = [
        target
        for row in rows
        if not active_only
        or condition_confirmed(row, tool_name, tool_input, cwd)
        and row_matches(row, tool_name, tool_input, cwd)
        for target in row.targets
    ]
    targets.extend(selected_targets(records, agent))
    targets.extend(read_triggers.ReadTarget(path) for path in _read_paths(tool_name, tool_input, cwd))
    targets.extend(
        read_triggers.ReadTarget(pathlib.Path(json.loads(key.removeprefix("startup-input-target:"))["path"]))
        for key, actors in records.items()
        if key.startswith("startup-input-target:") and agent in actors
    )
    return list(dict.fromkeys(targets))


def _record_receipts(session_id: str, agent: str, candidates: list[dict], texts: list[str]) -> None:
    observed = set()
    keys = []
    for metadata in document_reads.received_candidates(candidates, texts):
        scope = (metadata["logical"], metadata["heading"], json.dumps(metadata["parts"]))
        if scope in observed:
            continue
        observed.add(scope)
        keys.append(document_reads.receipt_key(metadata))
        path = pathlib.Path(metadata["path"])
        if not metadata["heading"]:
            # 旧キーも保持し、既存の起動・自己編集・参照宣言の消費側を同じ証拠へ接続する。
            if path.is_file() and document_reads.content_fingerprint(path) == metadata["version"]:
                _mark(session_id, path, agent, metadata["version"], record_scopes=False)
            else:
                update_state(session_id, operation_skills.mark_ready(document_key(path, metadata["version"]), agent))
    if keys:
        # 全文読取では多数の節が同時に届くため、状態JSONの読書きと排他を1回にまとめる。
        def mark(value: dict) -> dict:
            for key in keys:
                operation_skills.mark_ready(key, agent)(value)
            return value

        update_state(session_id, mark)


def _refresh_receipts(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    """Preで既知範囲のdigestを準備し、Codexの外側配送後の出力だけを差分で観測する。"""
    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return
    agent = agent_id.resolve_hook_agent_id(payload)
    state = read_state(session_id)
    candidates = state.get("document_read_candidates", {}).get(agent, [])
    current = document_reads.receipt_candidates(_receipt_targets(payload, tool_name, tool_input, session_id))
    if is_codex_payload(payload):
        raw_path = payload.get("agent_transcript_path") if agent != "main" else payload.get("transcript_path")
        if isinstance(raw_path, str) and raw_path:
            path = pathlib.Path(raw_path)
            cursor_key = json.dumps([agent, str(path)])
            offset = state.get("document_read_offsets", {}).get(cursor_key, 0)
            rotated = False
            texts = []
            try:
                with path.open("rb") as stream:
                    if cursor_key not in state.get("document_read_offsets", {}) and agent in state.get(
                        rules_context.OPERATION_SKILL_READY_KEY, {}
                    ).get(rules_context.READ_CONTEXT_RESET_KEY, []):
                        offset = path.stat().st_size
                    if offset > path.stat().st_size:
                        # 切り替わった記録の旧履歴を再受信として扱わない。
                        offset = path.stat().st_size
                        rotated = True
                    stream.seek(offset)
                    for raw in stream:
                        if not raw.endswith(b"\n"):
                            break
                        offset += len(raw)
                        try:
                            entry = json.loads(raw)
                        except ValueError:
                            continue
                        item = entry.get("payload") if isinstance(entry, dict) else None
                        if (
                            isinstance(entry, dict)
                            and entry.get("type") == "response_item"
                            and isinstance(item, dict)
                            and item.get("type") in {"custom_tool_call_output", "function_call_output"}
                        ):
                            output = item.get("output")
                            if isinstance(output, str):
                                texts.extend(document_reads.returned_texts(output))
                            elif isinstance(output, list):
                                texts.extend(
                                    block["text"]
                                    for block in output
                                    if isinstance(block, dict)
                                    and block.get("type") in {"text", "input_text", "output_text"}
                                    and isinstance(block.get("text"), str)
                                )
            except OSError:
                texts = []
            else:
                _record_receipts(session_id, agent, [*candidates, *current], texts)

                def advance(value: dict) -> dict:
                    positions = value.setdefault("document_read_offsets", {})
                    positions[cursor_key] = offset if rotated else max(positions.get(cursor_key, 0), offset)
                    return value

                update_state(session_id, advance)
    if current and current != candidates:

        def prepare(value: dict) -> dict:
            value.setdefault("document_read_candidates", {})[agent] = current
            return value

        update_state(session_id, prepare)


def _observe_content(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    if tool_name not in {"Read", "Bash"} or is_codex_payload(payload) or payload.get("hook_event_name") == "PermissionDenied":
        return
    response = payload.get("tool_response")
    texts = document_reads.returned_texts(response)
    if not texts:
        return
    agent = agent_id.resolve_hook_agent_id(payload)
    state = read_state(session_id)
    candidates = state.get("document_read_candidates", {}).get(agent, [])
    current = document_reads.receipt_candidates(_receipt_targets(payload, tool_name, tool_input, session_id))
    _record_receipts(session_id, agent, [*candidates, *current], texts)


def host_truncated(response: object) -> bool:
    """ホストが読取結果に付ける省略の表現だけで、省略の有無を判定する。

    資料の本文に省略を思わせる語句（`truncated output`など）が含まれていても省略とはしない。
    判定する表現は、Codexの応答先頭の包絡、Claude CodeのReadの`file.truncatedByTokenCap`、
    Bashの退避を示す`persistedOutputPath`、および退避時にホストが本文全体を置き換える
    `<persisted-output>`の囲みである。SkillとWebFetchの応答には固有の省略表現が無い。
    """
    if codex_read_observation.response_is_truncated(response):
        return True
    if isinstance(response, dict):
        file = response.get("file")
        if isinstance(file, dict) and file.get("truncatedByTokenCap"):
            return True
        if response.get("persistedOutputPath"):
            return True
        text = document_reads.response_text(response)
    elif isinstance(response, list):
        text = "\n".join(part["text"] for part in response if isinstance(part, dict) and isinstance(part.get("text"), str))
    else:
        text = response
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    return stripped.startswith(_PERSISTED_OUTPUT_OPEN) and stripped.endswith(_PERSISTED_OUTPUT_CLOSE)


@_input_scope()
def observe(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    """本文受信と操作全体の成否を分け、成功時の副作用だけを後続へ渡す。"""
    _observe_content(payload, tool_name, tool_input, session_id)
    if payload.get("hook_event_name") in {"PostToolUseFailure", "PermissionDenied"}:
        return
    response = payload.get("tool_response")
    if isinstance(response, dict) and (
        response.get("exit_code", 0) != 0 or response.get("interrupted") or response.get("is_error") or response.get("isError")
    ):
        return
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    agent = agent_id.resolve_hook_agent_id(payload)
    edited = document_reads.edited_document(tool_name, tool_input, response, cwd)
    if edited is not None:
        path, before = edited
        records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
        if agent in records.get(document_reads.read_trigger_key(path, before), []):
            _mark(session_id, path, agent)
        if agent in records.get(document_reads.skill_invocation_key(path, before), []):
            update_state(
                session_id,
                operation_skills.mark_ready(
                    document_reads.skill_invocation_key(path, document_reads.content_fingerprint(path)), agent
                ),
            )
    fetch_failed = isinstance(response, dict) and (
        bool(response.get("error")) or isinstance(response.get("code"), int) and not 200 <= response["code"] < 300
    )
    if (
        tool_name == "WebFetch"
        and isinstance(tool_input.get("url"), str)
        and response
        and not fetch_failed
        and not host_truncated(response)
    ):
        update_state(session_id, operation_skills.mark_ready("external-document:" + tool_input["url"], agent))
    for path in operation_skills.written_paths(tool_name, tool_input, cwd):
        if operation_skills.is_agent_doc_target_file(path):
            update_state(
                session_id, operation_skills.mark_ready("modified-agent-document:" + str(pathlib.Path(path).resolve()), agent)
            )
    text = document_reads.response_text(response)
    if (
        tool_name == "Bash"
        and isinstance(tool_input.get("command"), str)
        and any(
            segment.resolved
            and is_atk_command(segment.tokens)
            and segment.tokens[1:3] == ("run-script", "session-review-prepare")
            for segment in extract_execution_segments(tool_input["command"])
        )
        and text
    ):
        try:
            prepared = json.loads(text)
        except ValueError:
            prepared = None
        if prepared is not None:
            _review_reference(session_id, prepared, agent)
        else:
            saved = re.search(r"(?m)^保存先: (.+)$", text)
            if saved:
                path = pathlib.Path(saved[1])
                if not path.is_absolute() or not path.is_file():
                    raise ValueError(f"prepareの保存結果が実在しない: {path}")
                update_state(session_id, operation_skills.mark_ready("review-output:" + str(path), agent))
                _select_targets(session_id, [read_triggers.ReadTarget(path)], agent)
    paths = _read_paths(tool_name, tool_input, cwd)
    if tool_name == "Bash" and any(not path.is_file() for path in paths):
        return
    if tool_name != "Skill" and not paths:
        return
    rows = declarations(cwd, host="codex" if is_codex_payload(payload) else "claude")
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    rows = list(
        dict.fromkeys(
            [
                *rows,
                *_project_skill_rows(
                    records if isinstance(records, dict) else {},
                    agent,
                    tuple(paths),
                    known_sources={row.source for row in rows},
                    host="codex" if is_codex_payload(payload) else "claude",
                    project=cwd,
                ),
            ]
        )
    )
    chosen_targets = selected_targets(records if isinstance(records, dict) else {}, agent)
    targets = {path for row in rows for path in row.documents} | {target.path for target in chosen_targets}
    targets.update(path for path in paths if path.suffix == ".md" and _document_owner(path) is not None)
    if tool_name == "Skill":
        qualified = tool_input.get("skill")
        if not response:
            return
        targets.update(row.source for row in rows)
        targets.update(read_triggers.document_sources(plugin_resources.plugin_root(), cwd))
        targets.update(
            pathlib.Path(key.removeprefix("resolved-project-skill:"))
            for key, owners in records.items()
            if key.startswith("resolved-project-skill:") and agent in owners
        )
        resolved = resolve_skill_document(qualified, sorted(targets))
        if resolved is not None:
            update_state(
                session_id,
                operation_skills.mark_ready(
                    document_reads.skill_invocation_key(resolved, document_reads.content_fingerprint(resolved)), agent
                ),
            )
            _mark(session_id, resolved, agent)
        return
