"""CI待機で使うforge CLIの照会とGit識別子の解決。"""

from __future__ import annotations

import functools
import pathlib
import subprocess
from typing import Any
from urllib.parse import quote

from agent_toolkit._atk.wait_ci_records import _GH_JSON_FIELDS, JobRecord, RepositoryTarget, RunListError, RunListFn, RunRecord
from agent_toolkit._common import json_command as _json_command
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import forge_cli as _forge_cli
from agent_toolkit._git import remote as _git_remote


def _parse_repository(repository: str) -> RepositoryTarget:
    """URL・SCP形式・`[host/]owner/repo`をrepository対象へ正規化する。"""
    try:
        return _git_remote.parse_remote_location(repository)
    except ValueError as error:
        raise RunListError(f"repository指定が不正: {repository!r}") from error


def _short_ref(ref: str) -> str:
    """`refs/heads/`または`refs/tags/`をforge CLI用の短縮refへ変換する。"""
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref.removeprefix(prefix)
    return ref


def _gh_run_list(repository: str, ref: str, sha: str, subprocess_timeout: float) -> list[RunRecord]:
    """repository・ref・SHAを明示した`gh run list`結果を返す。"""
    payload = _run_forge_json_command(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repository,
            "--branch",
            _short_ref(ref),
            "--commit",
            sha,
            "--limit",
            "1000",
            "--json",
            _GH_JSON_FIELDS,
        ],
        subprocess_timeout,
        "gh run list",
    )
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RunListError(f"gh run list returned unexpected JSON shape: {payload!r}")
    return payload


def _gh_job_list(repository: str, run: RunRecord, subprocess_timeout: float) -> list[JobRecord]:
    """GitHub Actions runの最新試行ジョブを全ページ取得して正規化する。"""
    run_id = run.get("databaseId")
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        raise RunListError(f"GitHub run databaseId is invalid: {run_id!r}")
    target = _parse_repository(repository)
    if target.project_path.count("/") != 1:
        raise RunListError(f"GitHub repository指定が不正: {repository!r}")
    command = [
        "gh",
        "api",
        "-X",
        "GET",
        f"repos/{target.project_path}/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
        "--paginate",
        "--slurp",
    ]
    if target.hostname is not None:
        command.extend(["--hostname", target.hostname])
    payload = _run_forge_json_command(command, subprocess_timeout, "gh api jobs")
    if not isinstance(payload, list) or not all(isinstance(page, dict) for page in payload):
        raise RunListError(f"gh api jobs returned unexpected JSON shape: {payload!r}")
    job_pages = [page.get("jobs") for page in payload]
    if not all(isinstance(page_jobs, list) for page_jobs in job_pages):
        raise RunListError(f"gh api jobs returned unexpected jobs shape: {payload!r}")
    job_payload = [item for page_jobs in job_pages for item in page_jobs]
    if not all(isinstance(item, dict) for item in job_payload):
        raise RunListError(f"gh api jobs returned unexpected job shape: {payload!r}")
    jobs: list[JobRecord] = []
    for item in job_payload:
        if not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool) or not isinstance(item.get("name"), str):
            raise RunListError(f"gh api jobs returned unexpected job shape: {item!r}")
        status = item.get("status")
        conclusion = item.get("conclusion")
        if not isinstance(status, str) or (conclusion is not None and not isinstance(conclusion, str)):
            raise RunListError(f"gh api jobs returned unexpected job state: {item!r}")
        jobs.append(
            {
                "name": item["name"],
                "status": status,
                "conclusion": conclusion,
                "url": item.get("html_url", ""),
                "databaseId": item["id"],
                "allowFailure": False,
            }
        )
    return jobs


# GitLabパイプラインの未完了ステータス。`canceling`は取り消しの進行中のため未完了として待機を継続する。
# 典拠: https://docs.gitlab.com/api/pipelines/ の「List project pipelines」応答仕様。
_GITLAB_PENDING_STATUSES = frozenset(
    {
        "created",
        "waiting_for_resource",
        "preparing",
        "waiting_for_callback",
        "pending",
        "running",
        "canceling",
        "scheduled",
    }
)


# GitLabの単一`status`をGitHubの`conclusion`語彙へ写像する。
# `canceled`（lが1つ）と`cancelled`（lが2つ）の綴り差を吸収しないと、
# 待機側の全cancelled判定（`_all_cancelled`）が成立しない。
# `manual`は手動ジョブ待ちで自動進行しないため、完了かつ非成功として扱う
# （未完了とすると必ずタイムアウトへ至る。GitHubの`action_required`と意味が対応する）。
_GITLAB_STATUS_TO_CONCLUSION = {
    "success": "success",
    "failed": "failure",
    "canceled": "cancelled",
    "skipped": "skipped",
    "manual": "action_required",
}


def _normalize_gitlab_pipeline(pipeline: dict[str, Any]) -> RunRecord:
    """GitLabパイプライン1件を`gh run list`と同一スキーマの`RunRecord`へ正規化する。

    未知のステータスは完了かつ`conclusion`をステータス名のまま扱い、成功以外として判定させる
    （`conclusion`は`success`のみ通過とする厳格判定のため、未知値の混入で誤って通過することはない）。
    `name`は空の場合があるため、空のときはパイプライン識別子を含む代替表示へ置き換える。
    """
    status = str(pipeline.get("status", ""))
    pipeline_id = pipeline.get("id")
    completed = status not in _GITLAB_PENDING_STATUSES
    return {
        "name": pipeline.get("name") or f"pipeline #{pipeline_id}",
        "status": "completed" if completed else "in_progress",
        "conclusion": _GITLAB_STATUS_TO_CONCLUSION.get(status, status) if completed else None,
        "url": pipeline.get("web_url", ""),
        "databaseId": pipeline_id,
        "headSha": pipeline.get("sha", ""),
        "createdAt": pipeline.get("created_at", ""),
    }


def _glab_pipeline_list(repository: str, ref: str, sha: str, subprocess_timeout: float) -> list[RunRecord]:
    """repository・ref・SHAを明示した`glab ci list`結果を正規化して返す。

    `glab`はJSON出力でGitLab APIの応答オブジェクトを加工せず出力する。
    SHA指定で対象を限定できるサブコマンドは`ci list`のみのため、`ci status`・`ci get`は使わない。
    """
    payload = _run_forge_json_command(
        [
            "glab",
            "ci",
            "list",
            "--repo",
            _forge_cli.glab_repository(repository),
            "--ref",
            _short_ref(ref),
            "--sha",
            sha,
            "-F",
            "json",
            "-P",
            "100",
        ],
        subprocess_timeout,
        "glab ci list",
        env=_forge_cli.glab_environment(repository),
    )
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RunListError(f"glab ci list returned unexpected JSON shape: {payload!r}")
    pipelines = list(payload)
    seen_ids = {item.get("id") for item in pipelines}
    for parent in payload:
        parent_id = parent.get("id")
        parent_project_id = parent.get("project_id")
        if not isinstance(parent_id, int) or isinstance(parent_id, bool):
            raise RunListError(f"glab ci list returned invalid pipeline id: {parent!r}")
        if not isinstance(parent_project_id, int) or isinstance(parent_project_id, bool):
            continue
        for child in _glab_downstream_pipelines(repository, parent_id, parent_project_id, subprocess_timeout):
            child_id = child.get("id")
            if child_id not in seen_ids:
                pipelines.append(child)
                seen_ids.add(child_id)
    return [_normalize_gitlab_pipeline(item) for item in pipelines]


def _glab_downstream_pipelines(
    repository: str,
    parent_pipeline_id: int,
    parent_project_id: int,
    subprocess_timeout: float,
) -> list[dict[str, Any]]:
    """同一projectのbridgeが再帰的に指すdownstream pipeline詳細を返す。"""
    target = _parse_repository(repository)
    encoded_project = quote(target.project_path, safe="")
    children: list[dict[str, Any]] = []
    pending = [parent_pipeline_id]
    visited = {parent_pipeline_id}
    while pending:
        current_id = pending.pop(0)
        command = [
            "glab",
            "api",
            f"projects/{encoded_project}/pipelines/{current_id}/bridges?per_page=100",
            "--paginate",
            "--output",
            "json",
        ]
        bridges = _run_forge_json_command(
            command, subprocess_timeout, "glab api bridges", env=_forge_cli.glab_environment(repository)
        )
        if not isinstance(bridges, list) or not all(isinstance(item, dict) for item in bridges):
            raise RunListError(f"glab api bridges returned unexpected JSON shape: {bridges!r}")
        for bridge in bridges:
            downstream = bridge.get("downstream_pipeline")
            if downstream is None:
                continue
            if not isinstance(downstream, dict):
                raise RunListError(f"glab api bridges returned invalid downstream pipeline: {bridge!r}")
            if downstream.get("project_id") != parent_project_id:
                continue
            child_id = downstream.get("id")
            if not isinstance(child_id, int) or isinstance(child_id, bool):
                raise RunListError(f"glab api bridges returned invalid downstream pipeline id: {bridge!r}")
            if child_id in visited:
                continue
            visited.add(child_id)
            child_command = [
                "glab",
                "api",
                f"projects/{encoded_project}/pipelines/{child_id}",
                "--output",
                "json",
            ]
            child = _run_forge_json_command(
                child_command, subprocess_timeout, "glab api downstream pipeline", env=_forge_cli.glab_environment(repository)
            )
            if not isinstance(child, dict):
                raise RunListError(f"glab api downstream pipeline returned unexpected JSON shape: {child!r}")
            children.append(child)
            pending.append(child_id)
    return children


def _glab_job_list(repository: str, run: RunRecord, subprocess_timeout: float) -> list[JobRecord]:
    """GitLab pipelineの置換済み試行を除くジョブを全ページ取得して正規化する。"""
    pipeline_id = run.get("databaseId")
    if not isinstance(pipeline_id, int) or isinstance(pipeline_id, bool):
        raise RunListError(f"GitLab pipeline databaseId is invalid: {pipeline_id!r}")
    target = _parse_repository(repository)
    encoded_project = quote(target.project_path, safe="")
    command = [
        "glab",
        "api",
        f"projects/{encoded_project}/pipelines/{pipeline_id}/jobs?include_retried=false&per_page=100",
        "--paginate",
        "--output",
        "json",
    ]
    payload = _run_forge_json_command(command, subprocess_timeout, "glab api jobs", env=_forge_cli.glab_environment(repository))
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RunListError(f"glab api jobs returned unexpected JSON shape: {payload!r}")
    jobs: list[JobRecord] = []
    for item in payload:
        allow_failure = item.get("allow_failure")
        if not isinstance(allow_failure, bool):
            raise RunListError(f"glab api jobs returned invalid allow_failure: {item!r}")
        if not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool) or not isinstance(item.get("name"), str):
            raise RunListError(f"glab api jobs returned unexpected job shape: {item!r}")
        status = item.get("status")
        if not isinstance(status, str):
            raise RunListError(f"glab api jobs returned unexpected job state: {item!r}")
        normalized_status = "cancelled" if status == "canceled" else status
        jobs.append(
            {
                "name": item["name"],
                "status": normalized_status,
                "conclusion": "failure" if status == "failed" else normalized_status,
                "url": item.get("web_url", ""),
                "databaseId": item["id"],
                "allowFailure": allow_failure,
            }
        )
    return jobs


def _run_forge_json_command(
    command: list[str], subprocess_timeout: float, description: str, *, env: dict[str, str] | None = None
) -> Any:
    """外部CLIのJSON応答をUTF-8で厳格にデコードし、取得エラーを`RunListError`へ統合する。"""

    def error_factory(failure: _json_command.Failure) -> Exception:
        if failure.kind == "timeout":
            return RunListError(f"{description} timed out after {subprocess_timeout:.0f}s")
        if failure.kind == "not-found":
            return RunListError(f"{command[0]} command not found")
        if failure.kind == "decode":
            return RunListError(f"{description} returned non-UTF-8 output: {failure.detail}")
        if failure.kind == "exit":
            return RunListError(f"{description} failed (exit={failure.returncode}): {failure.stderr.strip()}")
        return RunListError(f"{description} returned invalid JSON: {failure.detail}")

    return _json_command.run(command, subprocess_timeout, error_factory=error_factory, env=env)


def _github_ci_configured(repository: str, sha: str, subprocess_timeout: float) -> bool | None:
    """GitHub tree応答からCI workflowの有無を確定する。

    判定不能な応答はCI定義不在と確定せず、呼び出し元が既存のrun登録猶予へ後退できるように`None`を返す。
    """
    try:
        target = _parse_repository(repository)
        if target.project_path.count("/") != 1:
            return None
        command = [
            "gh",
            "api",
            "-X",
            "GET",
            f"repos/{target.project_path}/git/trees/{sha}?recursive=1",
        ]
        if target.hostname is not None:
            command.extend(["--hostname", target.hostname])
        payload = _run_forge_json_command(command, subprocess_timeout, "gh api git tree")
    except RunListError:
        return None
    if not isinstance(payload, dict) or payload.get("truncated") is not False:
        return None
    tree = payload.get("tree")
    if not isinstance(tree, list):
        return None
    for item in tree:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str):
            continue
        workflow_path = pathlib.PurePosixPath(path)
        if (
            item.get("type") == "blob"
            and workflow_path.parent == pathlib.PurePosixPath(".github/workflows")
            and workflow_path.suffix in {".yml", ".yaml"}
        ):
            return True
    return False


def _gitlab_ci_exclusion(repository: str, sha: str, ref: str, subprocess_timeout: float) -> list[str] | None:
    """CI Lintで確定した非生成理由を返す。判定不能なら既存の登録待機へ進める。"""
    try:
        target = _parse_repository(repository)
        # GETのcontent_refで対象commitの設定を読み、dry_run_refでpush先の文脈を指定する。
        # API仕様: https://docs.gitlab.com/api/lint/#validate-existing-cicd-configuration
        endpoint = (
            f"projects/{quote(target.project_path, safe='')}/ci/lint?content_ref={quote(sha, safe='')}"
            f"&dry_run=true&dry_run_ref={quote(_short_ref(ref), safe='')}&include_jobs=true"
        )
        payload = _run_forge_json_command(
            ["glab", "api", endpoint, "-X", "GET", "--output", "json"],
            subprocess_timeout,
            "glab api ci lint",
            env=_forge_cli.glab_environment(repository),
        )
    except RunListError:
        return None
    if not isinstance(payload, dict) or payload.get("valid") is not False:
        return None
    jobs = payload.get("jobs")
    errors = payload.get("errors")
    if not isinstance(jobs, list) or jobs or not isinstance(errors, list) or not errors:
        return None
    # 設定誤りを非生成へ誤分類すると失敗CIを見逃すため、確認済みの完全一致だけを受理する。
    non_generation = {"The pipeline did not run. Review the workflow:rules configuration for the pipeline."}
    if not all(isinstance(error, str) and error in non_generation for error in errors):
        return None
    return errors


def _resolve_forge(explicit: str, repository: str, cwd: pathlib.Path | None = None) -> str | None:
    """対象forgeを`github`または`gitlab`へ解決する。判別できない場合は`None`を返す。

    `explicit`が`auto`以外ならその値をそのまま返す。
    `auto`では明示指定されたrepositoryのホスト名で判別する。
    ホスト名をドット区切りのラベルへ分割し、いずれかのラベルが`github`と完全一致すればGitHub、
    `gitlab`と完全一致すればGitLabとする。`github.com`とGitHub Enterprise Serverの
    標準的なホスト名（`github.<自社ドメイン>`）が前者に該当する。
    部分一致で判定すると`notgithub.example.com`のような無関係なホストを誤分類するため、ラベル単位の完全一致とする。
    ホストを含まない`owner/repo`形式は`--repo`が受理する形式であるため、作業ディレクトリ（`cwd`）の
    Git remoteのうちproject pathが一致するURLのホストで判別する。一致するremoteが無い場合と、
    一致したremoteのホストが異なるforgeへ分かれる場合、および未知の私設ホストは、`--forge`の明示指定が必要となる。
    """
    if explicit != "auto":
        return explicit
    try:
        target = _parse_repository(repository)
    except RunListError:
        return None
    if target.hostname is not None:
        return _forge_from_hostname(target.hostname)
    forges = {_forge_from_hostname(hostname) for hostname in _remote_hostnames(target.project_path, cwd)}
    return forges.pop() if len(forges) == 1 else None


def _forge_from_hostname(hostname: str) -> str | None:
    """ホスト名のラベルから`github`・`gitlab`を判別する。"""
    labels = hostname.lower().split(".")
    if "github" in labels:
        return "github"
    if "gitlab" in labels:
        return "gitlab"
    return None


def _remote_hostnames(project_path: str, cwd: pathlib.Path | None) -> list[str]:
    """`cwd`のGit remoteのうち、project pathが一致するURLのホスト名を返す。取得できない場合は空とする。"""
    hostnames: list[str] = []
    for url in _git_remote.remote_urls(cwd, timeout=30):
        try:
            remote = _parse_repository(url)
        except RunListError:
            continue
        if remote.hostname is not None and remote.project_path.lower() == project_path.lower():
            hostnames.append(remote.hostname)
    return hostnames


def _default_run_list_fn(
    forge: str,
    repository: str,
    ref: str,
    subprocess_timeout: float,
) -> RunListFn:
    """forgeに対応し、repository・ref・タイムアウトを固定した一覧取得関数を返す。"""
    fetcher = _glab_pipeline_list if forge == "gitlab" else _gh_run_list
    return functools.partial(fetcher, repository, ref, subprocess_timeout=subprocess_timeout)


def _resolve_sha(revision: str, subprocess_timeout: float) -> str | None:
    """`git rev-parse`でrevisionを完全形式のcommit shaへ再帰的にpeelする。

    明示指定されたSHAを同じ処理で完全形式へ変換することで、
    短縮形式を受理しないforge CLIとの扱いを揃える。
    解決失敗時は`None`を返し、呼び出し元で識別子解決失敗として区別できるようにする。
    """
    try:
        result = _git_command.run(
            ["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _is_ancestor_of_ref(ancestor_sha: str, ref: str, subprocess_timeout: float) -> bool:
    """`git merge-base --is-ancestor <sha> <ref>`で祖先関係を確認する。"""
    try:
        result = _git_command.run(
            ["merge-base", "--is-ancestor", ancestor_sha, ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _follow_shas(base_sha: str, ref: str, subprocess_timeout: float) -> list[str]:
    """`git log <base_sha>..<ref> --format=%H`で後続SHA集合を新しい順で返す。"""
    try:
        result = _git_command.run(
            ["log", f"{base_sha}..{ref}", "--format=%H"],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]
