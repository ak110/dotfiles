"""agent-toolkitプラグイン配下の`atk wi process-loop`アラート自動検出補助モジュール。

対象リポジトリのCI失敗（GitHub Actions run失敗・GitLabパイプライン失敗）を収集し、
AWIへの重複投入を防いだうえで`add_entries`へ引き渡す本文を組み立てる。
GitHubのDependabotアラートは処理回ごとの自動コードレビュー監査（`atk review-audit pending`）が扱い、
本モジュールはAWIを起票しない。GitLabの脆弱性アラート（Dependency Scanning等）は
GitLab Ultimateプラン限定機能のため対象外とする。
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
from collections.abc import Callable

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


def _run_alert_json_command(command: list[str], *, timeout: float, operation: str) -> list[dict]:
    """外部CLIを実行し、JSON配列応答を返す。"""

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
        "既に後続の実行で解消済みの場合は、解消済みであることを記録して不採用とする。"
    )
    completion = (
        f"対象パイプライン`{pipeline_id}`の失敗が解消し、ブランチ`{branch}`で後続のパイプラインが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    return [
        Alert(
            keys=(f"gitlab-pipeline:{pipeline_id}",),
            title=f"パイプライン{pipeline_id}失敗",
            body=body,
            completion=completion,
        )
    ]


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
) -> list[Alert]:
    """収集に失敗した種別を警告し、未投入の新規アラート一覧を返す。"""
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
    elif branch is not None:
        try:
            candidates.extend(collect_gitlab_ci_failures(repo_path, branch, ci_list_fn=ci_list_fn))
        except AlertCollectError as exc:
            _next_action.report(f"警告: GitLab CI状態の取得に失敗しました: {exc}", next_action=ALERT_FAILURE_NEXT_ACTION)
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
    ci_list_fn: GlabCiListFn = _run_glab_ci_list,
) -> int:
    """アラートを収集・重複除外し、新規分をAWIへ投入した件数を返す。"""
    alerts = collect_new_alerts(
        repo_id,
        resolve_target_branch(local_path, git_fn=git_fn),
        private_notes,
        forge=forge,
        run_list_fn=run_list_fn,
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
