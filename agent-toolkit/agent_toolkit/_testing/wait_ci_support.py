"""CI待機試験の入力とシグナルの後始末。"""

from __future__ import annotations

import json
import pathlib
import signal

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._testing import git_repository as _git_repository_support

# 直接テスト対象のprivate helperはモジュール冒頭で別名束縛し、抑制コメントを1箇所へ集約する。
# 理由はモジュールdocstringの「例外」記述に従う。
_resolve_forge = wait_ci._resolve_forge  # pylint: disable=protected-access


_normalize_gitlab_pipeline = wait_ci._normalize_gitlab_pipeline  # pylint: disable=protected-access


_gh_run_list = wait_ci._gh_run_list  # pylint: disable=protected-access


_glab_pipeline_list = wait_ci._glab_pipeline_list  # pylint: disable=protected-access


_gh_job_list = wait_ci._gh_job_list  # pylint: disable=protected-access


_glab_job_list = wait_ci._glab_job_list  # pylint: disable=protected-access


_run_json_command = wait_ci._run_forge_json_command  # pylint: disable=protected-access


_is_ancestor_of_ref = wait_ci._is_ancestor_of_ref  # pylint: disable=protected-access


_follow_shas = wait_ci._follow_shas  # pylint: disable=protected-access


_all_cancelled = wait_ci._all_cancelled  # pylint: disable=protected-access


_all_success = wait_ci._all_success  # pylint: disable=protected-access


_github_ci_configured = wait_ci._github_ci_configured  # pylint: disable=protected-access


_FULL_SHA = "a" * 40


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    """`main`が登録するSIGINT/SIGTERMハンドラをテストごとに元へ戻す。

    `wait_ci.main`は冒頭でプロセス全体のシグナルdispositionを差し替える。
    テストは同一プロセスで`main`を呼ぶため、復元しないと以降の全テストが
    `wait_ci`のハンドラを保持し、割り込み時に無関係なテストが`SystemExit`で失敗する。
    """
    saved = {name: signal.getsignal(name) for name in (signal.SIGINT, signal.SIGTERM)}
    yield
    for name, handler in saved.items():
        signal.signal(name, handler)


def _write_test_baseline(
    path: pathlib.Path,
    *,
    sha: str = _FULL_SHA,
    source_ref: str = "HEAD",
    run_ids: list[int] | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "forge": "github",
                "repository": "owner/repository",
                "ref": "refs/heads/main",
                "source_ref": source_ref,
                "sha": sha,
                "run_ids": run_ids or [],
            }
        ),
        encoding="utf-8",
    )


def _main_args(baseline: pathlib.Path, *, sha: str | None = "abc123", source_ref: str = "HEAD") -> list[str]:
    args = [
        "--baseline",
        str(baseline),
        "--forge=github",
        "--repo",
        "owner/repository",
        "--ref",
        "refs/heads/main",
        "--source-ref",
        source_ref,
    ]
    if sha is not None:
        args.append(f"--sha={sha}")
    return args


@pytest.fixture(name="git_repository")
def _git_repository(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """branchと2種類のtagが同じcommitを指すテスト用repositoryを返す。"""
    repository = _git_repository_support.init_repository(tmp_path / "repository", files={"tracked.txt": "first\n"})
    commit_sha = _git_repository_support.commit_all(repository, "first")
    _git_repository_support.run_git(repository, "branch", "source-branch")
    _git_repository_support.run_git(repository, "tag", "lightweight-tag")
    _git_repository_support.run_git(repository, "tag", "-a", "annotated-tag", "-m", "annotated")
    blob_sha = _git_repository_support.run_git(repository, "rev-parse", "HEAD:tracked.txt").stdout.strip()
    _git_repository_support.run_git(repository, "tag", "-a", "non-commit-tag", blob_sha, "-m", "non-commit")
    return repository, commit_sha


def _run_wait(
    run_list_fn,
    *,
    timeout=270.0,
    poll_interval=20.0,
    registration_grace=60.0,
    follow_cancelled=False,
    forge="github",
    job_list_fn=None,
    ancestor_check_fn=None,
    follow_shas_fn=None,
    baseline_ids=frozenset(),
    source_ref="HEAD",
    repository="owner/repository",
):
    """`wait_for_ci`をDI経由で駆動する。時刻・sleepはスタブ化。"""
    times = iter(t * 1.0 for t in range(0, 100_000))
    return wait_ci.wait_for_ci(
        "sha1",
        timeout,
        poll_interval,
        registration_grace,
        follow_cancelled,
        10.0,
        repository=repository,
        ref="refs/heads/main",
        source_ref=source_ref,
        baseline_ids=baseline_ids,
        forge=forge,
        sleep_fn=lambda _s: None,
        now_fn=lambda: next(times),
        run_list_fn=run_list_fn,
        job_list_fn=job_list_fn or (lambda _r: []),
        ancestor_check_fn=ancestor_check_fn or (lambda _a: True),
        follow_shas_fn=follow_shas_fn or (lambda _b: ["sha2"]),
    )


def _run(
    name="a",
    status="completed",
    conclusion: str | None = "success",
    db_id=1,
    head_sha="sha1",
    created_at="2026-07-22T00:00:00Z",
    event="push",
    workflow_name: str | None = None,
):
    return {
        "name": name,
        "workflowName": workflow_name or name,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "url": "u",
        "databaseId": db_id,
        "headSha": head_sha,
        "createdAt": created_at,
    }


def _job(
    name="job",
    status="completed",
    conclusion: str | None = "success",
    db_id=10,
    *,
    allow_failure=False,
):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "url": "job-url",
        "databaseId": db_id,
        "allowFailure": allow_failure,
    }


def _wait_for_ci_without_runs(repository: str, *, forge: str = "github") -> int:
    """runが1件も登録されない状態で、即時に時間切れとなる待機を実行して終了コードを返す。"""
    return wait_ci.wait_for_ci(
        "sha1",
        10.0,
        1.0,
        0.0,
        False,
        10.0,
        repository=repository,
        ref="refs/heads/main",
        source_ref="HEAD",
        baseline_ids=frozenset(),
        forge=forge,
        now_fn=lambda: 0.0,
        sleep_fn=lambda _seconds: None,
    )


def _next_action(stderr: str) -> str:
    """標準エラーから次の操作の行の本文を1件だけ取り出す。"""
    lines = [line.removeprefix("次の操作: ") for line in stderr.splitlines() if line.startswith("次の操作: ")]
    assert len(lines) == 1, stderr
    return lines[0]


def _port_endpoint_options(repository: str) -> list[str]:
    """baselineとSHA待機へ同じrepository・ref・登録猶予・poll間隔を与える。"""
    return [
        "--repo",
        repository,
        "--forge",
        "gitlab",
        "--ref",
        "refs/heads/main",
        "--source-ref",
        "HEAD",
        "--registration-grace",
        "0",
        "--poll-interval",
        "0.01",
    ]


def _assert_port_endpoint(command: list[str], environment: dict[str, str], repository: str, expected_host: str) -> None:
    """glabのCLI境界で明示URLのscheme・host・portとrepositoryを確認する。"""
    assert command[0] == "glab"
    assert "--hostname" not in command
    assert environment["GITLAB_HOST"] == expected_host
    assert environment["GITLAB_API_HOST"] == expected_host
    assert environment["GLAB_API_PROTOCOL"] == repository.split(":", 1)[0]
    if command[1:3] == ["ci", "list"]:
        assert command[command.index("--repo") + 1] == repository
