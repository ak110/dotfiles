"""レーン資源の作成・再利用と所有の移管。"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import typing

from agent_toolkit._atk import lane_activity as _lane_activity
from agent_toolkit._atk import lane_cleanup as _lane_cleanup
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._common import state_paths, worktree_hooks
from agent_toolkit._git import command as git_command


def release_fixed(worktree: pathlib.Path, session_id: str) -> None:
    """固定作成先を保持する別sessionの登録を、既存の回収条件を満たす場合だけ回収する。"""
    root = state_paths.state_dir() / "lanes"
    for path in sorted(root.glob("*/lane-*.json")) if root.exists() else []:
        owner = path.parent.name
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except OSError, UnicodeError, ValueError:
            # 読めない登録は所有を判定できない。作成先の実体の有無で上書きを防ぐ。
            continue
        if (
            owner == session_id
            or not isinstance(record, dict)
            or record.get("worktree") != str(worktree)
            or _lane_records.recovered(record)
        ):
            continue
        result = _lane_cleanup.cleanup_session(owner, only=path)
        if path.exists():
            reasons = [held["reason"] for held in result["retained"] if held["record_path"] == str(path)]
            raise ValueError(
                f"固定作成先{worktree}を別sessionの登録が保持している: {'; '.join(reasons) or '回収が未完了'}。"
                f"成果を引き継ぐ場合は--reuse-record {path}を指定し、"
                f"回収する場合は理由を解消して`atk lane delete --session-id {owner}`を実行してから再実行する"
            )


def prepare(record: dict[str, typing.Any], path: pathlib.Path) -> None:
    repo = pathlib.Path(record["repo"])
    worktree = pathlib.Path(record["worktree"])
    branch = record["branch"]
    refs = _lane_records.branches(repo)
    existing_worktrees = _lane_records.worktrees(repo)
    if str(worktree) not in existing_worktrees:
        if worktree.exists():
            raise ValueError("作成先に未登録の実体がある。登録と残った資源を確認する")
        if record.get("project_hook"):
            directory = _lane_records.hook_diagnostics(record, "WorktreeCreate")
            created = worktree_hooks.create(
                repo,
                record["hook_name"],
                record["session_id"],
                record["procedure_timeout"],
                diagnostics_directory=directory,
            )
            if str(created) in existing_worktrees:
                raise ValueError("WorktreeCreate hookが既存の作業ツリーを返した。別所有の資源を保持した")
            record["worktree"] = str(created)
            _lane_records.save(path, record)
            worktree = created
        elif record["commands"].get("create"):
            _lane_records.procedure(record, "create")
        elif branch in refs:
            if _lane_records.git(repo, "rev-parse", f"refs/heads/{branch}") != record["initial_head"]:
                raise ValueError("作成途中のbranchが開始版から変わっている。成果を確認して回復する")
            _lane_records.git(repo, "worktree", "add", str(worktree), branch)
        else:
            _lane_records.git(repo, "worktree", "add", "-b", branch, str(worktree), record["initial_head"])
    registered = _lane_records.worktrees(repo).get(str(worktree))
    if _lane_records.common(worktree) != record["common_dir"]:
        raise ValueError("作成先のGit共通ディレクトリが所有記録と異なる。途中資源を保持した")
    if record.get("project_hook") and registered and registered != branch:
        # hookのbranchと成果を残し、そのHEADからレーンの専用branchへ接続する。
        _lane_records.git(worktree, "-c", "submodule.recurse=false", "switch", "-c", branch)
        registered = _lane_records.worktrees(repo).get(str(worktree))
    if registered == "":
        branch_arguments = () if branch in refs else ("-c",)
        start_arguments = (record["initial_head"],) if branch_arguments else ()
        _lane_records.git(worktree, "-c", "submodule.recurse=false", "switch", *branch_arguments, branch, *start_arguments)
        registered = _lane_records.worktrees(repo).get(str(worktree))
    if registered != branch or _lane_records.common(worktree) != record["common_dir"]:
        raise ValueError("作成先のGit登録が入力と一致しない。記録と作成先を確認する")
    _lane_records.procedure(record, "prepare")
    record["ready"] = True
    _lane_records.save(path, record)


def reuse_source(args: argparse.Namespace, session_id: str) -> tuple[pathlib.Path | None, str | None]:
    source = args.reuse_record
    if source is None:
        return None, None
    source = source.resolve()
    owner = source.parent.name
    if source != _lane_records.session_directory(owner) / f"{args.lane}.json":
        raise ValueError("reuse-recordは登録済みレーンのrecord_pathを指定する")
    if owner == session_id:
        return None, None
    return source, owner


def transfer(source: pathlib.Path, destination: pathlib.Path, args: argparse.Namespace, session_id: str) -> None:
    record = json.loads(source.read_text(encoding="utf-8"))
    if record["session_id"] not in {source.parent.name, session_id}:
        raise ValueError("再利用する登録の所有を確認できない。record_pathを確認する")
    repo = args.repo.resolve()
    worktree = pathlib.Path(record["worktree"])
    if record["common_dir"] != _lane_records.common(repo) or record["base_branch"] != args.base_branch:
        raise ValueError("再利用するrepoまたは統合先が異なる。元の入力で再実行する")
    state = _lane_records.resource_state(record)
    if not state.worktree_path:
        raise ValueError(
            f"再利用元のworktreeが無い。`atk lane delete --session-id {record['session_id']}`で登録を回収してから作成し直す"
        )
    if not state.worktree_registered or _lane_records.worktrees(repo).get(str(worktree)) != record["branch"]:
        raise ValueError("再利用するworktreeの登録が異なる。資源を確認する")
    activity = _lane_activity.worktree_activity(worktree)
    if activity:
        raise ValueError(f"再利用元はまだ使用中: {activity}。担当と外部プロセスの終了を待つ")
    record.update(session_id=session_id, record_path=str(destination), selection_file=str(args.selection_file.resolve()))
    _lane_records.require_selection(record)
    _lane_records.save(source, record)
    # 本文を新所有者へ更新してからrenameする。中断しても同じreuse-recordで再開でき、二重登録を生じさせない。
    os.replace(source, destination)


def create(args: argparse.Namespace, session_id: str) -> dict[str, typing.Any]:
    """準備の前に所有を登録し、準備済み資源を返す。失敗時も登録を保持する。"""
    if not _lane_records.LANE.fullmatch(args.lane):
        raise ValueError("レーンはlane-NNで指定する")
    for path in (args.repo, args.selection_file, args.worktree, args.commands_file, args.reuse_record):
        if path is not None and not path.is_absolute():
            raise ValueError("repo・選定・作成先・手順・再利用記録は絶対パスで指定する")
    repo = args.repo.resolve()
    common = _lane_records.common(repo)
    commands = _lane_records.commands(args.commands_file)
    project_hook = worktree_hooks.configured(repo)
    if project_hook and commands.get("create"):
        raise ValueError("WorktreeCreate hookとcreate手順は同時に指定できない")
    source, source_owner = reuse_source(args, session_id)
    fixed = None if args.worktree else _lane_records.fixed_worktree(repo, args.lane)
    if (
        fixed is not None
        and source is None
        and not (_lane_records.session_directory(session_id) / f"{args.lane}.json").exists()
    ):
        # 別sessionの所有ロックを取るため、自sessionのロックより前に前回資源を回収する。
        if project_hook:
            for entry in _lane_records.list_resources()["records"]:
                previous = entry["record"]
                if (
                    previous.get("common_dir") == common
                    and previous.get("lane") == args.lane
                    and previous.get("session_id") != session_id
                ):
                    release_fixed(pathlib.Path(previous["worktree"]), session_id)
        release_fixed(fixed, session_id)
    owners = {session_id}
    if source_owner is not None:
        owners.add(source_owner)
    with contextlib.ExitStack() as stack:
        # 統合中の同じレーンの登録を書き換えないよう、レーン単位の排他を所有session単位より先に取る。
        lane_records: set[pathlib.Path] = {_lane_records.session_directory(session_id) / f"{args.lane}.json"}
        if source is not None:
            lane_records.add(source)
        for lane_record in sorted(lane_records):
            stack.enter_context(_lane_records.lane_locked(lane_record))
        directories = {owner: stack.enter_context(_lane_records.locked(owner)) for owner in sorted(owners)}
        directory = directories[session_id]
        path = directory / f"{args.lane}.json"
        if not path.exists() and source is not None:
            transfer(source, path, args, session_id)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing["common_dir"] != common or existing["base_branch"] != args.base_branch:
                raise ValueError("既存レーンのrepoまたは統合先が入力と異なる。登録の入力で再実行する")
            existing["selection_file"] = str(args.selection_file.resolve())
            _lane_records.require_selection(existing)
            if args.commands_file is not None:
                existing["commands"] = commands
            if not existing.get("ready"):
                prepare(existing, path)
                return dict(existing, record_path=str(path), reused=True)
            if _lane_records.worktrees(repo).get(existing["worktree"]) == existing["branch"]:
                refresh_finalized_lane(existing)
                _lane_records.save(path, existing)
                return dict(existing, record_path=str(path), reused=True)
            raise ValueError(f"レーンの作成が未完了: {path}。記録と残った資源を確認して回復する")
        initial_head = _lane_records.git(repo, "rev-parse", "--verify", f"refs/heads/{args.base_branch}")
        branch = args.branch or f"process-wi-{session_id}-{args.lane}"
        _lane_records.git(repo, "check-ref-format", "--branch", branch)
        if branch in _lane_records.branches(repo):
            raise ValueError(f"専用branchが既にある: {branch}。所有登録のあるレーンを再利用するか別名を指定する")
        if fixed is None:
            worktree = args.worktree.resolve()
            if worktree.exists():
                raise ValueError(f"作成先が既にある: {worktree}。未使用の作成先を指定する")
        else:
            worktree = fixed
            if worktree.exists() or str(worktree) in _lane_records.worktrees(repo):
                raise ValueError(
                    f"固定作成先に所有登録の無い資源がある: {worktree}。"
                    "内容とGit登録を確認し、成果を保存してから移動または削除して再実行する"
                )
            _lane_records.exclude_placement(_lane_records.main_worktree(repo))
            worktree.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, typing.Any] = {
            "session_id": session_id,
            "lane": args.lane,
            "repo": str(repo),
            "common_dir": common,
            "base_branch": args.base_branch,
            "branch": branch,
            "worktree": str(worktree),
            # 旧配置の登録だけが個別managed-tempを持ち、回収時に同じ値を使う。
            "managed_temp": None,
            "selection_file": str(args.selection_file.resolve()),
            "commands": commands,
            "procedure_timeout": args.timeout,
            "initial_head": initial_head,
            "record_path": str(path),
            "ready": False,
            "teardown_done": False,
            "project_hook": project_hook,
            "hook_name": f"process-wi-{args.lane}",
        }
        _lane_records.require_selection(record)
        _lane_records.save(path, record)
        prepare(record, path)
        return dict(record, record_path=str(path), reused=False)


def refresh_finalized_lane(record: dict[str, typing.Any]) -> None:
    """再開対象の完了済み資源だけを、成果を失わないfast-forwardで最新基点へ進める。"""
    integration = record.get("integration", {})
    if integration.get("phase") != "finalized" or record["selection"]["integrated"]:
        return
    worktree = pathlib.Path(record["worktree"])
    current = _lane_records.git(worktree, "rev-parse", "HEAD")
    base = _lane_records.git(pathlib.Path(record["repo"]), "rev-parse", f"refs/heads/{record['base_branch']}")
    if current == base:
        return
    known_head = integration.get("new_head") or integration.get("integrated_head")
    if current != known_head or _lane_records.git(worktree, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("再開するレーンに最終化後の成果か未コミット変更がある。現在の担当へ戻して成果を検収する")
    writers = _lane_activity.unowned_writers((worktree,), _lane_activity.caller_chain() | {record["session_id"]})
    if writers:
        raise ValueError(
            f"再開するレーンを別の担当が使用中: {[writer.session_id for writer in writers]}。終了を待って再実行する"
        )
    result = git_command.run(["merge-base", "--is-ancestor", current, base], worktree, check=False)
    if result.returncode:
        raise ValueError(
            "統合先と再開レーンをfast-forwardで対応付けられない。保存済み成果と履歴を検収し、元レーンの資源を再準備する"
        )
    _lane_records.git(worktree, "-c", "submodule.recurse=false", "merge", "--ff-only", base)
