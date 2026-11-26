import dataclasses
import json
import os
import pathlib
import re
import subprocess

from agent_toolkit._common import delegated_session, process_loop_session, session_state
from agent_toolkit._common.shell_segments import ExecutionSegment, extract_execution_segments
from agent_toolkit._git import command as git_command
from agent_toolkit._git.remote import resolve_repo_identifier
from agent_toolkit._hooks import agent_id, notice
from agent_toolkit._hooks.pretooluse.shell_checks_git import git_subcommand_tokens
lazy from agent_toolkit._atk.wi.formatters import NewWorkItem
lazy from agent_toolkit._atk.wi.listing import progress_new_wis
lazy from agent_toolkit._hooks.user_response_advisor import resend_scope

_RESEND_KEY = "publication_gate_resend_by_actor"
_CHECKED_PUBLICATION = "publication_gate_checked_unit"
_SHELL_TOOLS = {"Bash", "PowerShell", "exec_command", "functions.exec_command"}
_DIRECT_PUSH = re.compile(
    r"\s*git\s+push(?:\s+--force-with-lease=([A-Za-z0-9_./-]+):(?:[0-9a-f]{40}|[0-9a-f]{64}))?"
    r"(?:\s+([A-Za-z0-9_.-]+)\s+([A-Za-z0-9_./:-]+))?\s*"
)


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
    """開始時集合と比較し、新着を確認できた場合だけ同じ操作の再発行を受け付ける。  pyfltr: allow-comment"""
    if not _applies(payload, session_id, tool_name, tool_input):
        return None
    actor = agent_id.resolve_hook_agent_id(payload)
    found = _check(payload, session_id, tool_name, tool_input)
    if found is None:
        notice.forget_resends(session_id, actor, _RESEND_KEY)
        return None
    reason, fix, rows = found
    if rows is None:
        notice.forget_resends(session_id, actor, _RESEND_KEY)
        return reason, fix
    merge_pending = [row for row in rows if row.merge_pending]
    if not merge_pending and notice.consume_resend(
        session_id,
        actor,
        _RESEND_KEY,
        json.dumps(tool_input, ensure_ascii=False, sort_keys=True) + "\n" + found[0],
        label="公開前の新着WIを確認する操作",
        scope=resend_scope(payload),
        limit=1,
    ):
        return None
    if merge_pending:
        return (
            "取り込み結果が必要なマージ用AWI: " + "、".join(row.label(omit_defaults=True) for row in merge_pending) + "。",
            "未処置のマージ用AWIは同じ操作の再発行では解除できません。選定と処置結果を照合してください。"
            "担当済みなら再割当せず、取り込み・検証・採用の結果を回収してください。"
            "未着手なら同じ巡回で処理し、処理できない場合は理由と再開条件を保存してください。",
        )
    reason, snapshot = notice.new_wi_preview(session_id, actor, rows)
    reason = reason.replace("処理可能な新着WI:", "公開前の新着WI:", 1)
    notice.stage_new_wi_preview(reason, session_id, actor, snapshot, scoped=True)
    return reason, f"{fix}同じ操作を再発行すると実行する。"


def _owner(payload: dict, session_id: str) -> str:
    if agent_id.is_main_agent_context(payload):
        return session_id
    return delegated_session.owner_session_id(os.environ) or session_id


def _applies(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> bool:
    return process_loop_session.is_identified_process_loop_session(_owner(payload, session_id), os.environ) and _is_publication(
        tool_name, tool_input
    )


def _gh_publication(command: str, cwd: str) -> tuple[str, str, str | None] | None:
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
    options = {"-R": "repo", "--repo": "repo", "-B": "base", "--base": "base", "-H": "head", "--head": "head"}
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
    return (target, base, values.get("head")) if target else None


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
    direct = _DIRECT_PUSH.fullmatch(command)
    if direct is None:
        pr = _gh_publication(command, cwd)
        return frozenset({(pr[0], pr[1])}) if pr else None
    lease_ref, remote, refspec = direct.groups()
    if remote is None:
        if lease_ref is not None:
            return None
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
    if lease_ref is not None and lease_ref.removeprefix("refs/heads/") != ref.removeprefix("refs/heads/"):
        return None
    result = git_command.run(
        ["remote", "get-url", "--push", "--all", remote], cwd=cwd, capture_output=True, text=True, check=False, timeout=2
    )
    urls = result.stdout.splitlines()
    if result.returncode or len(urls) != 1:
        return None
    target = resolve_repo_identifier(urls[0])
    return frozenset({(target, ref.removeprefix("refs/heads/"))}) if target else None


def _source_ref(tool_name: str, tool_input: dict, cwd: str, repo: str, base: str | None) -> str | None:
    command = tool_input.get("command")
    if tool_name in _SHELL_TOOLS and isinstance(command, str):
        match = _DIRECT_PUSH.fullmatch(command)
        if match:
            return match[3].partition(":")[0] if match[3] else None
        pr = _gh_publication(command, cwd)
        head = pr[2] if pr else None
    elif tool_name.startswith("mcp__") and tool_name.endswith("__create_pull_request"):
        head = tool_input.get("head")
    else:
        return None
    if (
        not base
        or not isinstance(head, str)
        or head.startswith("-")
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", head)
        or resolve_repo_identifier(cwd) != repo
    ):
        return None
    return f"refs/heads/{head}"


def _checked_publication(
    payload: dict,
    session_id: str,
    tool_name: str,
    tool_input: dict,
    targets: frozenset[tuple[str, str | None]],
    *,
    remember: bool,
) -> bool:
    cwd = tool_input.get("workdir") or payload.get("cwd")
    if not isinstance(cwd, str) or len(targets) != 1:
        return False
    repo, base = next(iter(targets))
    source = _source_ref(tool_name, tool_input, cwd, repo, base)
    if not source or source.startswith("-"):
        return False
    request = tool_input.get("command")
    push = isinstance(request, str) and _DIRECT_PUSH.fullmatch(request) is not None
    args = ["rev-parse", "--verify", source + "^{commit}"] if push else ["ls-remote", "--refs", "--exit-code", "origin", source]
    try:
        resolved = git_command.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            recover_stale_lock=False,
        )
    except OSError, subprocess.SubprocessError:
        return False
    fields = resolved.stdout.split()
    if resolved.returncode or not fields or fields[1:] != ([] if push else [source]):
        return False
    oid = fields[0]
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid):
        return False
    identity = [cwd, tool_name, repo, base, source, oid]
    actor = agent_id.resolve_hook_agent_id(payload)
    if remember:

        def record(state: dict) -> dict:
            previous = state.get(_CHECKED_PUBLICATION)
            known = previous if isinstance(previous, dict) else {}
            return {**state, _CHECKED_PUBLICATION: {**known, actor: identity}}

        session_state.update_state(session_id, record)
        return True
    seen = session_state.read_state(session_id).get(_CHECKED_PUBLICATION)
    return isinstance(seen, dict) and seen.get(actor) == identity


def _check(
    payload: dict, session_id: str, tool_name: str, tool_input: dict
) -> tuple[str, str, list[NewWorkItem] | None] | None:
    owner = _owner(payload, session_id)
    try:
        targets = _publication_targets(payload, tool_name, tool_input)
        if targets is None:
            return (
                "公開対象を確定できないため、開始時集合との比較を行っていない。",
                "公開先のリポジトリとブランチを確認し、コマンドで明示して再試行してください。",
                None,
            )
        rows = progress_new_wis(owner, targets=targets, track_merges=True)
    except (Exception, SystemExit) as error:  # pylint: disable=broad-exception-caught
        return (
            f"公開前に処理可能な新着WIを取得できない（{type(error).__name__}）。",
            "開始時集合の所在とatk wi listの同期診断を確認し、原因を解消してから再試行してください。",
            None,
        )
    if rows is None:
        return (
            "公開前の新着WI確認に必要な開始時集合がない。",
            "同じ巡回の開始時に保存した集合と、対象ごとの保存結果を確認してください。現在の集合で置き換えないでください。",
            None,
        )
    if not rows:
        _checked_publication(payload, session_id, tool_name, tool_input, targets, remember=True)
        return None
    if any(row.merge_pending for row in rows) and _checked_publication(
        payload, session_id, tool_name, tool_input, targets, remember=False
    ):
        rows = tuple(row for row in rows if not row.merge_pending)
        if not rows:
            return None
    return (
        "公開前の新着WI: " + "、".join(row.filename for row in rows),
        "今回扱う項目があれば公開前に処理してください。次回扱う項目はinboxに残してください。",
        list(rows),
    )
