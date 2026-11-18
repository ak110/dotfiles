"""統括会話を起動するworkerの寿命を保持し、終了した対象の結果を回収する。"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import subprocess
import time
from collections.abc import Callable
from typing import Any, Self

import pydantic

from agent_toolkit._atk import config, managed_temp
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop_env as child_environment
from agent_toolkit._atk.wi import process_loop_launch_gate as launch_gate
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_update as update
from agent_toolkit._common import mcp_environment, next_action, process_tree
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.plugin_environment import launch_command

# 統括の対話CLIが常駐の端末を前景で使うため、同時に起動するworkerを1つに保つ。
MAX_ACTIVE_TARGETS = 1


class WorkerInput(pydantic.BaseModel):
    """起動時の候補と開始判定を、更新後の別プロセスへ渡す。"""

    snapshots: tuple[listing.RepositorySnapshot, ...]
    launch_inputs: dict[str, launch_gate.LaunchInput]
    worktree: str | None = None
    resume: str | None = None
    auto_resume: bool = False
    resume_request_id: str | None = None
    interactive_resume: bool = False
    reservation_lookup: bool = False
    output: pathlib.Path

    @pydantic.model_validator(mode="after")
    def validate_targets(self) -> Self:
        """対象の重複・不足と相対出力先を、実行の前に拒否する。"""
        identifiers = [snapshot.repository.key for snapshot in self.snapshots]
        if not identifiers or len(set(identifiers)) != len(identifiers) or set(identifiers) != set(self.launch_inputs):
            raise ValueError("workerの対象と開始判定が一致しない")
        if not self.output.is_absolute():
            raise ValueError("workerの結果保存先は絶対パスで指定する")
        return self


def run_worker(
    path: pathlib.Path,
    execute: Callable[..., dict[str, Any]],
) -> None:
    """凍結した入力の一対象だけを実行し、終了検収した結果を保存する。"""
    request = WorkerInput.model_validate_json(path.read_text(encoding="utf-8"))
    registered = {repository.key: repository for repository in config.resolve_configured_repositories()}
    if any(registered.get(snapshot.repository.key) != snapshot.repository for snapshot in request.snapshots):
        raise ValueError("workerの対象が現在の登録と一致しない")
    args = argparse.Namespace(
        worktree=request.worktree,
        resume=request.resume,
        auto_resume=request.auto_resume,
        resume_request_id=request.resume_request_id,
        interactive_resume=request.interactive_resume,
        reservation_lookup=request.reservation_lookup,
        worker_output=request.output,
    )
    groups: list[tuple[listing.RepositorySnapshot, ...]] = [request.snapshots]
    if request.auto_resume:
        prior: dict[str, list[listing.RepositorySnapshot]] = {}
        for snapshot in request.snapshots:
            target = snapshot.repository.key
            key = ownership.coordinator_session(target) or target
            prior.setdefault(key, []).append(snapshot)
        groups = [tuple(group) for group in prior.values()]
    completed: dict[str, Any] = {}
    for group in groups:
        results = execute(
            args,
            group,
            launch_inputs={snapshot.repository.key: request.launch_inputs[snapshot.repository.key] for snapshot in group},
        )
        completed.update((key, value.payload()) for key, value in results.items())
        atomic_write(request.output, json.dumps(completed, ensure_ascii=False))
        if getattr(args, "resume_started", False):
            args.resume_request_id = None


class _Worker:
    """プロセスと開いたログの所有はschedulerのコンテキストへ移す。"""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        resources: contextlib.ExitStack,
        directory: pathlib.Path,
        repositories: tuple[str, ...],
        request_id: str | None,
    ) -> None:
        self.process = process
        self.resources = resources
        self.directory = directory
        self.started = time.monotonic()
        self.repositories = repositories
        self.reported: set[str] = set()
        self.request_id = request_id


class RepositoryScheduler:
    """所有と結果検収を一対象のworkerへ任せ、常駐は実行識別子を保持する。"""

    def __init__(self) -> None:
        self.active: dict[str, _Worker] = {}
        self.results: dict[str, dict[str, Any]] = {}
        self.started: set[str] = set()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> None:
        # KeyboardInterrupt等でも、起動したプロセスの観測主体を失わない。
        # 未完了の所有・候補・レーンはworker側の永続記録へ残る。
        with contextlib.ExitStack() as cleanup:
            for worker in self.active.values():
                cleanup.callback(worker.resources.close)
        self.active.clear()

    @property
    def busy(self) -> set[str]:
        """既存の集合会話を明示再開する間も、その全対象の重複起動を防ぐ。"""
        return {repo for worker in self.active.values() for repo in worker.repositories if repo not in worker.reported}

    def start(
        self,
        args: argparse.Namespace,
        snapshots: tuple[listing.RepositorySnapshot, ...],
        launches: dict[str, launch_gate.LaunchInput],
    ) -> None:
        """同じ対象を重ねず、新しく配布された実体からworkerを開始する。"""
        repositories = tuple(snapshot.repository.key for snapshot in snapshots)
        lookup = getattr(args, "reservation_lookup", False)
        if not repositories or (not lookup and self.busy.intersection(repositories)):
            raise ValueError("workerの対象が空か、既に実行中の対象と重複している")
        # pickerの表示中は既存workerを保持し、選択後の所有取得で重複を拒否する。
        repo_id = f"lookup:{args.resume_request_id}" if lookup else repositories[0]
        directory = managed_temp.create_managed_temp("process-loop-worker")
        output = directory / "worker-output.json"
        request = WorkerInput(
            snapshots=snapshots,
            launch_inputs=launches,
            worktree=args.worktree,
            resume=getattr(args, "resume", None),
            auto_resume=getattr(args, "auto_resume", False),
            resume_request_id=getattr(args, "resume_request_id", None),
            interactive_resume=getattr(args, "interactive_resume", False),
            reservation_lookup=getattr(args, "reservation_lookup", False),
            output=output,
        )
        source = directory / "worker-input.json"
        atomic_write(source, request.model_dump_json())
        root = None if getattr(args, "no_update", False) else update.resolve_dotfiles_root()
        current = pathlib.Path(__file__).resolve().parents[3] / "agent_toolkit" / "atk.py"
        script, _arguments = update._build_restart_target([str(current)], root)  # pylint: disable=protected-access
        root = script.parent.parent
        bootstrap = root / "agent_toolkit" / "_agents_server" / "plugin_root.py"
        # worker自身が既存の保持root機構へleaseを持ち、更新中も旧実行の遅延importを保護する。
        command = [*launch_command(root, bootstrap), "--process-loop-worker", str(source)]
        env = child_environment.child_env()
        env.pop(mcp_environment.SESSION_START_INPUT_ENV, None)
        with contextlib.ExitStack() as resources:
            # worker自身の各外部操作とCLI監視が上限を持つ。常駐の実作業に固定期限を追加しない。
            # workerと対話CLIは常駐の端末を引き継ぎ、ユーザーが画面を見て直接入力できる。
            # 複数対象の統括会話は常駐のcwdで起動し、1対象だけの会話はその対象のworktreeで起動する。
            process = resources.enter_context(
                process_tree.process_scope(
                    command, env=env, cwd=snapshots[0].repository.worktree if len(snapshots) == 1 else None
                )
            )
            self.active[repo_id] = _Worker(process, resources.pop_all(), directory, repositories, request.resume_request_id)
        self.started.update(repositories)
        print(f"{repo_id}: workerを開始した（pid={process.pid}、入出力の記録={directory}）", flush=True)

    def poll(self) -> dict[str, dict[str, Any]]:
        """終了した対象だけを回収し、他対象の観測と未開始集合を保持する。"""
        completed: dict[str, dict[str, Any]] = {}
        for repo_id, worker in tuple(self.active.items()):
            code = worker.process.poll()
            if code is not None:
                worker.resources.close()
            try:
                payload = json.loads((worker.directory / "worker-output.json").read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or not set(payload) <= set(worker.repositories):
                    raise ValueError("workerの結果の対象が一致しない")
                for record in payload.values():
                    if not isinstance(record, dict) or record.get("status") not in {
                        "completed",
                        "incomplete",
                        "blocked",
                        "pending",
                        "owner_wait",
                        "prepare_failed",
                    }:
                        raise ValueError("workerの結果状態が不正")
            except (OSError, ValueError) as error:
                if code is None:
                    continue
                payload = {repo: {"status": "incomplete", "remaining": str(error)} for repo in worker.repositories}
            if code is not None:
                for repo in set(worker.repositories) - payload.keys():
                    payload[repo] = {"status": "incomplete", "remaining": "workerから結果が返っていない"}
            for repo, record in payload.items():
                if repo in worker.reported:
                    continue
                if record.get("reservation_excluded"):
                    worker.reported.add(repo)
                    self.started.discard(repo)
                    continue
                if code is not None and code != 0 and record["status"] == "completed":
                    record = {**record, "status": "incomplete"}
                record = {
                    **record,
                    "worker_log": str(worker.directory),
                    "worker_exit_code": code,
                    "resume_request_id": worker.request_id,
                }
                self.results[repo] = record
                completed[repo] = record
                worker.reported.add(repo)
                event = "対象の処理を回収した" if code is None else f"workerが終了した（終了{code}）"
                print(f"{repo}: {event}: {record['status']}", flush=True)
                if record.get("remaining"):
                    next_action.report(
                        f"{repo}: {record['remaining']}（入出力の記録: {worker.directory}）",
                        next_action="端末に出た診断を確認し、原因を解消してからatk wi process-loop resume autoで再開する",
                    )
            if code is not None:
                del self.active[repo_id]
        return completed

    def wait(self) -> None:
        """一つの実行識別子を短く待ち、終了や新着の再確認へ戻る。"""
        if not self.active:
            return
        worker = next(iter(self.active.values()))
        with contextlib.suppress(subprocess.TimeoutExpired):
            worker.process.wait(timeout=3)
