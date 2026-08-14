"""agent-toolkitプラグイン配下の`atk mq process-loop`アラート自動検出補助モジュール。

対象リポジトリのCI失敗（GitHub Actions run失敗・GitLabパイプライン失敗）とGitHub
Dependabotアラートの未解決分を収集し、フィードバックへの重複投入を防いだうえで
`add_entries`へ引き渡す本文を組み立てる。GitLabの脆弱性アラート（Dependency Scanning等）は
GitLab Ultimateプラン限定機能のため対象外とする。
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
import sys
from collections.abc import Callable

import _atk_mq_add as _add
import _git_command
import _json_command
from _atk_mq_common import (
    MQ_STATE_ADOPTED,
    MQ_STATE_INBOX,
    MQ_STATE_PROCESSING,
    MQ_STATE_REJECTED,
    _iter_entries,
)
from _atk_mq_formatters import _parse_alert_keys

_GH_SUBPROCESS_TIMEOUT = 30.0
_GLAB_SUBPROCESS_TIMEOUT = 30.0
_GIT_SUBPROCESS_TIMEOUT = 10.0
_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
_ALL_FEEDBACK_STATES = (
    MQ_STATE_INBOX,
    MQ_STATE_PROCESSING,
    MQ_STATE_ADOPTED,
    MQ_STATE_REJECTED,
)

GhRunListFn = Callable[[str, str], list[dict]]
GhDependabotAlertsFn = Callable[[str], list[dict]]
GlabCiListFn = Callable[[str, str], list[dict]]
GitCaptureFn = Callable[[pathlib.Path, list[str]], str | None]


class AlertCollectError(RuntimeError):
    """CI・Dependabotアラート収集中に発生した回復不能な失敗（CLI不在・非ゼロ終了・JSON不正等）。"""


_GH_DEPENDABOT_DISABLED_MESSAGE = "Dependabot alerts are disabled for this repository."
"""Dependabotアラート機能が無効なリポジトリに対しGitHub APIが返すメッセージ本文。

`gh api`は当該JSONを標準出力へ、要約1行を標準エラーへ出力する。実測で確認した文言をそのまま用いる。
"""


class AlertFeatureDisabledError(AlertCollectError):
    """対象リポジトリで当該機能が無効であることを示す応答。

    取得失敗ではなく設定上の正常状態のため、呼び出し側は警告を出力せず収集対象から除外する。
    GitLabの脆弱性アラートを上位エディション限定機能としてあらかじめ対象外とする既存方針へ揃える。
    """


def _is_disabled_response(stdout: str, disabled_messages: tuple[str, ...]) -> bool:
    """応答本文がHTTP 403かつ既知の機能無効メッセージと一致するかを判定する。

    権限不足・トークン失効・組織の利用停止など別原因の403を機能無効と誤認しないよう、
    メッセージは呼び出し側が渡した既知文言との完全一致でのみ判定する。
    `disabled_messages`が空の呼び出し（CI状態取得など機能無効の概念が無い経路）は常に`False`となる。
    JSON配列を返す正常応答および解析できない応答も`False`を返す。
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or str(payload.get("status")) != "403":
        return False
    return str(payload.get("message", "")) in disabled_messages


@dataclasses.dataclass(frozen=True)
class Alert:
    """収集した1件のアラート候補。`keys`は重複除外に使う安定識別子の集合。"""

    keys: tuple[str, ...]
    title: str
    body: str


def _now_iso() -> str:
    """検知日時をローカルタイムゾーン付きISO8601秒精度で返す。"""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _run_git_capture(local_path: pathlib.Path, args: list[str]) -> str | None:
    """`git -C <local_path> <args>`を実行し、成功時はstdout（末尾改行除去）を返す。"""
    return _git_command.optional_output(args, local_path, timeout=_GIT_SUBPROCESS_TIMEOUT)


def resolve_target_branch(local_path: pathlib.Path, *, git_fn: GitCaptureFn = _run_git_capture) -> str | None:
    """CI失敗収集の対象ブランチを解決する。"""
    upstream = git_fn(local_path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream is not None:
        remotes = (git_fn(local_path, ["remote"]) or "").splitlines()
        for remote in remotes:
            prefix = f"{remote}/"
            if upstream.startswith(prefix):
                return upstream[len(prefix) :]
    head_ref = git_fn(local_path, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    prefix = "refs/remotes/origin/"
    if head_ref is not None and head_ref.startswith(prefix):
        return head_ref[len(prefix) :]
    return None


def _run_alert_json_command(
    command: list[str], *, timeout: float, operation: str, disabled_messages: tuple[str, ...] = ()
) -> list[dict]:
    """外部CLIを実行し、JSON配列応答を返す。

    `disabled_messages`を渡した呼び出しでは、HTTP 403かつ当該文言と一致する応答を
    `AlertFeatureDisabledError`として区別する。既定は空で、従来どおり全失敗を`AlertCollectError`とする。
    """

    def error_factory(failure: _json_command.Failure) -> Exception:
        if failure.kind == "timeout":
            return AlertCollectError(f"{operation}がタイムアウトしました")
        if failure.kind == "not-found":
            return AlertCollectError(f"{command[0]}コマンドが見つかりません")
        if failure.kind == "decode":
            return AlertCollectError(f"{operation}の標準出力をUTF-8として復号できません: {failure.detail}")
        if failure.kind == "exit":
            if _is_disabled_response(failure.stdout, disabled_messages):
                return AlertFeatureDisabledError(f"{operation}: 対象リポジトリで当該機能が無効")
            return AlertCollectError(f"{operation}が失敗しました（exit={failure.returncode}）: {failure.stderr.strip()}")
        return AlertCollectError(f"{operation}の応答をJSONとして解析できません: {failure.detail}")

    payload = _json_command.run(command, timeout, error_factory=error_factory, strict_stderr=False)
    if not isinstance(payload, list):
        raise AlertCollectError(f"{operation}の応答形状が不正です: {json.dumps(payload, ensure_ascii=False)[:200]!r}")
    return payload


def _run_gh_run_list(repo: str, branch: str) -> list[dict]:
    """`gh run list`結果を返す。"""
    return _run_alert_json_command(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--branch",
            branch,
            "--limit",
            "20",
            "--json",
            "databaseId,workflowName,status,conclusion,headSha,url,createdAt,event",
        ],
        timeout=_GH_SUBPROCESS_TIMEOUT,
        operation=f"gh run list（{repo}）",
    )


def collect_github_ci_failures(repo: str, branch: str, *, run_list_fn: GhRunListFn = _run_gh_run_list) -> list[Alert]:
    """ワークフローごとの直近完了runが失敗している場合のみアラート化する。"""
    latest_by_workflow: dict[str, dict] = {}
    for run in run_list_fn(repo, branch):
        name = run.get("workflowName")
        if name is None or name in latest_by_workflow or run.get("status") != "completed":
            continue
        latest_by_workflow[name] = run
    alerts: list[Alert] = []
    for name, run in latest_by_workflow.items():
        if run.get("conclusion") not in _FAILURE_CONCLUSIONS:
            continue
        run_id = run.get("databaseId")
        if run_id is None:
            continue
        body = (
            f"ワークフロー`{name}`がブランチ`{branch}`で失敗している。\n\n"
            f"- 実行URL: {run.get('url', '')}\n"
            f"- 対象コミット: {str(run.get('headSha', ''))[:8]}\n"
            f"- 検知日時: {_now_iso()}\n\n"
            f"`gh run view {run_id} --log-failed`で失敗ログを取得し、根本原因を特定して修正する。\n"
            "既に後続の実行で解消済みの場合は、その旨を記録して不採用とする。"
        )
        alerts.append(Alert(keys=(f"github-run:{run_id}",), title=f"ワークフロー{name}失敗", body=body))
    return alerts


def _run_gh_dependabot_alerts(repo: str) -> list[dict]:
    """`gh api --paginate`で未解決Dependabotアラート全件を返す。

    リポジトリ側でDependabotアラート機能が無効な場合はHTTP 403が返るため、
    当該応答のみ`AlertFeatureDisabledError`として取得失敗と区別する。
    """
    return _run_alert_json_command(
        ["gh", "api", "--paginate", f"/repos/{repo}/dependabot/alerts?state=open&per_page=100"],
        timeout=_GH_SUBPROCESS_TIMEOUT,
        operation=f"dependabot/alerts取得（{repo}）",
        disabled_messages=(_GH_DEPENDABOT_DISABLED_MESSAGE,),
    )


def collect_github_dependabot_alerts(repo: str, *, alerts_fn: GhDependabotAlertsFn = _run_gh_dependabot_alerts) -> Alert | None:
    """未解決Dependabotアラート全件を1件のAlertへまとめる。未解決0件なら`None`を返す。"""
    payload = alerts_fn(repo)
    if not payload:
        return None
    keys = tuple(f"github-dependabot:{item['number']}" for item in payload)

    def _first_patched_version(item: dict) -> str:
        # GitHub REST APIは修正版が存在しない脆弱性で`first_patched_version`にnullを返す
        # （`security_vulnerability`オブジェクト自体がnullの場合も同様）。
        # `.get(key, {})`は既存キーの値がNoneの場合はNoneをそのまま返すため、
        # `or {}`でNone・キー欠落の双方を空dictへ正規化してから後続の`.get`を呼ぶ。
        vulnerability = item.get("security_vulnerability") or {}
        patched = vulnerability.get("first_patched_version") or {}
        return patched.get("identifier", "?")

    rows = "\n".join(
        f"| {item['number']} | {item.get('security_advisory', {}).get('severity', '?')} | "
        f"{item.get('dependency', {}).get('package', {}).get('name', '?')} | "
        f"{item.get('security_advisory', {}).get('summary', '?')} | "
        f"{(item.get('security_vulnerability') or {}).get('vulnerable_version_range', '?')} | "
        f"{_first_patched_version(item)} |"
        for item in payload
    )
    body = (
        f"Dependabotが未解決の脆弱性アラートを{len(payload)}件報告している。\n\n"
        "| 番号 | 深刻度 | パッケージ | 概要 | 脆弱バージョン範囲 | 修正版 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"{rows}\n\n"
        "(1) 対象パッケージのロック済みバージョンと表の修正版を突き合わせ、既に修正版以上であれば依存更新は不要である。\n"
        "(2) 修正版以上の場合はアラートが実態より遅れて未クローズになっている状態であるため、"
        f"`gh api --method PATCH /repos/{repo}/dependabot/alerts/<番号> -f state=dismissed "
        "-f dismissed_reason=inaccurate`に突合結果を記したコメントを添えてdismissし、フィードバックは採用として処理する。\n"
        "(3) 修正版未満の場合のみ依存を更新して解消する。更新できない場合は理由を記録して不採用とする。\n"
        f"詳細は`gh api /repos/{repo}/dependabot/alerts/<番号>`で取得できる。"
    )
    return Alert(keys=keys, title=f"Dependabot未解決アラート{len(payload)}件", body=body)


def _run_glab_ci_list(repo: str, ref: str) -> list[dict]:
    """`glab ci list`結果（created_at降順）を返す。"""
    return _run_alert_json_command(
        ["glab", "ci", "list", "-R", repo, "--ref", ref, "-F", "json", "--per-page", "5"],
        timeout=_GLAB_SUBPROCESS_TIMEOUT,
        operation=f"glab ci list（{repo}）",
    )


def collect_gitlab_ci_failures(repo: str, branch: str, *, ci_list_fn: GlabCiListFn = _run_glab_ci_list) -> list[Alert]:
    """最新パイプラインが失敗している場合のみアラート化する。"""
    pipelines = ci_list_fn(repo, branch)
    if not pipelines or pipelines[0].get("status") != "failed":
        return []
    latest = pipelines[0]
    pipeline_id = latest.get("id")
    if pipeline_id is None:
        return []
    body = (
        f"パイプライン`{pipeline_id}`がブランチ`{branch}`で失敗している。\n\n"
        f"- 実行URL: {latest.get('web_url', '')}\n"
        f"- 対象コミット: {str(latest.get('sha', ''))[:8]}\n"
        f"- 検知日時: {_now_iso()}\n\n"
        f"`glab ci view {pipeline_id} -R {repo}`で失敗ログを取得し、根本原因を特定して修正する。\n"
        "既に後続の実行で解消済みの場合は、その旨を記録して不採用とする。"
    )
    return [Alert(keys=(f"gitlab-pipeline:{pipeline_id}",), title=f"パイプライン{pipeline_id}失敗", body=body)]


def existing_alert_keys(private_notes: pathlib.Path, target_repo: str) -> set[str]:
    """対象リポジトリに限定したfeedback全状態の`alert_keys`を集合として返す。"""
    keys: set[str] = set()
    for _path, _entry_repo, text, _state, _entry_type in _iter_entries(
        private_notes, _ALL_FEEDBACK_STATES, target_repo, "feedback"
    ):
        keys.update(_parse_alert_keys(text))
    return keys


def _build_alert_message(target_repo_id: str, alert: Alert) -> str:
    """`add_entries`へ渡すfrontmatter付きメッセージ文字列を組み立てる。"""
    return (
        f"---\ntarget_repo: {target_repo_id}\nsource: alert-monitor\nalert_keys: {','.join(alert.keys)}\n---\n\n{alert.body}\n"
    )


def collect_new_alerts(
    repo_id: str,
    branch: str | None,
    private_notes: pathlib.Path,
    *,
    forge: str,
    run_list_fn: GhRunListFn = _run_gh_run_list,
    dependabot_fn: GhDependabotAlertsFn = _run_gh_dependabot_alerts,
    ci_list_fn: GlabCiListFn = _run_glab_ci_list,
) -> list[Alert]:
    """収集に失敗した種別を警告し、未投入の新規アラート一覧を返す。

    対象リポジトリで当該機能が無効な種別（`AlertFeatureDisabledError`）は
    設定上の正常状態のため警告を出力せず除外する。
    設定が変わらない限り同じ応答が返り続けるため、警告を出力すると監視間隔ごとに恒久的な雑音となる。
    """
    host = repo_id.split("/", 1)[0]
    resolved_forge = forge if forge != "auto" else ("github" if host == "github.com" else "gitlab")
    repo_path = repo_id.split("/", 1)[1] if "/" in repo_id else repo_id
    candidates: list[Alert] = []
    if resolved_forge == "github":
        if branch is not None:
            try:
                candidates.extend(collect_github_ci_failures(repo_path, branch, run_list_fn=run_list_fn))
            except AlertCollectError as exc:
                print(f"警告: GitHub CI状態の取得に失敗しました: {exc}", file=sys.stderr)
        try:
            dependabot_alert = collect_github_dependabot_alerts(repo_path, alerts_fn=dependabot_fn)
        except AlertFeatureDisabledError:
            dependabot_alert = None
        except AlertCollectError as exc:
            print(f"警告: Dependabotアラートの取得に失敗しました: {exc}", file=sys.stderr)
            dependabot_alert = None
        if dependabot_alert is not None:
            candidates.append(dependabot_alert)
    elif branch is not None:
        try:
            candidates.extend(collect_gitlab_ci_failures(repo_path, branch, ci_list_fn=ci_list_fn))
        except AlertCollectError as exc:
            print(f"警告: GitLab CI状態の取得に失敗しました: {exc}", file=sys.stderr)
    existing = existing_alert_keys(private_notes, repo_id)
    return [alert for alert in candidates if any(key not in existing for key in alert.keys)]


def check_and_submit_alerts(
    private_notes: pathlib.Path,
    repo_id: str,
    local_path: pathlib.Path,
    *,
    forge: str,
    now: datetime.datetime,
    git_fn: GitCaptureFn = _run_git_capture,
    run_list_fn: GhRunListFn = _run_gh_run_list,
    dependabot_fn: GhDependabotAlertsFn = _run_gh_dependabot_alerts,
    ci_list_fn: GlabCiListFn = _run_glab_ci_list,
) -> int:
    """アラートを収集・重複除外し、新規分をfeedbackへ投入した件数を返す。"""
    alerts = collect_new_alerts(
        repo_id,
        resolve_target_branch(local_path, git_fn=git_fn),
        private_notes,
        forge=forge,
        run_list_fn=run_list_fn,
        dependabot_fn=dependabot_fn,
        ci_list_fn=ci_list_fn,
    )
    if not alerts:
        return 0
    generated = _add.add_entries(
        private_notes,
        messages=[_build_alert_message(repo_id, alert) for alert in alerts],
        target_repo=repo_id,
        source="alert-monitor",
        now=now,
    )
    return len(generated)
