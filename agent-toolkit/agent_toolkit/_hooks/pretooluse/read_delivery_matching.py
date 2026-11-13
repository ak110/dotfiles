"""読込宣言の配送操作が現在のツール入力に当たるかを判定する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import fnmatch
import os
import pathlib
import shlex
import tempfile
import tomllib
from typing import TYPE_CHECKING

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import read_triggers
from agent_toolkit._common.read_triggers_model import WI_SAVE_OPERATIONS
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import is_atk_command, join_words
from agent_toolkit._hooks import (
    document_reads,
    plugin_resources,
)
from agent_toolkit._hooks import tool_input as tool_input_fields
from agent_toolkit._hooks.pretooluse import dependency_usage, operation_skills
from agent_toolkit._hooks.pretooluse.read_delivery_cache import _input_cached
from agent_toolkit._hooks.pretooluse.read_delivery_paths import (
    _document_owner,
    _document_plugin,
    _read_name_hints,
    _read_path_variants,
    _read_paths,
    _skill,
)
lazy from agent_toolkit._hooks.pretooluse import shell_checks

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


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

_CONFIRMING_OPERATIONS = WI_SAVE_OPERATIONS | {"awi-draft-writing", "uwi-draft-writing", "plan-draft-writing"}


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
    本文の作成（`body-writing`）も、一時領域の書込先だけの場合は成立させない。
    """
    if operation == "wi-standard-self-review":
        from agent_toolkit._hooks.pretooluse.read_delivery_roles import incidental_wi_author  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

        if incidental_wi_author():
            return False
    if operation in _FILE_SCOPED_OPERATIONS:
        written = operation_skills.written_paths(tool_name, tool_input, cwd)
        if written and all(_temporary_path(path) for path in written):
            return False
    if operation == "body-writing":
        # 一時領域の書込は、使い捨てのスクリプト・データと、作業記録などの個人記録であり、成果物の本文に当たらない。
        # WI原稿と計画初版は一時領域に置いても保存される成果物の原稿であり、本文の形で判定する専用の配送操作
        # （`awi-draft-writing`・`uwi-draft-writing`・`plan-draft-writing`）が一時領域の書込でも起草規範を届ける。
        written = operation_skills.written_paths(tool_name, tool_input, cwd)
        if written and all(_temporary_path(path) for path in written):
            return False
    matched = operation_skills.operation_matches(operation, tool_name, tool_input, cwd)
    if matched is not None:
        return matched
    prefix, separator, value = operation.partition(":")
    # 受領など別の時点が所有する共通語彙は、PreToolUseでは成立させない。
    if not separator:
        return False
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


@_input_cached
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


def _bound_operation_matches(
    row: read_triggers.ReadTrigger, operation: str, tool_name: str, tool_input: dict, cwd: str
) -> bool:
    """同名の別rootを読む操作と、宣言が所属する実体を区別する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if not matches(operation, tool_name, tool_input, cwd):
        return False
    owner = _document_owner(row.source)
    plugin = _document_plugin(str(row.source))
    if owner is not None and operation.startswith((f"read:{plugin}:", f"skill:{plugin}:")):
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
                active = read_delivery.sibling_plugin_root(plugin, active)
            if active is None or active.resolve() != owner:
                return False
    return True


def row_matches(row: read_triggers.ReadTrigger, tool_name: str, tool_input: dict, cwd: str) -> bool:
    if row.derived_read:
        # 配送操作を省略した行は資料自身の読取だけを契機にする。読取対象の名前が資料列に無い行は解決しない。
        hints = _read_name_hints(tool_name, tool_input, cwd)
        if not any(hint in row.material for hint in hints):
            return False
    plugin = _document_plugin(str(row.source))
    operations = tuple(
        "skill:" + plugin + ":" + operation.removeprefix("skill:")
        if operation.startswith("skill:") and ":" not in operation.removeprefix("skill:")
        else operation
        for operation in row.effective_operations
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
    """公開操作で確かめられる条件だけを確定し、自然文の判断を代行しない。"""
    if row.kind != "判断" or not row.condition:
        return True
    # 確認ツールの呼出しは、確認本文・選択肢を起草する操作が成立した観測になる。
    if (
        row.source == plugin_resources.plugin_root() / "share/read-trigger-bootstrap.md"
        and "confirmation" in row.operations
        and matches("confirmation", tool_name, tool_input, cwd)
    ):
        return True
    # 対象資料の読取だけでは、宣言元の役割・工程などの自然文条件を確定できない。
    # WIの保存種別と原稿・計画初版の本文は、保存・起草の時点にある観測として条件を確定する。
    return any(
        operation in _CONFIRMING_OPERATIONS and matches(operation, tool_name, tool_input, cwd) for operation in row.operations
    )
