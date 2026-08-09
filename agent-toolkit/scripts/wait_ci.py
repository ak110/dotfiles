#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""push前後の実行ID差分でCI通過確認を待機する補助スクリプト。

push前に対象repository・destination ref・source refから解決したcommit SHAの実行IDをbaselineへ保存し、push後は
GitHub ActionsまたはGitLab CIの実行一覧からbaselineに存在しないIDだけを待機する。
一覧とジョブ取得の両方に明示的なrepositoryを指定し、一覧はdestination refとSHAで限定する。
境界条件（run未登録・コマンド失敗・登録遅延・cancelled後の後続run追跡・タイムアウト・シグナル）を明示的に扱う。
`agent-toolkit:commit`の`references/push-and-ci.md`から参照される。
同節の実施主体の分離は`agent-toolkit:delegation`が定める。
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import math
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlparse

# 以下の終了コードはCLIの公開インターフェース（利用者が`echo $?`等で参照する契約）であり、
# private実装詳細ではないためアンダースコア接頭辞を付けない。
# `EXIT_GH_ERROR`はGitHub専用実装だった当時の名称を公開契約として維持する。
# 現在はforge CLI（`gh`・`glab`）呼び出し失敗と対象forge判別失敗の双方を表す。
EXIT_SUCCESS = 0
EXIT_CI_FAILED = 1
EXIT_TIMEOUT = 2
EXIT_GH_ERROR = 3
EXIT_NO_RUNS = 4
EXIT_INTERRUPTED = 130

_STDERR_FD = 2
"""標準エラー出力のファイルディスクリプタ。シグナルハンドラーからの再入しない書き込みに使う。"""

_MAX_CONSECUTIVE_SNAPSHOT_FAILURES = 3
_GH_JSON_FIELDS = "name,status,conclusion,url,databaseId,headSha,createdAt"
_BASELINE_VERSION = 2

RunRecord = dict[str, Any]
JobRecord = dict[str, Any]
RunListFn = Callable[[str], list[RunRecord]]
JobListFn = Callable[[RunRecord], list[JobRecord]]
AncestorCheckFn = Callable[[str], bool]
FollowShasFn = Callable[[str], list[str]]


class RunListError(RuntimeError):
    """CI実行一覧・ジョブ一覧の取得または応答検証の失敗。呼び出し側でretry判定に使う。"""


@dataclasses.dataclass(frozen=True)
class CiBaseline:
    """push前に観測したCI実行IDとその取得条件。"""

    forge: str
    repository: str
    ref: str
    source_ref: str
    sha: str
    run_ids: frozenset[int]


@dataclasses.dataclass(frozen=True)
class RepositoryTarget:
    """forge CLIに渡すrepositoryのホストとproject path。"""

    hostname: str | None
    project_path: str


def _parse_repository(repository: str) -> RepositoryTarget:
    """URL・SCP形式・`[host/]owner/repo`をrepository対象へ正規化する。"""
    value = repository.strip()
    hostname: str | None = None
    project_path = value
    if "://" in value:
        parsed = urlparse(value)
        hostname = parsed.hostname
        project_path = parsed.path
    elif match := re.match(r"^(?:[^@/]+@)?([^:/]+):(.+)$", value):
        hostname = match.group(1)
        project_path = match.group(2)
    else:
        parts = value.strip("/").split("/")
        if len(parts) >= 3 and "." in parts[0]:
            hostname = parts[0]
            project_path = "/".join(parts[1:])
    project_path = project_path.strip("/")
    if project_path.endswith(".git"):
        project_path = project_path[:-4]
    if not project_path or "/" not in project_path:
        raise RunListError(f"repository指定が不正: {repository!r}")
    return RepositoryTarget(hostname=hostname, project_path=project_path)


def _short_ref(ref: str) -> str:
    """`refs/heads/`または`refs/tags/`をforge CLI用の短縮refへ変換する。"""
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref.removeprefix(prefix)
    return ref


def _gh_run_list(repository: str, ref: str, sha: str, subprocess_timeout: float) -> list[RunRecord]:
    """repository・ref・SHAを明示した`gh run list`結果を返す。"""
    try:
        result = subprocess.run(
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
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunListError(f"gh run list timed out after {subprocess_timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise RunListError("gh command not found") from exc
    if result.returncode != 0:
        raise RunListError(f"gh run list failed (exit={result.returncode}): {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunListError(f"gh run list returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RunListError(f"gh run list returned unexpected JSON shape: {result.stdout[:200]!r}")
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
    payload = _run_json_command(command, subprocess_timeout, "gh api jobs")
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
# 本ファイルの全cancelled判定（`_all_cancelled`）が成立しない。
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
    try:
        result = subprocess.run(
            [
                "glab",
                "ci",
                "list",
                "--repo",
                repository,
                "--ref",
                _short_ref(ref),
                "--sha",
                sha,
                "-F",
                "json",
                "-P",
                "100",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunListError(f"glab ci list timed out after {subprocess_timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise RunListError("glab command not found") from exc
    if result.returncode != 0:
        raise RunListError(f"glab ci list failed (exit={result.returncode}): {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunListError(f"glab ci list returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RunListError(f"glab ci list returned unexpected JSON shape: {result.stdout[:200]!r}")
    return [_normalize_gitlab_pipeline(item) for item in payload]


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
    if target.hostname is not None:
        command.extend(["--hostname", target.hostname])
    payload = _run_json_command(command, subprocess_timeout, "glab api jobs")
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


def _run_json_command(command: list[str], subprocess_timeout: float, description: str) -> Any:
    """外部CLIのJSON応答を返し、実行・JSON解析エラーを`RunListError`へ統合する。"""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunListError(f"{description} timed out after {subprocess_timeout:.0f}s") from exc
    except FileNotFoundError as exc:
        raise RunListError(f"{command[0]} command not found") from exc
    if result.returncode != 0:
        raise RunListError(f"{description} failed (exit={result.returncode}): {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunListError(f"{description} returned invalid JSON: {exc}") from exc


def _resolve_forge(explicit: str, repository: str) -> str | None:
    """対象forgeを`github`または`gitlab`へ解決する。判別できない場合は`None`を返す。

    `explicit`が`auto`以外ならその値をそのまま返す。
    `auto`では明示指定されたrepositoryのホスト名で判別する。
    ホスト名をドット区切りのラベルへ分割し、いずれかのラベルが`github`と完全一致すればGitHub、
    `gitlab`と完全一致すればGitLabとする。`github.com`とGitHub Enterprise Serverの
    標準的なホスト名（`github.<自社ドメイン>`）が前者に該当する。
    部分一致で判定すると`notgithub.example.com`のような無関係なホストを誤分類するため、ラベル単位の完全一致とする。
    ホストを含まない短縮repositoryと未知の私設ホストは、`--forge`の明示指定が必要となる。
    """
    if explicit != "auto":
        return explicit
    try:
        hostname = _parse_repository(repository).hostname
    except RunListError:
        return None
    if hostname is None:
        return None
    labels = hostname.lower().split(".")
    if "github" in labels:
        return "github"
    if "gitlab" in labels:
        return "gitlab"
    return None


def _default_run_list_fn(
    forge: str,
    repository: str,
    ref: str,
    subprocess_timeout: float,
) -> RunListFn:
    """forgeに対応し、repository・ref・タイムアウトを固定した一覧取得関数を返す。"""
    fetcher = _glab_pipeline_list if forge == "gitlab" else _gh_run_list
    return functools.partial(fetcher, repository, ref, subprocess_timeout=subprocess_timeout)


def _write_baseline(path: pathlib.Path, baseline: CiBaseline) -> None:
    """CI baselineを再利用可能なJSONとして保存する。"""
    payload = {
        "version": _BASELINE_VERSION,
        "forge": baseline.forge,
        "repository": baseline.repository,
        "ref": baseline.ref,
        "source_ref": baseline.source_ref,
        "sha": baseline.sha,
        "run_ids": sorted(baseline.run_ids),
    }
    path.write_text(f"{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n", encoding="utf-8")


def _load_baseline(path: pathlib.Path) -> CiBaseline:
    """JSON baselineを読み、完全なスキーマ検証後に返す。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunListError(f"baselineの読み込みに失敗: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != _BASELINE_VERSION:
        raise RunListError(f"baselineのversionまたはJSON構造が不正: {path}")
    string_fields = ("forge", "repository", "ref", "source_ref", "sha")
    if not all(isinstance(payload.get(field), str) and payload[field] for field in string_fields):
        raise RunListError(f"baselineの対象識別子が不正: {path}")
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list):
        raise RunListError(f"baselineのrun_idsが不正: {path}")
    validated_run_ids: list[int] = []
    for run_id in run_ids:
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            raise RunListError(f"baselineのrun_idsが不正: {path}")
        validated_run_ids.append(run_id)
    return CiBaseline(
        forge=payload["forge"],
        repository=payload["repository"],
        ref=payload["ref"],
        source_ref=payload["source_ref"],
        sha=payload["sha"],
        run_ids=frozenset(validated_run_ids),
    )


def _validate_baseline_context(
    baseline: CiBaseline,
    forge: str,
    repository: str,
    ref: str,
    source_ref: str,
    sha: str,
) -> None:
    """baselineの取得条件が待機対象と完全に一致することを確認する。"""
    actual = (baseline.forge, baseline.repository, baseline.ref, baseline.source_ref, baseline.sha)
    expected = (forge, repository, ref, source_ref, sha)
    if actual != expected:
        raise RunListError(f"baselineの対象が不一致: baseline={actual!r}, requested={expected!r}")


def _print(elapsed: float, msg: str) -> None:
    print(f"[wait_ci] {elapsed:.0f}s: {msg}", file=sys.stderr, flush=True)


def _emit_summary(runs: list[RunRecord]) -> None:
    for r in runs:
        print(f"{r.get('name', '?')}: {r.get('status', '?')}/{r.get('conclusion', '?')} {r.get('url', '')}")


def _emit_failure_summary(record: RunRecord | JobRecord, record_type: str) -> None:
    state = record.get("conclusion") or record.get("status") or "?"
    print(f"{record_type} {record.get('name', '?')}: {state} {record.get('url', '')}")


_GITHUB_EARLY_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required"})
_GITHUB_EARLY_FAILURE_RUN_CONCLUSIONS = _GITHUB_EARLY_FAILURE_CONCLUSIONS | frozenset({"startup_failure", "stale"})


def _find_early_failure(runs: list[RunRecord], jobs: list[JobRecord], forge: str) -> tuple[RunRecord | JobRecord, str] | None:
    """forgeの確定的な失敗状態を1件返す。最終結論へ委ねる状態は返さない。

    - `gitlab`: statusが`failed`かつ`allowFailure is False`のjobを早期失敗とする
      （`allow_failure=true`・`manual`・`skipped`・`canceled`・進行中・未知状態は対象外）
    - それ以外（`github`）: 完了runのconclusionが`_GITHUB_EARLY_FAILURE_RUN_CONCLUSIONS`
      （`failure`・`timed_out`・`action_required`・`startup_failure`・`stale`）であれば先に返し、
      無ければ完了jobのconclusionが`_GITHUB_EARLY_FAILURE_CONCLUSIONS`
      （`failure`・`timed_out`・`action_required`）であるものを返す
      （`cancelled`・`neutral`・`skipped`・進行中・未知状態は対象外）
    """
    if forge == "gitlab":
        for job in jobs:
            if job.get("status") == "failed" and job.get("allowFailure") is False:
                return job, "job"
        return None
    for run in runs:
        if run.get("status") == "completed" and run.get("conclusion") in _GITHUB_EARLY_FAILURE_RUN_CONCLUSIONS:
            return run, "run"
    for job in jobs:
        if job.get("status") == "completed" and job.get("conclusion") in _GITHUB_EARLY_FAILURE_CONCLUSIONS:
            return job, "job"
    return None


def _fetch_snapshot(
    sha: str,
    run_list_fn: RunListFn,
    job_list_fn: JobListFn,
    expected_ids: set[int] | None = None,
    excluded_ids: frozenset[int] = frozenset(),
) -> tuple[list[RunRecord], list[JobRecord]]:
    """baselineを除いたrunと対応ジョブを不可分なpollスナップショットとして取得する。"""
    candidates = run_list_fn(sha)
    _run_ids(candidates)
    new_runs = [run for run in candidates if run["databaseId"] not in excluded_ids]
    runs = new_runs if expected_ids is None else [run for run in new_runs if run["databaseId"] in expected_ids]
    jobs = [job for run in runs for job in job_list_fn(run)]
    return runs, jobs


def _fetch_follow_snapshot(
    follow_shas: set[str],
    run_list_fn: RunListFn,
    job_list_fn: JobListFn,
    expected_ids: set[int] | None = None,
    excluded_ids: frozenset[int] = frozenset(),
) -> tuple[list[RunRecord], list[JobRecord]]:
    """全後続SHAのrun・ジョブ一覧を不可分なpollスナップショットとして取得する。"""
    candidates = [run for follow_sha in follow_shas for run in run_list_fn(follow_sha)]
    _run_ids(candidates)
    new_runs = [run for run in candidates if run.get("headSha") in follow_shas and run["databaseId"] not in excluded_ids]
    runs = new_runs if expected_ids is None else [run for run in new_runs if run["databaseId"] in expected_ids]
    jobs = [job for run in runs for job in job_list_fn(run)]
    return runs, jobs


def _run_ids(runs: list[RunRecord]) -> set[int]:
    """run一覧の整数ID集合を返し、不正なIDは取得失敗として扱う。"""
    identifiers: set[int] = set()
    for run in runs:
        run_id = run.get("databaseId")
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            raise RunListError(f"CI run databaseId is invalid: {run_id!r}")
        identifiers.add(run_id)
    return identifiers


def _all_completed(runs: list[RunRecord]) -> bool:
    return len(runs) > 0 and all(r.get("status") == "completed" for r in runs)


def _all_success(runs: list[RunRecord]) -> bool:
    return _all_completed(runs) and all(r.get("conclusion") == "success" for r in runs)


def _all_cancelled(runs: list[RunRecord]) -> bool:
    return _all_completed(runs) and all(r.get("conclusion") == "cancelled" for r in runs)


def wait_for_ci(
    sha: str,
    timeout: float,
    poll_interval: float,
    registration_grace: float,
    follow_cancelled: bool,
    subprocess_timeout: float,
    *,
    repository: str,
    ref: str,
    source_ref: str,
    baseline_ids: frozenset[int],
    forge: str = "github",
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    run_list_fn: RunListFn | None = None,
    job_list_fn: JobListFn | None = None,
    ancestor_check_fn: AncestorCheckFn | None = None,
    follow_shas_fn: FollowShasFn | None = None,
) -> int:
    """対象shaの明確な失敗run・ジョブ1件検出または期待run集合完了の早い方を待ちexit codeを返す。

    - 毎pollでbaseline IDを除いたrun一覧とジョブ一覧を、通常経路は`_fetch_snapshot`、
      後続SHA追跡時は`_fetch_follow_snapshot`で不可分なスナップショットとして取得し、
      `_find_early_failure`が確定的な失敗（forgeごとの判定は同関数docstring参照）を1件検出した時点で
      run/pipeline完了を待たずEXIT_CI_FAILEDを返す
    - 登録猶予期間全体でrun集合を継続収集し、期間末に1件以上あることを確認して完了待ちへ移る
    - 完了待ちフェーズでも対象shaのrunを毎pollで取り込み、猶予後に登録されたrunを期待集合へ加える
      （run一覧は対象shaで限定するため、猶予後のrunも同じcommitに対する実行である）
    - 期間末で0件ならEXIT_NO_RUNS
    - run一覧・ジョブ一覧いずれかの取得失敗を含むスナップショット取得の連続失敗が閾値到達でEXIT_GH_ERROR
      （1回でも全取得成功したスナップショットで連続失敗カウンターをリセットする。
      登録猶予末・後続SHA登録猶予末は、猶予末に到達した回の取得失敗のみでも
      3回連続を待たず即時EXIT_GH_ERRORとする）
    - 早期失敗が無く期待run集合全runが`conclusion==success`のときのみEXIT_SUCCESS
    - `follow_cancelled=True`かつ全run cancelled時は`git log <sha>..<source_ref>`の後続SHA上のrunで補完判定
    - 対象SHAが明示したsource refの祖先でない場合は`--follow-cancelled`を許容しない（`EXIT_GH_ERROR`）
    - `forge`が`gitlab`のとき既定の取得手段を`glab ci list --sha`・`glab api`へ切り替える
      （`run_list_fn`・`job_list_fn`を明示指定した場合、取得関数の既定選択には`forge`を参照しない）
    - `forge`は早期失敗の分類（`_find_early_failure`のforgeごとの判定）には
      `run_list_fn`・`job_list_fn`の明示指定有無によらず常に使う
    """
    if run_list_fn is None:
        run_list_fn = _default_run_list_fn(forge, repository, ref, subprocess_timeout)
    if job_list_fn is None:
        job_fetcher = _glab_job_list if forge == "gitlab" else _gh_job_list
        job_list_fn = functools.partial(job_fetcher, repository, subprocess_timeout=subprocess_timeout)
    ancestor_check_fn = ancestor_check_fn or (lambda ancestor: _is_ancestor_of_ref(ancestor, source_ref, subprocess_timeout))
    follow_shas_fn = follow_shas_fn or (lambda base: _follow_shas(base, source_ref, subprocess_timeout))
    start = now_fn()
    runs: list[RunRecord] = []
    consecutive_failures = 0
    expected_ids: set[int] = set()

    while True:  # 登録猶予フェーズ: 猶予末まで継続収集する
        last_call_failed = False
        try:
            runs, jobs = _fetch_snapshot(sha, run_list_fn, job_list_fn, excluded_ids=baseline_ids)
            consecutive_failures = 0
            expected_ids |= _run_ids(runs)
            if failure := _find_early_failure(runs, jobs, forge):
                _emit_failure_summary(*failure)
                return EXIT_CI_FAILED
        except RunListError as exc:
            consecutive_failures += 1
            last_call_failed = True
            _print(now_fn() - start, f"run list error (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_SNAPSHOT_FAILURES:
                return EXIT_GH_ERROR
        elapsed = now_fn() - start
        if elapsed >= registration_grace:
            if last_call_failed:
                _print(elapsed, "CI実行一覧またはジョブ一覧の取得失敗により期待run集合を確定できないまま登録猶予が経過")
                return EXIT_GH_ERROR
            if not expected_ids:
                _print(elapsed, f"run未登録のまま登録猶予{registration_grace:.0f}秒超過")
                return EXIT_NO_RUNS
            _print(elapsed, f"期待run集合確定（{len(expected_ids)}件）")
            break
        if elapsed >= timeout:
            _print(elapsed, f"タイムアウト（登録猶予中に{timeout:.0f}秒経過）")
            return EXIT_TIMEOUT
        sleep_fn(min(poll_interval, max(1.0, registration_grace - elapsed)))

    while True:  # 完了待ちフェーズ
        try:
            runs, jobs = _fetch_snapshot(sha, run_list_fn, job_list_fn, excluded_ids=baseline_ids)
            consecutive_failures = 0
        except RunListError as exc:
            consecutive_failures += 1
            _print(now_fn() - start, f"run list error (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_SNAPSHOT_FAILURES:
                return EXIT_GH_ERROR
            sleep_fn(poll_interval)
            continue
        elapsed = now_fn() - start
        if added := _run_ids(runs) - expected_ids:
            # run一覧は対象shaで限定するため、猶予後に現れるrunも同じcommitに対する実行である。
            # 待機対象へ加えないと当該runの失敗を待たずに通過と判定してしまう。
            _print(elapsed, f"追加登録されたrunを待機対象へ追加（{len(added)}件）")
            expected_ids |= added
        expected_runs = [r for r in runs if r.get("databaseId") in expected_ids] or []
        if len(expected_runs) < len(expected_ids):
            _print(elapsed, f"期待run集合の一部が取得結果から欠落（{len(expected_runs)}/{len(expected_ids)}）")
            if elapsed >= timeout:
                _emit_summary(expected_runs)
                return EXIT_TIMEOUT
            sleep_fn(poll_interval)
            continue
        if failure := _find_early_failure(expected_runs, jobs, forge):
            _emit_failure_summary(*failure)
            return EXIT_CI_FAILED
        if _all_completed(expected_runs):
            _emit_summary(expected_runs)
            if _all_success(expected_runs):
                return EXIT_SUCCESS
            if follow_cancelled and _all_cancelled(expected_runs):
                if not ancestor_check_fn(sha):
                    _print(elapsed, f"--follow-cancelled対象外: {sha}は{source_ref}の祖先ではない")
                    return EXIT_GH_ERROR
                _print(elapsed, f"全runがcancelled。{source_ref}の後続SHA集合を取得し追跡へ移行")
                return _follow_cancelled(
                    sha,
                    expected_runs,
                    max(0.0, timeout - elapsed),
                    poll_interval,
                    registration_grace,
                    sleep_fn=sleep_fn,
                    now_fn=now_fn,
                    run_list_fn=run_list_fn,
                    job_list_fn=job_list_fn,
                    follow_shas_fn=follow_shas_fn,
                    forge=forge,
                    excluded_ids=baseline_ids,
                )
            return EXIT_CI_FAILED
        if elapsed >= timeout:
            _print(elapsed, f"タイムアウト（{timeout:.0f}秒経過）")
            _emit_summary(expected_runs)
            return EXIT_TIMEOUT
        pending = [r.get("name", "?") for r in expected_runs if r.get("status") != "completed"]
        _print(elapsed, f"未完了run: {', '.join(pending)}")
        sleep_fn(poll_interval)


def _follow_cancelled(
    original_sha: str,
    _cancelled_runs: list[RunRecord],
    remaining_timeout: float,
    poll_interval: float,
    registration_grace: float,
    *,
    sleep_fn: Callable[[float], None],
    now_fn: Callable[[], float],
    run_list_fn: RunListFn,
    job_list_fn: JobListFn,
    follow_shas_fn: FollowShasFn,
    forge: str,
    excluded_ids: frozenset[int],
) -> int:
    """全run cancelled時、明示source refの後続SHA集合を判定対象とする。

    - 呼び出し前に`ancestor_check_fn`で`<original_sha>`が明示source refの祖先であることを確認済み
    - 後続SHAが未生成の場合は待機し、初回検出後は`registration_grace`秒の間
      `follow_shas_fn(original_sha)`と各後続SHAの`run_list_fn`を再呼び出しして、
      追加後続SHAの登録・同一SHA上での複数workflowの段階的なrun登録の両方を収集する
      （単一run・単一SHAのみで即断せず取りこぼしを防ぐ）
    - 登録猶予終了後は収集済みrun ID集合（`expected_ids`）に属するrunのみを判定対象とし、
      主フェーズの`expected_ids`方式と同じ考え方で扱う
    - 混在ケース（一部success/一部cancelled）は本処理の前提（全run cancelled）を満たさないため対象外
    """
    start = now_fn()
    follow_shas: set[str] = set()
    expected_ids: set[int] = set()
    grace_start: float | None = None
    consecutive_failures = 0
    last_call_failed = False
    while True:  # 後続SHA・後続run登録猶予フェーズ
        current_shas = set(follow_shas_fn(original_sha))
        follow_shas |= current_shas
        last_call_failed = False
        try:
            candidates, jobs = _fetch_follow_snapshot(
                follow_shas,
                run_list_fn,
                job_list_fn,
                excluded_ids=excluded_ids,
            )
            consecutive_failures = 0
            expected_ids |= _run_ids(candidates)
            if failure := _find_early_failure(candidates, jobs, forge):
                _emit_failure_summary(*failure)
                return EXIT_CI_FAILED
        except RunListError as exc:
            consecutive_failures += 1
            last_call_failed = True
            elapsed = now_fn() - start
            _print(elapsed, f"後続run取得失敗 (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_SNAPSHOT_FAILURES:
                return EXIT_GH_ERROR
        elapsed = now_fn() - start
        if follow_shas and grace_start is None:
            grace_start = now_fn()
            _print(elapsed, f"後続コミット検出（{len(follow_shas)}件）。登録猶予{registration_grace:.0f}秒を開始")
        if grace_start is not None and (now_fn() - grace_start) >= registration_grace:
            if last_call_failed:
                _print(elapsed, "後続run取得失敗により期待run集合を確定できないまま登録猶予が経過")
                return EXIT_GH_ERROR
            _print(elapsed, f"後続run集合確定（SHA{len(follow_shas)}件・run{len(expected_ids)}件）")
            break
        if elapsed >= remaining_timeout:
            if not follow_shas:
                _print(elapsed, "後続コミット未検出のままタイムアウト")
                return EXIT_TIMEOUT
            break
        sleep_fn(poll_interval)

    while True:  # 完了待ちフェーズ
        # 各後続SHAのrunを取得し、expected_idsに属するrunのみを集約する
        try:
            candidates, jobs = _fetch_follow_snapshot(
                follow_shas,
                run_list_fn,
                job_list_fn,
                expected_ids,
                excluded_ids,
            )
            consecutive_failures = 0
        except RunListError as exc:
            consecutive_failures += 1
            elapsed = now_fn() - start
            _print(elapsed, f"後続run取得失敗 (attempt {consecutive_failures}): {exc}")
            if consecutive_failures >= _MAX_CONSECUTIVE_SNAPSHOT_FAILURES:
                return EXIT_GH_ERROR
            sleep_fn(poll_interval)
            continue
        follow_runs = [r for r in candidates if r.get("headSha") in follow_shas and r.get("databaseId") in expected_ids]
        elapsed = now_fn() - start
        if len(follow_runs) < len(expected_ids):
            _print(elapsed, f"期待後続run集合の一部が取得結果から欠落（{len(follow_runs)}/{len(expected_ids)}）")
            if elapsed >= remaining_timeout:
                _emit_summary(follow_runs)
                return EXIT_TIMEOUT
            sleep_fn(poll_interval)
            continue
        if failure := _find_early_failure(follow_runs, jobs, forge):
            _emit_failure_summary(*failure)
            return EXIT_CI_FAILED
        if follow_runs and _all_completed(follow_runs):
            _emit_summary(follow_runs)
            return EXIT_SUCCESS if _all_success(follow_runs) else EXIT_CI_FAILED
        if elapsed >= remaining_timeout:
            _print(elapsed, "後続run追跡タイムアウト")
            _emit_summary(follow_runs)
            return EXIT_TIMEOUT
        pending = [r.get("name", "?") for r in follow_runs if r.get("status") != "completed"] or ["<未検出>"]
        _print(elapsed, f"後続run未完了: {', '.join(pending)}")
        sleep_fn(poll_interval)


def _resolve_sha(revision: str, subprocess_timeout: float) -> str | None:
    """`git rev-parse`でrevisionを完全形式のcommit shaへ再帰的にpeelする。

    明示指定されたSHAを同一経路で完全形式へ変換することで、
    短縮形式を受理しないforge CLIとの扱いを揃える。
    解決失敗時は`None`を返し、呼び出し元で識別子解決失敗として区別できるようにする。
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=subprocess_timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _is_ancestor_of_ref(ancestor_sha: str, ref: str, subprocess_timeout: float) -> bool:
    """`git merge-base --is-ancestor <sha> <ref>`で祖先関係を確認する。"""
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor_sha, ref],
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
        result = subprocess.run(
            ["git", "log", f"{base_sha}..{ref}", "--format=%H"],
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


def _install_signal_handlers() -> None:
    """SIGINT/SIGTERM受信時にexit codeを返す簡易ハンドラ。

    ハンドラは即座に`sys.exit`する。実行中の子`gh`プロセスは明示的に終了させず、
    `subprocess.run`側の`timeout`（`--subprocess-timeout`）到達による自然終了に委ねる。
    厳密なプロセスグループ制御は、複雑化に見合う利得が無いため実装しない。

    終了メッセージは`print`ではなく`os.write`で書く。`print`は`sys.stderr`の
    `BufferedWriter`を経由するため、メインフローが同じバッファへ書き込んでいる最中に
    シグナルが到達すると再入となり`RuntimeError: reentrant call inside <_io.BufferedWriter>`で
    異常終了する。`os.write`はバッファを介さないため再入が成立しない。
    """

    def _handler(signum, _frame):
        os.write(_STDERR_FD, f"[wait_ci] シグナル{signum}受信で終了\n".encode())
        sys.exit(EXIT_INTERRUPTED)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _positive_float(value: str) -> float:
    """0より大きい有限`float`へ変換する。不正時はargparse標準のusage表示で終了させる。"""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError(f"0より大きい有限の値を指定してください: {value!r}")
    return parsed


def _non_negative_float(value: str) -> float:
    """0以上の有限`float`へ変換する。不正時はargparse標準のusage表示で終了させる。"""
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(f"0以上の有限の値を指定してください: {value!r}")
    return parsed


def main(argv: list[str] | None = None) -> int:
    """コマンドライン引数を解析し、baseline作成またはCI通過確認を実行する。"""
    _install_signal_handlers()
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write-baseline", type=pathlib.Path, help="push前の実行IDを保存するJSONパス")
    mode.add_argument("--baseline", type=pathlib.Path, help="push前に保存したbaseline JSONパス")
    parser.add_argument("--repo", required=True, help="対象repository（owner/repoまたはホストを含むURL）")
    parser.add_argument("--ref", required=True, help="対象destination ref（例: refs/heads/main）")
    parser.add_argument("--source-ref", required=True, help="push元のローカルsource ref（例: HEAD）")
    parser.add_argument(
        "--sha",
        help="対象commit SHA（省略時はbaseline作成でsource ref、待機でbaseline保存SHAを使用）",
    )
    parser.add_argument("--timeout", type=_positive_float, default=900.0, help="全体タイムアウト秒数（既定900）")
    parser.add_argument("--poll-interval", type=_positive_float, default=20.0, help="ポーリング間隔秒数（既定20）")
    parser.add_argument("--registration-grace", type=_non_negative_float, default=60.0, help="run未登録許容秒数（既定60）")
    parser.add_argument(
        "--subprocess-timeout",
        type=_positive_float,
        default=60.0,
        help="個別forge CLI実行のタイムアウト秒数（既定60）",
    )
    parser.add_argument(
        "--forge",
        choices=("auto", "github", "gitlab"),
        default="auto",
        help="対象ホスティング種別（既定auto。--repoのホストから自動判定）",
    )
    parser.add_argument("--follow-cancelled", action="store_true", help="全run cancelled時にsource refの後続run成功を追跡")
    args = parser.parse_args(argv)
    forge = _resolve_forge(args.forge, args.repo)
    if forge is None:
        print("[wait_ci] 対象forgeを--repoから判別できない。--forgeで明示指定する", file=sys.stderr)
        return EXIT_GH_ERROR
    if args.write_baseline is not None:
        revision = args.sha if args.sha is not None else args.source_ref
        sha = _resolve_sha(revision, args.subprocess_timeout)
        if sha is None:
            print(f"[wait_ci] {revision}のcommit解決に失敗（git rev-parse）", file=sys.stderr)
            return EXIT_GH_ERROR
        try:
            runs = _default_run_list_fn(forge, args.repo, args.ref, args.subprocess_timeout)(sha)
            baseline = CiBaseline(
                forge=forge,
                repository=args.repo,
                ref=args.ref,
                source_ref=args.source_ref,
                sha=sha,
                run_ids=frozenset(_run_ids(runs)),
            )
            _write_baseline(args.write_baseline, baseline)
        except (OSError, RunListError) as exc:
            print(f"[wait_ci] baseline作成に失敗: {exc}", file=sys.stderr)
            return EXIT_GH_ERROR
        print(f"[wait_ci] baseline保存: {args.write_baseline} ({len(baseline.run_ids)}件)")
        return EXIT_SUCCESS
    try:
        baseline = _load_baseline(args.baseline)
        if args.sha is None:
            sha = baseline.sha
        else:
            sha = _resolve_sha(args.sha, args.subprocess_timeout)
            if sha is None:
                print(f"[wait_ci] {args.sha}のcommit解決に失敗（git rev-parse）", file=sys.stderr)
                return EXIT_GH_ERROR
        _validate_baseline_context(baseline, forge, args.repo, args.ref, args.source_ref, sha)
    except RunListError as exc:
        print(f"[wait_ci] baseline検証に失敗: {exc}", file=sys.stderr)
        return EXIT_GH_ERROR
    return wait_for_ci(
        sha,
        args.timeout,
        args.poll_interval,
        args.registration_grace,
        args.follow_cancelled,
        args.subprocess_timeout,
        repository=args.repo,
        ref=args.ref,
        source_ref=args.source_ref,
        baseline_ids=baseline.run_ids,
        forge=forge,
    )


if __name__ == "__main__":
    sys.exit(main())
