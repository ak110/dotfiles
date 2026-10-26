"""レーン資源を作成側で登録し、所有セッションの終了時に回収する。"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import typing
from collections.abc import Generator

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import psutil
lazy import yaml

from agent_toolkit._atk import help_text, managed_temp, outcome, run_command
from agent_toolkit._atk.managed_temp import permissions as temp_permissions
from agent_toolkit._common import atomic_file, file_lock, session_launchers, session_release, state_paths, worktree_hooks
from agent_toolkit._common.wait_schedule import get_wait_timeout
from agent_toolkit._git import command as git_command
from agent_toolkit._git import worktree_exclude
from agent_toolkit._git import worktrees as git_worktrees
from agent_toolkit._plan import selection
lazy from agent_toolkit._common import session_registry, task_documents

SESSION_ENV = "AGENT_TOOLKIT_LANE_SESSION_ID"
_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_LANE = re.compile(r"lane-[0-9]{2}\Z")
_TIMEOUT = 120


def _record_errors() -> tuple[type[BaseException], ...]:
    """所有登録の読取・判定・回収で保持対象へ変換する例外型を返す。"""
    return (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        psutil.Error,
        managed_temp.ManagedTempError,
    )


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
    """登録済みworktreeの解決済みパスからbranch名（branchを持たない登録は空文字）への対応を返す。"""
    return {str(entry.path.resolve()): entry.branch or "" for entry in git_worktrees.list_worktrees(repo, timeout=_TIMEOUT)}


@dataclasses.dataclass(frozen=True)
class _ResourceState:
    """存在する主repoから照会したレーン資源の状態。"""

    worktree_path: bool
    worktree_registered: bool
    branch: bool

    @property
    def absent(self) -> bool:
        """回収で守る実体が全て無いと確定したかを返す。"""
        return not (self.worktree_path or self.worktree_registered or self.branch)


def _resource_state(record: dict[str, typing.Any]) -> _ResourceState:
    """消え得るworktreeを起点にせず、資源の有無を一度に照会する。"""
    repo = pathlib.Path(record["repo"])
    if _common(repo) != record["common_dir"]:
        raise ValueError("repoのGit共通ディレクトリが所有記録と異なる")
    worktree = pathlib.Path(record["worktree"])
    registered = str(worktree) in _worktrees(repo)
    return _ResourceState(os.path.lexists(worktree), registered, record["branch"] in _branches(repo))


def _branch_is_integrated(record: dict[str, typing.Any], repo: pathlib.Path) -> bool:
    """最終化時の入力HEADを保つbranchは、統合先の履歴書換え後も統合済みとして扱う。"""
    integration = record.get("integration")
    if isinstance(integration, dict) and integration.get("phase") == "finalized":
        new_head = integration.get("new_head")
        if isinstance(new_head, str) and _git(repo, "rev-parse", f"refs/heads/{record['branch']}") == new_head:
            return True
    result = git_command.run(
        ["merge-base", "--is-ancestor", f"refs/heads/{record['branch']}", f"refs/heads/{record['base_branch']}"],
        repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT,
    )
    if result.returncode == 1:
        raise ValueError("レーンbranchに統合先branchへ含まれないcommitがある。成果を統合してから同じlane deleteを再実行する")
    if result.returncode:
        raise ValueError(f"git merge-baseが終了{result.returncode}: {result.stderr.strip()}")
    return True


def _main_worktree(repo: pathlib.Path) -> pathlib.Path:
    """Linked worktreeから呼んでも、同じGitリポジトリの主作業ツリーへ解決する。"""
    main = git_worktrees.list_worktrees(repo, timeout=_TIMEOUT)[0]
    if main.bare:
        raise ValueError("repoが主作業ツリーを持たない。--worktreeで作成先を指定する")
    return main.path.resolve()


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
        if owner == session_id or not isinstance(record, dict) or record.get("worktree") != str(worktree) or _recovered(record):
            continue
        result = cleanup_session(owner, only=path)
        if path.exists():
            reasons = [held["reason"] for held in result["retained"] if held["record_path"] == str(path)]
            raise ValueError(
                f"固定作成先{worktree}を別sessionの登録が保持している: {'; '.join(reasons) or '回収が未完了'}。"
                f"成果を引き継ぐ場合は--reuse-record {path}を指定し、"
                f"回収する場合は理由を解消して`atk lane delete --session-id {owner}`を実行してから再実行する"
            )


def _read_selection(record: dict[str, typing.Any]) -> dict[str, typing.Any] | None:
    """選定原本からこのレーンのWI集合と統合状態を読む。原本が存在しなければNoneを返す。"""
    try:
        text = pathlib.Path(record["selection_file"]).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    document = yaml.safe_load(text)
    return {
        "lane_wis": sorted(
            str(row[selection.WI_KEY])
            for row in selection.decisions(document) or []
            if isinstance(row, dict) and row.get(selection.LANE_KEY) == record["lane"] and selection.WI_KEY in row
        ),
        "integrated": record["lane"] in selection.integrated_lanes(document),
    }


def _selection_state(record: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """選定原本が読めれば所有登録の保持値を更新して返し、失効していれば保持値を返す。

    選定原本はmanaged-tempの7日保持へ委ねられ、所有登録は資源の回収まで残る。
    原本の消失後も再開と回収を判定できるようにするため、読めた時点の内容を所有登録へ写して保持する。
    """
    current = _read_selection(record)
    if current is not None:
        record["selection"] = current
        return current
    held = record.get("selection")
    if not isinstance(held, dict):
        raise ValueError(
            f"選定原本{record['selection_file']}が失効し、所有登録に選定情報が無い。"
            "成果のbranchを保持して作成先を解放するには"
            f"`atk lane recover --session-id {record['session_id']} --lane {record['lane']}`を実行する"
        )
    return held


def _require_selection(record: dict[str, typing.Any]) -> None:
    """今回渡された選定原本から保持値を再生成する。再利用でも前回の選定を引き継がない。"""
    current = _read_selection(record)
    if current is None:
        raise ValueError(f"選定YAMLが存在しない: {record['selection_file']}。選定結果の出力先を指定する")
    record["selection"] = current


def _recovered(record: dict[str, typing.Any]) -> bool:
    recovery = record.get("recovery")
    return isinstance(recovery, dict) and recovery.get("phase") == "done"


def _integration_directory(record: dict[str, typing.Any]) -> pathlib.Path:
    """統合の対応表と実行記録を、所有登録と同じ寿命の状態ディレクトリへ置く。"""
    return pathlib.Path(record["record_path"]).with_name(f"{record['lane']}-integration")


def _persist_observed(record: dict[str, typing.Any], key: str, observed: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """実行記録と両出力を状態ディレクトリへ複製し、複製先を指す記録を返す。

    run_commandの保存先はmanaged-tempの7日保持であり、検収待ちの間に失効し得る。
    """
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", key) + "-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    directory = _integration_directory(record) / name
    directory.mkdir(parents=True, exist_ok=True)
    persisted = dict(observed)
    for field in ("record_path", "stdout_path", "stderr_path"):
        source = observed.get(field)
        if isinstance(source, str) and pathlib.Path(source).is_file():
            # 実行ごとの保存先の名前を保ち、再実行の記録を前回の記録と区別する。
            destination = directory / pathlib.Path(source).parent.name / pathlib.Path(source).name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            persisted[field] = str(destination)
    return persisted


def _materials_missing(state: dict[str, typing.Any]) -> bool:
    """検収待ちの対応表か検証記録が失われているかを返す。"""
    mapping = state.get("rewrite_map")
    if not isinstance(mapping, str) or not pathlib.Path(mapping).is_file():
        return True
    for key, observed in state.get("results", {}).items():
        if key.startswith("verification-") and isinstance(observed, dict):
            for field in ("record_path", "stdout_path", "stderr_path"):
                value = observed.get(field)
                if isinstance(value, str) and not pathlib.Path(value).is_file():
                    return True
    return False


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
        _git(worktree, "-c", "submodule.recurse=false", "switch", "-c", branch)
        registered = _worktrees(repo).get(str(worktree))
    if registered == "":
        branch_arguments = () if branch in refs else ("-c",)
        start_arguments = (record["initial_head"],) if branch_arguments else ()
        _git(worktree, "-c", "submodule.recurse=false", "switch", *branch_arguments, branch, *start_arguments)
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
    state = _resource_state(record)
    if not state.worktree_path:
        raise ValueError(
            f"再利用元のworktreeが無い。`atk lane delete --session-id {record['session_id']}`で登録を回収してから作成し直す"
        )
    if not state.worktree_registered or _worktrees(repo).get(str(worktree)) != record["branch"]:
        raise ValueError("再利用するworktreeの登録が異なる。資源を確認する")
    activity = _worktree_activity(worktree)
    if activity:
        raise ValueError(f"再利用元はまだ使用中: {activity}。担当と外部プロセスの終端を待つ")
    record.update(session_id=session_id, record_path=str(destination), selection_file=str(args.selection_file.resolve()))
    _require_selection(record)
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
            _require_selection(existing)
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
        _require_selection(record)
        _save(path, record)
        _prepare(record, path)
        return dict(record, record_path=str(path), reused=False)


@dataclasses.dataclass(frozen=True)
class _RunningSession:
    """作業ツリーを使う稼働中の担当。統合の稼働判定・診断と回収の使用判定が同じ収集結果を使う。"""

    session_id: str
    label: str
    cwd: str
    worktree: pathlib.Path
    launch_kind: str
    # 起動本文が`task`の見出しで始まる場合だけ、その役割文書の絶対パスを持つ。
    task_document: pathlib.Path | None

    def writes_worktree(self) -> bool:
        """統合の稼働判定で書込主体として数えるかを返す。

        役割文書を特定できない起動と、宣言を読めない役割文書は、書き込む担当として数える。
        """
        if self.launch_kind == "explore":
            return False
        if self.task_document is None:
            return True
        declaration = task_documents.read_declaration(self.task_document)
        return isinstance(declaration, str) or declaration.writes_worktree

    def describe(self) -> str:
        """不足担当の診断へ示す1件分の識別情報を返す。"""
        document = f", 役割文書={self.task_document}" if self.task_document is not None else ""
        return (
            f"session_id={self.session_id}, label={self.label or '-'}{document}, cwd={self.cwd}, 所属worktree={self.worktree}"
        )


def _running_sessions(worktree: pathlib.Path) -> list[_RunningSession]:
    """session登録簿の解決結果から、その作業ツリーへ所属する稼働中の担当を全件返す。

    終端の判定は`session_registry.resolve`に従い、所有側が終端を公開せずに終了して実行プロセスも
    存在しない記録は終了した担当として扱う。解決できない登録は、未確認の担当を見逃さないよう例外にする。
    """
    state_root = state_paths.state_dir()
    directory = session_launchers.registry_directory(state_root)
    if not directory.exists():
        return []
    entries = git_worktrees.list_worktrees(worktree, timeout=_TIMEOUT)
    target = git_worktrees.owning_worktree(worktree, entries)
    running: list[_RunningSession] = []
    for path in sorted(directory.glob("*.json")):
        try:
            resolution = session_registry.resolve(path.stem, state_root=state_root)
        except ValueError as exc:
            raise ValueError(f"担当登録の形式を確認できない: {path}") from exc
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ValueError(f"担当登録の形式を確認できない: {path}")
        info = resolution.resume_info
        if resolution.state is not session_registry.Resolution.RUNNING or info is None or not info.cwd:
            continue
        owner = git_worktrees.owning_worktree(pathlib.Path(info.cwd), entries)
        if owner is None or owner != target:
            continue
        running.append(
            _RunningSession(
                session_id=path.stem,
                label=info.launch_info.label,
                cwd=info.cwd,
                worktree=owner.path.resolve(),
                launch_kind=info.launch_kind,
                task_document=task_documents.launched_task_document(info.launch_info.prompt),
            )
        )
    return running


def _session_activity(worktree: pathlib.Path) -> str | None:
    """その作業ツリーを使う担当が全て終了したかを判定し、稼働中なら最初の担当を理由として返す。"""
    running = _running_sessions(worktree)
    return f"担当の終了を確認できない: {running[0].session_id}" if running else None


def _caller_chain() -> frozenset[str]:
    """統合を呼び出した担当と、その委譲元の連鎖を登録簿と同じ識別子で返す。

    起点は呼出元のホストsessionとし、レーンの所有session（`SESSION_ENV`）は別の寿命を持つ識別子のため使わない。
    Codexの委譲先は起動元のClaude Codeの`CLAUDE_CODE_SESSION_ID`を継承し得るため、値を持つ両方の変数を起点にする。
    委譲元は統合中に作業ツリー全体へ作用する操作をしない契約のため、書込主体から外す。
    """
    directory = session_registry.registry_directory()
    chain: set[str] = set()
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        current = os.environ.get(name)
        while current and _ID.fullmatch(current) and current not in chain:
            chain.add(current)
            current = session_launchers.read_launcher(directory / f"{current}.json")
    return frozenset(chain)


def _unowned_writers(trees: tuple[pathlib.Path, ...], excluded: frozenset[str]) -> list[_RunningSession]:
    """両作業ツリーの稼働中の担当から、除外集合に無い書込主体を全件返す。"""
    return [
        session
        for tree in trees
        for session in _running_sessions(tree)
        if session.session_id not in excluded and session.writes_worktree()
    ]


def _process_activity(worktree: pathlib.Path) -> str | None:
    entries = git_worktrees.list_worktrees(worktree, timeout=_TIMEOUT)
    target = git_worktrees.owning_worktree(worktree, entries)
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
        if cwd is not None and git_worktrees.owning_worktree(cwd, entries) == target:
            return f"外部プロセスが使用中: pid={process.pid}"
        # 所属の分からない別プロセスの取得失敗を、当該レーンの保持条件へ広げない。
        try:
            argv = process.cmdline()
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
        for argument in argv:
            candidate = pathlib.Path(argument.split("=", 1)[-1])
            if candidate.is_absolute() and git_worktrees.owning_worktree(candidate, entries) == target:
                return f"外部プロセスが資源を参照中: pid={process.pid}"
    return None


def _worktree_activity(worktree: pathlib.Path, *, wait_release: bool = True) -> str | None:
    """所有側へ終端資源の解放を要求してから、担当と外部プロセスの使用を再判定する。"""
    directory = session_registry.registry_directory()
    deadline: float | None = None
    released: list[str] = []
    entries = git_worktrees.list_worktrees(worktree, timeout=_TIMEOUT)
    target = git_worktrees.owning_worktree(worktree, entries)
    for path in directory.glob("*.json"):
        resolution = session_registry.resolve(path.stem)
        info = resolution.resume_info
        if info is None or not info.cwd or git_worktrees.owning_worktree(pathlib.Path(info.cwd), entries) != target:
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
                deadline = time.monotonic() + (get_wait_timeout("main") if wait_release else 0)
            reason = session_release.request_release(
                path.stem, worktree, deadline=deadline, worktree_paths=tuple(entry.path for entry in entries)
            )
            if reason:
                return f"session_id={path.stem}: {reason}。{instruction}"
            released.append(path.stem)
    # 所有側の応答は要求対象の終端まで検収する。応答後の独立した使用は、その時点の保持理由にする。
    activity = _session_activity(worktree) or _process_activity(worktree)
    if activity and released:
        identifiers = ",".join(released)
        return f"{activity}。session_id={identifiers}。agents_serverのstopへ各session_idを渡し、同じlane deleteを再実行する"
    return activity


def _has_unignored_changes(worktree: pathlib.Path) -> bool:
    """Gitの保護対象だけを返し、無視された生成物は固有撤去へ渡す。"""
    # indexの列挙は作業木を読まない。ignore配下でも追跡済みファイルとその親は必ず保護する。
    tracked: set[str] = set()
    for name in _git(worktree, "ls-files", "--cached", "-z").split("\0"):
        if name:
            path = pathlib.PurePosixPath(name)
            tracked.update(item.as_posix() for item in (path, *path.parents))

    def ignored(path: pathlib.Path) -> bool:
        relative = path.relative_to(worktree).as_posix()
        if relative in tracked or relative == ".git":
            return False
        result = git_command.run(
            ["check-ignore", "--quiet", "--no-index", "--", relative],
            worktree,
            capture_output=True,
            text=True,
            check=False,
            timeout=_TIMEOUT,
        )
        if result.returncode not in {0, 1}:
            raise ValueError(
                f"git check-ignoreが終了{result.returncode}: {result.stderr.strip()}。"
                f"{worktree}のGit設定と読取権限を確認し、表示された原因を解消して同じlane操作を再実行してください。"
            )
        return result.returncode == 0

    temp_permissions.prepare_tree(worktree, skip=ignored)
    return bool(_git(worktree, "status", "--porcelain=v1", "--untracked-files=all"))


def _eligible(record: dict[str, typing.Any], *, wait_release: bool = True) -> str | None:
    repo = pathlib.Path(record["repo"])
    worktree = pathlib.Path(record["worktree"])
    state = _resource_state(record)
    if state.absent:
        return None
    if not record.get("ready"):
        return "作成または環境準備が未完了"
    if not _selection_state(record)["integrated"]:
        return "選定の統合状態が未統合"
    if state.worktree_path:
        activity = _worktree_activity(worktree, wait_release=wait_release)
        if activity:
            return activity
    trees = _worktrees(repo)
    if state.worktree_registered:
        if trees.get(str(worktree)) != record["branch"]:
            return "worktreeの登録が所有記録と異なる"
        if state.worktree_path and _common(worktree) != record["common_dir"]:
            return "worktreeのGit共通ディレクトリが所有記録と異なる"
        if state.worktree_path and _has_unignored_changes(worktree):
            return "未commitまたは未追跡の変更がある"
    elif state.worktree_path:
        return "登録の無いworktreeが残っている"
    if state.branch:
        _branch_is_integrated(record, repo)
    return None


def _retained_resource(path: pathlib.Path, error: BaseException) -> dict[str, str]:
    retained = {"record_path": str(path), "reason": str(error)}
    if isinstance(error, managed_temp.ManagedTempError):
        retained["next_action"] = error.next_action
    return retained


def cleanup_session(
    session_id: str | None, *, only: pathlib.Path | None = None, wait_release: bool = True
) -> dict[str, typing.Any]:
    """全登録を先に判定し、安全な対象だけを回収する。結果は削除対象の外へ保持する。

    onlyを指定すると、その登録だけを同じ条件で判定・回収する。
    自動終了ではwait_release=Falseを渡し、終端資源の解放が未確認なら理由付きで保持する。
    """
    result: dict[str, typing.Any] = {"session_id": session_id, "removed": [], "retained": [], "recovered": [], "missing": []}
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
                state = _resource_state(record)
                if _recovered(record) and not state.absent:
                    # 回復で作業ツリーを解放し、成果のbranchを残した登録。所在を結果へ示し、回収しない。
                    result["recovered"].append({"record_path": str(path), **record["recovery"]})
                    continue
                held = record.get("selection")
                reason = _eligible(record, wait_release=wait_release)
                if record.get("selection") != held:
                    _save(path, record)
                if reason:
                    result["retained"].append({"record_path": str(path), "reason": reason})
                else:
                    if not state.worktree_path:
                        integration = record.get("integration")
                        recovery = record.get("recovery")
                        last_head = (
                            integration.get("new_head")
                            if isinstance(integration, dict) and isinstance(integration.get("new_head"), str)
                            else recovery.get("head")
                            if isinstance(recovery, dict) and isinstance(recovery.get("head"), str)
                            else record.get("initial_head")
                        )
                        result["missing"].append(
                            {
                                "lane": record["lane"],
                                "worktree": record["worktree"],
                                "branch": record["branch"],
                                "last_head": last_head,
                                "skipped_procedure": "delete" if record["commands"].get("delete") else None,
                            }
                        )
                    records.append((path, record))
            except _record_errors() as error:
                result["retained"].append(_retained_resource(path, error))
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
                reason = _eligible(record, wait_release=wait_release)
                if reason:
                    raise ValueError(reason)
                state = _resource_state(record)
                if not record["teardown_done"] and state.worktree_path:
                    _procedure(record, "delete")
                    record["teardown_done"] = True
                    _save(path, record)
                reason = _eligible(record, wait_release=wait_release)
                if reason:
                    raise ValueError(reason)
                repo = pathlib.Path(record["repo"])
                state = _resource_state(record)
                if (
                    state.worktree_registered
                    and record.get("project_hook")
                    and state.worktree_path
                    and worktree_hooks.configured(repo, "WorktreeRemove")
                ):
                    worktree_hooks.remove(
                        repo,
                        pathlib.Path(record["worktree"]),
                        session_id,
                        record["procedure_timeout"],
                        diagnostics_directory=_hook_diagnostics(record, "WorktreeRemove"),
                    )
                    remaining = _resource_state(record)
                    if remaining.worktree_path or remaining.worktree_registered:
                        raise ValueError("WorktreeRemove hookの後にworktreeが残っている。hookを直して同じ回収操作を再実行する")
                elif state.worktree_registered:
                    remove_args = ("--force",) if not state.worktree_path else ()
                    if state.worktree_path:
                        # Gitの保護判定は既に完了しているため、ここでだけ全木を準備する。
                        # 固有手順が別所有者の生成物を撤去できなければ、所有記録を残して失敗する。
                        temp_permissions.prepare_tree(pathlib.Path(record["worktree"]))
                    _git(repo, "worktree", "remove", *remove_args, record["worktree"])
                if _resource_state(record).branch:
                    _branch_is_integrated(record, repo)
                    _git(repo, "branch", "-D", record["branch"])
                if record["managed_temp"]:
                    managed_temp.cleanup_managed_temp(pathlib.Path(record["managed_temp"]))
                integration_directory = _integration_directory(record)
                if integration_directory.exists():
                    # 統合の対応表と検証記録は証拠として回収結果と同じ期限付き領域へ移し、状態ディレクトリから外す。
                    moved = result_path.parent / integration_directory.name
                    shutil.move(integration_directory, moved)
                    result.setdefault("integration_materials", {})[record["lane"]] = str(moved)
                path.unlink()
                result["removed"].append(record["lane"])
            except (OSError, ValueError, KeyError, subprocess.SubprocessError, managed_temp.ManagedTempError) as error:
                result["retained"].append(_retained_resource(path, error))
            result["pending"].remove(record["lane"])
            _save(result_path, result)
        for retained in result["retained"]:
            retained.setdefault(
                "next_action", "理由を解消し、所有登録の入力で作成を再開するか同じlane deleteで残る回収を実行する"
            )
        _save(result_path, result)
    return result


def _recover(record: dict[str, typing.Any], path: pathlib.Path) -> None:
    """成果のbranchを残して作業ツリーの占有を解放し、到達点を所有登録へ記録する。"""
    if record.get("recovery") is None and (_read_selection(record) is not None or record.get("selection") is not None):
        raise ValueError("選定情報が残る登録は回復の対象外。統合と`atk lane delete`か、--reuse-recordによる再利用で扱う")
    repo = pathlib.Path(record["repo"])
    worktree = pathlib.Path(record["worktree"])
    if _common(repo) != record["common_dir"]:
        raise ValueError("repoのGit共通ディレクトリが所有記録と異なる")
    trees = _worktrees(repo)
    registered = str(worktree) in trees
    if registered:
        if trees[str(worktree)] != record["branch"]:
            raise ValueError("worktreeの登録が所有記録と異なる")
        if worktree.exists():
            if _common(worktree) != record["common_dir"]:
                raise ValueError("worktreeのGit共通ディレクトリが所有記録と異なる")
            activity = _worktree_activity(worktree)
            if activity:
                raise ValueError(activity)
            if _has_unignored_changes(worktree):
                raise ValueError("未commitまたは未追跡の変更がある。成果をcommitしてから再実行する")
    elif worktree.exists():
        raise ValueError("登録の無いworktreeが残っている。内容とGit登録を確認する")
    if record["branch"] not in _branches(repo):
        raise ValueError(f"元のbranchが無い: {record['branch']}。成果の所在を確認する")
    head = _git(repo, "rev-parse", "--verify", f"refs/heads/{record['branch']}")
    recovery = record.setdefault("recovery", {"phase": "started", "branch": record["branch"], "head": head})
    if recovery["head"] != head:
        raise ValueError("回復の開始後に元branchのHEADが変わった。成果を確認して同じ操作を再実行する")
    _save(path, record)
    if registered:
        if worktree.exists() and record.get("project_hook") and worktree_hooks.configured(repo, "WorktreeRemove"):
            worktree_hooks.remove(
                repo,
                worktree,
                record["session_id"],
                record["procedure_timeout"],
                diagnostics_directory=_hook_diagnostics(record, "WorktreeRemove"),
            )
            remaining = _resource_state(record)
            if remaining.worktree_path or remaining.worktree_registered:
                raise ValueError("WorktreeRemove hookの後にworktreeが残っている。hookを直して同じ回復操作を再実行する")
        elif worktree.exists():
            # Gitのclean判定後にだけ全木を準備し、残る別所有者の内容は保持する。
            temp_permissions.prepare_tree(worktree)
            _git(repo, "worktree", "remove", str(worktree))
        else:
            # 実体の無い登録は、その作業ツリーだけをGit登録から外す。
            _git(repo, "worktree", "remove", "--force", str(worktree))
    recovery["phase"] = "done"
    _save(path, record)


def recover(session_id: str, lanes: list[str] | None = None) -> dict[str, typing.Any]:
    """選定情報を失った旧登録の作業ツリーを、成果のbranchを残して解放する。"""
    for lane_id in lanes or []:
        if not _LANE.fullmatch(lane_id):
            raise ValueError("レーンはlane-NNで指定する")
    directory = _directory(session_id)
    if not directory.is_dir():
        raise ValueError(f"所有sessionの登録が無い: {session_id}。`atk lane delete --list`で所有sessionを確認する")
    result: dict[str, typing.Any] = {"session_id": session_id, "recovered": [], "removed": [], "retained": []}
    with _locked(session_id):
        paths = sorted(directory.glob("lane-*.json"))
        if lanes:
            missing = sorted(set(lanes) - {path.stem for path in paths})
            if missing:
                raise ValueError(f"指定したレーンの登録が無い: {', '.join(missing)}")
            paths = [path for path in paths if path.stem in lanes]
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["session_id"] != session_id or path.stem != record["lane"]:
                    raise ValueError("登録の所有sessionまたはlaneが異なる")
                if _resource_state(record).absent:
                    path.unlink()
                    result["removed"].append(record["lane"])
                    continue
                if not _recovered(record):
                    _recover(record, path)
                result["recovered"].append({"record_path": str(path), "worktree": record["worktree"], **record["recovery"]})
            except _record_errors() as error:
                result["retained"].append(_retained_resource(path, error))
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
    if not isinstance(manifest, dict) or set(manifest) - {
        "plans",
        "wis",
        "verification",
        "handoff",
        "lane_started_at",
    }:
        raise ValueError("manifestはplans・wis・verification・handoff・lane_started_atを持つobjectで指定する")
    for name in ("plans", "wis"):
        rows = manifest.get(name)
        if not isinstance(rows, list):
            raise ValueError(f"manifestの{name}は順序付き配列で指定する")
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or not {"name", "commands"} <= set(row)
                or set(row) - {"name", "commands", "saved", "no_merge"}
                or not isinstance(row["name"], str)
                or ("no_merge" in row and not isinstance(row["no_merge"], bool))
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
    expected = set(_selection_state(record)["lane_wis"])
    if expected and expected != {row["name"] for row in manifest["wis"]}:
        raise ValueError("manifestのWI集合が選定の今回のレーン集合と一致しない")
    if "lane_started_at" in manifest:
        # Git統合後に最終化だけが入力の誤りで止まらないよう、完了時刻より後の値は統合の前に拒否する。
        _check_lane_started_before_now(_lane_started_at(manifest["lane_started_at"]))
    finalization = [value for name in ("plans", "wis") for row in manifest[name] for argv in row["commands"] for value in argv]
    referenced = False
    for value in finalization:
        for reference in _REFERENCE.findall(value):
            referenced = True
            name, separator, file = reference.partition(":")
            if separator and name == "expand-file":
                template = pathlib.Path(file)
                if not template.is_absolute() or not template.is_file():
                    raise ValueError(f"展開するテンプレートは実在する絶対パスで指定する: {file}")
            elif separator or reference not in _VALUE_REFERENCES:
                raise ValueError(
                    f"最終化argvの参照が不明: {{{{{reference}}}}}。受理する参照は"
                    "{{integrated_head_short}}・{{completed_at}}・{{lane_seconds}}・{{expand-file:<絶対パス>}}で、"
                    "manifestのargvをこのいずれかへ直して同じlane integrateを再実行する"
                )
    if referenced and "lane_started_at" not in manifest:
        raise ValueError("最終化argvが確定値を参照する場合はmanifestのlane_started_atを指定する")
    return manifest


# 最終化argvが参照する確定値。値は最終化の最初の操作の前に1回だけ生成し、再開では保存値を使う。
_REFERENCE = re.compile(r"\{\{([^{}]*)\}\}")
_VALUE_REFERENCES = frozenset({"integrated_head_short", "completed_at", "lane_seconds"})


def _lane_started_at(value: object) -> datetime.datetime:
    """manifestのレーン開始時刻を時差付きの日時として解釈する。"""
    try:
        started = datetime.datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        started = None
    if started is None or started.tzinfo is None:
        raise ValueError(f"lane_started_atは時差付きISO 8601で指定する: {value}")
    return started


def _check_lane_started_before_now(started: datetime.datetime) -> datetime.datetime:
    """レーン開始時刻が現在時刻より後なら拒否し、現在時刻を返す。"""
    now = datetime.datetime.now().astimezone()
    if started > now:
        raise ValueError(
            f"lane_started_atが現在時刻より後にある: 受け取った値={started.isoformat()}, "
            f"現在時刻={now.isoformat(timespec='seconds')}。"
            "最初のレーン担当の起動時刻を時差付きISO 8601（例: 2026-10-19T09:00:00+09:00）で指定し直して再実行する"
        )
    return now


def _finalization_values(state: dict[str, typing.Any], started: datetime.datetime) -> dict[str, str]:
    """統合後HEAD・完了時刻・レーン稼働秒数を生成する。計測の終点は最終化の最初の操作の前とする。"""
    completed = _check_lane_started_before_now(started)
    seconds = math.floor((completed - started).total_seconds())
    target = pathlib.Path(state["input"]["target"])
    return {
        "integrated_head_short": _git(target, "rev-parse", "--short=7", state["integrated_head"]),
        "completed_at": completed.isoformat(timespec="seconds"),
        "lane_seconds": str(seconds),
    }


def _expand(record: dict[str, typing.Any], values: dict[str, str] | None, argv: list[str]) -> list[str]:
    """最終化argvの確定値の参照をshellを介さず展開する。テンプレートは展開した複製のパスへ置き換える。

    テンプレート本文では確定値の参照だけを置き換え、他の`{{...}}`はそのまま残す。
    """
    if not values:
        return argv

    def replace(text: str) -> str:
        return _REFERENCE.sub(lambda match: values.get(match.group(1), match.group(0)), text)

    def resolve(match: re.Match[str]) -> str:
        name, separator, file = match.group(1).partition(":")
        if not separator or name != "expand-file":
            return replace(match.group(0))
        template = pathlib.Path(file)
        digest = hashlib.sha256(file.encode("utf-8")).hexdigest()[:8]
        expanded = _integration_directory(record) / "expanded" / f"{digest}-{template.name}"
        expanded.parent.mkdir(parents=True, exist_ok=True)
        atomic_file.atomic_write(expanded, replace(template.read_text(encoding="utf-8")))
        return str(expanded)

    return [_REFERENCE.sub(resolve, value) for value in argv]


def _integration_command(
    record: dict[str, typing.Any],
    state: dict[str, typing.Any],
    key: str,
    argv: list[str],
    cwd: pathlib.Path,
    *,
    timeout: float | None,
) -> None:
    if key in state["completed"]:
        return
    observed, code, failure = run_command.execute(argv, cwd, timeout, verification=key.startswith("verification-"))
    observed = _persist_observed(record, key, observed)
    state.setdefault("results", {})[key] = observed
    if code == 0:
        state["completed"].append(key)
    _save(pathlib.Path(record["record_path"]), record)
    if code:
        raise ValueError(f"統合の{key}が終了{code}: {failure or observed['record_path']}。同じlane integrateで再開する")


def _verify(record: dict[str, typing.Any], state: dict[str, typing.Any], argvs: list[list[str]], cwd: pathlib.Path) -> None:
    # 検証argvは計画の検証コマンドをそのまま渡し、自身の上限（pyfltrのcommand-timeoutなど）で終わる。
    # 固有手順向けのprocedure_timeoutを重ねると、検証が自身の上限より先に打ち切られる。
    for index, argv in enumerate(argvs):
        _integration_command(record, state, f"verification-{index}", argv, cwd, timeout=None)


def _atk_script(*arguments: str) -> list[str]:
    # 統合先cwdやPATH上の旧プラグインへ版を切り替えず、現在のCLIと同じ実体・依存環境を使う。
    root = pathlib.Path(__file__).resolve().parents[2]
    source = (
        f"import sys; sys.path.insert(0, {str(root)!r}); from agent_toolkit import atk; "
        "raise SystemExit(atk.main(sys.argv[1:]))"
    )
    return [sys.executable, "-c", source, *arguments]


def _amend_finalization(state: dict[str, typing.Any], identity: dict[str, typing.Any], digest: str) -> None:
    previous = state["input"]
    if state["phase"] not in {"awaiting-validation", "git-integrated"}:
        raise ValueError(
            "最終化入力の補完はrebase後の検収待ちかGit統合済みの工程で行う。"
            f"現在の工程は{state['phase']}のため、--amend-finalizationを外して同じ入力で再実行する"
        )
    if any(previous[key] != identity[key] for key in ("reviewed_head", "target", "branch", "no_merge")):
        raise ValueError(
            "最終化入力の補完ではレビューHEADと統合先を変更しない。変更する場合はGit統合前に--restart-reviewで再開し、"
            "Git統合後は前回と同じレビューHEADと統合先を指定する"
        )
    old, new = previous["manifest"], identity["manifest"]
    if any(old.get(key) != new.get(key) for key in ("verification", "handoff", "lane_started_at")):
        raise ValueError(
            "最終化入力の補完では検証・引き継ぎ記録・レーン開始時刻を変更しない。"
            "Git統合前なら--amend-finalizationを外して--restart-reviewで是正して再開し、"
            "Git統合後は前回と同じ値のまま未完了の最終化argvだけを補完する"
        )
    for group in ("plans", "wis"):
        if [row["name"] for row in old[group]] != [row["name"] for row in new[group]]:
            raise ValueError(
                "最終化入力の補完では全計画・WIの集合と順序を変更しない。前回と同じ名前と順序のmanifestへ直して再実行する"
            )
        for before, after in zip(old[group], new[group], strict=True):
            if before.get("no_merge", False) != after.get("no_merge", False):
                raise ValueError("最終化入力の補完では実装有無の区分を変更しない。前回と同じno_mergeの値で再実行する")
            for index, argv in enumerate(before["commands"]):
                key = f"{group}:{before['name']}:{index}"
                if key in state["completed"] and (index >= len(after["commands"]) or after["commands"][index] != argv):
                    raise ValueError(
                        f"成功済みの最終化操作を変更できない: {key}。このargvは前回と同じ値へ戻し、未完了の操作だけを補完する"
                    )
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
        excluded: frozenset[str] = frozenset([session_id, *(args.writer_session or []), *_caller_chain()])
        writers = _unowned_writers((worktree, target), excluded)
        if writers:
            raise ValueError(
                f"現在の書込所有に含まれない担当が稼働中: {len(writers)}件。"
                + "; ".join(writer.describe() for writer in writers)
                + "。各担当の担当範囲と統合への合意を確かめ、合意した担当は--writer-sessionへ反復指定し、"
                "合意しない担当は終端を待ってから同じlane integrateを再実行する"
            )
        manifest = _integration_input(args, record)
        if (
            "lane_started_at" in manifest
            and "lane_started_at" in record
            and _lane_started_at(manifest["lane_started_at"]) != _lane_started_at(record["lane_started_at"])
        ):
            raise ValueError(
                f"lane_started_atが所有登録の最初のレーン開始時刻と異なる: {record['lane_started_at']}。"
                "担当の置換や追加統合でも最初の起点を渡す"
            )
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
            current = _git(worktree, "rev-parse", "HEAD")
            record["previous_integration"] = state
            if (
                state["phase"] in {"rebased", "awaiting-validation", "verified"}
                and all(state.get(name) for name in ("old_base", "old_head", "new_base", "new_head"))
                and current == state["new_head"]
            ):
                if reviewed not in {state["old_head"], state["new_head"]}:
                    raise ValueError("再開のレビュー対象HEADは記録のold_headかnew_headを指定する")
                state = {
                    **state,
                    "input_digest": digest,
                    "input": identity,
                    "phase": "rebased",
                    "completed": [key for key in state["completed"] if not key.startswith("verification-")],
                    "results": {
                        key: value for key, value in state.get("results", {}).items() if not key.startswith("verification-")
                    },
                }
                record["integration"] = state
                _save(path, record)
            else:
                if current != reviewed:
                    raise ValueError("再レビューしたHEADが現在HEADと一致しない")
                state = None
        if state is not None and state["input_digest"] != digest:
            if args.amend_finalization:
                _amend_finalization(state, identity, digest)
                _save(path, record)
            elif state["phase"] != "finalized":
                raise ValueError(
                    "統合途中の入力が異なる。Git統合前の検証argv・最終化argv・handoffの是正は--restart-reviewで再開する。"
                    "HEADが記録のnew_headのままならold_headかnew_headを--reviewed-headへ渡し、rebase結果を再検証して検収待ちへ戻る。"
                    "未完了の最終化argvだけの補完は--amend-finalizationを指定する"
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
                _git(worktree, "-c", "submodule.recurse=false", "rebase", base)
                state.update(phase="rebased", new_head=_git(worktree, "rev-parse", "HEAD"))
                _save(path, record)
            else:
                state.update(phase="verified", new_head=current)
                _save(path, record)
        if state["phase"] == "rebasing":
            state.update(phase="rebased", new_head=_git(worktree, "rev-parse", "HEAD"))
            _save(path, record)
        if state["phase"] == "awaiting-validation" and _materials_missing(state):
            # 旧状態の対応表・検証記録が期限付き領域から失われた。保存済みの両系列から再生成して検収待ちへ戻る。
            state.update(
                phase="rebased",
                completed=[
                    key for key in state["completed"] if key != "history-compare" and not key.startswith("verification-")
                ],
            )
            _save(path, record)
        if state["phase"] == "rebased":
            mapping = _integration_directory(record) / "rewrite-map.json"
            if state.get("rewrite_map") != str(mapping):
                state["completed"] = [key for key in state["completed"] if key != "history-compare"]
                state["rewrite_map"] = str(mapping)
                _save(path, record)
            mapping.parent.mkdir(parents=True, exist_ok=True)
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
                timeout=record["procedure_timeout"],
            )
            state["rewrite_map"] = str(mapping)
            _verify(record, state, manifest["verification"], worktree)
            state["phase"] = "awaiting-validation"
            _save(path, record)
        if state["phase"] == "awaiting-validation":
            if args.restart_review or args.accept_rebased_head != state["new_head"]:
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
            _verify(record, state, manifest.get("verification", []), worktree)
            _assert_integration_tree(record, target, args.target_branch)
            if not args.no_merge:
                if _git(worktree, "rev-parse", "HEAD") != state["new_head"]:
                    raise ValueError("検証済みHEADが変わっている")
                _git(target, "merge", "--ff-only", state["new_head"])
            state.update(phase="git-integrated", integrated_head=_git(target, "rev-parse", "HEAD"))
            _save(path, record)
        if state["phase"] == "git-integrated":
            _git(target, "merge-base", "--is-ancestor", state["integrated_head"], "HEAD")
            if "lane_started_at" in manifest and "finalization_values" not in state:
                # 再開と--amend-finalizationでは保存した確定値を使い、全消費先へ同じ値を渡す。
                state["finalization_values"] = _finalization_values(state, _lane_started_at(manifest["lane_started_at"]))
                record["lane_started_at"] = manifest["lane_started_at"]
                _save(path, record)
            for group in ("plans", "wis"):
                for row in manifest[group]:
                    for index, argv in enumerate(row["commands"]):
                        _integration_command(
                            record,
                            state,
                            f"{group}:{row['name']}:{index}",
                            _expand(record, state.get("finalization_values"), argv),
                            worktree if group == "plans" else target,
                            timeout=record["procedure_timeout"],
                        )
            _integration_input(args, record)
            selected = pathlib.Path(record["selection_file"])
            if not selected.exists() and "selection-mark" not in state["completed"]:
                # 選定原本は失効した。統合済みの事実を所有登録の選定情報へ記録して回収判定へ渡す。
                state["completed"].append("selection-mark")
                state.setdefault("results", {})["selection-mark"] = {"skipped": "選定原本が失効したため所有登録へ記録した"}
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
                timeout=record["procedure_timeout"],
            )
            record["selection"] = {**_selection_state(record), "integrated": True}
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
        "--timeout",
        type=_positive_timeout,
        default=600,
        help="固有手順・timeoutの無いhook・統合の最終化操作の正の上限秒数。省略すると600秒。",
    )
    create_parser.add_argument("--session-id", help="所有session。省略すると実行環境から解決する。")
    delete_parser = help_text.add_command(children, "delete", **help_text.HELP["atk lane delete"])
    delete_parser.add_argument("--session-id", help="回収する所有session。省略すると実行環境から解決する。")
    delete_parser.add_argument("--list", action="store_true", help="全sessionの登録と前回結果を読み、削除しない。")
    recover_parser = help_text.add_command(children, "recover", **help_text.HELP["atk lane recover"])
    recover_parser.add_argument("--session-id", required=True, help="回復する旧登録の所有session。")
    recover_parser.add_argument("--lane", action="append", default=None, help="対象のlane-NN。省略すると全登録。複数指定可能。")
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
            "lane_started_atは最初のレーン開始時刻（時差付きISO 8601）。plans/wisのargvの"
            "{{integrated_head_short}}・{{completed_at}}・{{lane_seconds}}と{{expand-file:ABS}}を確定値で展開する。"
            "実装なしのplans/wisにはno_merge:trueを指定する。"
        ),
    )
    integrate_parser.add_argument("--no-merge", action="store_true", help="Gitを変更せず計画保存・WI終端だけ行う。")
    integrate_parser.add_argument("--accept-rebased-head", help="対応表と再検証の診断を受理したHEADの完全なOID。")
    integrate_parser.add_argument(
        "--restart-review",
        action="store_true",
        help="Git統合前の入力是正から再開する。HEAD不変のrebase結果は再検証して検収待ちへ戻る。",
    )
    integrate_parser.add_argument("--session-id", help="所有登録のsession（atk lane createの所有session）。")
    integrate_parser.add_argument(
        "--amend-finalization", action="store_true", help="全計画・WIの集合を変えずに、未完了の最終化操作のargvを補完する。"
    )
    integrate_parser.add_argument(
        "--writer-session",
        action="append",
        default=None,
        help=(
            "判定が数える稼働中の担当（両worktreeをcwdとし、書き込む役割か宣言の無い起動）のうち、統合に合意した担当のsession。"
            "呼出元とその委譲元の連鎖、explore、書き込まない役割の担当は指定しなくても数えない。複数指定可能。"
        ),
    )


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
        elif args.lane_subcommand == "recover":
            result = recover(session_id, args.lane)
        else:
            result = cleanup_session(session_id)
        print(json.dumps(result, ensure_ascii=False))
        if result.get("phase") == "awaiting-validation":
            outcome.report_warning("rebase後の検収を待っている", next_action=result["next_action"])
            return 1
        if result.get("retained"):
            if args.lane_subcommand == "recover":
                outcome.report_warning(
                    "回復できない登録を保持した", next_action="retainedの理由を解消し同じlane recoverを再実行する"
                )
            else:
                outcome.report_warning(
                    "未回収のレーン資源を保持した", next_action="result_pathの理由を解消し同じlane deleteを再実行する"
                )
            return 1
        for missing in result.get("missing", []):
            outcome.report_warning(
                f"実体の無いレーン登録を回収した: lane={missing['lane']}, last_head={missing['last_head']}",
                next_action="消失の原因を確認し、統合されていない成果があれば記録したHEADから復元する",
            )
        outcome.report_success("レーン資源の操作が完了した", outcome.ResultKind.VALUE_OUTPUT)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, managed_temp.ManagedTempError) as error:
        outcome.report_failure(str(error), next_action="入力と所有記録、残った資源を確認して同じ操作を再実行する")
        return 2
