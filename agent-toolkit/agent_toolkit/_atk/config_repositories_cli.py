from __future__ import annotations

import argparse
import json
from typing import Any

from agent_toolkit._atk import config_migration, config_repositories, config_settings, help_text, outcome
from agent_toolkit._git import command as git


def build_parser(sub: argparse._SubParsersAction) -> None:
    parser = help_text.add_command(sub, "repos", **help_text.HELP["atk config repos"])
    commands = help_text.add_subcommands(parser, dest="repos_subcommand", required=True)
    for name in ("add", "set", "remove", "list"):
        command = help_text.add_command(commands, name, **help_text.HELP[f"atk config repos {name}"])
        if name == "list":
            continue
        command.add_argument("repo", metavar="REPO", help="originのURL・host/owner/repository・ローカルworktreeのパス。")
        if name == "remove":
            continue
        command.add_argument("--edit", choices=("allow", "awi", "deny"), required=name == "add", help="編集可否。addでは必須。")
        command.add_argument("--path", help="ローカルworktreeのパス。相対パスと~は絶対パスへ解決する。")
        command.add_argument(
            "--process-loop",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="process-loopの対象か。対象にはpathが必要。",
        )
        command.add_argument(
            "--github-issues",
            action=argparse.BooleanOptionalAction,
            default=None,
            help="公開GitHubリポジトリのIssueとPRを巡回で取り込む。既定は無効。",
        )
        command.add_argument(
            "--github-issue-trusted-authors",
            help="Issueを自動投入するアカウントをカンマ区切りで指定する。空文字列は誰も信頼せず、既定値に戻すには@defaultを指定する。",
        )
        command.add_argument("--platforms", help="検証OSをlinux,windowsのカンマ区切りで指定する。省略時は両方。")
        command.add_argument(
            "--branches",
            help="継続して作業が入るoriginのブランチ（系統）をカンマ区切りで指定する。空文字列で系統の登録を外す。",
        )
        command.add_argument("--default-branch", help="系統を省略したWIの宛先にする系統。branchesの名前を指定する。")
        command.add_argument(
            "--merge-from",
            help="リリース前に取り込む系統を<取り込む側>=<取り込み元>のカンマ区切りで指定する。空文字列で外す。",
        )


def update_repositories(args: argparse.Namespace) -> str:
    config_migration.check_legacy_environment()
    config_migration.migrate_legacy_settings()
    stored = config_settings.load_config_strict()
    raw = stored.get("repositories", "{}")
    if not isinstance(raw, str):
        raise ValueError("repositoriesの保存値はJSON文字列で指定する")

    repositories = config_repositories.parse(raw, check_paths=False)
    if args.repos_subcommand == "list":
        return json.dumps(repositories, ensure_ascii=False, indent=2)
    repo_id, local = config_repositories.identifier(args.repo)
    exists = repo_id in repositories
    if args.repos_subcommand == "add" and exists:
        raise ValueError(f"登録済み: {repo_id}。`atk config repos set`で属性を変更する")
    if args.repos_subcommand != "add" and not exists:
        raise ValueError(f"未登録: {repo_id}。現在の登録: {', '.join(repositories) or 'なし'}")
    if args.repos_subcommand == "remove":
        del repositories[repo_id]
    else:
        item = dict(repositories.get(repo_id, {"process_loop": False, "platforms": ["linux", "windows"]}))
        if args.edit is not None:
            item["edit"] = args.edit
        if args.path is not None:
            item["path"] = args.path
        elif args.repos_subcommand == "add" and local:
            item["path"] = local
        if args.process_loop is not None:
            item["process_loop"] = args.process_loop
        if args.platforms is not None:
            item["platforms"] = args.platforms.split(",")
        if args.github_issues is not None:
            item["github_issues"] = args.github_issues
        if args.github_issue_trusted_authors is not None:
            authors = args.github_issue_trusted_authors
            item["github_issue_trusted_authors"] = (
                None if authors == "@default" else [name.strip() for name in authors.split(",")] if authors else []
            )
        _apply_branch_arguments(args, item)
        repositories[repo_id] = item
    value = config_settings.normalize_setting("repositories", json.dumps(repositories, ensure_ascii=False))
    if args.repos_subcommand != "remove" and args.branches is not None:
        _require_remote_branches(repo_id, json.loads(value)[repo_id])
    config_settings.save_config({**stored, "repositories": value})
    return repo_id


def dispatch(args: argparse.Namespace) -> None:
    result = update_repositories(args)
    if args.repos_subcommand == "list":
        print(result)
    else:
        outcome.report_success(f"repository登録を更新した: {result}")


def _apply_branch_arguments(args: argparse.Namespace, item: dict[str, Any]) -> None:
    if args.branches is not None:
        names = [name.strip() for name in args.branches.split(",") if name.strip()]
        if names:
            item["branches"] = names
        else:
            for key in ("branches", "default_branch", "merge_from"):
                item.pop(key, None)
    if args.default_branch is not None:
        item["default_branch"] = args.default_branch
    if args.merge_from is not None:
        pairs = [pair.strip() for pair in args.merge_from.split(",") if pair.strip()]
        mapping: dict[str, str] = {}
        for pair in pairs:
            target, separator, source = pair.partition("=")
            if not separator or not target.strip() or not source.strip():
                raise ValueError(f"--merge-fromは<取り込む側>=<取り込み元>の形で指定する: {pair}")
            mapping[target.strip()] = source.strip()
        if mapping:
            item["merge_from"] = mapping
        else:
            item.pop("merge_from", None)


def _require_remote_branches(repo_id: str, item: dict[str, Any]) -> None:
    settings = config_repositories.branch_settings(item)
    if settings is None:
        return
    path = item.get("path")
    if not path:
        raise ValueError(f"{repo_id}の系統を登録するにはpathが必要。--path <ローカルworktree>を指定する")
    result = git.run(
        ["-C", path, "ls-remote", "--heads", "origin", *(f"refs/heads/{name}" for name in settings.branches)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            f"originのブランチを照会できない: {result.stderr.strip()}。"
            f"`git -C {path} ls-remote --heads origin`が成功する状態にして再実行する"
        )
    present = {line.split("\t", 1)[1].removeprefix("refs/heads/") for line in result.stdout.splitlines() if "\t" in line}
    missing = [name for name in settings.branches if name not in present]
    if missing:
        raise ValueError(
            f"originに無いブランチは系統に登録できない: {', '.join(missing)}。"
            f"originへブランチをpushするか、`git -C {path} branch -r`で名前を確かめて--branchesを直す"
        )
