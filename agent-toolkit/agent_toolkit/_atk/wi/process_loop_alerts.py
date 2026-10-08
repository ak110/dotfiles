"""`atk wi process-loop`の待機中と各セッションの開始前のアラートの確認。"""

import argparse
import datetime
import pathlib
import subprocess
import time

from agent_toolkit._atk import review_audit as _review_audit
from agent_toolkit._atk.wi import alerts as _alerts
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._common import next_action as _next_action


def check_process_loop_alerts(
    args: argparse.Namespace,
    private_notes: pathlib.Path,
    target_repo_id: str,
    local_path: pathlib.Path,
    last_alert_check: float | None,
    *,
    count_dependabot: bool,
) -> tuple[float | None, int, int]:
    """確認間隔を満たす場合だけアラートを確認し、確認時刻、CI失敗のAWI投入件数、未判定のDependabotアラート件数を返す。

    CI失敗の確認は、キューが空の待機中に加えて各セッションの開始前にも呼ぶ。処理中の期間に起きた失敗も
    人の操作を介さずにAWIへ入れるためであり、確認時刻を両方の呼び出しで共有して`--alert-interval`より短い間隔で
    外部APIを呼ばない。
    Dependabotアラートはprocess-wiの実行が行う自動コードレビュー監査が判定するため、AWIを起票せず件数だけを返す。
    呼び出し側は投入が無く件数が1以上のとき、監査を実施させるためにprocess-wiを1回実行させる。
    セッション開始前の呼び出し（`count_dependabot`が偽）はこれから起動するセッションが監査を行うため件数を数えない。
    """
    if args.no_alerts:
        return last_alert_check, 0, 0
    monotonic_now = time.monotonic()
    if last_alert_check is not None and monotonic_now - last_alert_check < args.alert_interval:
        return last_alert_check, 0, 0
    try:
        submitted = _alerts.check_and_submit_alerts(
            private_notes,
            target_repo_id,
            local_path,
            forge=args.alert_forge,
            now=datetime.datetime.now(),
        ).submitted
    except (_alerts.AlertCollectError, subprocess.CalledProcessError) as exc:
        _next_action.report(
            f"警告: アラート確認処理に失敗しました: {exc}",
            next_action=_alerts.ALERT_FAILURE_NEXT_ACTION,
        )
        submitted = 0
    dependabot_pending = _count_dependabot_pending(args, target_repo_id) if count_dependabot else 0
    _process_loop_log.append(
        "alert_check",
        submitted=submitted,
        dependabot_pending=dependabot_pending,
        session_started=submitted == 0 and dependabot_pending > 0,
    )
    return monotonic_now, submitted, dependabot_pending


def _count_dependabot_pending(args: argparse.Namespace, target_repo_id: str) -> int:
    """GitHubの対象リポジトリで未判定のDependabotアラート件数を返す。取得できない場合は警告して0とする。"""
    host, _, repo_path = target_repo_id.partition("/")
    forge = args.alert_forge if args.alert_forge != "auto" else ("github" if host == "github.com" else "gitlab")
    if forge != "github" or not repo_path:
        return 0
    try:
        return len(_review_audit.dependabot_pending(repo_path)["alerts"])
    except _next_action.ActionableError as exc:
        _next_action.report(
            f"警告: Dependabotアラートの確認に失敗しました: {exc}",
            next_action=_alerts.ALERT_FAILURE_NEXT_ACTION,
        )
        return 0
