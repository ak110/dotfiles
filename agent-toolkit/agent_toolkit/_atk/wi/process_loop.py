"""登録全体のprocess-loop。統括会話の実行とリポジトリ別の処理所有を管理する。"""

import argparse
import dataclasses
import json
import os
import pathlib
import shlex
import sys
import time
import uuid
from typing import Any

from agent_toolkit._atk import config as _config
from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_alerts as _alerts
from agent_toolkit._atk.wi import process_loop_control as _control
from agent_toolkit._atk.wi import process_loop_env as _env
from agent_toolkit._atk.wi import process_loop_launch_gate as _gate
from agent_toolkit._atk.wi import process_loop_log as _log
from agent_toolkit._atk.wi import process_loop_mise as _mise
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._atk.wi import process_loop_session as _session
from agent_toolkit._atk.wi import process_loop_update as _update
from agent_toolkit._atk.wi import process_loop_watch as _watch
from agent_toolkit._atk.wi import process_loop_worktree as _worktree
from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import session_registry as _registry
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._git import command as _git_command


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


def _wait_for_changes(private_notes: pathlib.Path, *, owner_wait: bool = False) -> bool:
    """登録の変更・所有の解放・中断の要求を、キューの変更と同じ待機処理で監視する。"""
    return _watch.wait_for_changes(
        private_notes,
        None,
        timeout=3.0 if owner_wait else 600.0,
        state_directories=(
            _config.process_loop_configuration_path().parent,
            _ownership.ownership_directory(),
            _log.abort_path().parent,
        ),
    )


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


def _read_results(path: pathlib.Path, owners: dict[str, _ownership.Ownership]) -> dict[str, dict[str, Any]]:
    """担当が保存した対象別の返却を検証し、欠落を成功として扱わない。"""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("repositories"), dict):
        raise ValueError(f"統括結果のrepositoriesが不正: {path}")
    records = data["repositories"]
    if records.keys() - owners.keys():
        raise ValueError(f"統括結果に今回の対象外のリポジトリがある: {path}")
    for repo_id, record in records.items():
        if not isinstance(record, dict) or record.get("status") not in ("completed", "incomplete", "blocked", "pending"):
            raise ValueError(f"統括結果の対象別状態が不正: {repo_id}")
        writers = record.get("writer_sessions", [])
        if not isinstance(writers, list) or any(not isinstance(value, str) or not value for value in writers):
            raise ValueError(f"統括結果のwriter_sessionsが不正: {repo_id}")
    return records


def _writers_stopped(
    owner: _ownership.Ownership,
    *,
    writers: list[str] | None = None,
    other_writers: set[str] | None = None,
) -> bool:
    """レーン登録に結び付く実行主体が停止済みの場合だけ回収へ進む。"""
    if _ownership.executor_running(owner):
        _ownership.hold(owner, writers=writers)
        return False
    descendants = _ownership.descendant_sessions({owner.session_id} if owner.session_id else set())
    # 他対象が申告した担当とその子孫は、その対象が停止・回収を所有する。
    # 未申告の子孫は所属を確定できないため、各対象の停止条件へ残す。
    descendants.difference_update(_ownership.descendant_sessions(other_writers or set()))
    descendants.update(_ownership.descendant_sessions(set(writers or [])))
    stopped = all(
        _registry.resolve(identifier).state not in {_registry.Resolution.RUNNING, _registry.Resolution.UNREADABLE}
        for identifier in descendants
    )
    if not stopped:
        _ownership.hold(owner, writers=sorted(descendants))
    return stopped


def _recover_completed(
    target: dict[str, Any], snapshot: _listing.RepositorySnapshot, owner: _ownership.Ownership
) -> dict[str, Any] | None:
    """親の終了処理が中断した対象について、保存済み申告と実体から完了を確定する。"""
    previous_manifest = (owner.previous or {}).get("manifest")
    if not isinstance(previous_manifest, str):
        return None
    data = json.loads(pathlib.Path(previous_manifest).read_text(encoding="utf-8"))
    result_path = pathlib.Path(data["result_file"])
    returned = _read_results(result_path, {item["target_repo"]: owner for item in data["repositories"]})
    record = returned.get(owner.repo_id, {})
    if record.get("status") != "completed":
        return None
    candidates = json.loads(pathlib.Path(target["candidate_file"]).read_text(encoding="utf-8"))
    if any(_progress(snapshot, owner).unfinished({item["filename"] for item in candidates}).values()):
        return None
    if not _writers_stopped(owner, writers=record.get("writer_sessions", [])):
        return None
    return {**record, "result_path": str(result_path), "session_id": owner.session_id, "recovered": True}


def run_coordinator(
    args: argparse.Namespace,
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    *,
    launch_inputs: dict[str, _gate.LaunchInput] | None = None,
) -> dict[str, dict[str, Any]]:
    """常駐cwdで1つの統括会話を実行し、対象別の成果を検収して同じ会話を継続する。"""
    owners: dict[str, _ownership.Ownership] = {}
    targets: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    progress: dict[str, _Progress] = {}
    unchanged: dict[str, int] = {}
    by_repo = {snapshot.repository.repo_id: snapshot for snapshot in snapshots}
    cwd = pathlib.Path.cwd().resolve()
    env = _env.child_env()
    requested = getattr(args, "resume", None)
    resume = requested
    if resume is None and getattr(args, "auto_resume", False):
        previous = {
            value
            for snapshot in snapshots
            if (value := _ownership.coordinator_session(snapshot.repository.repo_id)) is not None
        }
        resume = next(iter(previous)) if len(previous) == 1 else None
    directory = _ownership.ownership_directory() / "rounds"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / f"{uuid.uuid4()}.json"
    result_path = manifest.with_suffix(".result.json")
    try:
        for snapshot in snapshots:
            repository = snapshot.repository
            owner = _ownership.try_acquire(repository.repo_id, worktree=repository.worktree, mode="coordinator")
            if owner is None:
                results[repository.repo_id] = {"status": "owner_wait"}
                print(f"{repository.repo_id}: 別のprocess-loopが処理中です。所有の解放を待ちます。", flush=True)
                continue
            owners[repository.repo_id] = owner
            previous_worktree = (owner.previous or {}).get("prepared_worktree")
            resuming = isinstance(previous_worktree, str)
            prepared = _worktree.prepare_session_target(
                pathlib.Path(previous_worktree) if isinstance(previous_worktree, str) else repository.worktree,
                repository.repo_id,
                "",
                worktree_name=args.worktree,
                resume_pending=resuming,
            )
            if prepared is None:
                _ownership.release(owner, completed=False)
                owners.pop(repository.repo_id)
                results[repository.repo_id] = {"status": "prepare_failed"}
                continue
            target = _session.repository_input(
                snapshot,
                owner,
                worktree=prepared[0],
                reuse_candidates=resume is not None and (owner.previous or {}).get("session_id") == resume,
            )
            if resume is not None and (owner.previous or {}).get("session_id") == resume:
                owner = _ownership.update_session(owner, resume)
                owners[repository.repo_id] = owner
                recovered = _recover_completed(target, snapshot, owner)
                if recovered is not None:
                    recovered["previous_result_path"] = recovered["result_path"]
                    saved_result = manifest.with_name(f"{manifest.stem}.{owner.token}.result.json")
                    recovered["result_path"] = str(saved_result)
                    atomic_write(saved_result, json.dumps(recovered, ensure_ascii=False) + "\n")
                    results[repository.repo_id] = recovered
                    _gate.clear(repository.repo_id)
                    _finish_repository(owner, completed=True)
                    owners.pop(repository.repo_id)
                    continue
            if "launch_items" not in target:
                start = (launch_inputs or {}).get(repository.repo_id) or _gate.repository_input(snapshot, None)
                target["launch_items"] = sorted(start.items)
                target["launch_alerts"] = sorted(start.alerts) if start.alerts is not None else None
            _ownership.update_prepared(owner, prepared[0], manifest)
            targets[repository.repo_id] = target
            _gate.clear(repository.repo_id)
        if not owners:
            return results
        instruction = _log.consume_instructions()
        while owners:
            atomic_write(
                manifest,
                json.dumps(
                    {
                        "repositories": list(targets.values()),
                        "result_file": str(result_path),
                        "instruction": instruction,
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
            result_path.unlink(missing_ok=True)
            prompt = _session.build_process_loop_prompt(manifest)
            engine, model, effort = _session.select_available_orchestrator(
                _session.resolve_orchestrator_specs(),
                env,
                cwd,
                resume_session_id=resume,
            )
            session_args = argparse.Namespace(**vars(args))
            session_args.resume = resume

            def started(identifier: str) -> None:
                for repo_id, owner in tuple(owners.items()):
                    owners[repo_id] = _ownership.update_session(owner, identifier)

            def process_started(process: _registry.ProcessIdentity) -> None:
                for owner in owners.values():
                    _ownership.update_process(owner, process)

            execution = _session.run_process_session(
                session_args,
                cwd,
                prompt,
                env,
                orchestrator=engine,
                model=model,
                effort=effort,
                resume_pending=resume is not None,
                on_session_started=started,
                on_process_started=process_started,
            )
            identifier = env.get(_env.PROCESS_LOOP_SESSION_ID_ENV)
            if identifier:
                started(identifier)
                resume = identifier
            returned = _read_results(result_path, owners)
            for repo_id, owner in tuple(owners.items()):
                record = returned.get(repo_id, {})
                writers = record.get("writer_sessions", [])
                other_writers = {
                    writer
                    for other, result in returned.items()
                    if other != repo_id
                    for writer in result.get("writer_sessions", [])
                }
                observed = _progress(by_repo[repo_id], owner)
                candidate_records = json.loads(pathlib.Path(targets[repo_id]["candidate_file"]).read_text(encoding="utf-8"))
                candidate_names = {record["filename"] for record in candidate_records}
                observed = dataclasses.replace(
                    observed,
                    items=frozenset(
                        (name, state) for name, state in observed.items if state == "processing" or name in candidate_names
                    ),
                )
                remaining = observed.unfinished(candidate_names)
                completed = execution and record.get("status") == "completed" and not any(remaining.values())
                blocked = record.get("status") == "blocked"
                unchanged[repo_id] = unchanged.get(repo_id, 0) + 1 if progress.get(repo_id) == observed else 0
                progress[repo_id] = observed
                abandoned = unchanged.get(repo_id, 0) >= 2
                saved_result = manifest.with_name(f"{manifest.stem}.{owner.token}.result.json")
                results[repo_id] = {
                    **record,
                    "status": "completed" if completed else "blocked" if blocked else "incomplete",
                    "observed_remaining": remaining,
                    "result_path": str(saved_result),
                    "session_id": resume,
                }
                stopped = _writers_stopped(owner, writers=writers, other_writers=other_writers)
                if not stopped or not execution or completed or blocked or abandoned:
                    if stopped:
                        _finish_repository(owner, completed=bool(completed))
                    else:
                        _ownership.hold(owner)
                        results[repo_id]["status"] = "owner_wait"
                    if blocked or abandoned:
                        target = targets[repo_id]
                        initial = _gate.LaunchInput(
                            frozenset((name, digest, kind) for name, digest, kind in target["launch_items"]),
                            frozenset(target["launch_alerts"]) if target["launch_alerts"] is not None else None,
                        )
                        _record_no_progress(by_repo[repo_id], initial)
                    owners.pop(repo_id)
                    targets.pop(repo_id)
                atomic_write(saved_result, json.dumps(results[repo_id], ensure_ascii=False) + "\n")
            instruction = "残る対象の未完了工程を再開する。完了済み対象へ再着手しない。"
            if _control.consume_process_loop_abort():
                results["abort"] = {"status": "aborted"}
                break
            if owners and resume is None:
                raise ValueError("統括session IDを取得できないため続行しなかった。セッション記録を確認する")
        return results
    finally:
        for owner in owners.values():
            if _writers_stopped(owner):
                _finish_repository(owner, completed=False)
            else:
                _ownership.hold(owner)


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
) -> None:
    """登録集合の同期・開始前更新を共有し、統括のprocess-wiの実行を繰り返す。"""
    if args.resume == "":
        raise ValueError("process-loopの--resumeには担当session IDを指定する")
    gate = _watch.StartGate(private_notes)
    dotfiles_root = _update.resolve_dotfiles_root()
    startup_hash = _update.code_hash(dotfiles_root / "agent-toolkit") if dotfiles_root else None
    last_alert_checks: dict[str, float | None] = {}
    mise_refreshed: float | None = None
    once = getattr(args, "once", False)
    print("atk wi process-loop 1回処理開始。" if once else "atk wi process-loop 常駐モード開始。Ctrl+Cで終了。", flush=True)
    try:
        while not _control.consume_process_loop_abort():
            repositories = _config.resolve_configured_repositories()
            target_ids = {repository.repo_id for repository in repositories}
            if (
                not args.no_update
                and dotfiles_root is not None
                and _worktree.DOTFILES_REPO_ID in target_ids
                and (mise_refreshed is None or time.monotonic() - mise_refreshed >= _mise.MISE_REFRESH_INTERVAL_SEC)
            ):
                if mise_refreshed is not None or not args.internal_mise_refreshed:
                    _mise.refresh_mise_tools(dotfiles_root)
                mise_refreshed = time.monotonic()
            print(f"private-notesを同期し、登録した{len(repositories)}件のリポジトリのWIを取得しています。", flush=True)
            if not _watch.pull_private_notes(private_notes):
                if once:
                    raise SystemExit(1)
                print("同期を再試行するまで変更検知を待機します。", flush=True)
                _wait_for_changes(private_notes)
                continue
            snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            _print_work(snapshots)
            explicit: set[str] = set()
            if args.resume is not None:
                explicit.update(_ownership.resume_repositories(args.resume, tuple(target_ids)))
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
                    retry = (
                        "`atk wi process-loop --once`でDependabotを含む確認を再試行する"
                        if once
                        else "Dependabotを含む自動確認は常駐の次の取得時に再試行する"
                    )
                    next_action = (
                        f"{_log.log_path()}のrepository_alert_failedを確認し、原因の解消後に"
                        f"`atk wi check-alerts --target-repo {shlex.quote(str(repository.worktree))} "
                        f"--forge {args.alert_forge}`でCI失敗を再確認する。"
                        f"{retry}"
                    )
                    _log.append(
                        "repository_alert_failed", target_repo=repository.repo_id, detail=str(error), next_action=next_action
                    )
                    _next_action.report(
                        f"{repository.repo_id}のアラート確認に失敗しました: {error}",
                        next_action=next_action,
                        stream=sys.stdout,
                    )
            launch_inputs, skipped = _launch_plan(snapshots, explicit, alerts)
            for repo_id in skipped:
                _gate.report_skip(repo_id)
            if not launch_inputs:
                if once:
                    print("今回処理できる対象は0件です。", flush=True)
                    raise SystemExit(1 if skipped else 0)
                print(
                    "起動を見送った対象を除き処理対象が0件のため変更検知を待機します。"
                    if skipped
                    else "処理対象が0件のため変更検知を待機します。",
                    flush=True,
                )
                changed = _wait_for_changes(private_notes)
                if not changed and not args.no_update and dotfiles_root is not None and startup_hash is not None:
                    _update.check_and_restart_on_update(dotfiles_root, startup_hash, sys.argv)
                continue
            if not _watch.wait_for_start_permission(private_notes, target_ids, gate):
                if once:
                    raise SystemExit(1)
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
                if _updated:
                    mise_refreshed = time.monotonic()
                if not ready:
                    if once:
                        raise SystemExit(1)
                    print("同期を再試行するまで変更検知を待機します。", flush=True)
                    _wait_for_changes(private_notes)
                    continue
                snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            if _config.resolve_configured_repositories() != repositories:
                print("登録先の変更を検知したため、WIを取得し直します。", flush=True)
                continue
            launch_inputs, skipped = _launch_plan(snapshots, explicit, alerts)
            for repo_id, current in tuple(launch_inputs.items()):
                if current.alerts is None:
                    # 起動時の未判定アラートを基準に記録し、終了後に増えたアラートだけを新しい入力として扱う。
                    launch_inputs[repo_id] = current.with_alerts(_alerts.observe_dependabot_pending(args, repo_id))
            launching = [snapshot for snapshot in snapshots if snapshot.repository.repo_id in launch_inputs]
            if args.resume is not None:
                launching = [snapshot for snapshot in launching if snapshot.repository.repo_id in explicit]
            if not launching:
                if once:
                    print("開始待機の間に処理対象が無くなったため終了します。", flush=True)
                    return
                print("開始待機の間に処理対象が無くなったため、WIを取得し直します。", flush=True)
                continue
            names = [snapshot.repository.repo_id for snapshot in launching]
            print(f"{len(launching)}件のリポジトリの所有・準備と統括処理を開始します: {', '.join(names)}", flush=True)
            gate.started()
            results = run_coordinator(args, tuple(launching), launch_inputs=launch_inputs)
            args.resume = None
            # --auto-resumeも既存の共通CLI契約どおり初回だけ有効とする。
            args.auto_resume = False
            if "abort" in results:
                if once:
                    raise SystemExit(1)
                return
            print("今回起動した全担当が終了しました。", flush=True)
            if once:
                failed = {repo_id: result["status"] for repo_id, result in results.items() if result["status"] != "completed"}
                if failed:
                    _next_action.report(
                        f"未完了の対象があります: {json.dumps(failed, ensure_ascii=False)}",
                        next_action=(
                            f"{_ownership.ownership_directory()}の対象別の所有記録とrounds配下の結果を確認し、原因の解消後に"
                            "`atk wi process-loop --once --auto-resume`で再開する"
                        ),
                    )
                raise SystemExit(1 if failed or skipped else 0)
            if not args.no_update:
                print("process-loopを再起動します。", flush=True)
                _update.restart_process_loop(
                    sys.argv, dotfiles_root, resume_consumed=True, mise_refreshed=False, dotfiles_updated=False
                )
            _wait_for_changes(private_notes, owner_wait=any(result["status"] == "owner_wait" for result in results.values()))
    except KeyboardInterrupt:
        print("Ctrl+Cを検知しました。process-loopを終了します。")
        if once:
            raise SystemExit(1) from None


def cmd_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """登録全体のprocess-loopを開始し、呼出元の環境を復元する。"""
    previous = {
        _env.PROCESS_LOOP_SESSION_ENV: os.environ.get(_env.PROCESS_LOOP_SESSION_ENV),
        _env.PROCESS_LOOP_SESSION_ID_ENV: os.environ.get(_env.PROCESS_LOOP_SESSION_ID_ENV),
    }
    os.environ[_env.PROCESS_LOOP_SESSION_ENV] = "1"
    os.environ.pop(_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    try:
        with _console_title.console_title("atk wi process-loop"):
            _run_loop(args, private_notes)
    finally:
        _env.restore_process_loop_env(previous)
