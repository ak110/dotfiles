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
    """
    if args.no_alerts:
        return last_alert_check, 0, None
    monotonic_now = time.monotonic()
    if last_alert_check is not None and monotonic_now - last_alert_check < args.alert_interval:
        return last_alert_check, 0, None
    # 外部APIの照会は1件あたり数秒から十数秒かかるため、待機中と区別できるよう着手を表示する。
    print(f"{target_repo_id}のCI失敗とアラートを確認しています。", flush=True)
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
    host, _, repo_path = target_repo_id.partition("/")
    forge = args.alert_forge if args.alert_forge != "auto" else ("github" if host == "github.com" else "gitlab")
    if forge != "github" or not repo_path:
        return frozenset()
    try:
        return frozenset(int(alert["number"]) for alert in _review_audit.dependabot_pending(repo_path)["alerts"])
    except _next_action.ActionableError as exc:
        _next_action.report(
            f"警告: Dependabotアラートの確認に失敗しました: {exc}",
            next_action=_alerts.ALERT_FAILURE_NEXT_ACTION,
        )
        return None
