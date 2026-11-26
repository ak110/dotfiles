"""`atk wi process-loop`の待機中と各セッションの開始前のアラートの確認。"""

import argparse
import datetime
import hashlib
import pathlib
import subprocess
import time

from filelock import FileLock, Timeout

from agent_toolkit._atk import config_settings
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk import review_audit as _review_audit
from agent_toolkit._atk.wi import alerts as _alerts
from agent_toolkit._atk.wi import branches as _wi_branches
from agent_toolkit._atk.wi import github_submission as _github_submission
from agent_toolkit._atk.wi import process_loop_env as _env
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._atk.wi import repo as _wi_repo
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import runtime_environment, state_paths
from agent_toolkit._common.atomic_file import atomic_write


def _resolve_forge(repo_id: str, forge: str) -> str:
    if forge != "auto":
        return forge
    return "github" if repo_id.partition("/")[0] == "github.com" else "gitlab"


def claim_periodic_check(repo_id: str, forge: str) -> bool:
    environment = runtime_environment.environment()
    if environment.get(_env.NO_ALERTS_ENV) == "1":
        return False
    if _resolve_forge(repo_id, forge) == "github":
        if _github_submission.canonical_source(repo_id, "issues", 1) is None:
            return False
        settings = config_settings.repository_settings(check_paths=False).get(repo_id, {})
        if settings.get("github_issues") is not True:
            return False
    try:
        interval = _env.positive_alert_interval(environment.get(_env.ALERT_INTERVAL_ENV, ""))
    except ValueError:
        interval = _env.DEFAULT_ALERT_INTERVAL
    key = f"wi-periodic-alerts-{hashlib.sha256(repo_id.lower().encode()).hexdigest()}"
    path = state_paths.state_dir() / f"{key}.time"
    lock = state_paths.lock_dir() / f"{key}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(lock, timeout=10):
            now = int(time.time())
            try:
                previous = int(path.read_text(encoding="utf-8"))
            except FileNotFoundError, ValueError:
                previous = -1
            if 0 <= previous <= now and now - previous < interval:
                return False
            atomic_write(path, f"{now}\n")
    except Timeout:
        return False
    return True


def check_process_loop_alerts(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    target_repo_id: str,
    local_path: pathlib.Path,
    last_alert_check: float | None,
    *,
    count_dependabot: bool,
    branch: str | None = None,
    include_repository_alerts: bool = True,
) -> tuple[float | None, int, frozenset[int] | None]:
    """確認間隔を満たす場合だけアラートを確認し、確認時刻、CI失敗のAWI投入件数、未判定のDependabotアラート番号を返す。

    CI失敗の確認は、キューが空の待機中に加えて各セッションの開始前にも呼ぶ。処理中の期間に起きた失敗も
    人の操作を介さずにAWIへ入れるためであり、確認時刻を両方の呼び出しで共有して`--alert-interval`より短い間隔で
    外部APIを呼ばない。
    Dependabotアラートはprocess-wiの実行が行う自動コードレビュー監査が判定するため、AWIを起票せず番号だけを返す。
    呼び出し側は投入が無く番号が1件以上のとき、監査を実施させるためにprocess-wiを1回実行させる。
    セッション開始前の呼び出し（`count_dependabot`が偽）はこれから起動するセッションが監査を行うため数えない。
    番号の代わりのNoneはアラートを観測しなかったこと（`--no-alerts`、確認間隔内の省略、数えない呼び出し、
    取得の失敗）を示し、起動要否の判定はその反復のアラートを比較に使わない。
    系統を登録したrepositoryでは系統（`branch`）ごとに呼び、その系統のブランチのCI失敗をその系統宛てのAWIにする。
    repository単位のアラート（GitLabのPipeline Schedule）は`include_repository_alerts`の対象（default_branchの系統）だけが扱う。
    """
    if args.no_alerts:
        return last_alert_check, 0, None
    monotonic_now = time.monotonic()
    if last_alert_check is not None and monotonic_now - last_alert_check < args.alert_interval:
        return last_alert_check, 0, None
    # 外部APIの照会は1件あたり数秒から十数秒かかるため、待機中と区別できるよう着手を表示する。
    label = target_repo_id if branch is None else f"{target_repo_id}@{branch}"
    print(f"{label}のCI失敗とアラートを確認しています。", flush=True)
    try:
        submitted = _alerts.check_and_submit_alerts(
            private_notes,
            target_repo_id,
            local_path,
            forge=args.alert_forge,
            now=datetime.datetime.now(),
            branch=branch,
            include_repository_alerts=include_repository_alerts,
        ).submitted
    except (_alerts.AlertCollectError, subprocess.CalledProcessError) as exc:
        _next_action.report(
            f"警告: アラート確認処理に失敗しました: {exc}",
            next_action=_alerts.ALERT_FAILURE_NEXT_ACTION,
        )
        submitted = 0
    dependabot_pending = _dependabot_pending(args, target_repo_id) if count_dependabot else None
    pending_count = len(dependabot_pending or ())
    _process_loop_log.append(
        "alert_check",
        submitted=submitted,
        dependabot_pending=pending_count,
        session_started=submitted == 0 and pending_count > 0,
    )
    return monotonic_now, submitted, dependabot_pending


def observe_dependabot_pending(args: argparse.Namespace, target_repo_id: str) -> frozenset[int] | None:
    """確認間隔によらず未判定のDependabotアラート番号を取得する。`--no-alerts`と取得の失敗ではNoneを返す。

    セッションの終了時に、起動時の未判定アラートが判定されたかを確かめるために使う。
    """
    if args.no_alerts:
        return None
    return _dependabot_pending(args, target_repo_id)


def _dependabot_pending(args: argparse.Namespace, target_repo_id: str) -> frozenset[int] | None:
    """GitHubの対象リポジトリで未判定のDependabotアラート番号を返す。取得できない場合は警告してNoneを返す。"""
    _, _, repo_path = target_repo_id.partition("/")
    forge = _resolve_forge(target_repo_id, args.alert_forge)
    if forge != "github" or not repo_path:
        return frozenset()
    try:
        result = _review_audit.dependabot_pending(repo_path)
        if result["status"] == "unauthorized":
            _next_action.report(
                f"警告: Dependabotアラートの取得権限がありません: {result.get('message', '')}",
                next_action=_alerts.ALERT_FAILURE_NEXT_ACTION,
            )
            return None
        return frozenset(int(alert["number"]) for alert in result["alerts"])
    except _next_action.ActionableError as exc:
        _next_action.report(
            f"警告: Dependabotアラートの確認に失敗しました: {exc}",
            next_action=_alerts.ALERT_FAILURE_NEXT_ACTION,
        )
        return None


def cmd_check_alerts(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> int:
    local_path = _wi_repo.resolve_local_worktree(args.target_repo)
    repo_id = _wi_repo.resolve_repo_id(None, cwd=local_path)
    next_action = (
        "診断に示した対象・ブランチ設定と`gh auth status`（GitLabは`glab auth status`）を確認し、"
        "`atk wi check-alerts`を再実行してください"
    )
    periodic = getattr(args, "periodic", False)
    if periodic:
        try:
            due = claim_periodic_check(repo_id, args.forge)
        except (OSError, ValueError) as error:
            _outcome.report_failure(f"定期確認の設定または状態を読めません: {error}", next_action=next_action)
            return 1
        if not due:
            _outcome.report_success(f"定期確認を省略しました: 対象={repo_id}")
            return 0
    settings = _wi_branches.registry().get(repo_id)

    branches: tuple[str | None, ...] = (None,) if settings is None else settings.branches
    submitted = 0
    failures: list[str] = []
    for branch in branches:
        result = _alerts.check_and_submit_alerts(
            private_notes,
            repo_id,
            local_path,
            forge=args.forge,
            now=now,
            next_action=next_action,
            branch=branch,
            include_repository_alerts=settings is None or branch == settings.default_branch,
        )
        submitted += result.submitted
        failures.extend(result.failures)
    if periodic and _resolve_forge(repo_id, args.forge) == "github":
        imported = _github_submission.check_and_submit(private_notes, repo_id, now=now)
        _outcome.report_success(f"GitHubのIssueとPRの取込: 対象={repo_id} WI投入={imported}件")
    scope = repo_id if settings is None else f"{repo_id}（系統: {', '.join(settings.branches)}）"
    message = f"CI失敗監視: 対象={scope} AWI投入={submitted}件"
    if failures:
        _outcome.report_failure(f"{message} 取得不能={len(failures)}種別", next_action=next_action)
        return 1
    _outcome.report_success(message)
    return 0
