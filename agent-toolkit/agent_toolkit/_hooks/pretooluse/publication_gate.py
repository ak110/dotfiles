import dataclasses
import json
import os
import pathlib
import re

from agent_toolkit._common import delegated_session, process_loop_session
from agent_toolkit._common.shell_segments import ExecutionSegment, extract_execution_segments
from agent_toolkit._git import command as git_command
from agent_toolkit._git.remote import resolve_repo_identifier
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
    found = _check(payload, session_id, tool_name, tool_input)
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
        limit=1,
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


def _gh_target(command: str, cwd: str) -> frozenset[tuple[str, str | None]] | None:
    segments = tuple(extract_execution_segments(command))
    if len(segments) != 1:
        return None
    segment = segments[0]
    tokens = segment.tokens
    if (
        not segment.resolved
        or not tokens
        or pathlib.PureWindowsPath(tokens[0]).name.lower() not in {"gh", "gh.exe"}
        or tokens[1:3] != ("pr", "create")
    ):
        return None
    options = {"-R": "repo", "--repo": "repo", "-B": "base", "--base": "base"}
    values: dict[str, str] = {}
    arguments = iter(tokens[3:])
    for arg in arguments:
        flag, equals, inline = arg.partition("=")
        if flag in options:
            value = inline if equals else next(arguments, "")
            if not value or options[flag] in values:
                return None
            values[options[flag]] = value
        elif flag in {
            "-H",
            "--head",
            "-t",
            "--title",
            "-b",
            "--body",
            "-F",
            "--body-file",
            "--template",
            "-a",
            "--assignee",
            "-l",
            "--label",
            "-m",
            "--milestone",
            "-p",
            "--project",
            "-r",
            "--reviewer",
        }:
            if not equals and next(arguments, None) is None:
                return None
        elif arg not in {"--draft", "-d", "--fill", "--fill-first", "--fill-verbose", "--no-maintainer-edit", "--web", "-w"}:
            return None
    repo = values.get("repo")
    if repo:
        if not re.fullmatch(r"(?:[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            return None
        target = resolve_repo_identifier("https://" + (repo if repo.count("/") == 2 else f"github.com/{repo}"))
    else:
        target = resolve_repo_identifier(cwd)
    base = values.get("base")
    if not base or not re.fullmatch(r"[A-Za-z0-9_./-]+", base):
        return None
    return frozenset({(target, base)}) if target else None


def _publication_targets(payload: dict, tool_name: str, tool_input: dict) -> frozenset[tuple[str, str | None]] | None:
    if tool_name.startswith("mcp__") and tool_name.endswith("__create_pull_request"):
        owner, repo, base = (tool_input.get(key) for key in ("owner", "repo", "base"))
        if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in (owner, repo)):
            return None
        target = resolve_repo_identifier(f"https://github.com/{owner}/{repo}")
        return frozenset({(target, base if isinstance(base, str) and base else None)}) if target else None
    command = tool_input.get("command")
    cwd = tool_input.get("workdir") or payload.get("cwd")
    if tool_name not in _SHELL_TOOLS or not isinstance(command, str) or not isinstance(cwd, str) or not cwd:
        return None
    direct = re.fullmatch(r"\s*git\s+push(?:\s+([A-Za-z0-9_.-]+)\s+([A-Za-z0-9_./:-]+))?\s*", command)
    if direct is None:
        return _gh_target(command, cwd)
    remote, refspec = direct.groups()
    if remote is None:
        upstream = git_command.run(
            ["rev-parse", "--symbolic-full-name", "@{push}"], cwd=cwd, capture_output=True, text=True, check=False, timeout=2
        )
        refname = upstream.stdout.strip()
        if upstream.returncode or not refname.startswith("refs/remotes/"):
            return None
        remote, _, refspec = refname.removeprefix("refs/remotes/").partition("/")
    if remote.startswith("-") or not refspec:
        return None
    source, separator, destination = refspec.partition(":")
    ref = destination if separator else source
    if not ref or ref.startswith("-") or ":" in ref or (ref.startswith("refs/") and not ref.startswith("refs/heads/")):
        return None
    result = git_command.run(
        ["remote", "get-url", "--push", "--all", remote], cwd=cwd, capture_output=True, text=True, check=False, timeout=2
    )
    urls = result.stdout.splitlines()
    if result.returncode or len(urls) != 1:
        return None
    target = resolve_repo_identifier(urls[0])
    return frozenset({(target, ref.removeprefix("refs/heads/"))}) if target else None


def _check(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    owner = _owner(payload, session_id)
    try:
        targets = _publication_targets(payload, tool_name, tool_input)
        if targets is None:
            return (
                "公開対象を確定できないため、開始時集合との比較を行っていない。",
                "公開先のリポジトリとブランチを確認し、コマンドで明示して再試行する。",
            )
        rows = progress_new_wis(owner, targets=targets)
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
