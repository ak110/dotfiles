"""process-loopの常駐反復。登録集合の同期・開始前更新を共有し、統括の起動を繰り返す。"""

import argparse
import concurrent.futures
import json
import pathlib
import shlex
import sys
import time

from agent_toolkit._atk import config as _config
from agent_toolkit._atk.wi import listing as _listing
from agent_toolkit._atk.wi import process_loop_alerts as _alerts
from agent_toolkit._atk.wi import process_loop_control as _control
from agent_toolkit._atk.wi import process_loop_env as _env
from agent_toolkit._atk.wi import process_loop_launch_gate as _gate
from agent_toolkit._atk.wi import process_loop_log as _log
from agent_toolkit._atk.wi import process_loop_mise as _mise
from agent_toolkit._atk.wi import process_loop_ownership as _ownership
from agent_toolkit._atk.wi import process_loop_scheduler as _scheduler
from agent_toolkit._atk.wi import process_loop_update as _update
from agent_toolkit._atk.wi import process_loop_watch as _watch
from agent_toolkit._atk.wi import process_loop_worktree as _worktree
from agent_toolkit._common import next_action as _next_action


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


def _print_work(snapshots: tuple[_listing.RepositorySnapshot, ...]) -> None:
    """取得したWIのうち、担当の起動を要する件数をリポジトリごとに表示する。"""
    for snapshot in snapshots:
        if snapshot.has_work:
            print(f"  {snapshot.repository.key}: 処理候補{len(snapshot.candidates)}件", flush=True)


def _launch_plan(
    snapshots: tuple[_listing.RepositorySnapshot, ...],
    explicit: set[str],
    alerts: dict[str, frozenset[int] | None],
) -> tuple[dict[str, _gate.LaunchInput], list[str]]:
    """起動する対象と起動入力、無進捗記録を理由に見送る対象を、共通の起動要否の判定で決める。

    明示指定（`resume ID`・`resume auto`）の対象は記録によらず起動する。候補・診断・未判定アラートが無く、
    アラートの取得に成功して0件だった対象は起動入力が空になったため記録を削除する。
    """
    inputs: dict[str, _gate.LaunchInput] = {}
    skipped: list[str] = []
    for snapshot in snapshots:
        repo_id = snapshot.repository.key
        observed = alerts.get(repo_id)
        current = _gate.repository_input(snapshot, observed)
        if repo_id in explicit or ((snapshot.has_work or observed) and _gate.decide(repo_id, current).launch):
            inputs[repo_id] = current
        elif snapshot.has_work or observed:
            skipped.append(repo_id)
        elif observed is not None:
            _gate.clear(repo_id)
    return inputs, skipped


def _run_scheduled_loop(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    scheduler: _scheduler.RepositoryScheduler,
) -> None:
    """登録集合の同期・開始前更新を共有し、統括のprocess-wiの実行を繰り返す。"""
    gate = _watch.StartGate(private_notes)
    dotfiles_root = _update.resolve_dotfiles_root()
    startup_hash = _update.code_hash(dotfiles_root / "agent-toolkit") if dotfiles_root else None
    last_alert_checks: dict[str, float | None] = {}
    mise_refreshed: float | None = None
    once = getattr(args, "once", False)
    last_pull: float | None = None
    once_targets: set[str] | None = None
    once_snapshots: tuple[_listing.RepositorySnapshot, ...] | None = None
    failed_resume: str | None = None

    def wait_idle(*, owner_wait: bool = False) -> None:
        changed = _wait_for_changes(private_notes, owner_wait=owner_wait)
        if not changed and not args.no_update and dotfiles_root is not None and startup_hash is not None:
            _update.check_and_restart_on_update(dotfiles_root, startup_hash, sys.argv)

    print("atk wi process-loop 1回処理開始。" if once else "atk wi process-loop 常駐モード開始。Ctrl+Cで終了。", flush=True)
    try:
        while True:
            finished = scheduler.poll()
            for record in finished.values():
                if record.get("worker_exit_code") not in (None, 0):
                    failed_resume = record.get("resume_request_id")
            if _log.abort_path().exists():
                if scheduler.active:
                    scheduler.wait()
                    continue
                if not _control.consume_process_loop_abort():
                    continue
                if once:
                    raise SystemExit(1)
                return
            if finished and not scheduler.active and not once and not args.no_update:
                _update.restart_process_loop(sys.argv, dotfiles_root, mise_refreshed=False, dotfiles_updated=False)
            elif finished and not scheduler.active and not once:
                wait_idle(
                    owner_wait=any(result["status"] == "owner_wait" for result in finished.values()),
                )
                continue
            if len(scheduler.active) >= _scheduler.MAX_ACTIVE_TARGETS:
                scheduler.wait()
                continue
            repositories = _config.resolve_configured_repositories()
            target_ids = {repository.key for repository in repositories}
            repo_ids = {repository.repo_id for repository in repositories}
            try:
                reservation = _control.read_resume_request()
            except (OSError, ValueError) as error:
                _next_action.report(
                    str(error), next_action="process-loop statusで診断し、resume-cancelまたはresumeで予約を直す"
                )
                if once and not scheduler.active:
                    raise SystemExit(1) from error
                if scheduler.active:
                    scheduler.wait()
                else:
                    wait_idle()
                continue
            args.resume = None
            args.auto_resume = False
            args.resume_request_id = None
            args.reservation_lookup = False
            args.interactive_resume = False
            if reservation is not None:
                if reservation.request_id == failed_resume:
                    if once and not scheduler.active:
                        raise SystemExit(1)
                    if scheduler.active:
                        scheduler.wait()
                    else:
                        wait_idle()
                    continue
                related = (
                    {repo for repo in target_ids if _ownership.coordinator_session(repo) == reservation.session_id}
                    if reservation.mode == "id"
                    else (
                        _ownership.unfinished_repository_ids(tuple(target_ids))
                        if reservation.mode == "auto"
                        else set(target_ids)
                    )
                )
                if reservation.mode != "picker" and related and scheduler.busy.intersection(related):
                    scheduler.wait()
                    continue
                if reservation.mode == "auto" and not related:
                    _next_action.report(
                        "自動再開できる未完了会話がないため予約を保持した",
                        next_action="再開IDを指定するか、resume-cancelで予約を取り消す",
                    )
                    if once:
                        raise SystemExit(1)
                    wait_idle()
                    continue
                if reservation.mode == "picker" and (not sys.stdin.isatty() or not sys.stdout.isatty()):
                    _next_action.report("pickerを使える対話端末がない", next_action="再開IDまたはautoを指定して予約する")
                    if once:
                        raise SystemExit(1)
                    wait_idle()
                    continue
                args.resume = "" if reservation.mode == "picker" else reservation.session_id
                args.auto_resume = reservation.mode == "auto"
                args.resume_request_id = reservation.request_id
                args.reservation_lookup = reservation.mode == "picker" or (reservation.mode == "id" and not related)
                args.interactive_resume = reservation.mode == "picker"
            if (
                not args.no_update
                and not scheduler.active
                and dotfiles_root is not None
                and _worktree.DOTFILES_REPO_ID in repo_ids
                and (mise_refreshed is None or time.monotonic() - mise_refreshed >= _mise.MISE_REFRESH_INTERVAL_SEC)
            ):
                if mise_refreshed is not None or not args.internal_mise_refreshed:
                    _mise.refresh_mise_tools(dotfiles_root)
                mise_refreshed = time.monotonic()
            synchronize = not scheduler.active or last_pull is None or time.monotonic() - last_pull >= 30
            if synchronize:
                print(f"private-notesを同期し、登録した{len(repositories)}件のリポジトリのWIを取得しています。", flush=True)
            if synchronize and not _watch.pull_private_notes(private_notes):
                if once:
                    if not scheduler.active:
                        raise SystemExit(1)
                    scheduler.wait()
                    continue
                print("同期を再試行するまで変更検知を待機します。", flush=True)
                _wait_for_changes(private_notes)
                continue
            if synchronize:
                last_pull = time.monotonic()
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
            # APIの同時要求を少数に保つ。キュー保存はadd_entriesの既存repo_lockで直列化される。
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                checks = {
                    snapshot.repository.key: executor.submit(
                        _alerts.check_process_loop_alerts,
                        args,
                        private_notes,
                        snapshot.repository.repo_id,
                        snapshot.repository.worktree,
                        last_alert_checks.get(snapshot.repository.key),
                        # Dependabotはrepository単位の仕事のため、default_branchの系統の対象だけが数える。
                        count_dependabot=snapshot.repository.is_default and snapshot.repository.key not in item_launches,
                        branch=snapshot.repository.branch,
                        include_repository_alerts=snapshot.repository.is_default,
                    )
                    for snapshot in snapshots
                }
            submitted_any = False
            for snapshot in snapshots:
                repository = snapshot.repository
                try:
                    checked, submitted, pending = checks[repository.key].result()
                    last_alert_checks[repository.key] = checked
                    alerts[repository.key] = pending
                    if pending:
                        print(f"{repository.key}に未判定のDependabotアラートが{len(pending)}件あります。", flush=True)
                    if submitted:
                        print(f"アラート監視により{repository.key}へ{submitted}件のAWIを投入しました。", flush=True)
                        submitted_any = True
                except (OSError, ValueError) as error:
                    retry = "`atk wi process-loop --once`でDependabotを含む確認を再試行せよ" if once else ""
                    next_action = (
                        f"{_log.log_path()}のrepository_alert_failedを確認し、原因の解消後に"
                        f"`atk wi check-alerts --target-repo {shlex.quote(str(repository.worktree))} "
                        f"--forge {args.alert_forge}`でCI失敗を再確認せよ。" + (retry if once else "")
                    )
                    _log.append(
                        "repository_alert_failed", target_repo=repository.key, detail=str(error), next_action=next_action
                    )
                    _next_action.report(
                        f"{repository.key}のアラート確認に失敗しました: {error}。"
                        + ("Dependabotを含む自動確認は常駐の次の取得時に再試行する。" if not once else ""),
                        next_action=next_action,
                        stream=sys.stdout,
                    )
            if submitted_any:
                snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            if once:
                if once_snapshots is None:
                    once_snapshots = snapshots
                snapshots = once_snapshots
            available = tuple(
                snapshot
                for snapshot in snapshots
                if snapshot.repository.key not in scheduler.busy
                and (not once or snapshot.repository.key not in scheduler.started)
                and (once_targets is None or snapshot.repository.key in (once_targets or set()))
            )
            launch_inputs, skipped = _launch_plan(available, explicit, alerts)
            if once and once_targets is None:
                once_targets = set(launch_inputs)
            for repo_id in skipped:
                _gate.report_skip(repo_id)
            if not launch_inputs:
                if scheduler.active:
                    scheduler.wait()
                    continue
                if once:
                    if scheduler.results:
                        failed = {
                            repo: result["status"]
                            for repo, result in scheduler.results.items()
                            if result["status"] != "completed"
                        }
                        if failed:
                            _next_action.report(
                                f"未完了の対象があります: {json.dumps(failed, ensure_ascii=False)}",
                                next_action="対象別の所有・結果記録と残工程を確認して再開する",
                            )
                        raise SystemExit(1 if failed or skipped else 0)
                    print("今回処理できる対象は0件です。", flush=True)
                    raise SystemExit(1 if skipped else 0)
                print(
                    "起動を見送った対象を除き処理対象が0件のため変更検知を待機します。"
                    if skipped
                    else "処理対象が0件のため変更検知を待機します。",
                    flush=True,
                )
                wait_idle()
                continue
            launch_repositories = {
                snapshot.repository.repo_id for snapshot in available if snapshot.repository.key in launch_inputs
            }
            if not _watch.wait_for_start_permission(private_notes, launch_repositories, gate):
                # 中断の消費は先頭のdrain判定だけが所有し、他対象の終了回収を保つ。
                continue
            if _config.resolve_configured_repositories() != repositories:
                # 起動前の登録変更は次の取得へ渡し、新集合の同期・監査・A/B判定をやり直す。
                print("登録先の変更を検知したため、WIを取得し直します。", flush=True)
                continue
            snapshots = _listing.configured_repo_snapshot(private_notes, repositories)
            if not args.no_update and not args.internal_dotfiles_updated:
                print("処理開始前にdotfilesとprivate-notesを更新します。", flush=True)
                ready, _updated = _update.update_before_session(
                    private_notes,
                    dotfiles_root,
                    startup_hash,
                    sys.argv,
                    _env.child_env(),
                    mark_mise_refreshed=False,
                    restart_on_change=not scheduler.active,
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
            if once and once_snapshots is not None:
                snapshots = once_snapshots
            available = tuple(
                snapshot
                for snapshot in snapshots
                if snapshot.repository.key not in scheduler.busy
                and (not once or snapshot.repository.key not in scheduler.started)
                and (once_targets is None or snapshot.repository.key in (once_targets or set()))
            )
            launch_inputs, skipped = _launch_plan(available, explicit, alerts)
            by_key = {snapshot.repository.key: snapshot.repository for snapshot in available}
            for repo_id, current in tuple(launch_inputs.items()):
                if current.alerts is None:
                    # 起動時の未判定アラートを基準に記録し、終了後に増えたアラートだけを新しい入力として扱う。
                    launch_inputs[repo_id] = current.with_alerts(
                        _alerts.observe_dependabot_pending(args, by_key[repo_id].repo_id)
                        if by_key[repo_id].is_default
                        else frozenset()
                    )
            launching = [snapshot for snapshot in available if snapshot.repository.key in launch_inputs]
            if args.resume is not None or (args.auto_resume and args.resume_request_id is not None):
                launching = [snapshot for snapshot in launching if snapshot.repository.key in explicit]
            if not launching:
                if once:
                    print("開始待機の間に処理対象が無くなったため終了します。", flush=True)
                    return
                print("開始待機の間に処理対象が無くなったため、WIを取得し直します。", flush=True)
                continue
            if _log.abort_path().exists():
                continue
            names = [snapshot.repository.key for snapshot in launching]
            print(f"{len(launching)}件のリポジトリの所有・準備と統括処理を開始します: {', '.join(names)}", flush=True)
            gate.started()
            # 今回の全対象を1つの統括会話へ渡す。autoの再開では元会話ごとの結果をworkerが途中回収する。
            scheduler.start(args, tuple(launching), {repo: launch_inputs[repo] for repo in names})
            args.resume = None
            args.auto_resume = False
            scheduler.wait()
    except KeyboardInterrupt:
        print("Ctrl+Cを検知しました。process-loopを終了します。")
        if once:
            raise SystemExit(1) from None


def run_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    """常駐の全反復を通じて対象別workerの観測主体を保持する。"""
    with _scheduler.RepositoryScheduler() as scheduler:
        _run_scheduled_loop(args, private_notes, scheduler)
