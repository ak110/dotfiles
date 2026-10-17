"""登録集合の処理を開始するかの判定と、各対象cwdで起動する通常delegateへの処理の受け渡し。"""

import argparse
import asyncio
import json
import os
import pathlib
import sys
from collections.abc import Callable
from typing import Any, Literal, Protocol

from agent_toolkit._atk import config as _config
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_alerts as _alerts
from agent_toolkit._atk.wi import process_loop_control as _control
from agent_toolkit._atk.wi import process_loop_env as _env
from agent_toolkit._atk.wi import process_loop_log as _log
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._atk.wi import process_loop_session as _session
from agent_toolkit._atk.wi import process_loop_update as _update
from agent_toolkit._atk.wi import process_loop_watch as _watch
from agent_toolkit._common.atomic_file import atomic_write


class RepositoryDelegateManager(Protocol):
    """公開CLIから注入される、通常delegateの起動・再開・終端の契約。"""

    def activate(self) -> None:
        """登録と終端監視を有効にする。"""
        ...

    async def start(
        self,
        model_type: str,
        prompt: str,
        cwd: str,
        *,
        launch_kind: Literal["delegate"],
        label: str | None = None,
    ) -> dict[str, Any]:
        """対象cwdの通常delegateを起動する。"""
        ...

    async def send_message(self, session_id: str, prompt: str) -> dict[str, Any]:
        """保持した担当会話へ再開指示を渡す。"""
        ...

    async def wait(self) -> dict[str, Any]:
        """終端と通知を既存の回収形式で受け取る。"""
        ...

    async def close(self) -> None:
        """起動した実行主体と登録を閉じる。"""
        ...


type RepositoryManagerFactory = Callable[[], RepositoryDelegateManager]


def _wait_for_changes(private_notes: pathlib.Path, *, owner_wait: bool = False) -> None:
    """登録の変更・所有の解放・中断の要求を、キューの変更と同じ待機処理で監視する。"""
    _watch.wait_for_changes(
        private_notes,
        None,
        timeout=3.0 if owner_wait else 600.0,
        state_directories=(
            _config.process_loop_configuration_path().parent,
            _ownership.ownership_directory(),
            _log.abort_path().parent,
        ),
    )


async def _launch_repository(
    manager: RepositoryDelegateManager,
    snapshot: _listing.RepositorySnapshot,
    args: argparse.Namespace,
    instruction: Callable[[], str],
    resume_repository_id: str | None,
) -> tuple[_ownership.Ownership, str] | None:
    repository = snapshot.repository
    ownership = _ownership.try_acquire(repository.repo_id, worktree=repository.worktree, mode="all-repos")
    if ownership is None:
        _log.append("repository_owner_wait", target_repo=repository.repo_id)
        return None
    try:
        requested = getattr(args, "resume", None) if resume_repository_id == repository.repo_id else None
        resume_id = _session.all_repos_resume_session(ownership, requested)
        prompt = _session.build_repository_delegate_prompt(
            snapshot,
            ownership,
            instruction=instruction(),
            worktree_name=args.worktree,
        )
        if resume_id is not None:
            response = await manager.send_message(resume_id, prompt)
            if response["delivery"] not in {"reply_started", "steered"}:
                raise RuntimeError(f"担当の再開を確定できない: {resume_id}: {response}")
            session_id = resume_id
        else:
            response = await manager.start(
                # 起動手段にかかわらず、用途からClaude接続の役割を決める。
                "orchestrate",
                prompt,
                str(repository.worktree),
                launch_kind="delegate",
                label=f"process-wi: {repository.repo_id}",
            )
            session_id = response["session_id"]
        ownership = _ownership.update_session(ownership, session_id)
        _log.append("repository_session_start", target_repo=repository.repo_id, session_id=session_id)
        return ownership, session_id
    except Exception:
        _ownership.release(ownership, completed=False)
        raise


async def run_repository_delegates(
    args: argparse.Namespace,
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    *,
    manager: RepositoryDelegateManager | None = None,
    manager_factory: RepositoryManagerFactory | None = None,
    audit_repositories: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """リポジトリを独立に起動・回収し、1担当の失敗を他担当へ伝播させない。"""
    if manager is not None:
        active_manager = manager
    else:
        if manager_factory is None:
            raise ValueError("all-reposの担当起動には公開atk CLIのmanager factoryが必要")
        active_manager = manager_factory()
    active_manager.activate()
    owners: dict[str, _ownership.Ownership] = {}
    results: dict[str, dict[str, Any]] = {}
    shared_instruction: str | None = None

    def instruction() -> str:
        nonlocal shared_instruction
        if shared_instruction is None:
            shared_instruction = _log.consume_instructions()
        return shared_instruction

    try:
        resume_repository_id = None
        if getattr(args, "resume", None) is not None:
            resume_repository_id = _ownership.resume_repository(
                args.resume,
                tuple(snapshot.repository.repo_id for snapshot in snapshots),
            )
        targets = [
            snapshot
            for snapshot in snapshots
            if snapshot.has_work
            or snapshot.repository.repo_id in (audit_repositories or set())
            or snapshot.repository.repo_id == resume_repository_id
        ]
        launches = await asyncio.gather(
            *(_launch_repository(active_manager, snapshot, args, instruction, resume_repository_id) for snapshot in targets),
            return_exceptions=True,
        )
        for snapshot, launched in zip(targets, launches, strict=True):
            repo_id = snapshot.repository.repo_id
            if isinstance(launched, BaseException):
                results[repo_id] = {"status": "failed", "error": str(launched)}
                _log.append("repository_launch_failed", target_repo=repo_id, detail=str(launched))
            elif launched is None:
                results[repo_id] = {"status": "owner_wait"}
            else:
                ownership, session_id = launched
                owners[session_id] = ownership
        aborted = False
        while owners:
            wait_task = asyncio.create_task(active_manager.wait())
            while not wait_task.done():
                await asyncio.wait({wait_task}, timeout=1)
                if not aborted and _control.consume_process_loop_abort():
                    aborted = True
            result = await wait_task
            for notice in result.get("notices", []):
                print(f"担当からの通知: {notice}")
            session_id = result.get("session_id")
            if session_id not in owners or result.get("status") not in {"completed", "failed", "interrupted", "expired"}:
                continue
            ownership = owners.pop(session_id)
            result_path = ownership.path.with_name(ownership.path.stem + ".result.json")
            atomic_write(result_path, json.dumps(result, ensure_ascii=False, default=str) + "\n")
            results[ownership.repo_id] = {**result, "result_path": str(result_path)}
            blocked = any(line.startswith("続行できない理由:") for line in str(result.get("agent_message", "")).splitlines())
            completed = result["status"] == "completed" and not blocked
            _ownership.release(ownership, completed=completed)
            _log.append(
                "repository_session_end",
                target_repo=ownership.repo_id,
                session_id=session_id,
                status=result["status"],
                result_path=str(result_path),
            )
        if aborted:
            results["abort"] = {"status": "aborted"}
        return results
    finally:
        await active_manager.close()
        for ownership in owners.values():
            _ownership.release(ownership, completed=False)


def _run_loop(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    manager_factory: RepositoryManagerFactory,
) -> None:
    """登録集合の同期・開始前更新を共有し、対象固有の全工程を担当へ委譲する。"""
    if args.target_repo is not None:
        raise ValueError("all-reposは--target-repoを受理しない。process_loop_repositoriesで登録する")
    if args.resume == "":
        raise ValueError("all-reposの--resumeには担当session IDを指定する")
    gate = _watch.StartGate(private_notes)
    dotfiles_root = _update.resolve_dotfiles_root()
    startup_hash = _update.code_hash(dotfiles_root / "agent-toolkit" / "scripts") if dotfiles_root else None
    last_alert_checks: dict[str, float | None] = {}
    print("atk wi process-loop all-repos 常駐モード開始。Ctrl+Cで終了。")
    try:
        while not _control.consume_process_loop_abort():
            repositories = _config.resolve_configured_repositories()
            target_ids = {repository.repo_id for repository in repositories}
            if not _watch.pull_private_notes(private_notes):
                _wait_for_changes(private_notes)
                continue
            snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            audit: set[str] = set()
            if args.resume is not None:
                audit.add(_ownership.resume_repository(args.resume, tuple(target_ids)))
            elif args.auto_resume:
                audit.update(_ownership.unfinished_repository_ids(tuple(target_ids)))
            for snapshot in snapshots:
                repository = snapshot.repository
                try:
                    checked, submitted, pending = _alerts.check_process_loop_alerts(
                        args,
                        private_notes,
                        repository.repo_id,
                        repository.worktree,
                        last_alert_checks.get(repository.repo_id),
                        count_dependabot=not snapshot.has_work,
                    )
                    last_alert_checks[repository.repo_id] = checked
                    if pending:
                        audit.add(repository.repo_id)
                    if submitted:
                        snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
                except (OSError, ValueError) as error:
                    _log.append("repository_alert_failed", target_repo=repository.repo_id, detail=str(error))
            if not any(snapshot.has_work for snapshot in snapshots) and not audit:
                _wait_for_changes(private_notes)
                if not args.no_update and dotfiles_root is not None and startup_hash is not None:
                    _update.check_and_restart_on_update(dotfiles_root, startup_hash, sys.argv)
                continue
            if not _watch.wait_for_start_permission(private_notes, target_ids, gate):
                return
            if _config.resolve_configured_repositories() != repositories:
                # 起動前の登録変更は次の取得へ渡し、新集合の同期・監査・A/B判定をやり直す。
                continue
            snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            if not args.no_update and not args.internal_dotfiles_updated:
                ready, _updated = _update.update_before_session(
                    private_notes, dotfiles_root, startup_hash, sys.argv, _env.child_env(), mark_mise_refreshed=False
                )
                if not ready:
                    _wait_for_changes(private_notes)
                    continue
                snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            if _config.resolve_configured_repositories() != repositories:
                continue
            gate.started()
            results = asyncio.run(
                run_repository_delegates(args, snapshots, manager_factory=manager_factory, audit_repositories=audit)
            )
            args.resume = None
            # --auto-resumeも既存の共通CLI契約どおり初回だけ有効とする。
            args.auto_resume = False
            if "abort" in results:
                return
            if not args.no_update:
                _update.restart_process_loop(
                    sys.argv, dotfiles_root, resume_consumed=True, mise_refreshed=False, dotfiles_updated=False
                )
            _wait_for_changes(private_notes, owner_wait=any(result["status"] == "owner_wait" for result in results.values()))
    except KeyboardInterrupt:
        print("Ctrl+Cを検知しました。all-reposを終了します。")


def cmd_process_loop_all_repos(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    *,
    manager_factory: RepositoryManagerFactory | None = None,
) -> None:
    """全対象の常駐ログと子環境を有効にし、終了時に起動前の環境へ戻す。"""
    if manager_factory is None:
        raise ValueError("all-reposは公開atk CLIから起動する")
    previous = {
        _env.PROCESS_LOOP_SESSION_ENV: os.environ.get(_env.PROCESS_LOOP_SESSION_ENV),
        _env.PROCESS_LOOP_SESSION_ID_ENV: os.environ.get(_env.PROCESS_LOOP_SESSION_ID_ENV),
    }
    os.environ[_env.PROCESS_LOOP_SESSION_ENV] = "1"
    os.environ.pop(_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    try:
        _run_loop(args, private_notes, manager_factory)
    finally:
        _env.restore_process_loop_env(previous)
