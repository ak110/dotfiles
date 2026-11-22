import argparse
import pathlib
from collections.abc import Mapping

from agent_toolkit._atk import config as _config
from agent_toolkit._atk import config_repositories as _config_repositories
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi.entry_text import parse_frontmatter
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import refs as _git_refs

KEY = "target_branch"

type Registry = Mapping[str, _config_repositories.BranchSettings]


def registry(*, strict: bool = False) -> Registry:
    try:
        return _config.repository_branch_settings()
    except (OSError, UnicodeError, ValueError) as error:
        if strict:
            raise
        _outcome.report_warning(
            f"repository登録を読めないため系統を判定できない: {error}",
            next_action="`atk config repos list`で登録を確認して直す",
        )
        return {}


def stored(text: str) -> str | None:
    if KEY not in text:
        return None
    value = (parse_frontmatter(text) or ({}, ""))[0].get(KEY)
    return value if isinstance(value, str) and value else None


def effective(text: str, repo_id: str | None, branches: Registry) -> str | None:
    value = stored(text)
    if value is not None:
        return value
    settings = branches.get(repo_id or "")
    return settings.default_branch if settings is not None else None


def unregistered(text: str, repo_id: str | None, branches: Registry) -> bool:
    value = stored(text)
    if value is None:
        return False
    settings = branches.get(repo_id or "")
    return settings is None or value not in settings.branches


def matches(text: str, repo_id: str | None, branch: str, branches: Registry) -> bool:
    return effective(text, repo_id, branches) == branch


def resolve_for_save(repo_id: str, requested: str | None, branches: Registry) -> str | None:
    settings = branches.get(repo_id)
    if settings is None:
        if requested is None:
            return None
        raise WebInputError(
            f"{repo_id}は系統を登録していないため、target_branchを指定できない: {requested}",
            next_action=(
                f"target_branchを外すか、`atk config repos set {repo_id} --branches <名前,名前> "
                "--default-branch <名前>`で系統を登録してから再実行する"
            ),
        )
    if requested is None:
        return settings.default_branch
    if requested not in settings.branches:
        raise WebInputError(
            f"{repo_id}に登録されていない系統: {requested}（登録した系統: {', '.join(settings.branches)}）",
            next_action="登録した系統のいずれかを指定するか、`atk config repos set`で系統を登録してから再実行する",
        )
    return requested


AUTO = "auto"


def upstream_branch(worktree: pathlib.Path) -> str | None:

    def capture(path: pathlib.Path, args: list[str]) -> str | None:
        result = _git_command.run(["-C", str(path), *args], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    resolved = _git_refs.resolve_branch(worktree, git_fn=capture, upstream=True)
    return resolved.branch if resolved is not None else None


def resolve_filter(value: str | None, repo_id: str | None, worktree: pathlib.Path | None, branches: Registry) -> str | None:
    if value != AUTO:
        return value
    settings = branches.get(repo_id or "")
    if settings is None:
        return None
    current = upstream_branch(worktree) if worktree is not None else None
    if current in settings.branches:
        return current
    raise WebInputError(
        f"{repo_id}の作業ツリーが追跡するブランチ{current or '（解決不能）'}は登録した系統ではない"
        f"（登録した系統: {', '.join(settings.branches)}）",
        next_action=(
            "登録した系統のブランチを追跡する作業ツリー（`git worktree add <パス> -b <名前> --track origin/<系統>`など）で"
            "起動し直すか、`--target-branch <系統>`で系統を指定する"
        ),
    )


def requested_filter(args: argparse.Namespace, repo_ids: tuple[str, ...], branches: Registry) -> str | None:
    value = getattr(args, "target_branch", None)
    if value != AUTO:
        return value
    if len(repo_ids) != 1:
        raise WebInputError(
            "--target-branch autoは対象repositoryが1つの場合だけ使える",
            next_action="--target-repoへ作業ツリーのパスを1つ指定するか、--target-branchへ系統の名前を指定する",
        )
    raw = args.target_repo[0] if isinstance(args.target_repo, list) and args.target_repo else args.target_repo
    worktree: pathlib.Path | None = None
    if isinstance(raw, str) and pathlib.Path(raw).expanduser().exists():
        worktree = pathlib.Path(raw).expanduser().resolve()
    elif raw is None or getattr(args, "target_repo_defaulted", False):
        toplevel = _git_command.run(["rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
        worktree = pathlib.Path(toplevel.stdout.strip()) if toplevel.returncode == 0 else None
    return resolve_filter(AUTO, repo_ids[0], worktree, branches)
