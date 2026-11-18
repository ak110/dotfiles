"""成果作業と資料取得の操作抽出。"""

from __future__ import annotations

import os
import pathlib
lazy import subprocess
lazy from typing import Any

lazy from agent_toolkit._agents_server import tool_names
lazy from agent_toolkit._atk.wi import uwi_scan
lazy from agent_toolkit._common import read_triggers, session_state, shell_cwd, skill_modes
lazy from agent_toolkit._common.bash_invocations import extract_bash_invocations
lazy from agent_toolkit._common.process_loop_session import is_process_loop_session
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_atk_command, join_words
lazy from agent_toolkit._git import command as git_command
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import document_reads as _document_reads
lazy from agent_toolkit._hooks import tool_input as _hook_tool_input
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.pretooluse.operation_skills import written_paths
lazy from agent_toolkit._hooks.pretooluse.read_delivery import host_truncated
lazy from agent_toolkit._hooks.pretooluse.shell_checks import _git_subcommand_tokens, git_commit_options
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.termination_evidence_state import (
    _GIT_PUSH_VALUE_OPTIONS,
    COMPLETION_REPORT,
    STATE_KEY,
    TARGET_ENTRY_WORKS,
    completion_playbook_path,
)
lazy from agent_toolkit._plan.path_kinds import is_plan_main_file


def _skill_name(value: object) -> str | None:
    """Skillの名前から`agent-toolkit:`の修飾を除いた短縮名を返す。"""
    if not isinstance(value, str) or not value:
        return None
    return value.removeprefix("agent-toolkit:")


def published_git_operations(command: str) -> list[str]:
    """Bashのコマンドが実行する`git commit`・`git push`を、試行・ヘルプ・背景実行を除いて返す。"""
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
    """Bashのコマンドから、WI・計画を保存する操作を返す。"""
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
# 読取が本文を返したかの判定は`document_reads.py`が持つ。
def _started_work(skill: object, arguments: object) -> str | None:
    """Skillの起動が始める対象の名前を返す。入口の起動は引数の先頭語から作業の名前を読む。"""
    if _skill_name(skill) == skill_modes.ENTRY_SKILL:
        work = (arguments.split() or [""])[0] if isinstance(arguments, str) else ""
        return work if work in {COMPLETION_REPORT, *TARGET_ENTRY_WORKS} else None
    return None


def _start_label(work: str) -> str:
    """成果作業の開始を示す表示名を返す。入口の作業は入口の起動の形で示す。"""
    name = f"{skill_modes.ENTRY_SKILL} {work}" if work in TARGET_ENTRY_WORKS else work
    return f"agent-toolkit:{name}の開始"


def _read_work(read: _document_reads.DocumentRead) -> str | None:
    """読取の対象が開始資料（入口の作業の手順書）であれば、対象の名前を返す。"""
    if read.plugin != "agent-toolkit":
        return None
    if read.skill == skill_modes.ENTRY_SKILL:
        works = (COMPLETION_REPORT, *TARGET_ENTRY_WORKS)
        return next((work for work in works if read.relative_path == f"playbooks/{work}.md"), None)
    return None


def _completion_read(payload: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    """Claude CodeのメインのReadが、完了報告の手順書の現在の本文全体を1回の応答で返したかを返す。

    `offset`・`limit`の値は信じず、応答の`file.content`に現在の版の全文が含まれるかで判定する。
    範囲を限った読取、ホストのトークン上限で本文を省略した読取、旧版の読取は本文全体を含まないため受理しない。
    分割した読取の集約は受理しない。手順書は1回のReadで全文を返す長さに保つ。
    """
    if not agent_id.is_main_agent_context(payload):
        return False
    value = tool_input.get("file_path")
    if not isinstance(value, str) or not value:
        return False
    document = _document_reads.skill_document(value)
    if document is None or _document_reads.document_plugin(value) != "agent-toolkit":
        return False
    if _read_work(_document_reads.DocumentRead(*document, plugin="agent-toolkit", path=value)) != COMPLETION_REPORT:
        return False
    path = pathlib.Path(value)
    candidates = [item for item in _document_reads.receipt_candidates([read_triggers.ReadTarget(path)]) if not item["heading"]]
    texts = _document_reads.returned_texts(payload.get("tool_response"))
    return bool(candidates) and bool(_document_reads.received_candidates(candidates, texts))


def _read_skills(command: str) -> list[tuple[str, _document_reads.DocumentRead]]:
    """Toolkitの対象資料を、対象の名前とともに、返された本文の確認まで所有pluginと実体を保持して返す。"""
    return [(work, read) for read in _document_reads.bash_document_reads(command) if (work := _read_work(read)) is not None]


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
    if tool_name == "Read" and not codex and not is_antigravity_payload(payload):
        return [{"kind": "completion"}] if _completion_read(payload, tool_input) else []
    if tool_name == "Skill" and not codex:
        skill = _started_work(tool_input.get("skill"), tool_input.get("args"))
        if skill == COMPLETION_REPORT:
            completion: dict[str, Any] = {"kind": "completion"}
            response = payload.get("tool_response")
            path = completion_playbook_path()
            if (
                response
                and not (
                    isinstance(response, dict)
                    and any(response.get(key) for key in ("isError", "is_error", "interrupted", "error"))
                )
                and path.is_file()
            ):
                # 入口の起動は入口の本文を返し、手順書の本文は返さない。手順書の取得記録とは別の起動の記録として、
                # 会話圧縮までの完了報告の読込証拠に使う。
                completion["skill_invocation"] = _document_reads.skill_invocation_key(
                    path, _document_reads.content_fingerprint(path)
                )
            return [completion]
        if skill is not None:
            record: dict[str, Any] = {"kind": "deliverable", "labels": [_start_label(skill)]}
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
            if repository is not None:
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
        for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd if isinstance(cwd, str) else ""):
            single = join_words(invocation.segment.tokens)
            item_labels = [*_saved_operations(single), *([] if invocation.background else published_git_operations(single))]
            if not codex and is_antigravity_payload(payload) and not host_truncated(payload.get("tool_response")):
                text = _document_reads.response_text(payload.get("tool_response"))
                for work, document in _read_skills(single):
                    if not _document_reads.body_returned(
                        document.skill,
                        document.relative_path,
                        text,
                        plugin=document.plugin,
                        path=document.path,
                        host="agy",
                        project=cwd,
                    ):
                        continue
                    if work == COMPLETION_REPORT:
                        records.append({"kind": "completion"})
                    else:
                        item_labels.append(_start_label(work))
            if item_labels:
                repository = _invocation_repository(invocation, current)
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
    """操作ごとの実行位置とGitの位置指定から所属先を求める。"""
    if not cwd.resolved or not invocation.arguments_known:
        return None
    tokens = invocation.segment.tokens
    parsed = _git_subcommand_tokens(invocation.segment)
    options = tokens[1 : len(tokens) - len(parsed[1]) - 1] if parsed is not None and not is_atk_command(tokens) else ()
    return _git_root(cwd.path, options=options)


def _git_root(cwd: str, *, options: tuple[str, ...] = ()) -> pathlib.Path | None:
    """書込時の作業ディレクトリが属するGitリポジトリだけを解決する。"""
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


def _uncommitted_paths(repository: str, paths: list[str]) -> list[str] | None:
    """成功した自編集の対象だけを現在のGit状態と比べる。"""
    try:
        result = git_command.run(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths],
            cwd=repository,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if result.returncode:
        return None
    fields = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    changed = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        if field[3:] in paths:
            changed.append(field[3:])
        if "R" in field[:2] or "C" in field[:2]:
            index += 1
    return sorted(set(changed))


def unscoped_self_edits(payload: dict[str, Any], work: dict[str, Any]) -> list[str]:
    """協調Claudeメインの作業が残す、公開範囲が未確定の未コミットの自編集を返す。"""
    if is_codex_payload(payload) or not agent_id.is_main_agent_context(payload):
        return []
    state = session_state.read_state(payload["session_id"])
    if state.get("process_wi_skill_invoked") or is_process_loop_session(payload["session_id"], os.environ):
        return []
    data = state.get(STATE_KEY)
    reference = data.get("inputs", {}).get(work.get("reference"), {}) if isinstance(data, dict) else {}
    if reference.get("human") is not True or work.get("publish_scope"):
        return []
    changed: list[str] = []
    for repository, paths in work.get("edits", {}).items():
        current = _uncommitted_paths(repository, paths)
        if current is None:
            append_stop_log(payload["session_id"], "self_edit_state_unavailable", {"repository": repository})
            continue
        changed.extend(str(pathlib.Path(repository) / path) for path in current)
    return changed
