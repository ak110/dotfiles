"""単発コマンドと`atk wi process-loop`が共有するCI失敗監視。

対象リポジトリのCI失敗（GitHub Actions run失敗・GitLabパイプライン失敗）を収集し、
AWIへの重複投入を防いだうえで`add_entries`へ引き渡す本文を組み立てる。
定期実行の失敗は、同じブランチで後に起動した別の起動元（push、別のスケジュール）の実行に隠れないよう、
GitHubではワークフローとイベントの組ごと、GitLabでは有効なPipeline Scheduleごとに直近の実行を確かめる。
GitHubのDependabotアラートはprocess-wiの実行ごとの自動コードレビュー監査（`atk review-audit pending`）が扱い、
本モジュールはAWIを起票しない。GitLabの脆弱性アラート（Dependency Scanning等）は
GitLab Ultimateプラン限定機能のため対象外とする。
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import urllib.parse
from collections.abc import Callable
from typing import Any

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import repo as _wi_repo
from agent_toolkit._atk.wi.constants import WI_STATES, WI_TYPE_AWI
from agent_toolkit._atk.wi.formatters import parse_alert_keys
from agent_toolkit._common import json_command as _json_command
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import forge_cli as _forge_cli
from agent_toolkit._git import refs as _git_refs
from agent_toolkit._git import remote as _git_remote

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
HeadFn = Callable[[str, str], str]


ALERT_FAILURE_NEXT_ACTION = (
    "対応不要（待機は継続した）。繰り返す場合は`gh auth status`（GitLabは`glab auth status`）で認証を確認する"
)
"""アラート取得の失敗に続ける次の操作。取得失敗は待機ループを止めないため、認証の確認だけを案内する。"""


class AlertCollectError(RuntimeError):
    """CI状態の収集中に発生した回復不能な失敗（CLI不在・非ゼロ終了・JSON不正等）。"""


@dataclasses.dataclass(frozen=True)
class Alert:
    """収集した1件のアラート候補。

    `keys`は重複除外に使う安定識別子の集合。`completion`はAWIの`## 完成条件`へ書く文であり、
    アラートの種別ごとに、外部から観測できる解消後の状態と、AWIの処理主体に追加の変更を求めるかを示す。
    """

    keys: tuple[str, ...]
    title: str
    body: str
    completion: str


@dataclasses.dataclass(frozen=True)
class AlertCheckResult:
    """保存できたAWI件数と、収集できなかった種別の理由を分けて返す。"""

    submitted: int
    failures: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class RunContext:
    """通知を組み立てるために必須となる実行とブランチHEADの観測。"""

    created_at: str
    sha: str
    head_sha: str | None
    head_error: str | None

    def description(self) -> str:
        """観測した実行情報と比較結果を本文へ渡す。"""
        comparison = "一致" if self.sha == self.head_sha else "不一致"
        head = self.head_sha or f"取得不能（{self.head_error}）"
        if self.head_sha is None or not self.sha:
            comparison = "判定不能"
        return (
            f"- 実行作成日時: {self.created_at or '応答に作成日時が無い'}\n"
            f"- 対象コミット: {self.sha or '応答にSHAが無い'}\n"
            f"- 収集時点のブランチHEAD: {head}\n"
            f"- HEADとの比較: {comparison}\n"
        )

    def investigation(self) -> str:
        """HEADとの差を踏まえて、後続実行の無い状態にも適用する調査条件を返す。"""
        if self.head_sha is not None and self.sha == self.head_sha:
            return ""
        return (
            "ブランチHEADで実行が作成されない理由（CI定義の有無と起動条件）を確認し、"
            "失敗の原因がブランチHEADに残るか判定する。原因が残らない場合は確認結果を記録して完了とし、"
            "追加の変更や後続の実行を要しない。"
        )


def _run_context(created_at: str, sha: str, repo: str, branch: str, head_fn: HeadFn) -> RunContext:
    try:
        return RunContext(created_at, sha, head_fn(repo, branch), None)
    except AlertCollectError as exc:
        return RunContext(created_at, sha, None, str(exc))


def _branch_head(payload: Any, sha_field: str) -> str:
    commit = payload.get("commit") if isinstance(payload, dict) else None
    sha = commit.get(sha_field) if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not sha:
        raise AlertCollectError("ブランチ応答にcommitのSHAが無い")
    return sha


def _github_head(repo: str, branch: str) -> str:
    target = _git_remote.parse_remote_location(repo)
    command = ["gh", "api", f"repos/{target.project_path}/branches/{urllib.parse.quote(branch, safe='')}"]
    if target.hostname:
        command.extend(("--hostname", target.hostname))
    payload = _run_alert_json_command(
        command, timeout=_GH_SUBPROCESS_TIMEOUT, operation="GitHubブランチHEAD", expected_type=dict
    )
    return _branch_head(payload, "sha")


def _gitlab_head(repo: str, branch: str) -> str:
    target = _git_remote.parse_remote_location(repo)
    endpoint = (
        f"projects/{urllib.parse.quote(target.project_path, safe='')}/repository/branches/{urllib.parse.quote(branch, safe='')}"
    )
    return _branch_head(_run_glab_api(repo, endpoint), "id")


def _now_iso() -> str:
    """検知日時をローカルタイムゾーン付きISO8601秒精度で返す。"""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _run_git_capture(local_path: pathlib.Path, args: list[str]) -> str | None:
    """`git -C <local_path> <args>`を実行し、成功時はstdout（末尾改行除去）を返す。"""
    return _git_command.optional_output(args, local_path, timeout=_GIT_SUBPROCESS_TIMEOUT)


def resolve_target_branch(local_path: pathlib.Path, *, git_fn: GitCaptureFn = _run_git_capture) -> str | None:
    """CI失敗収集の対象ブランチを解決する。"""
    resolved = _git_refs.resolve_branch(local_path, git_fn=git_fn, upstream=True)
    return resolved.branch if resolved else None


def _run_alert_json_command(
    command: list[str],
    *,
    timeout: float,
    operation: str,
    expected_type: type | tuple[type, ...] = list,
    env: dict[str, str] | None = None,
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

    payload = _json_command.run(command, timeout, error_factory=error_factory, strict_stderr=False, env=env)
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


def collect_github_ci_failures(
    repo: str, branch: str, *, run_list_fn: GhRunListFn = _run_gh_run_list, head_fn: HeadFn | None = None
) -> list[Alert]:
    """ワークフローとイベントの組ごとに、直近に完了したrunが失敗している場合だけアラート候補を返す。

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
    context_by_branch: dict[str, RunContext] = {}
    for (name, _event), run in latest_by_trigger.items():
        if run.get("conclusion") not in _FAILURE_CONCLUSIONS:
            continue
        run_id = run.get("databaseId")
        if run_id is None:
            continue
        if branch not in context_by_branch:
            context_by_branch[branch] = _run_context("", "", repo, branch, head_fn or _github_head)
        head = context_by_branch[branch]
        context = dataclasses.replace(head, created_at=str(run.get("createdAt", "")), sha=str(run.get("headSha", "")))
        body = (
            f"ワークフロー`{name}`がブランチ`{branch}`で失敗している。\n\n"
            f"- 実行URL: {run.get('url', '')}\n"
            f"{context.description()}"
            f"- 検知日時: {_now_iso()}\n\n"
            f"{_forge_cli.failure_next_action('github', repo, str(run_id))}。\n"
            f"{context.investigation()}\n"
            "既に後続の実行で解消済みの場合は、解消済みであることを記録して不採用とする。"
        )
        completion = (
            f"対象ワークフロー`{name}`の失敗が解消し、ブランチ`{branch}`でそのワークフローが成功する。"
            "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
            f"。{context.investigation()}"
        )
        alerts.append(Alert(keys=(f"github-run:{run_id}",), title=f"ワークフロー{name}失敗", body=body, completion=completion))
    return alerts


def _run_glab_ci_list(repo: str, ref: str) -> list[dict]:
    """`glab ci list`結果（created_at降順）を返す。"""
    return _run_alert_json_command(
        [
            "glab",
            "ci",
            "list",
            "-R",
            _forge_cli.glab_repository(repo, prefer_environment=True),
            "--ref",
            ref,
            "-F",
            "json",
            "--per-page",
            "5",
        ],
        timeout=_GLAB_SUBPROCESS_TIMEOUT,
        operation=f"glab ci list（{repo}）",
        env=_forge_cli.glab_environment(repo, prefer_environment=True),
    )


def collect_gitlab_ci_failures(
    repo: str, branch: str, *, ci_list_fn: GlabCiListFn = _run_glab_ci_list, head_fn: HeadFn | None = None
) -> list[Alert]:
    """対象ブランチの最新パイプラインが失敗している場合だけアラート候補を返す。"""
    pipelines = ci_list_fn(repo, branch)
    if not pipelines or pipelines[0].get("status") != "failed" or pipelines[0].get("id") is None:
        return []
    pipeline = pipelines[0]
    context = _run_context(
        str(pipeline.get("created_at", "")), str(pipeline.get("sha", "")), repo, branch, head_fn or _gitlab_head
    )
    return [_gitlab_pipeline_alert(pipeline, repo, branch, context=context)]


def _gitlab_pipeline_alert(pipeline: dict, repo: str, ref: str, *, context: RunContext, origin: str = "") -> Alert:
    """失敗したGitLabパイプライン1件のアラートを組み立てる。`origin`は起動元の説明を本文の先頭へ加える。"""
    pipeline_id = pipeline["id"]
    body = (
        f"{origin}パイプライン`{pipeline_id}`がブランチ`{ref}`で失敗している。\n\n"
        f"- 実行URL: {pipeline.get('web_url', '')}\n"
        f"{context.description()}"
        f"- 検知日時: {_now_iso()}\n\n"
        f"{_forge_cli.failure_next_action('gitlab', repo, str(pipeline_id), prefer_environment=True)}。\n"
        f"{context.investigation()}\n"
        "既に後続の実行で解消済みの場合は、解消済みであることを記録して不採用とする。"
    )
    completion = (
        f"対象パイプライン`{pipeline_id}`の失敗が解消し、ブランチ`{ref}`で後続のパイプラインが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
        f"。{context.investigation()}"
    )
    return Alert(
        keys=(f"gitlab-pipeline:{pipeline_id}",),
        title=f"パイプライン{pipeline_id}失敗",
        body=body,
        completion=completion,
    )


def _run_glab_api(host: str, endpoint: str) -> Any:
    """`glab api`で対象ホストのREST APIを呼び、JSON応答（配列またはオブジェクト）を返す。"""
    target: str | _git_remote.RemoteLocation = host
    if "/" not in host:
        parsed = urllib.parse.urlsplit(f"//{host}")
        target = _git_remote.RemoteLocation(parsed.hostname, "", parsed.port)
    return _run_alert_json_command(
        ["glab", "api", endpoint],
        timeout=_GLAB_SUBPROCESS_TIMEOUT,
        operation=f"glab api {endpoint}",
        expected_type=(list, dict),
        env=_forge_cli.glab_environment(target, prefer_environment=True),
    )


def collect_gitlab_schedule_failures(
    host: str, repo: str, *, api_fn: GlabApiFn = _run_glab_api, repository_url: str | None = None
) -> list[Alert]:
    """有効なPipeline Scheduleごとに、直近のパイプラインが失敗している場合にアラート候補を返す。

    スケジュールの一覧は`last_pipeline`を持たないため、有効なスケジュールごとに個別の取得で直近のパイプラインを得る。
    スケジュールの`ref`は対象ブランチに限らない。
    """
    project = f"projects/{urllib.parse.quote(repo, safe='')}/pipeline_schedules"
    api_target = repository_url or host
    repository = repository_url or f"{host}/{repo}"
    schedules = api_fn(api_target, f"{project}?per_page=100")
    if not isinstance(schedules, list):
        raise AlertCollectError(
            f"Pipeline Scheduleの一覧の応答形状が不正です: {json.dumps(schedules, ensure_ascii=False)[:200]!r}"
        )
    alerts: list[Alert] = []
    heads: dict[str, tuple[str | None, str | None]] = {}
    for schedule in schedules:
        if not isinstance(schedule, dict) or schedule.get("active") is not True or schedule.get("id") is None:
            continue
        detail = api_fn(api_target, f"{project}/{schedule['id']}")
        pipeline = detail.get("last_pipeline") if isinstance(detail, dict) else None
        if not isinstance(pipeline, dict) or pipeline.get("status") != "failed" or pipeline.get("id") is None:
            continue
        ref = str(pipeline.get("ref") or schedule.get("ref") or "")
        description = schedule.get("description") or schedule["id"]
        if ref not in heads:
            endpoint = f"projects/{urllib.parse.quote(repo, safe='')}/repository/branches/{urllib.parse.quote(ref, safe='')}"
            try:
                heads[ref] = (_branch_head(api_fn(api_target, endpoint), "id"), None)
            except AlertCollectError as exc:
                heads[ref] = (None, str(exc))
        head_sha, head_error = heads[ref]
        context = RunContext(str(pipeline.get("created_at", "")), str(pipeline.get("sha", "")), head_sha, head_error)
        alerts.append(
            _gitlab_pipeline_alert(
                pipeline,
                repository,
                ref,
                context=context,
                origin=f"定期実行`{description}`（Pipeline Schedule {schedule['id']}）の",
            )
        )
    return alerts


def existing_alert_keys(private_notes: pathlib.Path, target_repo: str) -> set[str]:
    """対象リポジトリに限定したAWI全状態の`alert_keys`を集合として返す。"""
    keys: set[str] = set()
    for _path, _entry_repo, text, _state, _entry_type in _wi_entries.iter_entries(
        private_notes, _ALL_AWI_STATES, target_repo, WI_TYPE_AWI
    ):
        keys.update(parse_alert_keys(text))
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
    failures: list[str] | None = None,
    next_action: str = ALERT_FAILURE_NEXT_ACTION,
    repository_url: str | None = None,
) -> list[Alert]:
    """収集に失敗した種別を警告し、未投入の新規アラート一覧を返す。

    ブランチの確認と定期実行の確認が同じ実行を見つけた場合は、同じキーの候補を1件にまとめる。
    """
    host = repo_id.split("/", 1)[0]
    resolved_forge = forge if forge != "auto" else ("github" if host == "github.com" else "gitlab")
    repo_path = repo_id.split("/", 1)[1] if "/" in repo_id else repo_id
    candidates: list[Alert] = []

    def report_failure(message: str) -> None:
        if failures is not None:
            failures.append(message)
        _outcome.report_warning(message, next_action=next_action)

    if branch is None:
        report_failure("CI監視対象ブランチを解決できません（追跡先とorigin/HEADが無い）")
    if resolved_forge == "github":
        if branch is not None:
            try:
                candidates.extend(collect_github_ci_failures(repo_id, branch, run_list_fn=run_list_fn))
            except AlertCollectError as exc:
                report_failure(f"GitHub CI状態の取得に失敗しました: {exc}")
    else:
        if branch is not None:
            try:
                candidates.extend(collect_gitlab_ci_failures(repository_url or repo_id, branch, ci_list_fn=ci_list_fn))
            except AlertCollectError as exc:
                report_failure(f"GitLab CI状態の取得に失敗しました: {exc}")
        try:
            candidates.extend(
                collect_gitlab_schedule_failures(host, repo_path, api_fn=glab_api_fn, repository_url=repository_url)
            )
        except AlertCollectError as exc:
            report_failure(f"GitLabのPipeline Scheduleの状態の取得に失敗しました: {exc}")
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
    next_action: str = ALERT_FAILURE_NEXT_ACTION,
) -> AlertCheckResult:
    """共有の収集・重複除外・AWI保存を実行し、保存件数と取得失敗を返す。"""
    failures: list[str] = []
    alerts = collect_new_alerts(
        repo_id,
        resolve_target_branch(local_path, git_fn=git_fn),
        private_notes,
        forge=forge,
        run_list_fn=run_list_fn,
        ci_list_fn=ci_list_fn,
        glab_api_fn=glab_api_fn,
        failures=failures,
        next_action=next_action,
        repository_url=(
            _git_remote.origin_url(local_path, timeout=_GIT_SUBPROCESS_TIMEOUT)
            if forge == "gitlab" or forge == "auto" and not repo_id.startswith("github.com/")
            else None
        ),
    )
    if not alerts:
        return AlertCheckResult(0, tuple(failures))
    generated = _add.add_entries(
        private_notes,
        messages=[_build_alert_message(repo_id, alert) for alert in alerts],
        target_repo=repo_id,
        source="alert-monitor",
        now=now,
    )
    return AlertCheckResult(len(generated), tuple(failures))


def cmd_check_alerts(args: argparse.Namespace, private_notes: pathlib.Path, now: datetime.datetime) -> int:
    """ローカル対象のCI監視を1回実行して終了する。"""
    local_path = _wi_repo.resolve_local_worktree(args.target_repo)
    repo_id = _wi_repo.resolve_repo_id(None, cwd=local_path)
    next_action = (
        "診断に示した対象・ブランチ設定と`gh auth status`（GitLabは`glab auth status`）を確認し、"
        "`atk wi check-alerts`を再実行する"
    )
    result = check_and_submit_alerts(private_notes, repo_id, local_path, forge=args.forge, now=now, next_action=next_action)
    message = f"CI失敗監視: 対象={repo_id} AWI投入={result.submitted}件"
    if result.failures:
        _outcome.report_failure(f"{message} 取得不能={len(result.failures)}種別", next_action=next_action)
        return 1
    _outcome.report_success(message)
    return 0
