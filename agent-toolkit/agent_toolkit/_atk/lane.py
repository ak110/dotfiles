"""レーン資源を作成側で登録し、所有セッションの終了時に回収する。"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import pathlib
import re
import subprocess
import typing
from collections.abc import Generator

import psutil
import yaml

from agent_toolkit._atk import help_text, managed_temp, outcome, run_command
from agent_toolkit._common import atomic_file, file_lock, session_launchers, state_paths
from agent_toolkit._git import command as git_command
from agent_toolkit._plan import selection

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
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = str(pathlib.Path(line[9:]).resolve())
            entries[current] = ""
        elif current and line.startswith("branch refs/heads/"):
            entries[current] = line[18:]
    return entries


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
    if str(worktree) not in _worktrees(repo):
        if worktree.exists():
            raise ValueError("作成先に未登録の実体がある。登録と残った資源を確認する")
        if record["commands"].get("create"):
            _procedure(record, "create")
        elif branch in refs:
            if _git(repo, "rev-parse", f"refs/heads/{branch}") != record["initial_head"]:
                raise ValueError("作成途中のbranchが開始版から変わっている。成果を確認して回復する")
            _git(repo, "worktree", "add", str(worktree), branch)
        else:
            _git(repo, "worktree", "add", "-b", branch, str(worktree), record["initial_head"])
    registered = _worktrees(repo).get(str(worktree))
    if registered == "":
        branch_arguments = () if branch in refs else ("-c",)
        _git(worktree, "switch", *branch_arguments, branch)
        registered = _worktrees(repo).get(str(worktree))
    if registered != branch or _common(worktree) != record["common_dir"]:
        raise ValueError("作成先のGit登録が入力と一致しない。記録と作成先を確認する")
    _procedure(record, "prepare")
    record["ready"] = True
    _save(path, record)


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
    activity = _session_activity(worktree) or _process_activity(worktree)
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
    source, source_owner = _reuse_source(args, session_id)
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
        if args.worktree:
            temp = None
            worktree = args.worktree.resolve()
        else:
            temp = managed_temp.create_managed_temp(args.lane)
            worktree = temp / "wt"
        if worktree.exists():
            raise ValueError(f"作成先が既にある: {worktree}。未使用の作成先を指定する")
        record: dict[str, typing.Any] = {
            "session_id": session_id,
            "lane": args.lane,
            "repo": str(repo),
            "common_dir": common,
            "base_branch": args.base_branch,
            "branch": branch,
            "worktree": str(worktree),
            "managed_temp": str(temp) if temp else None,
            "selection_file": str(args.selection_file.resolve()),
            "commands": commands,
            "procedure_timeout": args.timeout,
            "initial_head": initial_head,
            "record_path": str(path),
            "ready": False,
            "teardown_done": False,
        }
        _save(path, record)
        _prepare(record, path)
        return dict(record, record_path=str(path), reused=False)


def _session_activity(worktree: pathlib.Path) -> str | None:
    """保存済みの終端登録から、その作業ツリーを使う担当の終了を判定する。"""
    directory = session_launchers.registry_directory(state_paths.state_dir())
    if not directory.exists():
        return None
    for path in directory.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"担当登録の形式を確認できない: {path}")
        cwd = payload.get("cwd")
        if (
            isinstance(cwd, str)
            and cwd
            and pathlib.Path(cwd).resolve().is_relative_to(worktree)
            and payload.get("terminal") is not True
        ):
            return f"担当の終了を確認できない: {payload.get('session_id', path.stem)}"
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
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for argument in argv:
            candidate = pathlib.Path(argument.split("=", 1)[-1])
            if candidate.is_absolute() and candidate.is_relative_to(worktree):
                return f"外部プロセスが資源を参照中: pid={process.pid}"
    return None


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
    activity = _session_activity(worktree) or _process_activity(worktree)
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


def cleanup_session(session_id: str | None) -> dict[str, typing.Any]:
    """全登録を先に判定し、安全な対象だけを回収する。結果は削除対象の外へ保持する。"""
    result: dict[str, typing.Any] = {"session_id": session_id, "removed": [], "retained": []}
    if session_id is None or not _directory(session_id).exists():
        return result
    with _locked(session_id) as directory:
        records: list[tuple[pathlib.Path, dict[str, typing.Any]]] = []
        for path in sorted(directory.glob("lane-*.json")):
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
                if record["worktree"] in _worktrees(repo):
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


def build_parser(parser: argparse.ArgumentParser) -> None:
    """レーンの作成と、指定sessionの回収を公開する。"""
    children = help_text.add_subcommands(parser, dest="lane_subcommand", required=True)
    create_parser = help_text.add_command(children, "create", **help_text.HELP["atk lane create"])
    create_parser.add_argument("--repo", type=pathlib.Path, required=True, help="対象repoの絶対パス。")
    create_parser.add_argument("--base-branch", required=True, help="統合先branch。")
    create_parser.add_argument("--lane", required=True, help="lane-NN。")
    create_parser.add_argument("--selection-file", type=pathlib.Path, required=True, help="統合状態を読む選定YAML。")
    create_parser.add_argument("--branch", help="専用branch名。省略するとsessionとlaneから決める。")
    create_parser.add_argument("--worktree", type=pathlib.Path, help="固有作成手順が使う絶対作成先。")
    create_parser.add_argument("--commands-file", type=pathlib.Path, help="create・prepare・deleteのargvを持つJSON。")
    create_parser.add_argument("--reuse-record", type=pathlib.Path, help="終端済みの担当が使っていた資源のrecord_path。")
    create_parser.add_argument(
        "--timeout", type=_positive_timeout, default=600, help="各固有手順の正の上限秒数。省略すると600秒。"
    )
    create_parser.add_argument("--session-id", help="所有session。省略すると実行環境から解決する。")
    delete_parser = help_text.add_command(children, "delete", **help_text.HELP["atk lane delete"])
    delete_parser.add_argument("--session-id", help="回収する所有session。省略すると実行環境から解決する。")
    delete_parser.add_argument("--list", action="store_true", help="全sessionの登録と前回結果を読み、削除しない。")


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
        result = create(args, session_id) if args.lane_subcommand == "create" else cleanup_session(session_id)
        print(json.dumps(result, ensure_ascii=False))
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
