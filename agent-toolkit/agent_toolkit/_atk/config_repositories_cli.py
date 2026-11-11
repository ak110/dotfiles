"""repository登録を操作するconfig reposの公開CLI。"""

from __future__ import annotations

import argparse
import json

from agent_toolkit._atk import config_migration, config_repositories, config_settings, help_text, outcome


def build_parser(sub: argparse._SubParsersAction) -> None:
    """repository専用の属性操作を登録する。"""
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
        command.add_argument("--platforms", help="検証OSをlinux,windowsのカンマ区切りで指定する。省略時は両方。")


def dispatch(args: argparse.Namespace) -> None:
    """登録を厳密に読み、操作後のmapを全体検証して原子的に保存する。"""
    config_migration.check_legacy_environment()
    config_migration.migrate_legacy_settings()
    stored = config_settings.load_config_strict()
    raw = stored.get("repositories", "{}")
    if not isinstance(raw, str):
        raise ValueError("repositoriesの保存値はJSON文字列で指定する")
    # 以前は存在したworktreeが失われていても、listとset/removeで診断・修復できるようにする。
    repositories = config_repositories.parse(raw, check_paths=False)
    if args.repos_subcommand == "list":
        print(json.dumps(repositories, ensure_ascii=False, indent=2))
        return
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
        repositories[repo_id] = item
    value = config_settings.normalize_setting("repositories", json.dumps(repositories, ensure_ascii=False))
    config_settings.save_config({**stored, "repositories": value})
    outcome.report_success(f"repository登録を更新した: {repo_id}")
