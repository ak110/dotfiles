"""登録集合の処理を開始するかの判定と、各対象cwdで起動する通常delegateへの処理の受け渡し。"""

import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import json
import os
import pathlib
import sys
from collections.abc import Callable
from typing import Any, Literal, Protocol

from agent_toolkit._atk import config as _config
from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_alerts as _alerts
from agent_toolkit._atk.wi import process_loop_control as _control
from agent_toolkit._atk.wi import process_loop_env as _env
from agent_toolkit._atk.wi import process_loop_launch_gate as _gate
from agent_toolkit._atk.wi import process_loop_log as _log
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._atk.wi import process_loop_session as _session
from agent_toolkit._atk.wi import process_loop_update as _update
from agent_toolkit._atk.wi import process_loop_watch as _watch
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command

# resume_waits.AUTO_RESUME_DEADLINE_SECONDSへ配送と再開の余裕を加える。
# _atkから_agents_serverへの逆向きimportを避け、一致は契約テストで確かめる。
STALL_THRESHOLD_SECONDS = 7800 + 1800
_TERMINAL_STATUSES = {"completed", "failed", "interrupted", "expired"}


class RepositoryDelegateManager(Protocol):
    """公開CLIから注入される、通常delegateの起動・再開・終端の契約。"""

    async def activate_async(self) -> None:
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

    async def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
        """担当の出力更新時刻と起動時刻を照会する。"""
        ...

    async def kill(self, session_id: str, timeout: float = 270, stop: bool = False) -> dict[str, Any]:
        """待機上限を省略した場合は270秒で担当の終端を確認する。"""
        ...

    async def close(self) -> None:
        """起動した実行主体と登録を閉じる。"""
        ...


type RepositoryManagerFactory = Callable[[], RepositoryDelegateManager]


@dataclasses.dataclass(frozen=True)
class _Progress:
    items: frozenset[tuple[str, str]]
    lanes: frozenset[tuple[str, bool, str]]

    def unfinished(self, candidates: set[str]) -> dict[str, list[str]]:
        return {
            "wi": sorted(name for name, state in self.items if state == "processing" or name in candidates),
            "lanes": sorted(name for name, integrated, _head in self.lanes if not integrated),
        }


def _progress(snapshot: _listing.RepositorySnapshot, ownership: _ownership.Ownership) -> _Progress:
    current = _listing.configured_repo_snapshot(snapshot.private_notes, (snapshot.repository,))[0]
    resources = _lane.list_resources(ownership.lane_session_id)
    if resources["errors"]:
        raise ValueError(f"レーン登録を照会できない: {resources['errors']}")
    lanes: set[tuple[str, bool, str]] = set()
    for entry in resources["records"]:
        record = entry["record"]
        integrated = record.get("integration", {}).get("phase") in {"git-integrated", "finalized"}
        result = _git_command.run(
            ["rev-parse", "--short", "--verify", f"refs/heads/{record['branch']}"],
            pathlib.Path(record["repo"]),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ValueError(f"レーンbranchのHEADを取得できない: {record['lane']}: {result.stderr.strip()}")
        lanes.add((record["lane"], integrated, result.stdout.strip()))
    return _Progress(
        frozenset((entry[0].name, entry[3]) for entry in current.entries if entry[3] in {"inbox", "processing"}),
        frozenset(lanes),
    )


def _report(event: str, ownership: _ownership.Ownership, detail: str) -> None:
    _log.append(event, target_repo=ownership.repo_id, session_id=ownership.session_id, detail=detail)
    print(f"{ownership.repo_id}: {detail}", flush=True)


def _finish_repository(ownership: _ownership.Ownership, *, completed: bool) -> None:
    """続行の予定が無くなった担当の所有を解放し、その担当のレーン所有sessionの資源を回収する。

    停止を確認できない担当と続行を配送する担当はここへ到達させない。回収の可否は`lane.cleanup_session`の
    `_eligible`が判定する保持条件（未統合、使用中、未commitの変更など）が決め、未統合の資源は次の反復の再開（同じレーン所有session）へ残る。
    """
    _ownership.release(ownership, completed=completed)
    _session.cleanup_lane_session(ownership.lane_session_id, target_repo=ownership.repo_id)


async def _stop_repository(manager: RepositoryDelegateManager, ownership: _ownership.Ownership) -> dict[str, Any]:
    try:
        assert ownership.session_id is not None
        result = await manager.kill(ownership.session_id)
        if result.get("status") in _TERMINAL_STATUSES:
            _finish_repository(ownership, completed=False)
            return result
        detail = str(result)
    except Exception as error:
        detail = str(error)
    _ownership.hold(ownership)
    _report(
        "repository_ownership_held", ownership, f"停止を確認できず所有を保持した: {detail}。次の反復で担当の生存を照会する。"
    )
    return {"status": "owner_wait", "error": detail}


async def _stop_stalled(
    manager: RepositoryDelegateManager,
    owners: dict[str, _ownership.Ownership],
    results: dict[str, dict[str, Any]],
) -> None:
    for session_id, ownership in tuple(owners.items()):
        try:
            state = await manager.show_session(session_id, verbose=True)
            timestamp = state.get("output_updated_at") or state["started_at"]
            elapsed = (datetime.datetime.now(datetime.UTC) - datetime.datetime.fromisoformat(timestamp)).total_seconds()
        except Exception as error:
            _report(
                "repository_observation_failed", ownership, f"出力時刻の照会に失敗した: {error}。次の待機で照会を再試行する。"
            )
            continue
        if state.get("status") == "running" and elapsed > STALL_THRESHOLD_SECONDS:
            _report("repository_stalled", ownership, "出力停止を検知した。担当を停止し、再開情報を次の反復へ保持する。")
            results[ownership.repo_id] = await _stop_repository(manager, ownership)
            owners.pop(session_id)


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
    except BaseException:
        _ownership.release(ownership, completed=False)
        raise


def _record_no_progress(snapshot: _listing.RepositorySnapshot, start: _gate.LaunchInput | None) -> None:
    """進捗なしで終わった担当の起動入力を記録し、同じ入力の間は次の反復の判定がその対象の起動を見送る。"""
    repo_id = snapshot.repository.repo_id
    try:
        current = _listing.configured_repo_snapshot(snapshot.private_notes, (snapshot.repository,))[0]
        _gate.record_no_progress(
            repo_id,
            start if start is not None else _gate.repository_input(snapshot, None),
            _gate.repository_input(current, None),
        )
    except (OSError, ValueError) as error:
        # 記録できない場合は次の反復を従来どおり起動させ、他担当の終了処理を続ける。
        next_action = _gate.record_failure_next_action()
        _log.append("no_progress_record_failed", target_repo=repo_id, detail=str(error), next_action=next_action)
        _next_action.report(f"{repo_id}: 無進捗記録を保存できなかった: {error}", next_action=next_action, stream=sys.stdout)


async def run_repository_delegates(
    args: argparse.Namespace,
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    *,
    manager: RepositoryDelegateManager | None = None,
    manager_factory: RepositoryManagerFactory | None = None,
    audit_repositories: set[str] | None = None,
    launch_inputs: dict[str, _gate.LaunchInput] | None = None,
) -> dict[str, dict[str, Any]]:
    """リポジトリを独立に起動・回収し、1担当の失敗を他担当へ伝播させない。

    `launch_inputs`は起動判定に使った対象別の起動入力で、省略した対象は取得済み候補からアラート未観測として作成する。
    続行できない返却と無進捗の打切りでは、この起動入力と終了時の起動入力に共通する要素を無進捗記録へ残す。
    """
    if manager is not None:
        active_manager = manager
    else:
        if manager_factory is None:
            raise ValueError("all-reposの担当起動には公開atk CLIのmanager factoryが必要")
        active_manager = manager_factory()
    async with contextlib.AsyncExitStack() as lifetime:
        lifetime.push_async_callback(active_manager.close)
        return await _run_repository_delegates_owned(
            args, snapshots, active_manager, audit_repositories=audit_repositories, launch_inputs=launch_inputs
        )


async def _run_repository_delegates_owned(
    args: argparse.Namespace,
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    active_manager: RepositoryDelegateManager,
    *,
    audit_repositories: set[str] | None,
    launch_inputs: dict[str, _gate.LaunchInput] | None,
) -> dict[str, dict[str, Any]]:
    """managerの寿命の内側で担当を所有し、全停止の後に呼出元へ戻る。"""
    owners: dict[str, _ownership.Ownership] = {}
    results: dict[str, dict[str, Any]] = {}
    shared_instruction: str | None = None
    progress: dict[str, _Progress] = {}
    unchanged: dict[str, int] = {}
    snapshots_by_repo = {snapshot.repository.repo_id: snapshot for snapshot in snapshots}
    wait_task: asyncio.Task[dict[str, Any]] | None = None

    def instruction() -> str:
        nonlocal shared_instruction
        if shared_instruction is None:
            shared_instruction = _log.consume_instructions()
        return shared_instruction

    try:
        await active_manager.activate_async()
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
                print(f"{repo_id}の担当を起動できませんでした: {launched}", flush=True)
            elif launched is None:
                results[repo_id] = {"status": "owner_wait"}
                print(f"{repo_id}は別のprocess-loopが処理中です。所有者の終了を待って再取得します。", flush=True)
            else:
                ownership, session_id = launched
                owners[session_id] = ownership
                # 見送らずに起動した時点で記録を削除し、この担当の結果から記録し直す。
                _gate.clear(repo_id)
                print(f"{repo_id}の担当を起動しました（session: {session_id}）。", flush=True)
        if owners:
            print(f"{len(owners)}件の担当の終了を待機します。", flush=True)
        aborted = False
        wait_failures = 0
        while owners:
            wait_task = asyncio.create_task(active_manager.wait())
            try:
                while not wait_task.done():
                    await asyncio.wait({wait_task}, timeout=1)
                    if not aborted and _control.consume_process_loop_abort():
                        aborted = True
                        print("中断要求を検知しました。起動済みの担当の終了後にall-reposを終了します。", flush=True)
                    await _stop_stalled(active_manager, owners, results)
                    if not owners:
                        wait_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await wait_task
                        break
                if not owners:
                    break
                result = await wait_task
            except Exception as error:
                wait_failures += 1
                _log.append("repository_wait_failed", detail=str(error), consecutive=wait_failures)
                print(f"担当の待機に失敗した（{wait_failures}回）: {error}。待機を再試行する。", flush=True)
                if wait_failures >= 3:
                    for session_id, owner in tuple(owners.items()):
                        results[owner.repo_id] = await _stop_repository(active_manager, owner)
                        owners.pop(session_id)
                    print("待機の失敗が続いたため今回の担当を停止した。次の反復で再開する。", flush=True)
                continue
            wait_failures = 0
            for notice in result.get("notices", []):
                print(f"担当からの通知: {notice}", flush=True)
            session_id = result.get("session_id")
            if session_id not in owners or result.get("status") not in _TERMINAL_STATUSES:
                continue
            ownership = owners[session_id]
            result_path = ownership.path.with_name(ownership.path.stem + ".result.json")
            atomic_write(result_path, json.dumps(result, ensure_ascii=False, default=str) + "\n")
            results[ownership.repo_id] = {**result, "result_path": str(result_path)}
            blocked = any(line.startswith("続行できない理由:") for line in str(result.get("agent_message", "")).splitlines())
            completed = False
            abandoned = False
            if not blocked:
                try:
                    snapshot = snapshots_by_repo[ownership.repo_id]
                    observed = _progress(snapshot, ownership)
                    remaining_work = observed.unfinished({entry[0].name for entry in snapshot.candidates})
                    error = result.get("error")
                    held_result = isinstance(error, dict) and bool(error.get("heldResultFinalized"))
                    completed = not any(remaining_work.values()) and not held_result
                    if not completed:
                        unchanged[session_id] = unchanged.get(session_id, 0) + 1 if progress.get(session_id) == observed else 0
                        progress[session_id] = observed
                        if unchanged[session_id] < 2:
                            response = await active_manager.send_message(
                                session_id,
                                "残りの工程を再開し、agent-toolkit:process-wiを完遂する。\n"
                                + json.dumps(remaining_work, ensure_ascii=False),
                            )
                            if response.get("delivery") not in {"reply_started", "steered"}:
                                raise RuntimeError(f"続行指示の配送を確定できない: {response}")
                            _report("repository_session_continue", ownership, "未完了の工程の続行を指示した。")
                            continue
                        _report(
                            "repository_no_progress",
                            ownership,
                            "続行後に2回連続で進捗が無いため打ち切った。再開情報を次の反復へ保持する。",
                        )
                        abandoned = True
                except Exception as error:
                    _report(
                        "repository_completion_failed",
                        ownership,
                        f"完遂判定または続行に失敗した: {error}。担当を停止して次の反復で再開する。",
                    )
                    results[ownership.repo_id] = await _stop_repository(active_manager, ownership)
                    owners.pop(session_id)
                    continue
            owners.pop(session_id)
            _finish_repository(ownership, completed=completed)
            if blocked or abandoned:
                _record_no_progress(snapshots_by_repo[ownership.repo_id], (launch_inputs or {}).get(ownership.repo_id))
            _log.append(
                "repository_session_end",
                target_repo=ownership.repo_id,
                session_id=session_id,
                status=result["status"],
                result_path=str(result_path),
            )
            remaining = f"残り{len(owners)}件の担当を待機します。" if owners else ""
            print(f"{ownership.repo_id}の担当が終了しました（{result['status']}）。{remaining}", flush=True)
        if aborted:
            results["abort"] = {"status": "aborted"}
        return results
    finally:
        async with contextlib.AsyncExitStack() as cleanup:
            for owner in reversed(tuple(owners.values())):
                cleanup.push_async_callback(_stop_repository, active_manager, owner)
            if wait_task is not None and not wait_task.done():
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wait_task


def _print_work(snapshots: tuple[_listing.RepositorySnapshot, ...]) -> None:
    """取得したWIのうち、担当の起動を要する件数をリポジトリごとに表示する。"""
    for snapshot in snapshots:
        if snapshot.has_work:
            print(f"  {snapshot.repository.repo_id}: 処理候補{len(snapshot.candidates)}件", flush=True)


def _launch_plan(
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    explicit: set[str],
    alerts: dict[str, frozenset[int] | None],
) -> tuple[dict[str, _gate.LaunchInput], list[str]]:
    """起動する対象と起動入力、無進捗記録を理由に見送る対象を、共通の起動要否の判定で決める。

    明示指定（`--resume`・`--auto-resume`）の対象は記録によらず起動する。候補・診断・未判定アラートが無く、
    アラートの取得に成功して0件だった対象は起動入力が空になったため記録を削除する。
    """
    inputs: dict[str, _gate.LaunchInput] = {}
    skipped: list[str] = []
    for snapshot in snapshots:
        repo_id = snapshot.repository.repo_id
        observed = alerts.get(repo_id)
        current = _gate.repository_input(snapshot, observed)
        if repo_id in explicit or ((snapshot.has_work or observed) and _gate.decide(repo_id, current).launch):
            inputs[repo_id] = current
        elif snapshot.has_work or observed:
            skipped.append(repo_id)
        elif observed is not None:
            _gate.clear(repo_id)
    return inputs, skipped


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
    startup_hash = _update.code_hash(dotfiles_root / "agent-toolkit") if dotfiles_root else None
    last_alert_checks: dict[str, float | None] = {}
    print("atk wi process-loop all-repos 常駐モード開始。Ctrl+Cで終了。", flush=True)
    try:
        while not _control.consume_process_loop_abort():
            repositories = _config.resolve_configured_repositories()
            target_ids = {repository.repo_id for repository in repositories}
            print(f"private-notesを同期し、登録した{len(repositories)}件のリポジトリのWIを取得しています。", flush=True)
            if not _watch.pull_private_notes(private_notes):
                print("同期を再試行するまで変更検知を待機します。", flush=True)
                _wait_for_changes(private_notes)
                continue
            snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            _print_work(snapshots)
            explicit: set[str] = set()
            if args.resume is not None:
                explicit.add(_ownership.resume_repository(args.resume, tuple(target_ids)))
            elif args.auto_resume:
                explicit.update(_ownership.unfinished_repository_ids(tuple(target_ids)))
            alerts: dict[str, frozenset[int] | None] = {}
            # 候補WIだけで起動する対象はアラートを数えない。見送る対象と候補の無い対象は数え、新しい未判定アラートを観測する。
            item_launches, _skipped = _launch_plan(snapshots, explicit, alerts)
            for snapshot in snapshots:
                repository = snapshot.repository
                try:
                    checked, submitted, pending = _alerts.check_process_loop_alerts(
                        args,
                        private_notes,
                        repository.repo_id,
                        repository.worktree,
                        last_alert_checks.get(repository.repo_id),
                        count_dependabot=repository.repo_id not in item_launches,
                    )
                    last_alert_checks[repository.repo_id] = checked
                    alerts[repository.repo_id] = pending
                    if pending:
                        print(f"{repository.repo_id}に未判定のDependabotアラートが{len(pending)}件あります。", flush=True)
                    if submitted:
                        print(f"アラート監視により{repository.repo_id}へ{submitted}件のAWIを投入しました。", flush=True)
                        snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
                except (OSError, ValueError) as error:
                    _log.append("repository_alert_failed", target_repo=repository.repo_id, detail=str(error))
                    print(f"{repository.repo_id}のアラート確認に失敗しました: {error}", flush=True)
            launch_inputs, skipped = _launch_plan(snapshots, explicit, alerts)
            for repo_id in skipped:
                _gate.report_skip(repo_id)
            if not launch_inputs:
                print(
                    "起動を見送った対象を除き処理対象が0件のため変更検知を待機します。"
                    if skipped
                    else "処理対象が0件のため変更検知を待機します。",
                    flush=True,
                )
                _wait_for_changes(private_notes)
                if not args.no_update and dotfiles_root is not None and startup_hash is not None:
                    _update.check_and_restart_on_update(dotfiles_root, startup_hash, sys.argv)
                continue
            if not _watch.wait_for_start_permission(private_notes, target_ids, gate):
                return
            if _config.resolve_configured_repositories() != repositories:
                # 起動前の登録変更は次の取得へ渡し、新集合の同期・監査・A/B判定をやり直す。
                print("登録先の変更を検知したため、WIを取得し直します。", flush=True)
                continue
            snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            if not args.no_update and not args.internal_dotfiles_updated:
                print("処理開始前にdotfilesとprivate-notesを更新します。", flush=True)
                ready, _updated = _update.update_before_session(
                    private_notes, dotfiles_root, startup_hash, sys.argv, _env.child_env(), mark_mise_refreshed=False
                )
                if not ready:
                    print("同期を再試行するまで変更検知を待機します。", flush=True)
                    _wait_for_changes(private_notes)
                    continue
                snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            if _config.resolve_configured_repositories() != repositories:
                print("登録先の変更を検知したため、WIを取得し直します。", flush=True)
                continue
            launch_inputs, _skipped = _launch_plan(snapshots, explicit, alerts)
            for repo_id, current in tuple(launch_inputs.items()):
                if current.alerts is None:
                    # 起動時の未判定アラートを基準に記録し、終了後に増えたアラートだけを新しい入力として扱う。
                    launch_inputs[repo_id] = current.with_alerts(_alerts.observe_dependabot_pending(args, repo_id))
            launching = [snapshot for snapshot in snapshots if snapshot.repository.repo_id in launch_inputs]
            if not launching:
                print("開始待機の間に処理対象が無くなったため、WIを取得し直します。", flush=True)
                continue
            names = [snapshot.repository.repo_id for snapshot in launching]
            print(f"{len(launching)}件のリポジトリの担当へ委譲します: {', '.join(names)}", flush=True)
            gate.started()
            results = asyncio.run(
                run_repository_delegates(
                    args,
                    tuple(launching),
                    manager_factory=manager_factory,
                    audit_repositories=set(names),
                    launch_inputs=launch_inputs,
                )
            )
            args.resume = None
            # --auto-resumeも既存の共通CLI契約どおり初回だけ有効とする。
            args.auto_resume = False
            if "abort" in results:
                return
            print("今回起動した全担当が終了しました。", flush=True)
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
