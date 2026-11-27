"""成果作業と非同期対象の操作の抽出。  pyfltr: allow-comment"""

from __future__ import annotations

import pathlib
lazy import subprocess
lazy from typing import Any

lazy from agent_toolkit._agents_server import tool_names
lazy from agent_toolkit._atk.managed_temp import temp_root_path
lazy from agent_toolkit._atk.wi import uwi_scan
lazy from agent_toolkit._common import shell_cwd, skill_modes
lazy from agent_toolkit._common.bash_invocations import extract_bash_invocations
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_atk_command, join_words
lazy from agent_toolkit._git import command as git_command
lazy from agent_toolkit._hooks import full_reads
lazy from agent_toolkit._hooks import tool_input as _hook_tool_input
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.pretooluse.operation_skills import written_paths
lazy from agent_toolkit._hooks.pretooluse.shell_checks import _git_subcommand_tokens, git_commit_options
lazy from agent_toolkit._hooks.termination_evidence_state import (
    _GIT_PUSH_VALUE_OPTIONS,
    TARGET_ENTRY_WORKS,
)
lazy from agent_toolkit._plan.path_kinds import is_plan_main_file


def _skill_name(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.removeprefix("agent-toolkit:")


def published_git_operations(command: str) -> list[str]:
    operations: list[str] = []
    for invocation in extract_bash_invocations(command):
        parsed = _git_subcommand_tokens(invocation.segment)
        if invocation.background or parsed is None or parsed[0] not in {"commit", "push"}:
            continue
        arguments = parsed[1]
        if any("\x00" in argument for argument in arguments):
            continue
        excluded = False
        if parsed[0] == "commit":
            excluded = any(option in {"--dry-run", "--help", "-h"} for option, _value in git_commit_options(arguments))
        else:
            options = iter(arguments)
            for option in options:
                if option == "--":
                    break
                if option in _GIT_PUSH_VALUE_OPTIONS:
                    next(options, None)
                elif option in {"--dry-run", "--help", "-h", "-n"}:
                    excluded = True
                    break
        prefix = invocation.segment.tokens[: len(invocation.segment.tokens) - len(arguments)]
        if not excluded and "--help" not in prefix:
            operations.append(parsed[0])
    return operations


def _saved_operations(command: str) -> list[str]:
    saved: list[str] = []
    for segment in extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        tokens = list(segment.tokens)
        name = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name
        if name.startswith("python") and len(tokens) > 1 and not tokens[1].startswith("-"):
            tokens.pop(0)
            name = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name
        if "--help" in tokens or "-h" in tokens:
            continue
        if is_atk_command(tokens):
            if tokens[1:2] == ["wi"] and tokens[2:3] in (["add"], ["edit"]):
                saved.append("WIの保存")
            elif tokens[1:3] == ["run-script", "plan-create"]:
                saved.append("計画の保存")
        elif name == "create_plan_files.py":
            saved.append("計画の保存")
    return saved


# 読取の`tool_response`の形とStopの出力契約の監査記録は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_hooks/termination_evidence.py：終了工程の証拠のStop判定：2026年10月3日」にある。
def _started_work(skill: object, arguments: object) -> str | None:
    if _skill_name(skill) == skill_modes.ENTRY_SKILL:
        work = (arguments.split() or [""])[0] if isinstance(arguments, str) else ""
        return work if work in TARGET_ENTRY_WORKS else None
    return None


def _start_label(work: str) -> str:
    name = f"{skill_modes.ENTRY_SKILL} {work}" if work in TARGET_ENTRY_WORKS else work
    return f"agent-toolkit:{name}の開始"


def _read_work(path: pathlib.Path) -> str | None:
    parts = path.parts
    if len(parts) < 4 or parts[-4:-1] != ("skills", skill_modes.ENTRY_SKILL, "playbooks") or not path.is_file():
        return None
    return next((work for work in TARGET_ENTRY_WORKS if parts[-1] == f"{work}.md"), None)


def _read_works(invocation: shell_cwd.BashInvocation, cwd: shell_cwd.CwdResolution, response: str) -> list[str]:
    return [
        work
        for path in full_reads.invocation_full_reads(invocation, cwd, response, lambda path: _read_work(path) is not None)
        if (work := _read_work(path)) is not None
    ]


def _invocations(payload: dict[str, Any], *, after: bool, failed: bool) -> list[dict[str, Any]]:
    """ツール呼び出しから、成果作業・起動証拠・非同期の待機対象の記録を返す。

    成果作業と起動証拠は成功した応答の後だけで返す。非同期の待機対象は開始時点の作業へ対応付けるため、
    開始前にも返す。
    """
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    if tool_name in {
        namespace + operation for namespace in tool_names.MCP_NAMESPACES for operation in tool_names.RECORDED_START_OPERATIONS
    }:
        return [{"kind": "async"}]
    if not after or failed:
        return []
    codex = is_codex_payload(payload)
    if tool_name == "Skill" and not codex:
        skill = _started_work(tool_input.get("skill"), tool_input.get("args"))
        if skill is not None:
            record: dict[str, Any] = {"kind": "deliverable", "labels": [_start_label(skill)], "entry_work": skill}
            if isinstance(payload.get("cwd"), str) and (repository := _git_root(payload["cwd"])) is not None:
                record["repository"] = str(repository)
            return [record]
        return []
    labels: list[str] = []
    records: list[dict[str, Any]] = []
    cwd = payload.get("cwd")
    if isinstance(cwd, str):
        paths = written_paths(tool_name, tool_input, cwd)
        grouped: dict[pathlib.Path, list[str]] = {}
        private = uwi_scan.private_notes_root() if paths else None
        for value in paths:
            path = pathlib.Path(value)
            if not path.is_absolute() or private is not None and path.is_relative_to(private):
                continue
            repository = _git_root(str(path.parent))
            if repository is not None and not _temporary_git_repository(repository):
                grouped.setdefault(repository, []).append(path.relative_to(repository).as_posix())
        for repository, relative in grouped.items():
            if relative:
                records.append(
                    {"kind": "deliverable", "labels": ["リポジトリの編集"], "repository": str(repository), "paths": relative}
                )
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd if isinstance(cwd, str) else "")
    if any(operation.exists_after_apply and is_plan_main_file(operation.display_path) for operation in operations or []):
        labels.append("計画の保存")
    command = tool_input.get("command")
    if tool_name in ATK_SHELL_TOOLS and isinstance(command, str):
        # Codex・Antigravityはスキルを読取で始めるため、手順書の全文を受け取った読取を起動証拠にする。
        response = payload.get("tool_response")
        shared_read = codex or is_antigravity_payload(payload)
        text = full_reads.response_text(response) if shared_read else ""
        for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd if isinstance(cwd, str) else "", effects=True):
            if not invocation.execution_known:
                continue
            single = join_words(invocation.segment.tokens)
            item_labels = [*_saved_operations(single), *([] if invocation.background else published_git_operations(single))]
            repository = _invocation_repository(invocation, current) if item_labels else None
            if text and current.resolved and shared_read:
                for work in _read_works(invocation, current, text):
                    item_labels.append(_start_label(work))
                    repository = _git_root(current.path)
            if item_labels:
                records.append(
                    {"kind": "deliverable", "labels": item_labels, "repository": str(repository) if repository else None}
                )
    if labels:
        record = {"kind": "deliverable", "labels": list(dict.fromkeys(labels))}
        repository = _git_root(cwd) if isinstance(cwd, str) else None
        record["repository"] = str(repository) if repository is not None else None
        records.append(record)
    return records


def _invocation_repository(invocation: shell_cwd.BashInvocation, cwd: shell_cwd.CwdResolution) -> pathlib.Path | None:
    if not cwd.resolved or not invocation.arguments_known:
        return None
    tokens = invocation.segment.tokens
    parsed = _git_subcommand_tokens(invocation.segment)
    options = tokens[1 : len(tokens) - len(parsed[1]) - 1] if parsed is not None and not is_atk_command(tokens) else ()
    return _git_root(cwd.path, options=options)


def _git_root(cwd: str, *, options: tuple[str, ...] = ()) -> pathlib.Path | None:
    try:
        result = git_command.run(
            [*options, "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return pathlib.Path(result.stdout.strip()).resolve() if result.returncode == 0 and result.stdout.strip() else None


def _temporary_git_repository(root: pathlib.Path) -> bool:
    try:
        temporary = temp_root_path()
        if not root.is_relative_to(temporary):
            return False
        result = git_command.run(
            ["rev-parse", "--git-common-dir"], cwd=root, capture_output=True, text=True, check=False, timeout=5
        )
        common = result.stdout.strip()
        return result.returncode == 0 and bool(common) and (root / common).resolve().is_relative_to(temporary)
    except OSError, subprocess.TimeoutExpired:
        return False
