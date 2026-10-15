"""レーン資源を作成側で登録し、所有セッションの終了時に回収する。"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import time
import typing
from collections.abc import Generator

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import psutil
lazy import yaml

from agent_toolkit._atk import help_text, managed_temp, outcome, run_command
from agent_toolkit._common import atomic_file, file_lock, session_launchers, session_release, state_paths, worktree_hooks
from agent_toolkit._common.wait_schedule import get_wait_timeout
from agent_toolkit._git import command as git_command
from agent_toolkit._git import worktree_exclude
from agent_toolkit._plan import selection
lazy from agent_toolkit._common import session_registry

SESSION_ENV = "AGENT_TOOLKIT_LANE_SESSION_ID"
_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_LANE = re.compile(r"lane-[0-9]{2}\Z")
_TIMEOUT = 120


def current_session_id() -> str | None:
    """同じ実行の作成と終了操作が使う識別子を解決する。"""
    for name in (SESSION_ENV, "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        value = os.environ.get(name)
        if value and _ID.fullmatch(value):
            return value
    return None


def _directory(session_id: str) -> pathlib.Path:
    if not _ID.fullmatch(session_id):
        raise ValueError("セッション識別子は英数字・ハイフン・アンダースコアで指定する")
    return state_paths.state_dir() / "lanes" / session_id


@contextlib.contextmanager
def _locked(session_id: str) -> Generator[pathlib.Path]:
    directory = _directory(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "operation.lock").open("a+", encoding="utf-8") as stream:
        file_lock.acquire_lock(stream)
        try:
            yield directory
        finally:
            file_lock.release_lock(stream)


def _save(path: pathlib.Path, record: dict[str, typing.Any]) -> None:
    atomic_file.atomic_write(path, json.dumps(record, ensure_ascii=False) + "\n")


def _git(repo: pathlib.Path, *argv: str) -> str:
    result = git_command.run(list(argv), repo, capture_output=True, text=True, check=False, timeout=_TIMEOUT)
    if result.returncode:
        raise ValueError(f"git {argv[0]}が終了{result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def _common(repo: pathlib.Path) -> str:
    return _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")


def _branches(repo: pathlib.Path) -> list[str]:
    return [
        ref.removeprefix("refs/heads/") for ref in _git(repo, "for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
    ]


def _worktrees(repo: pathlib.Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    current: str | None = None
    for line in _git(repo, "worktree", "list", "--porcelain", "-z").split("\0"):
        if line.startswith("worktree "):
            current = str(pathlib.Path(line[9:]).resolve())
            entries[current] = ""
        elif current and line.startswith("branch refs/heads/"):
            entries[current] = line[18:]
    return entries


def _main_worktree(repo: pathlib.Path) -> pathlib.Path:
    """Linked worktreeから呼んでも、同じGitリポジトリの主作業ツリーへ解決する。"""
    block = _git(repo, "worktree", "list", "--porcelain").split("\n\n", 1)[0].splitlines()
    if not block or not block[0].startswith("worktree ") or "bare" in block:
        raise ValueError("repoが主作業ツリーを持たない。--worktreeで作成先を指定する")
    return pathlib.Path(block[0][9:]).resolve()


def _exclude_placement(main: pathlib.Path) -> None:
    """主作業ツリーの状態へ固定作成先が未追跡として現れないよう、info/excludeへ加える。"""
    try:
        worktree_exclude.ensure_excluded(main)
    except worktree_exclude.ExcludeError as error:
        raise ValueError(f"固定作成先の除外を設定できない（{error}）。`git status`と`info/exclude`を確認する") from error


def _fixed_worktree(repo: pathlib.Path, lane: str) -> pathlib.Path:
    # --worktreeを省略した作成先は主作業ツリーからの相対で固定し、session・日時・呼出し元を識別へ含めない。
    return _main_worktree(repo).joinpath(*worktree_exclude.WORKTREE_PARENT.parts, f"process-wi-{lane}")


def _release_fixed(worktree: pathlib.Path, session_id: str) -> None:
    """固定作成先を保持する別sessionの登録を、既存の回収条件を満たす場合だけ回収する。"""
    root = state_paths.state_dir() / "lanes"
    for path in sorted(root.glob("*/lane-*.json")) if root.exists() else []:
        owner = path.parent.name
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except OSError, UnicodeError, ValueError:
            # 読めない登録は所有を判定できない。作成先の実体の有無で上書きを防ぐ。
            continue
        if owner == session_id or not isinstance(record, dict) or record.get("worktree") != str(worktree):
            continue
        result = cleanup_session(owner, only=path)
        if path.exists():
            reasons = [held["reason"] for held in result["retained"] if held["record_path"] == str(path)]
            raise ValueError(
                f"固定作成先{worktree}を別sessionの登録が保持している: {'; '.join(reasons) or '回収が未完了'}。"
                f"成果を引き継ぐ場合は--reuse-record {path}を指定し、"
                f"回収する場合は理由を解消して`atk lane delete --session-id {owner}`を実行してから再実行する"
            )


def _commands(path: pathlib.Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) - {"create", "prepare", "delete"}:
        raise ValueError("手順ファイルはcreate・prepare・deleteのargvを持つJSON objectで指定する")
    for argv in value.values():
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("各手順は空でない文字列のargv配列で指定する")
    return value


def _procedure(record: dict[str, typing.Any], phase: str) -> None:
    argv = record["commands"].get(phase)
    if argv is None:
        return
    fields = {name: record[name] for name in ("repo", "worktree", "branch", "lane")}
    expanded = []
    for argument in argv:
        for name, value in fields.items():
            argument = argument.replace("{" + name + "}", value)
        expanded.append(argument)
    observed, code, failure = run_command.execute(expanded, pathlib.Path(record["repo"]), record["procedure_timeout"])
    record.setdefault("procedure_results", {})[phase] = observed
    _save(pathlib.Path(record["record_path"]), record)
    if code:
        raise ValueError(f"{phase}手順が終了{code}: {failure or observed['record_path']}。手順を直して同じ操作を再実行する")


def _prepare(record: dict[str, typing.Any], path: pathlib.Path) -> None:
    repo = pathlib.Path(record["repo"])
    worktree = pathlib.Path(record["worktree"])
    branch = record["branch"]
    refs = _branches(repo)
    existing_worktrees = _worktrees(repo)
    if str(worktree) not in existing_worktrees:
        if worktree.exists():
            raise ValueError("作成先に未登録の実体がある。登録と残った資源を確認する")
        if record.get("project_hook"):
            directory = _hook_diagnostics(record, "WorktreeCreate")
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
            _save(path, record)
            worktree = created
        elif record["commands"].get("create"):
            _procedure(record, "create")
        elif branch in refs:
            if _git(repo, "rev-parse", f"refs/heads/{branch}") != record["initial_head"]:
                raise ValueError("作成途中のbranchが開始版から変わっている。成果を確認して回復する")
            _git(repo, "worktree", "add", str(worktree), branch)
        else:
            _git(repo, "worktree", "add", "-b", branch, str(worktree), record["initial_head"])
    registered = _worktrees(repo).get(str(worktree))
    if _common(worktree) != record["common_dir"]:
        raise ValueError("作成先のGit共通ディレクトリが所有記録と異なる。途中資源を保持した")
    if record.get("project_hook") and registered and registered != branch:
        # hookのbranchと成果を残し、そのHEADからレーンの専用branchへ接続する。
        _git(worktree, "switch", "-c", branch)
        registered = _worktrees(repo).get(str(worktree))
    if registered == "":
        branch_arguments = () if branch in refs else ("-c",)
        start_arguments = (record["initial_head"],) if branch_arguments else ()
        _git(worktree, "switch", *branch_arguments, branch, *start_arguments)
        registered = _worktrees(repo).get(str(worktree))
    if registered != branch or _common(worktree) != record["common_dir"]:
        raise ValueError("作成先のGit登録が入力と一致しない。記録と作成先を確認する")
    _procedure(record, "prepare")
    record["ready"] = True
    _save(path, record)


def _hook_diagnostics(record: dict[str, typing.Any], event: str) -> pathlib.Path:
    """操作ごとの診断を期限付き領域へ置き、既存所有記録へ最新の所在を残す。"""
    directory = managed_temp.create_managed_temp("worktree-hook")
    record.setdefault("hook_diagnostics", {})[event] = str(directory)
    _save(pathlib.Path(record["record_path"]), record)
    return directory


def _reuse_source(args: argparse.Namespace, session_id: str) -> tuple[pathlib.Path | None, str | None]:
    source = args.reuse_record
    if source is None:
        return None, None
    source = source.resolve()
    owner = source.parent.name
    if source != _directory(owner) / f"{args.lane}.json":
        raise ValueError("reuse-recordは登録済みレーンのrecord_pathを指定する")
    if owner == session_id:
        return None, None
    return source, owner


def _transfer(source: pathlib.Path, destination: pathlib.Path, args: argparse.Namespace, session_id: str) -> None:
    record = json.loads(source.read_text(encoding="utf-8"))
    if record["session_id"] not in {source.parent.name, session_id}:
        raise ValueError("再利用する登録の所有を確認できない。record_pathを確認する")
    repo = args.repo.resolve()
    worktree = pathlib.Path(record["worktree"])
    if record["common_dir"] != _common(repo) or record["base_branch"] != args.base_branch:
        raise ValueError("再利用するrepoまたは統合先が異なる。元の入力で再実行する")
    if _worktrees(repo).get(str(worktree)) != record["branch"]:
        raise ValueError("再利用するworktreeの登録が異なる。資源を確認する")
    activity = _worktree_activity(worktree)
    if activity:
        raise ValueError(f"再利用元はまだ使用中: {activity}。担当と外部プロセスの終端を待つ")
    record.update(session_id=session_id, record_path=str(destination), selection_file=str(args.selection_file.resolve()))
    _save(source, record)
    # 本文を新所有者へ更新してからrenameする。中断しても同じreuse-recordで再開でき、二重登録を生じさせない。
    os.replace(source, destination)


def create(args: argparse.Namespace, session_id: str) -> dict[str, typing.Any]:
    """準備の前に所有を登録し、準備済み資源を返す。失敗時も登録を保持する。"""
    if not _LANE.fullmatch(args.lane):
        raise ValueError("レーンはlane-NNで指定する")
    for path in (args.repo, args.selection_file, args.worktree, args.commands_file, args.reuse_record):
        if path is not None and not path.is_absolute():
            raise ValueError("repo・選定・作成先・手順・再利用記録は絶対パスで指定する")
    repo = args.repo.resolve()
    common = _common(repo)
    commands = _commands(args.commands_file)
    project_hook = worktree_hooks.configured(repo)
    if project_hook and commands.get("create"):
        raise ValueError("WorktreeCreate hookとcreate手順は同時に指定できない")
    source, source_owner = _reuse_source(args, session_id)
    fixed = None if args.worktree else _fixed_worktree(repo, args.lane)
    if fixed is not None and source is None and not (_directory(session_id) / f"{args.lane}.json").exists():
        # 別sessionの所有ロックを取るため、自sessionのロックより前に前回資源を回収する。
        if project_hook:
            for entry in list_resources()["records"]:
                previous = entry["record"]
                if (
                    previous.get("common_dir") == common
                    and previous.get("lane") == args.lane
                    and previous.get("session_id") != session_id
                ):
                    _release_fixed(pathlib.Path(previous["worktree"]), session_id)
        _release_fixed(fixed, session_id)
    owners = {session_id}
    if source_owner is not None:
        owners.add(source_owner)
    with contextlib.ExitStack() as stack:
        directories = {owner: stack.enter_context(_locked(owner)) for owner in sorted(owners)}
        directory = directories[session_id]
        path = directory / f"{args.lane}.json"
        if not path.exists() and source is not None:
            _transfer(source, path, args, session_id)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing["common_dir"] != common or existing["base_branch"] != args.base_branch:
                raise ValueError("既存レーンのrepoまたは統合先が入力と異なる。登録の入力で再実行する")
            existing["selection_file"] = str(args.selection_file.resolve())
            if args.commands_file is not None:
                existing["commands"] = commands
            if not existing.get("ready"):
                _prepare(existing, path)
                return dict(existing, record_path=str(path), reused=True)
            if _worktrees(repo).get(existing["worktree"]) == existing["branch"]:
                _save(path, existing)
                return dict(existing, record_path=str(path), reused=True)
            raise ValueError(f"レーンの作成が未完了: {path}。記録と残った資源を確認して回復する")
        initial_head = _git(repo, "rev-parse", "--verify", f"refs/heads/{args.base_branch}")
        branch = args.branch or f"process-wi-{session_id}-{args.lane}"
        _git(repo, "check-ref-format", "--branch", branch)
        if branch in _branches(repo):
            raise ValueError(f"専用branchが既にある: {branch}。所有登録のあるレーンを再利用するか別名を指定する")
        if fixed is None:
            worktree = args.worktree.resolve()
            if worktree.exists():
                raise ValueError(f"作成先が既にある: {worktree}。未使用の作成先を指定する")
        else:
            worktree = fixed
            if worktree.exists() or str(worktree) in _worktrees(repo):
                raise ValueError(
                    f"固定作成先に所有登録の無い資源がある: {worktree}。"
                    "内容とGit登録を確認し、成果を保存してから移動または削除して再実行する"
                )
            _exclude_placement(_main_worktree(repo))
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
        _save(path, record)
        _prepare(record, path)
        return dict(record, record_path=str(path), reused=False)


def _session_activity(worktree: pathlib.Path, allowed: frozenset[str] = frozenset()) -> str | None:
    """session登録簿の解決結果から、その作業ツリーを使う担当の終了を判定する。

    終端の判定は`session_registry.resolve`に従い、所有側が終端を公開せずに終了して実行プロセスも
    存在しない記録は終了した担当として扱う。
    """
    state_root = state_paths.state_dir()
    directory = session_launchers.registry_directory(state_root)
    if not directory.exists():
        return None
    for path in directory.glob("*.json"):
        if path.stem in allowed:
            continue
        try:
            resolution = session_registry.resolve(path.stem, state_root=state_root)
        except ValueError as exc:
            raise ValueError(f"担当登録の形式を確認できない: {path}") from exc
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ValueError(f"担当登録の形式を確認できない: {path}")
        info = resolution.resume_info
        if (
            resolution.state is session_registry.Resolution.RUNNING
            and info is not None
            and info.cwd
            and pathlib.Path(info.cwd).resolve().is_relative_to(worktree)
        ):
            return f"担当の終了を確認できない: {path.stem}"
    return None


def _process_activity(worktree: pathlib.Path) -> str | None:
    username = psutil.Process().username()
    for process in psutil.process_iter(["username"]):
        if process.info["username"] != username:
            continue
        try:
            cwd = pathlib.Path(process.cwd()).resolve()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            cwd = None
        if cwd is not None and cwd.is_relative_to(worktree):
            return f"外部プロセスが使用中: pid={process.pid}"
        # 所属の分からない別プロセスの取得失敗を、当該レーンの保持条件へ広げない。
        try:
            argv = process.cmdline()
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
        for argument in argv:
            candidate = pathlib.Path(argument.split("=", 1)[-1])
            if candidate.is_absolute() and candidate.is_relative_to(worktree):
                return f"外部プロセスが資源を参照中: pid={process.pid}"
    return None


def _worktree_activity(worktree: pathlib.Path) -> str | None:
    """所有側へ終端資源の解放を要求してから、担当と外部プロセスの使用を再判定する。"""
    directory = session_registry.registry_directory()
    deadline: float | None = None
    released: list[str] = []
    for path in directory.glob("*.json"):
        resolution = session_registry.resolve(path.stem)
        info = resolution.resume_info
        if info is None or not info.cwd or not pathlib.Path(info.cwd).resolve().is_relative_to(worktree):
            continue
        instruction = f"agents_serverのstopへsession_id={path.stem}を渡し、同じlane deleteを再実行する"
        if resolution.state is session_registry.Resolution.RUNNING:
            return (
                f"担当の終了を確認できない: session_id={path.stem}。"
                "自然終端を確認し、atk agents waitで結果を回収して再開する予定が無ければ、"
                f"{instruction}"
            )
        if resolution.state is session_registry.Resolution.TERMINAL:
            if deadline is None:
                deadline = time.monotonic() + get_wait_timeout("main")
            reason = session_release.request_release(path.stem, worktree, deadline=deadline)
            if reason:
                return f"session_id={path.stem}: {reason}。{instruction}"
            released.append(path.stem)
    while True:
        activity = _session_activity(worktree) or _process_activity(worktree)
        if not activity:
            return None
        if not released or deadline is None or time.monotonic() >= deadline:
            if released:
                identifiers = ",".join(released)
                return (
                    f"{activity}。session_id={identifiers}。agents_serverのstopへ各session_idを渡し、"
                    "同じlane deleteを再実行する"
                )
            return activity
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _eligible(record: dict[str, typing.Any]) -> str | None:
    repo = pathlib.Path(record["repo"])
    worktree = pathlib.Path(record["worktree"])
    if _common(repo) != record["common_dir"]:
        return "repoのGit共通ディレクトリが所有記録と異なる"
    if not record.get("ready"):
        return "作成または環境準備が未完了"
    document = yaml.safe_load(pathlib.Path(record["selection_file"]).read_text(encoding="utf-8"))
    if record["lane"] not in selection.integrated_lanes(document):
        return "選定の統合状態が未統合"
    activity = _worktree_activity(worktree)
    if activity:
        return activity
    trees = _worktrees(repo)
    if str(worktree) in trees:
        if trees[str(worktree)] != record["branch"] or _common(worktree) != record["common_dir"]:
            return "worktreeの登録が所有記録と異なる"
        if _git(worktree, "status", "--porcelain=v1", "--untracked-files=all"):
            return "未commitまたは未追跡の変更がある"
    elif worktree.exists():
        return "登録の無いworktreeが残っている"
    refs = _branches(repo)
    if record["branch"] in refs:
        _git(repo, "merge-base", "--is-ancestor", f"refs/heads/{record['branch']}", f"refs/heads/{record['base_branch']}")
    return None


def cleanup_session(session_id: str | None, *, only: pathlib.Path | None = None) -> dict[str, typing.Any]:
    """全登録を先に判定し、安全な対象だけを回収する。結果は削除対象の外へ保持する。

    onlyを指定すると、その登録だけを同じ条件で判定・回収する。
    """
    result: dict[str, typing.Any] = {"session_id": session_id, "removed": [], "retained": []}
    if session_id is None or not _directory(session_id).exists():
        return result
    with _locked(session_id) as directory:
        records: list[tuple[pathlib.Path, dict[str, typing.Any]]] = []
        for path in sorted(directory.glob("lane-*.json")):
            if only is not None and path != only:
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["session_id"] != session_id or path.stem != record["lane"]:
                    raise ValueError("登録の所有sessionまたはlaneが異なる")
                reason = _eligible(record)
                if reason:
                    result["retained"].append({"record_path": str(path), "reason": reason})
                else:
                    records.append((path, record))
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, psutil.Error) as error:
                result["retained"].append({"record_path": str(path), "reason": str(error)})
        result_index = directory / "cleanup-result.json"
        result_path = managed_temp.create_managed_temp("lane-cleanup") / "cleanup-result.json"
        result["result_path"] = str(result_path)
        result["pending"] = [record["lane"] for _path, record in records]
        # 保存先を確保して全登録の判定を残してから削除する。保存失敗では資源を保持する。
        _save(result_path, result)
        _save(result_index, {"session_id": session_id, "result_path": str(result_path)})
        for path, record in records:
            try:
                # 判定後に状態が変わった対象も、削除の直前に再判定する。
                reason = _eligible(record)
                if reason:
                    raise ValueError(reason)
                if not record["teardown_done"]:
                    _procedure(record, "delete")
                    record["teardown_done"] = True
                    _save(path, record)
                reason = _eligible(record)
                if reason:
                    raise ValueError(reason)
                repo = pathlib.Path(record["repo"])
                if record.get("project_hook") and worktree_hooks.configured(repo, "WorktreeRemove"):
                    worktree_hooks.remove(
                        repo,
                        pathlib.Path(record["worktree"]),
                        session_id,
                        record["procedure_timeout"],
                        diagnostics_directory=_hook_diagnostics(record, "WorktreeRemove"),
                    )
                elif record["worktree"] in _worktrees(repo):
                    _git(repo, "worktree", "remove", record["worktree"])
                refs = _branches(repo)
                if record["branch"] in refs:
                    _git(
                        repo,
                        "merge-base",
                        "--is-ancestor",
                        f"refs/heads/{record['branch']}",
                        f"refs/heads/{record['base_branch']}",
                    )
                    _git(repo, "branch", "-D", record["branch"])
                if record["managed_temp"]:
                    managed_temp.cleanup_managed_temp(pathlib.Path(record["managed_temp"]))
                path.unlink()
                result["removed"].append(record["lane"])
            except (OSError, ValueError, KeyError, subprocess.SubprocessError, managed_temp.ManagedTempError) as error:
                result["retained"].append({"record_path": str(path), "reason": str(error)})
            result["pending"].remove(record["lane"])
            _save(result_path, result)
        for retained in result["retained"]:
            retained["next_action"] = "理由を解消し、所有登録の入力で作成を再開するか同じlane deleteで残る回収を実行する"
        _save(result_path, result)
    return result


def _positive_timeout(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("上限秒数は有限の正の値で指定する")
    return seconds


def list_resources(session_id: str | None = None) -> dict[str, typing.Any]:
    """前回の残存資源と回収結果を読む。新しい所有登録や削除を行わない。"""
    root = state_paths.state_dir() / "lanes"
    directories = [_directory(session_id)] if session_id else sorted(root.iterdir()) if root.exists() else []
    records: list[dict[str, typing.Any]] = []
    results: list[dict[str, typing.Any]] = []
    errors: list[dict[str, typing.Any]] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("登録または回収結果はJSON objectである必要がある")
                if path.name == "cleanup-result.json" and "result_path" in value:
                    observed_path = pathlib.Path(value["result_path"])
                    if not observed_path.is_absolute():
                        raise ValueError("回収結果の保存先は絶対パスである必要がある")
                    if not observed_path.is_file():
                        results.append({"record_path": str(path), "record": value, "expired": True})
                        continue
                    value = json.loads(observed_path.read_text(encoding="utf-8"))
                target = results if path.name == "cleanup-result.json" else records
                target.append({"record_path": str(path), "record": value})
            except (OSError, UnicodeError, ValueError) as error:
                errors.append({"record_path": str(path), "reason": str(error)})
    return {"records": records, "results": results, "errors": errors}


def _integration_input(args: argparse.Namespace, record: dict[str, typing.Any]) -> dict[str, typing.Any]:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) - {"plans", "wis", "verification", "handoff"}:
        raise ValueError("manifestはplans・wis・verification・handoffを持つobjectで指定する")
    for name in ("plans", "wis"):
        rows = manifest.get(name)
        if not isinstance(rows, list):
            raise ValueError(f"manifestの{name}は順序付き配列で指定する")
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or not {"name", "commands"} <= set(row)
                or set(row) - {"name", "commands", "saved"}
                or not isinstance(row["name"], str)
            ):
                raise ValueError(f"{name}の各項目はnameとcommandsを持つobjectで指定する")
            if row["name"] in seen:
                raise ValueError(f"{name}の名前が重複している: {row['name']}")
            seen.add(row["name"])
            if name == "plans" and not row["commands"]:
                saved = pathlib.Path(row.get("saved", ""))
                if not saved.is_absolute() or not saved.is_file() or saved.name != row["name"]:
                    raise ValueError(f"保存済み計画の所在をsavedへ絶対パスで指定する: {row['name']}")
    commands = [*manifest.get("verification", [])]
    for name in ("plans", "wis"):
        for row in manifest[name]:
            commands.extend(row["commands"])
    for argv in commands:
        if not isinstance(argv, list) or not argv or not all(isinstance(value, str) for value in argv):
            raise ValueError("各操作は空でない文字列のargv配列で指定する")
    if not manifest["plans"]:
        handoff = pathlib.Path(manifest.get("handoff", ""))
        if not handoff.is_absolute() or not handoff.is_file():
            raise ValueError("計画なしでは既存の引き継ぎ記録をhandoffへ絶対パスで指定する")
    document = yaml.safe_load(pathlib.Path(record["selection_file"]).read_text(encoding="utf-8"))
    expected = {
        row[selection.WI_KEY]
        for row in selection.decisions(document) or []
        if isinstance(row, dict) and row.get(selection.LANE_KEY) == record["lane"]
    }
    if expected and expected != {row["name"] for row in manifest["wis"]}:
        raise ValueError("manifestのWI集合が選定の今回のレーン集合と一致しない")
    return manifest


def _integration_command(
    record: dict[str, typing.Any], state: dict[str, typing.Any], key: str, argv: list[str], cwd: pathlib.Path
) -> None:
    if key in state["completed"]:
        return
    observed, code, failure = run_command.execute(argv, cwd, record["procedure_timeout"])
    state.setdefault("results", {})[key] = observed
    if code == 0:
        state["completed"].append(key)
    _save(pathlib.Path(record["record_path"]), record)
    if code:
        raise ValueError(f"統合の{key}が終了{code}: {failure or observed['record_path']}。同じlane integrateで再開する")


def _atk_script(*arguments: str) -> list[str]:
    # 統合先cwdやPATH上の旧プラグインへ版を切り替えず、現在のCLIと同じ実体・依存環境を使う。
    return [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "atk.py"), *arguments]


def _amend_finalization(state: dict[str, typing.Any], identity: dict[str, typing.Any], digest: str) -> None:
    previous = state["input"]
    if state["phase"] not in {"awaiting-validation", "git-integrated"}:
        raise ValueError("最終化入力の補完はrebase後の検収待ちかGit統合済みの工程で行う")
    if any(previous[key] != identity[key] for key in ("reviewed_head", "target", "branch", "no_merge")):
        raise ValueError("最終化入力の補完ではレビューHEADと統合先を変更しない")
    old, new = previous["manifest"], identity["manifest"]
    if old.get("verification", []) != new.get("verification", []) or old.get("handoff") != new.get("handoff"):
        raise ValueError("最終化入力の補完では検証と引き継ぎ記録を変更しない")
    for group in ("plans", "wis"):
        if [row["name"] for row in old[group]] != [row["name"] for row in new[group]]:
            raise ValueError("最終化入力の補完では全計画・WIの集合と順序を変更しない")
        for before, after in zip(old[group], new[group], strict=True):
            for index, argv in enumerate(before["commands"]):
                key = f"{group}:{before['name']}:{index}"
                if key in state["completed"] and (index >= len(after["commands"]) or after["commands"][index] != argv):
                    raise ValueError(f"成功済みの最終化操作を変更できない: {key}")
    state.update(previous_manifest=old, input=identity, input_digest=digest)


def _assert_integration_tree(record: dict[str, typing.Any], target: pathlib.Path, branch: str) -> pathlib.Path:
    worktree = pathlib.Path(record["worktree"])
    if not record.get("ready") or _common(target) != record["common_dir"]:
        raise ValueError("準備状態または統合先のGit共通ディレクトリが所有記録と異なる")
    for tree, expected in ((worktree, record["branch"]), (target, branch)):
        if _worktrees(tree).get(str(tree)) != expected or _common(tree) != record["common_dir"]:
            raise ValueError(f"worktreeとbranchの登録が所有記録と異なる: {tree}")
        if _git(tree, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError(f"統合作業ツリーに未commitまたは未追跡の変更がある: {tree}")
        for name in (
            "rebase-merge",
            "rebase-apply",
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "index.lock",
            "HEAD.lock",
            "packed-refs.lock",
            f"refs/heads/{expected}.lock",
        ):
            if pathlib.Path(_git(tree, "rev-parse", "--path-format=absolute", "--git-path", name)).exists():
                raise ValueError(f"Gitの中断状態がある: {tree}: {name}")
    return worktree


def integrate(args: argparse.Namespace, session_id: str) -> dict[str, typing.Any]:
    """所有登録からGit統合と最終化を再開し、全操作の成功後に選定を統合済みにする。"""
    for path in (args.record, args.manifest, args.target_worktree):
        if not path.is_absolute():
            raise ValueError("record・manifest・target-worktreeは絶対パスで指定する")
    path = args.record.resolve()
    if path.parent != _directory(session_id) or not _LANE.fullmatch(path.stem):
        raise ValueError("recordは現在の所有sessionの登録済みlane recordを指定する")
    with _locked(session_id):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["session_id"] != session_id or record["record_path"] != str(path):
            raise ValueError("統合するレーンの書込所有を確認できない")
        target = args.target_worktree.resolve()
        if args.target_branch != record["base_branch"]:
            raise ValueError("統合先branchが所有登録と異なる")
        worktree = _assert_integration_tree(record, target, args.target_branch)
        allowed: frozenset[str] = frozenset([session_id, *(args.writer_session or [])])
        for tree in (worktree, target):
            activity = _session_activity(tree, allowed)
            if activity:
                raise ValueError(f"現在の書込所有に含まれない担当が稼働中: {activity}")
        manifest = _integration_input(args, record)
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", args.reviewed_head):
            raise ValueError("reviewed-headは7文字以上の一意なcommit OIDで指定する")
        reviewed = _git(worktree, "rev-parse", "--verify", f"{args.reviewed_head}^{{commit}}")
        identity = {
            "reviewed_head": reviewed,
            "target": str(target),
            "branch": args.target_branch,
            "manifest": manifest,
            "no_merge": args.no_merge,
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        state: dict[str, typing.Any] | None = record.get("integration")
        if state is not None and args.restart_review:
            if state["phase"] in {"git-integrated", "finalized"}:
                raise ValueError("Git統合後は--restart-reviewで再開せず、同じ入力で残る最終化を実行する")
            if _git(worktree, "rev-parse", "HEAD") != reviewed:
                raise ValueError("再レビューしたHEADが現在HEADと一致しない")
            record["previous_integration"] = state
            state = None
        if state is not None and state["input_digest"] != digest:
            if args.amend_finalization:
                _amend_finalization(state, identity, digest)
                _save(path, record)
            elif state["phase"] != "finalized":
                raise ValueError(
                    "統合途中の入力が異なる。同じ入力で再開する。"
                    "Git統合前の是正は--restart-review、未完了の最終化argvの補完は--amend-finalizationを指定する"
                )
            else:
                state = None
        if state is None:
            if _git(worktree, "rev-parse", "HEAD") != reviewed:
                raise ValueError("現在HEADがレビュー対象HEADと一致しない")
            state = {
                "input_digest": digest,
                "input": identity,
                "phase": "prepared",
                "completed": [],
                "old_head": reviewed,
                "target_head": _git(target, "rev-parse", "HEAD"),
            }
            record["integration"] = state
            _save(path, record)
        if state["phase"] == "finalized" and _git(worktree, "rev-parse", "HEAD") != state.get("new_head", state["old_head"]):
            raise ValueError("前回最終化後に現在HEADが変わっている。今回のレビュー対象HEADとmanifestを渡す")
        if state["phase"] == "prepared" and not args.no_merge:
            current = _git(worktree, "rev-parse", "HEAD")
            if current != reviewed:
                raise ValueError("現在HEADがレビュー対象HEADと一致しない")
            base = _git(target, "rev-parse", "HEAD")
            ancestor = git_command.run(["merge-base", "--is-ancestor", base, current], worktree, check=False).returncode
            if ancestor not in {0, 1}:
                raise ValueError("統合先との祖先判定が失敗した")
            if ancestor == 1:
                old_base = _git(worktree, "merge-base", base, current)
                commits = _git(worktree, "rev-list", f"{old_base}..{current}").splitlines()
                _git(worktree, "fetch", "--all", "--no-tags")
                for commit in commits:
                    if _git(worktree, "for-each-ref", "--format=%(refname)", f"--contains={commit}", "refs/remotes"):
                        raise ValueError(f"push済みcommitの書換えを拒否した: {commit}")
                if not manifest.get("verification"):
                    raise ValueError("rebase後の検証argvがmanifestに無い")
                state.update(phase="rebasing", old_base=old_base, new_base=base)
                _save(path, record)
                _git(worktree, "rebase", base)
                state.update(phase="rebased", new_head=_git(worktree, "rev-parse", "HEAD"))
                _save(path, record)
            else:
                state.update(phase="verified", new_head=current)
                _save(path, record)
        if state["phase"] == "rebasing":
            state.update(phase="rebased", new_head=_git(worktree, "rev-parse", "HEAD"))
            _save(path, record)
        if state["phase"] == "rebased":
            if "rewrite_map" not in state:
                directory = managed_temp.create_managed_temp("lane-integration")
                state["rewrite_map"] = str(directory / "rewrite-map.json")
                _save(path, record)
            mapping = pathlib.Path(state["rewrite_map"])
            _integration_command(
                record,
                state,
                "history-compare",
                _atk_script(
                    "run-script",
                    "history-compare",
                    "--",
                    "--operation",
                    "rebase",
                    "--work-dir",
                    str(worktree),
                    "--old-base",
                    state["old_base"],
                    "--old-head",
                    state["old_head"],
                    "--new-base",
                    state["new_base"],
                    "--new-head",
                    state["new_head"],
                    "--output",
                    str(mapping),
                ),
                worktree,
            )
            state["rewrite_map"] = str(mapping)
            for index, argv in enumerate(manifest["verification"]):
                _integration_command(record, state, f"verification-{index}", argv, worktree)
            state["phase"] = "awaiting-validation"
            _save(path, record)
        if state["phase"] == "awaiting-validation":
            if args.accept_rebased_head != state["new_head"]:
                return dict(
                    state,
                    record_path=str(path),
                    next_action=(
                        "保存済み対応表・検証診断とcommit対応を検収し、"
                        f"同じ操作に--accept-rebased-head {state['new_head']}を指定する"
                    ),
                )
            state["phase"] = "verified"
            _save(path, record)
        if state["phase"] == "verified" or (state["phase"] == "prepared" and args.no_merge):
            for index, argv in enumerate(manifest.get("verification", [])):
                _integration_command(record, state, f"verification-{index}", argv, worktree)
            _assert_integration_tree(record, target, args.target_branch)
            if not args.no_merge:
                if _git(worktree, "rev-parse", "HEAD") != state["new_head"]:
                    raise ValueError("検証済みHEADが変わっている")
                _git(target, "merge", "--ff-only", state["new_head"])
            state.update(phase="git-integrated", integrated_head=_git(target, "rev-parse", "HEAD"))
            _save(path, record)
        if state["phase"] == "git-integrated":
            _git(target, "merge-base", "--is-ancestor", state["integrated_head"], "HEAD")
            for group in ("plans", "wis"):
                for row in manifest[group]:
                    for index, argv in enumerate(row["commands"]):
                        _integration_command(
                            record, state, f"{group}:{row['name']}:{index}", argv, worktree if group == "plans" else target
                        )
            _integration_input(args, record)
            selected = pathlib.Path(record["selection_file"])
            _integration_command(
                record,
                state,
                "selection-mark",
                _atk_script(
                    "run-script",
                    "pick-wi-check",
                    "--",
                    str(selected),
                    "--mark-integrated",
                    record["lane"],
                    "--output",
                    str(selected),
                    "--work-dir",
                    str(target),
                ),
                target,
            )
            state["phase"] = "finalized"
            _save(path, record)
        return dict(state, record_path=str(path))


def build_parser(parser: argparse.ArgumentParser) -> None:
    """レーンの作成と、指定sessionの回収を公開する。"""
    children = help_text.add_subcommands(parser, dest="lane_subcommand", required=True)
    create_parser = help_text.add_command(children, "create", **help_text.HELP["atk lane create"])
    create_parser.add_argument("--repo", type=pathlib.Path, required=True, help="対象repoの絶対パス。")
    create_parser.add_argument("--base-branch", required=True, help="統合先branch。")
    create_parser.add_argument("--lane", required=True, help="lane-NN。")
    create_parser.add_argument("--selection-file", type=pathlib.Path, required=True, help="統合状態を読む選定YAML。")
    create_parser.add_argument("--branch", help="専用branch名。省略するとsessionとlaneから決める。")
    create_parser.add_argument(
        "--worktree", type=pathlib.Path, help="作成先の絶対パス。省略すると主作業ツリーの固定作成先を使う。"
    )
    create_parser.add_argument("--commands-file", type=pathlib.Path, help="create・prepare・deleteのargvを持つJSON。")
    create_parser.add_argument("--reuse-record", type=pathlib.Path, help="終端済みの担当が使っていた資源のrecord_path。")
    create_parser.add_argument(
        "--timeout", type=_positive_timeout, default=600, help="各固有手順の正の上限秒数。省略すると600秒。"
    )
    create_parser.add_argument("--session-id", help="所有session。省略すると実行環境から解決する。")
    delete_parser = help_text.add_command(children, "delete", **help_text.HELP["atk lane delete"])
    delete_parser.add_argument("--session-id", help="回収する所有session。省略すると実行環境から解決する。")
    delete_parser.add_argument("--list", action="store_true", help="全sessionの登録と前回結果を読み、削除しない。")
    integrate_parser = help_text.add_command(children, "integrate", **help_text.HELP["atk lane integrate"])
    integrate_parser.add_argument("--record", type=pathlib.Path, required=True, help="所有登録の絶対パス。")
    integrate_parser.add_argument("--reviewed-head", required=True, help="受理済みレビュー対象HEAD。")
    integrate_parser.add_argument("--target-worktree", type=pathlib.Path, required=True, help="統合先worktreeの絶対パス。")
    integrate_parser.add_argument("--target-branch", required=True, help="所有記録と同じ統合先branch。")
    integrate_parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        required=True,
        help=(
            "JSONのplans/wisは{name,commands:[argv,...]}の順序付き配列、verificationはargv配列。"
            "計画なしはplans:[]とhandoff:ABS。保存済みplansはcommands:[]とsaved:ABS、延期wisはcommands:[]。"
        ),
    )
    integrate_parser.add_argument("--no-merge", action="store_true", help="Gitを変更せず計画保存・WI終端だけ行う。")
    integrate_parser.add_argument("--accept-rebased-head", help="対応表と再検証の診断を受理したHEADの完全なOID。")
    integrate_parser.add_argument(
        "--restart-review", action="store_true", help="Git統合前の是正を再レビューした現在HEADから再開する。"
    )
    integrate_parser.add_argument("--session-id", help="現在の書込所有session。")
    integrate_parser.add_argument(
        "--amend-finalization", action="store_true", help="全計画・WIの集合を変えずに、未完了の最終化操作のargvを補完する。"
    )
    integrate_parser.add_argument("--writer-session", action="append", help="統合に合意した現在の担当のsession。複数指定可能。")


def dispatch(args: argparse.Namespace) -> int:
    """機械可読な資源と、残存理由を出力する。"""
    if args.lane_subcommand == "delete" and getattr(args, "list", False):
        try:
            listed = list_resources(args.session_id)
            print(json.dumps(listed, ensure_ascii=False))
            return 1 if listed["errors"] else 0
        except (OSError, ValueError) as error:
            outcome.report_failure(str(error), next_action="所有登録の保存先と読取権限を確認する")
            return 2
    session_id = args.session_id or current_session_id()
    if session_id is None:
        outcome.report_failure("所有sessionを解決できない", next_action="--session-idで作成したsessionを指定する")
        return 2
    try:
        if args.lane_subcommand == "create":
            result = create(args, session_id)
        elif args.lane_subcommand == "integrate":
            result = integrate(args, session_id)
        else:
            result = cleanup_session(session_id)
        print(json.dumps(result, ensure_ascii=False))
        if result.get("phase") == "awaiting-validation":
            outcome.report_warning("rebase後の検収を待っている", next_action=result["next_action"])
            return 1
        if result.get("retained"):
            outcome.report_warning(
                "未回収のレーン資源を保持した", next_action="result_pathの理由を解消し同じlane deleteを再実行する"
            )
            return 1
        outcome.report_success("レーン資源の操作が完了した", outcome.ResultKind.VALUE_OUTPUT)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, managed_temp.ManagedTempError) as error:
        outcome.report_failure(str(error), next_action="入力と所有記録、残った資源を確認して同じ操作を再実行する")
        return 2
