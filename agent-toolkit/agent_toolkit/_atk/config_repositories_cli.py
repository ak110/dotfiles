from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from agent_toolkit._atk import (
    config_migration,
    config_repositories,
    config_repository_readers,
    config_settings,
    config_shared_migration,
    help_text,
    outcome,
)
from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._git import command as git


def build_parser(sub: argparse._SubParsersAction) -> None:
    parser = help_text.add_command(sub, "repos", **help_text.HELP["atk config repos"])
    commands = help_text.add_subcommands(parser, dest="repos_subcommand", required=True)
    for name in ("add", "set", "remove", "list"):
        command = help_text.add_command(commands, name, **help_text.HELP[f"atk config repos {name}"])
        if name == "list":
            command.add_argument("--json", action="store_true", help="登録をJSONで表示する")
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


def update_repositories(args: argparse.Namespace) -> str | dict[str, Any]:
    config_migration.check_legacy_environment()
    if args.repos_subcommand == "list":
        return config_settings.repository_settings(check_paths=False, read_only=True)
    config_migration.migrate_legacy_settings()
    stored = config_settings.load_config_strict()
    raw = stored.get("repositories", "{}")
    if not isinstance(raw, str):
        raise ValueError("repositoriesの保存値はJSON文字列で指定する")
    local = config_repositories.parse_local(raw)
    repositories = config_repositories.merge(_shared_repositories(), local)
    repo_id, located = config_repositories.identifier(args.repo)
    exists = repo_id in repositories
    if args.repos_subcommand == "add" and exists:
        raise ValueError(f"登録済み: {repo_id}。`atk config repos set`で属性を変更する")
    if args.repos_subcommand != "add" and not exists:
        raise ValueError(f"未登録: {repo_id}。現在の登録: {', '.join(repositories) or 'なし'}")
    item: dict[str, Any] | None = None
    if args.repos_subcommand != "remove":
        item = dict(repositories.get(repo_id, {"process_loop": False, "platforms": ["linux", "windows"]}))
        if args.edit is not None:
            item["edit"] = args.edit
        if args.path is not None:
            item["path"] = args.path
        elif args.repos_subcommand == "add" and located:
            item["path"] = located
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
        if not item.get("path"):
            item.pop("path", None)
        normalized = json.loads(config_settings.normalize_setting("repositories", json.dumps({repo_id: item})))
        validated: dict[str, Any] = normalized[next(iter(normalized))]
        if args.process_loop is True and not validated.get("path"):
            raise ValueError(f"{repo_id}をこの端末で巡回するには--pathでcloneを関連付ける")
        _require_matching_origin(repo_id, validated.get("path"))
        if args.branches is not None:
            _require_remote_branches(repo_id, validated)
        item = validated
    shared_path = config_shared_migration.shared_write_path() if config_repositories.is_shared_id(repo_id) else None
    before = repositories.get(repo_id, {})
    if shared_path is not None:
        config_repository_readers.require_compatible_readers()
    shared_fields = {key for key in config_repositories.SHARED_ATTRIBUTES if getattr(args, key, None) is not None}
    fields = {key for unit in config_repositories.SHARED_UNITS if set(unit) & shared_fields for key in unit}
    changes_shared = args.repos_subcommand != "set" or bool(fields)
    if shared_path is not None and changes_shared:
        attributes = None if item is None else config_repositories.shared_attributes(item)
        config_shared_migration.update_shared_settings(
            shared_path,
            lambda shared: _with_shared_entry(shared, repo_id, attributes, fields if exists else None),
            touched_repositories={
                repo_id: fields if args.repos_subcommand == "set" else set(config_repositories.SHARED_ATTRIBUTES)
            },
        )

    path = config_settings.config_file_path()
    with config_settings.local_config_lock(path):
        latest = config_settings.load_config_strict()
        entries = config_repositories.local_entries(latest.get("repositories", "{}"))
        matches = config_repositories.local_entries_by_id(entries).get(repo_id, [])
        if len(matches) > 1:
            raise ValueError(f"同じリポジトリの端末登録が重複している: {repo_id}")
        old_key, current = matches[0] if matches else (repo_id, None)
        if shared_path is None:
            if item is None:
                entries.pop(old_key, None)
            else:
                entries.pop(old_key, None)
                entries[repo_id] = item
        elif item is None:
            entries.pop(old_key, None)
        elif args.repos_subcommand == "add" or args.path is not None or args.process_loop is not None:
            entry = config_repositories.parse_local_entry(old_key, current)[1] if matches else {}
            if args.path is not None or args.repos_subcommand == "add":
                if item.get("path"):
                    entry["path"] = item["path"]
                else:
                    entry.pop("path", None)
            if args.process_loop is not None:
                entry["process_loop"] = args.process_loop
            elif "process_loop" not in entry:
                entry["process_loop"] = before.get("process_loop", False)
            entries.pop(old_key, None)
            entries[repo_id] = entry
        if entries != config_repositories.local_entries(latest.get("repositories", "{}")):
            config_settings.write_config_locked(path, {**latest, "repositories": json.dumps(entries, ensure_ascii=False)})
    return repo_id


def _shared_repositories() -> dict[str, dict[str, Any]]:
    path = config_settings.shared_settings_path()
    shared = config_settings.load_shared_settings(path).get("repositories") if path is not None else None
    return config_repositories.parse_shared(shared) if shared is not None else {}


def _with_shared_entry(
    shared: dict[str, str],
    repo_id: str,
    attributes: dict[str, Any] | None,
    fields: set[str] | None,
) -> dict[str, str]:
    repositories = config_repositories.parse_shared(shared.get("repositories", "{}"))
    if attributes is None:
        repositories.pop(repo_id, None)
    elif fields is None:
        if repo_id in repositories:
            raise ValueError(f"別の端末が先に登録した: {repo_id}")
        repositories[repo_id] = attributes
    elif fields:
        if repo_id not in repositories:
            raise ValueError(f"共有側に登録がない: {repo_id}。設定画面で残す値を選ぶ")
        repositories[repo_id] = {key: value for key, value in repositories[repo_id].items() if key not in fields} | {
            key: value for key, value in attributes.items() if key in fields
        }
    config_repositories.parse_shared(json.dumps(repositories))
    return {**shared, "repositories": json.dumps(repositories, ensure_ascii=False, sort_keys=True)}


def _require_matching_origin(repo_id: str, path: str | None) -> None:
    if not path or not config_repositories.is_shared_id(repo_id):
        return
    location = config_repositories.locate(pathlib.Path(path))
    if location is None or location.repo_id != repo_id:
        found = "Git作業ツリーではない" if location is None else f"originは{location.repo_id}"
        raise ValueError(f"{path}は{repo_id}のcloneではない（{found}）。同じoriginのcloneを指定する")


def dispatch(args: argparse.Namespace) -> None:
    result = update_repositories(args)
    if isinstance(result, dict):
        if args.json or not is_human_terminal(sys.stdout):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif result:
            _print_repositories(result)
        else:
            print("登録済みのリポジトリはない。")
    else:
        outcome.report_success(f"repository登録を更新した: {result}")


def _print_repositories(repositories: dict[str, Any]) -> None:
    edit = {"allow": "可", "awi": "AWIのみ", "deny": "不可"}
    for repo, entry in sorted(repositories.items()):
        print(repo)
        print(f"  編集: {edit[entry['edit']]} / 巡回: {'対象' if entry['process_loop'] else '対象外'}")
        print(f"  検証OS: {', '.join(entry['platforms'])}")
        if path := entry.get("path"):
            print(f"  作業ツリー: {path}")
        else:
            print(f"  この端末には未登録: atk config repos set {repo} --path <clone>")
        if branches := entry.get("branches"):
            print(f"  系統: {', '.join(branches)}（省略時: {entry['default_branch']}）")
        for target, source in entry.get("merge_from", {}).items():
            print(f"  リリース前の取込: {source} → {target}")
        if entry.get("github_issues"):
            print("  GitHub Issue・PRの取込: 有効")
        if (authors := entry.get("github_issue_trusted_authors")) is not None:
            print(f"  Issueの自動投入を許す投稿者: {', '.join(authors) or 'なし'}")


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
