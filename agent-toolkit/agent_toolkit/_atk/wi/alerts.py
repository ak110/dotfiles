"""agent-toolkitプラグイン配下の`atk wi process-loop`アラート自動検出補助モジュール。

対象リポジトリのCI失敗（GitHub Actions run失敗・GitLabパイプライン失敗）を収集し、
AWIへの重複投入を防いだうえで`add_entries`へ引き渡す本文を組み立てる。
定期実行の失敗は、同じブランチで後に起動した別の起動元（push、別のスケジュール）の実行に隠れないよう、
GitHubではワークフローとイベントの組ごと、GitLabでは有効なPipeline Scheduleごとに直近の実行を確かめる。
GitHubのDependabotアラートはprocess-wiの実行ごとの自動コードレビュー監査（`atk review-audit pending`）が扱い、
本モジュールはAWIを起票しない。GitLabの脆弱性アラート（Dependency Scanning等）は
GitLab Ultimateプラン限定機能のため対象外とする。
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
import urllib.parse
from collections.abc import Callable
from typing import Any

from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi.common import WI_STATES, WI_TYPE_AWI, _iter_entries
from agent_toolkit._atk.wi.formatters import _parse_alert_keys
from agent_toolkit._common import json_command as _json_command
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._git import command as _git_command

_GH_SUBPROCESS_TIMEOUT = 30.0
_GLAB_SUBPROCESS_TIMEOUT = 30.0
_GIT_SUBPROCESS_TIMEOUT = 10.0
_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
_ALL_AWI_STATES = WI_STATES
"""重複投入の判定で走査する保存状態。全ての保存状態を対象とする。"""

GhRunListFn = Callable[[str, str], list[dict]]
GlabCiListFn = Callable[[str, str], list[dict]]
GlabApiFn = Callable[[str, str], Any]
"""`glab api`をホスト名とエンドポイントで呼び、JSON応答を返す関数。"""
GitCaptureFn = Callable[[pathlib.Path, list[str]], str | None]


ALERT_FAILURE_NEXT_ACTION = (
    "対応不要（待機は継続した）。繰り返す場合は`gh auth status`（GitLabは`glab auth status`）で認証を確認する"
)
"""アラート取得の失敗に続ける次の操作。取得失敗は待機ループを止めないため、認証の確認だけを案内する。"""


class AlertCollectError(RuntimeError):
    """CI状態の収集中に発生した回復不能な失敗（CLI不在・非ゼロ終了・JSON不正等）。"""


@dataclasses.dataclass(frozen=True)
class Alert:
    """収集した1件のアラート候補。

    `keys`は重複除外に使う安定識別子の集合。`completion`は種別固有の外部可視の終了状態と、
    消費主体へ要求する操作の要否を保持する。
    """

    keys: tuple[str, ...]
    title: str
    body: str
    completion: str


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
    command: list[str], *, timeout: float, operation: str, expected_type: type | tuple[type, ...] = list
) -> Any:
    """外部CLIを実行し、`expected_type`（省略時はJSON配列）の応答を返す。"""

    def error_factory(failure: _json_command.Failure) -> Exception:
        if failure.kind == "timeout":
            return AlertCollectError(f"{operation}がタイムアウトしました")
        if failure.kind == "not-found":
            return AlertCollectError(f"{command[0]}コマンドが見つかりません")
        if failure.kind == "decode":
            return AlertCollectError(f"{operation}の標準出力をUTF-8としてデコードできません: {failure.detail}")
        if failure.kind == "exit":
            return AlertCollectError(f"{operation}が失敗しました（exit={failure.returncode}）: {failure.stderr.strip()}")
        return AlertCollectError(f"{operation}の応答をJSONとして解析できません: {failure.detail}")

    payload = _json_command.run(command, timeout, error_factory=error_factory, strict_stderr=False)
    if not isinstance(payload, expected_type):
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
    """ワークフローとイベントの組ごとの直近完了runが失敗している場合のみアラート化する。

    同じワークフローを`push`と`schedule`の両方で起動する場合も、後の`push`の成功に関係なく`schedule`の直近の失敗を返す。
    """
    latest_by_trigger: dict[tuple[str, object], dict] = {}
    for run in run_list_fn(repo, branch):
        name = run.get("workflowName")
        if not isinstance(name, str):
            continue
        trigger = (name, run.get("event"))
        if trigger in latest_by_trigger or run.get("status") != "completed":
            continue
        latest_by_trigger[trigger] = run
    alerts: list[Alert] = []
    for (name, _event), run in latest_by_trigger.items():
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
            "既に後続の実行で解消済みの場合は、解消済みであることを記録して不採用とする。"
        )
        completion = (
            f"対象ワークフロー`{name}`の失敗が解消し、ブランチ`{branch}`でそのワークフローが成功する。"
            "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
        )
        alerts.append(Alert(keys=(f"github-run:{run_id}",), title=f"ワークフロー{name}失敗", body=body, completion=completion))
    return alerts


def _run_glab_ci_list(repo: str, ref: str) -> list[dict]:
    """`glab ci list`結果（created_at降順）を返す。"""
    return _run_alert_json_command(
        ["glab", "ci", "list", "-R", repo, "--ref", ref, "-F", "json", "--per-page", "5"],
        timeout=_GLAB_SUBPROCESS_TIMEOUT,
        operation=f"glab ci list（{repo}）",
    )


def collect_gitlab_ci_failures(repo: str, branch: str, *, ci_list_fn: GlabCiListFn = _run_glab_ci_list) -> list[Alert]:
    """対象ブランチの最新パイプラインが失敗している場合のみアラート化する。"""
    pipelines = ci_list_fn(repo, branch)
    if not pipelines or pipelines[0].get("status") != "failed" or pipelines[0].get("id") is None:
        return []
    return [_gitlab_pipeline_alert(pipelines[0], repo, branch)]


def _gitlab_pipeline_alert(pipeline: dict, repo: str, ref: str, *, origin: str = "") -> Alert:
    """失敗したGitLabパイプライン1件のアラートを組み立てる。`origin`は起動元の説明を本文の先頭へ加える。"""
    pipeline_id = pipeline["id"]
    body = (
        f"{origin}パイプライン`{pipeline_id}`がブランチ`{ref}`で失敗している。\n\n"
        f"- 実行URL: {pipeline.get('web_url', '')}\n"
        f"- 対象コミット: {str(pipeline.get('sha', ''))[:8]}\n"
        f"- 検知日時: {_now_iso()}\n\n"
        f"`glab ci view {pipeline_id} -R {repo}`で失敗ログを取得し、根本原因を特定して修正する。\n"
        "既に後続の実行で解消済みの場合は、解消済みであることを記録して不採用とする。"
    )
    completion = (
        f"対象パイプライン`{pipeline_id}`の失敗が解消し、ブランチ`{ref}`で後続のパイプラインが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    return Alert(
        keys=(f"gitlab-pipeline:{pipeline_id}",),
        title=f"パイプライン{pipeline_id}失敗",
        body=body,
        completion=completion,
    )


def _run_glab_api(host: str, endpoint: str) -> Any:
    """`glab api`で対象ホストのREST APIを呼び、JSON応答（配列またはオブジェクト）を返す。"""
    return _run_alert_json_command(
        ["glab", "api", "--hostname", host, endpoint],
        timeout=_GLAB_SUBPROCESS_TIMEOUT,
        operation=f"glab api {endpoint}",
        expected_type=(list, dict),
    )


def collect_gitlab_schedule_failures(host: str, repo: str, *, api_fn: GlabApiFn = _run_glab_api) -> list[Alert]:
    """有効なPipeline Scheduleごとの直近パイプラインが失敗している場合にアラート化する。

    スケジュールの一覧は`last_pipeline`を持たないため、有効なスケジュールごとに個別の取得で直近のパイプラインを得る。
    スケジュールの`ref`は対象ブランチに限らない。
    """
    project = f"projects/{urllib.parse.quote(repo, safe='')}/pipeline_schedules"
    schedules = api_fn(host, f"{project}?per_page=100")
    if not isinstance(schedules, list):
        raise AlertCollectError(
            f"Pipeline Scheduleの一覧の応答形状が不正です: {json.dumps(schedules, ensure_ascii=False)[:200]!r}"
        )
    alerts: list[Alert] = []
    for schedule in schedules:
        if not isinstance(schedule, dict) or schedule.get("active") is not True or schedule.get("id") is None:
            continue
        detail = api_fn(host, f"{project}/{schedule['id']}")
        pipeline = detail.get("last_pipeline") if isinstance(detail, dict) else None
        if not isinstance(pipeline, dict) or pipeline.get("status") != "failed" or pipeline.get("id") is None:
            continue
        ref = str(pipeline.get("ref") or schedule.get("ref") or "")
        description = schedule.get("description") or schedule["id"]
        alerts.append(
            _gitlab_pipeline_alert(
                pipeline,
                repo,
                ref,
                origin=f"定期実行`{description}`（Pipeline Schedule {schedule['id']}）の",
            )
        )
    return alerts


def existing_alert_keys(private_notes: pathlib.Path, target_repo: str) -> set[str]:
    """対象リポジトリに限定したAWI全状態の`alert_keys`を集合として返す。"""
    keys: set[str] = set()
    for _path, _entry_repo, text, _state, _entry_type in _iter_entries(
        private_notes, _ALL_AWI_STATES, target_repo, WI_TYPE_AWI
    ):
        keys.update(_parse_alert_keys(text))
    return keys


def _build_alert_message(target_repo_id: str, alert: Alert) -> str:
    """`add_entries`へ渡すfrontmatter付きメッセージ文字列を組み立てる。"""
    body = (
        f"# {alert.title}\n\n"
        "自動監視が未解決の事象を検知した。この事象を解消し、継続して行う自動チェックを正常な状態へ戻す。\n\n"
        "## 反映内容と反映先\n\n"
        f"検知した事象を調査し、必要な是正と検証を行う。反映先は`{target_repo_id}`とする。\n\n"
        "## 適用範囲\n\n検知した事象を発生させる条件と、その条件に該当する実装を対象とする。\n\n"
        "## 実現性\n\n検知元が返した識別子と詳細から、調査対象および検収場所を確定できる。\n\n"
        f"## 完成条件\n\n{alert.completion}\n\n"
        f"## 詳細\n\n{alert.body}"
    )
    return f"---\ntarget_repo: {target_repo_id}\nsource: alert-monitor\nalert_keys: {','.join(alert.keys)}\n---\n\n{body}\n"


def collect_new_alerts(
    repo_id: str,
    branch: str | None,
    private_notes: pathlib.Path,
    *,
    forge: str,
    run_list_fn: GhRunListFn = _run_gh_run_list,
    ci_list_fn: GlabCiListFn = _run_glab_ci_list,
    glab_api_fn: GlabApiFn = _run_glab_api,
) -> list[Alert]:
    """収集に失敗した種別を警告し、未投入の新規アラート一覧を返す。

    ブランチの確認と定期実行の確認が同じ実行を見つけた場合は、同じキーの候補を1件にまとめる。
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
                _next_action.report(f"警告: GitHub CI状態の取得に失敗しました: {exc}", next_action=ALERT_FAILURE_NEXT_ACTION)
    else:
        if branch is not None:
            try:
                candidates.extend(collect_gitlab_ci_failures(repo_path, branch, ci_list_fn=ci_list_fn))
            except AlertCollectError as exc:
                _next_action.report(f"警告: GitLab CI状態の取得に失敗しました: {exc}", next_action=ALERT_FAILURE_NEXT_ACTION)
        try:
            candidates.extend(collect_gitlab_schedule_failures(host, repo_path, api_fn=glab_api_fn))
        except AlertCollectError as exc:
            _next_action.report(
                f"警告: GitLabのPipeline Scheduleの状態の取得に失敗しました: {exc}", next_action=ALERT_FAILURE_NEXT_ACTION
            )
    existing = existing_alert_keys(private_notes, repo_id)
    alerts: list[Alert] = []
    for alert in candidates:
        if all(key in existing for key in alert.keys):
            continue
        existing.update(alert.keys)
        alerts.append(alert)
    return alerts


def check_and_submit_alerts(
    private_notes: pathlib.Path,
    repo_id: str,
    local_path: pathlib.Path,
    *,
    forge: str,
    now: datetime.datetime,
    git_fn: GitCaptureFn = _run_git_capture,
    run_list_fn: GhRunListFn = _run_gh_run_list,
    ci_list_fn: GlabCiListFn = _run_glab_ci_list,
    glab_api_fn: GlabApiFn = _run_glab_api,
) -> int:
    """アラートを収集・重複除外し、新規分をAWIへ投入した件数を返す。"""
    alerts = collect_new_alerts(
        repo_id,
        resolve_target_branch(local_path, git_fn=git_fn),
        private_notes,
        forge=forge,
        run_list_fn=run_list_fn,
        ci_list_fn=ci_list_fn,
        glab_api_fn=glab_api_fn,
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
