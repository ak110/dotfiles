import dataclasses
import json
import os
import pathlib

from agent_toolkit._common import delegated_session, process_loop_session
from agent_toolkit._common.shell_segments import ExecutionSegment, extract_execution_segments
from agent_toolkit._hooks import agent_id, notice, work_contract
from agent_toolkit._hooks.pretooluse.shell_checks_git import git_subcommand_tokens
lazy from agent_toolkit._atk.wi.listing import progress_new_wis
lazy from agent_toolkit._hooks.user_response_advisor import resend_scope

_RESEND_KEY = "publication_gate_resend_by_actor"
_SHELL_TOOLS = {"Bash", "PowerShell", "exec_command", "functions.exec_command"}


def _publishes(segment: ExecutionSegment) -> bool:
    tokens = segment.tokens
    if not segment.resolved or not tokens or "--help" in tokens or "-h" in tokens:
        return False
    executable = pathlib.PureWindowsPath(tokens[0]).name.lower()
    git = (
        git_subcommand_tokens(dataclasses.replace(segment, tokens=("git", *tokens[1:])))
        if executable in {"git", "git.exe"}
        else None
    )
    if git and git[0] == "push":
        return not {"--dry-run", "-n"}.intersection(git[1])
    return executable in {"gh", "gh.exe"} and tokens[1:3] == ("pr", "create")


def _is_publication(tool_name: str, tool_input: dict) -> bool:
    if tool_name.startswith("mcp__") and tool_name.endswith("__create_pull_request"):
        return True
    command = tool_input.get("command")
    return (
        tool_name in _SHELL_TOOLS
        and isinstance(command, str)
        and any(_publishes(segment) for segment in extract_execution_segments(command))
    )


def check(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    """公開前の新着WIの判定漏れを止める。同じ発話の中で同じ操作を再発行すれば解除する。  pyfltr: allow-comment"""
    if not _applies(payload, session_id, tool_name, tool_input):
        return None
    actor = agent_id.resolve_hook_agent_id(payload)
    found = _check(payload, session_id)
    if found is None:
        notice.forget_resends(session_id, actor, _RESEND_KEY)
        return None
    if notice.consume_resend(
        session_id,
        actor,
        _RESEND_KEY,
        json.dumps(tool_input, ensure_ascii=False, sort_keys=True) + "\n" + found[0],
        label=f"公開前の新着WIの判定を確認できない公開の操作（{found[0]}）",
        scope=resend_scope(payload),
    ):
        return None
    return found[0], f"{found[1]}判定を記録できない場合は、同じ操作を再発行すれば実行する。"


def _owner(payload: dict, session_id: str) -> str:
    if agent_id.is_main_agent_context(payload):
        return session_id
    return delegated_session.owner_session_id(os.environ) or session_id


def _applies(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> bool:
    return process_loop_session.is_identified_process_loop_session(_owner(payload, session_id), os.environ) and _is_publication(
        tool_name, tool_input
    )


def _check(payload: dict, session_id: str) -> tuple[str, str] | None:
    owner = _owner(payload, session_id)
    try:
        rows = progress_new_wis(owner)
    except (Exception, SystemExit) as error:  # pylint: disable=broad-exception-caught
        return (
            f"公開前に処理可能な新着WIを取得できない（{type(error).__name__}）。",
            "開始時集合の所在とatk wi listの同期診断を確認し、原因を解消してから再試行する。",
        )
    if rows is None:
        return (
            "公開前の新着WI確認に必要な開始時集合がない。",
            "同じ巡回の開始時に保存した集合と、対象ごとの保存結果を確認する。現在の集合で置き換えない。",
        )
    if not rows:
        return None
    decided = work_contract.recorded_new_wi_decisions(owner)
    pending = [row for row in rows if row.rsplit(" (source:", 1)[0] not in decided]
    if not pending:
        return None
    return (
        "公開前に判定を要する新着WI: " + "、".join(pending),
        "巡回の統括がatk wi showで本文を読み、今回扱うものは公開前に処理する。処理可能な状態で残る項目は"
        "統括の作業契約に「- 新着WIの判定: <ファイル名> | <判定と根拠>」を各1行記録して再試行する。",
    )
