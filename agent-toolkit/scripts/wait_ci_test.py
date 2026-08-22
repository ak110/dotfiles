"""`wait_ci`モジュールのテスト。公開API（`main`・`wait_for_ci`）経由で境界条件を網羅する。
private helper（`_gh_run_list`・`_resolve_sha`等）は原則として直接テストせず、`main`経由の
シナリオテストで挙動を確認する（`coding-standards/references/testing.md`
「private関数の直接テスト禁止」に従う）。
例外はforge判別・forge応答正規化と外部コマンド境界とし、入力形態ごとの網羅を`main`経由で行うと
1形態あたり複数の外部コマンド応答を組み立てる必要があり、判定対象の入出力関係が読み取れなくなるため
最小限の範囲で直接テストする。
"""

from __future__ import annotations

import json
import locale
import os
import pathlib
import signal
import subprocess
import sys
import time
from unittest import mock

import pytest
import wait_ci

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


def _git(repository: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    """テスト用repositoryでgitを実行する。"""
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture(name="git_repository")
def _git_repository(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """branchと2種類のtagが同じcommitを指すテスト用repositoryを返す。"""
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "Wait CI Test")
    _git(repository, "config", "user.email", "wait-ci@example.invalid")
    (repository / "tracked.txt").write_text("first\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "first")
    commit_sha = _git(repository, "rev-parse", "HEAD").stdout.strip()
    _git(repository, "branch", "source-branch")
    _git(repository, "tag", "lightweight-tag")
    _git(repository, "tag", "-a", "annotated-tag", "-m", "annotated")
    blob_sha = _git(repository, "rev-parse", "HEAD:tracked.txt").stdout.strip()
    _git(repository, "tag", "-a", "non-commit-tag", blob_sha, "-m", "non-commit")
    return repository, commit_sha


def _run_wait(
    run_list_fn,
    *,
    timeout=900.0,
    poll_interval=20.0,
    registration_grace=60.0,
    follow_cancelled=False,
    forge="github",
    job_list_fn=None,
    ancestor_check_fn=None,
    follow_shas_fn=None,
    baseline_ids=frozenset(),
    source_ref="HEAD",
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
        repository="owner/repository",
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
):
    return {
        "name": name,
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


class TestSuccessAndFailurePaths:
    def test_all_success_returns_exit_success(self):
        assert _run_wait(lambda _s: [_run()]) == wait_ci.EXIT_SUCCESS

    @pytest.mark.parametrize(
        "conclusion", ["failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale", "skipped", None]
    )
    def test_non_success_conclusion_returns_ci_failed(self, conclusion):
        assert _run_wait(lambda _s: [_run(conclusion=conclusion)]) == wait_ci.EXIT_CI_FAILED

    def test_mixed_success_and_failure_returns_ci_failed(self):
        runs = [_run(name="a", db_id=1), _run(name="b", conclusion="failure", db_id=2)]
        assert _run_wait(lambda _s: runs) == wait_ci.EXIT_CI_FAILED

    @pytest.mark.parametrize("conclusion", ["failure", "timed_out", "action_required"])
    def test_github_failed_job_returns_early(self, conclusion):
        run = _run(status="in_progress", conclusion=None)
        assert (
            _run_wait(
                lambda _s: [run],
                registration_grace=100.0,
                job_list_fn=lambda _r: [_job(conclusion=conclusion)],
            )
            == wait_ci.EXIT_CI_FAILED
        )

    @pytest.mark.parametrize(
        ("status", "conclusion"),
        [
            ("completed", "cancelled"),
            ("completed", "neutral"),
            ("completed", "skipped"),
            ("in_progress", None),
            ("completed", "unknown"),
        ],
    )
    def test_github_non_failure_job_waits_for_run_conclusion(self, status, conclusion):
        assert (
            _run_wait(
                lambda _s: [_run()],
                registration_grace=0.0,
                job_list_fn=lambda _r: [_job(status=status, conclusion=conclusion)],
            )
            == wait_ci.EXIT_SUCCESS
        )

    @pytest.mark.parametrize("conclusion", ["startup_failure", "stale"])
    def test_github_failed_completed_run_returns_before_other_run(self, conclusion):
        runs = [
            _run(name="failed", conclusion=conclusion, db_id=1),
            _run(name="pending", status="in_progress", conclusion=None, db_id=2),
        ]
        assert _run_wait(lambda _s: runs, registration_grace=100.0) == wait_ci.EXIT_CI_FAILED

    def test_gitlab_non_allowed_failure_returns_early(self):
        run = _run(status="in_progress", conclusion=None)
        job = _job(status="failed", conclusion="failure", allow_failure=False)
        assert (
            _run_wait(
                lambda _s: [run],
                forge="gitlab",
                registration_grace=100.0,
                job_list_fn=lambda _r: [job],
            )
            == wait_ci.EXIT_CI_FAILED
        )

    @pytest.mark.parametrize(
        ("status", "allow_failure"),
        [
            ("failed", True),
            ("cancelled", False),
            ("manual", False),
            ("skipped", False),
            ("running", False),
            ("unknown", False),
        ],
    )
    def test_gitlab_non_early_failure_waits_for_pipeline_conclusion(self, status, allow_failure):
        assert (
            _run_wait(
                lambda _s: [_run()],
                forge="gitlab",
                registration_grace=0.0,
                job_list_fn=lambda _r: [_job(status=status, conclusion=status, allow_failure=allow_failure)],
            )
            == wait_ci.EXIT_SUCCESS
        )


class TestRegistrationGrace:
    def test_registers_after_initial_empty_responses(self):
        calls = {"n": 0}

        def run_list_fn(_s):
            calls["n"] += 1
            return [] if calls["n"] < 3 else [_run()]

        assert _run_wait(run_list_fn, registration_grace=100.0) == wait_ci.EXIT_SUCCESS

    def test_no_runs_after_grace_returns_no_runs(self):
        """登録猶予経過後もrun 0件なら EXIT_NO_RUNS。成功への誤変換を防ぐ。"""
        assert _run_wait(lambda _s: [], registration_grace=5.0, timeout=900.0) == wait_ci.EXIT_NO_RUNS

    def test_timeout_before_grace_elapses(self):
        assert _run_wait(lambda _s: [], registration_grace=100.0, timeout=1.0) == wait_ci.EXIT_TIMEOUT

    def test_failed_job_ends_registration_grace_immediately(self):
        assert (
            _run_wait(
                lambda _s: [_run(status="in_progress", conclusion=None)],
                registration_grace=100.0,
                job_list_fn=lambda _r: [_job(conclusion="failure")],
            )
            == wait_ci.EXIT_CI_FAILED
        )


class TestGithubCiConfiguration:
    """GitHub tree応答によるCI定義不在の即時判定と後退経路。"""

    @pytest.mark.parametrize(
        "payload",
        [
            {"truncated": False, "tree": []},
            {"truncated": False, "tree": [{"type": "tree", "path": ".github/workflows"}]},
        ],
    )
    def test_no_workflow_blob_is_confirmed_absent(self, monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            assert command == [
                "gh",
                "api",
                "-X",
                "GET",
                "repos/owner/repository/git/trees/sha1?recursive=1",
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is False

    def test_workflow_blob_is_confirmed_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {"truncated": False, "tree": [{"type": "blob", "path": ".github/workflows/test.yml"}]}
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b""),
        )
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/README.md",
            ".github/workflows/archive/disabled.yml",
            ".github/workflows/archive/disabled.yaml",
        ],
    )
    def test_non_workflow_blob_is_confirmed_absent(self, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
        payload = {"truncated": False, "tree": [{"type": "blob", "path": path}]}
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b""),
        )
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is False

    @pytest.mark.parametrize(
        "response",
        [
            {"truncated": True, "tree": []},
            b"not-json",
        ],
    )
    def test_uncertain_tree_response_falls_back(self, monkeypatch: pytest.MonkeyPatch, response: object) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = response if isinstance(response, bytes) else json.dumps(response).encode()
            return subprocess.CompletedProcess(command, 0, stdout, b"")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is None

    @pytest.mark.parametrize("status", [403, 404, 422])
    def test_tree_cli_failure_falls_back_to_registration_grace(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
    ) -> None:
        """tree取得のCLI失敗時はrun登録猶予へ後退する。"""

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if command[1:2] == ["api"]:
                return subprocess.CompletedProcess(command, 1, b"", f"HTTP {status}".encode())
            return subprocess.CompletedProcess(command, 0, b"[]", b"")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        assert (
            wait_ci.wait_for_ci(
                "sha1",
                10.0,
                1.0,
                0.0,
                False,
                10.0,
                repository="owner/repository",
                ref="refs/heads/main",
                source_ref="HEAD",
                baseline_ids=frozenset(),
                now_fn=lambda: 0.0,
                sleep_fn=lambda _seconds: None,
            )
            == wait_ci.EXIT_NO_RUNS
        )

    def test_non_github_forge_skips_tree_query_and_uses_existing_wait(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GitHub以外ではtreeを照会せず既存のrun登録猶予を使う。"""
        monkeypatch.setattr(
            wait_ci,
            "_github_ci_configured",
            lambda *_args: (_ for _ in ()).throw(AssertionError("GitHub treeを照会しないこと")),
        )
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, b"[]", b""),
        )
        assert (
            wait_ci.wait_for_ci(
                "sha1",
                10.0,
                1.0,
                0.0,
                False,
                10.0,
                repository="group/repository",
                ref="refs/heads/main",
                source_ref="HEAD",
                baseline_ids=frozenset(),
                forge="gitlab",
                now_fn=lambda: 0.0,
                sleep_fn=lambda _seconds: None,
            )
            == wait_ci.EXIT_NO_RUNS
        )

    def test_no_definition_exits_before_run_listing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(wait_ci, "_github_ci_configured", lambda *_args: False)
        result = wait_ci.wait_for_ci(
            "sha1",
            10.0,
            1.0,
            0.0,
            False,
            10.0,
            repository="owner/repository",
            ref="refs/heads/main",
            source_ref="HEAD",
            baseline_ids=frozenset(),
            now_fn=lambda: 0.0,
            sleep_fn=lambda _seconds: None,
        )
        assert result == wait_ci.EXIT_NO_CI_CONFIG


class TestPushIdentityDifferential:
    """push前baselineに存在しない実行IDだけを判定対象とする。"""

    def test_old_success_is_ignored_while_new_run_progresses_to_success(self):
        old = _run(name="old", db_id=1)
        calls = {"n": 0}
        job_run_ids: list[int] = []

        def run_list(_sha):
            calls["n"] += 1
            new = _run(
                name="new",
                db_id=2,
                status="in_progress" if calls["n"] == 1 else "completed",
                conclusion=None if calls["n"] == 1 else "success",
            )
            return [old, new]

        def job_list(run):
            job_run_ids.append(run["databaseId"])
            return []

        result = _run_wait(
            run_list,
            registration_grace=0.0,
            baseline_ids=frozenset({1}),
            job_list_fn=job_list,
        )

        assert result == wait_ci.EXIT_SUCCESS
        assert job_run_ids == [2, 2]

    def test_old_failure_does_not_fail_new_success(self):
        runs = [_run(name="old", db_id=1, conclusion="failure"), _run(name="new", db_id=2)]
        assert _run_wait(lambda _sha: runs, registration_grace=0.0, baseline_ids=frozenset({1})) == wait_ci.EXIT_SUCCESS

    def test_baseline_only_returns_no_runs_without_fetching_old_jobs(self):
        def unexpected_job_fetch(_run_record):
            raise AssertionError("baseline runのジョブを取得してはならない")

        assert (
            _run_wait(
                lambda _sha: [_run(db_id=1)],
                registration_grace=0.0,
                baseline_ids=frozenset({1}),
                job_list_fn=unexpected_job_fetch,
            )
            == wait_ci.EXIT_NO_RUNS
        )


class TestPollingCompletion:
    def test_polls_until_all_completed(self):
        state = {"n": 0}

        def run_list_fn(_s):
            state["n"] += 1
            if state["n"] < 3:
                return [_run(status="in_progress", conclusion=None)]
            return [_run()]

        assert _run_wait(run_list_fn) == wait_ci.EXIT_SUCCESS

    def test_timeout_while_polling_incomplete(self):
        runs = [_run(status="in_progress", conclusion=None)]
        assert _run_wait(lambda _s: runs, timeout=1.0, registration_grace=0.0) == wait_ci.EXIT_TIMEOUT

    def test_failed_job_ends_completion_wait(self):
        run = _run(status="in_progress", conclusion=None)
        assert (
            _run_wait(
                lambda _s: [run],
                registration_grace=0.0,
                job_list_fn=lambda _r: [_job(conclusion="failure")],
            )
            == wait_ci.EXIT_CI_FAILED
        )

    def test_successful_generated_jobs_do_not_finish_in_progress_dag(self):
        calls = {"n": 0}

        def run_list_fn(_s):
            calls["n"] += 1
            if calls["n"] < 4:
                return [_run(status="in_progress", conclusion=None)]
            return [_run()]

        assert _run_wait(run_list_fn, registration_grace=0.0, job_list_fn=lambda _r: [_job()]) == wait_ci.EXIT_SUCCESS
        assert calls["n"] >= 4

    def test_job_failure_from_run_registered_after_grace_is_detected(self):
        """猶予後に登録されたrunも対象shaの実行であるため待機対象へ加え、その失敗を検出する。"""
        calls = {"n": 0}
        fetched_job_run_ids: list[int] = []

        def run_list_fn(_s):
            calls["n"] += 1
            expected = _run(name="expected", db_id=1)
            late = _run(name="late", status="in_progress", conclusion=None, db_id=2)
            return [expected] if calls["n"] == 1 else [expected, late]

        def job_list_fn(run):
            fetched_job_run_ids.append(run["databaseId"])
            if run["databaseId"] == 2:
                return [_job(conclusion="failure")]
            return []

        assert _run_wait(run_list_fn, registration_grace=0.0, job_list_fn=job_list_fn) == wait_ci.EXIT_CI_FAILED
        assert 2 in fetched_job_run_ids

    def test_run_registered_after_grace_is_waited_until_completion(self):
        """猶予後に登録されたrunが未完了の間は成功と判定せず、完了まで待つ。"""
        calls = {"n": 0}

        def run_list_fn(_s):
            calls["n"] += 1
            expected = _run(name="expected", db_id=1)
            if calls["n"] == 1:
                return [expected]
            late_status = "completed" if calls["n"] >= 4 else "in_progress"
            late_conclusion = "success" if calls["n"] >= 4 else None
            return [expected, _run(name="late", status=late_status, conclusion=late_conclusion, db_id=2)]

        assert _run_wait(run_list_fn, registration_grace=0.0) == wait_ci.EXIT_SUCCESS
        assert calls["n"] >= 4


class TestGhErrorHandling:
    def test_consecutive_gh_failures_return_gh_error(self):
        def run_list_fn(_s):
            raise wait_ci.RunListError("mock failure")

        assert _run_wait(run_list_fn) == wait_ci.EXIT_GH_ERROR

    def test_intermittent_failure_recovers(self):
        state = {"n": 0}

        def run_list_fn(_s):
            state["n"] += 1
            if state["n"] == 1:
                raise wait_ci.RunListError("transient")
            return [_run()]

        assert _run_wait(run_list_fn) == wait_ci.EXIT_SUCCESS

    def test_intermittent_job_failure_recovers(self):
        calls = {"n": 0}

        def job_list_fn(_r):
            calls["n"] += 1
            if calls["n"] == 1:
                raise wait_ci.RunListError("transient jobs failure")
            return []

        assert _run_wait(lambda _s: [_run()], job_list_fn=job_list_fn) == wait_ci.EXIT_SUCCESS

    def test_three_consecutive_job_failures_return_gh_error(self):
        def job_list_fn(_r):
            raise wait_ci.RunListError("jobs failure")

        assert _run_wait(lambda _s: [_run()], job_list_fn=job_list_fn) == wait_ci.EXIT_GH_ERROR

    def test_partial_multi_run_snapshot_does_not_reset_failure_counter(self):
        runs = [_run(db_id=1), _run(db_id=2)]
        calls = {1: 0, 2: 0}

        def job_list_fn(run):
            run_id = run["databaseId"]
            calls[run_id] += 1
            if run_id == 2:
                raise wait_ci.RunListError("second run jobs failure")
            return []

        assert _run_wait(lambda _s: runs, job_list_fn=job_list_fn) == wait_ci.EXIT_GH_ERROR
        assert calls == {1: 3, 2: 3}

    def test_registration_grace_ending_on_job_failure_returns_gh_error(self):
        def job_list_fn(_r):
            raise wait_ci.RunListError("jobs failure")

        assert (
            _run_wait(
                lambda _s: [_run()],
                registration_grace=0.0,
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_GH_ERROR
        )

    def test_polling_timeout_with_non_consecutive_job_failures(self):
        calls = {"n": 0}

        def job_list_fn(_r):
            calls["n"] += 1
            if calls["n"] % 2:
                raise wait_ci.RunListError("intermittent")
            return []

        assert (
            _run_wait(
                lambda _s: [_run(status="in_progress", conclusion=None)],
                registration_grace=3.0,
                timeout=8.0,
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_TIMEOUT
        )


class TestUnifiedCompletionLoop:
    """主SHA・後続SHAが共有する完了待機ループを同一条件で検証する。"""

    @pytest.mark.parametrize("follow_mode", [False, True])
    def test_snapshot_failure_recovers(self, follow_mode: bool) -> None:
        """一時的な取得失敗後の成功を両経路で受理する。"""
        calls = 0

        def fetch() -> tuple[list[wait_ci.RunRecord], list[wait_ci.JobRecord]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise wait_ci.RunListError("transient completion failure")
            return [_run()], []

        result, runs, _ = wait_ci._wait_for_completion(  # pylint: disable=protected-access  # noqa: SLF001
            start=0.0,
            timeout=10.0,
            poll_interval=1.0,
            expected_ids={1},
            fetch_fn=fetch,
            select_fn=lambda candidates: candidates,
            forge="github",
            sleep_fn=lambda _seconds: None,
            now_fn=lambda: float(calls),
            consecutive_failures=0,
            follow_mode=follow_mode,
        )

        assert result == wait_ci.EXIT_SUCCESS
        assert runs == [_run()]

    @pytest.mark.parametrize("follow_mode", [False, True])
    def test_three_snapshot_failures_return_gh_error(self, follow_mode: bool) -> None:
        """3回連続取得失敗を両経路で同じ終了コードへ変換する。"""

        def fetch() -> tuple[list[wait_ci.RunRecord], list[wait_ci.JobRecord]]:
            raise wait_ci.RunListError("completion failure")

        result, runs, _ = wait_ci._wait_for_completion(  # pylint: disable=protected-access  # noqa: SLF001
            start=0.0,
            timeout=10.0,
            poll_interval=1.0,
            expected_ids={1},
            fetch_fn=fetch,
            select_fn=lambda candidates: candidates,
            forge="github",
            sleep_fn=lambda _seconds: None,
            now_fn=lambda: 0.0,
            consecutive_failures=0,
            follow_mode=follow_mode,
        )

        assert result == wait_ci.EXIT_GH_ERROR
        assert not runs


class TestFollowCancelled:
    def _run_list_dispatch(self, cancelled_runs, follow_map):
        """sha別にrun一覧を返すスタブ。cancelled_runs=元sha, follow_map={後続sha: [run]}。"""

        def _fn(sha):
            if sha == "sha1":
                return cancelled_runs
            return follow_map.get(sha, [])

        return _fn

    def test_failed_job_ends_follow_registration_grace(self):
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(status="in_progress", conclusion=None, db_id=2, head_sha="sha2")]

        def job_list_fn(run):
            if run["databaseId"] == 2:
                return [_job(conclusion="failure")]
            return []

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                registration_grace=100.0,
                follow_shas_fn=lambda _b: ["sha2"],
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_CI_FAILED
        )

    def test_failed_job_ends_follow_completion_wait(self):
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(status="in_progress", conclusion=None, db_id=2, head_sha="sha2")]
        calls = {"n": 0}

        def job_list_fn(run):
            if run["databaseId"] != 2:
                return []
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return [_job(conclusion="failure")]

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: ["sha2"],
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_CI_FAILED
        )

    def test_follow_job_failures_retry_three_times(self):
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(db_id=2, head_sha="sha2")]

        def job_list_fn(run):
            if run["databaseId"] == 2:
                raise wait_ci.RunListError("follow jobs failure")
            return []

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                registration_grace=100.0,
                follow_shas_fn=lambda _b: ["sha2"],
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_GH_ERROR
        )

    def test_follow_registration_grace_ending_on_job_failure_returns_gh_error(self):
        """後続SHA登録猶予末の直近ジョブ取得失敗は、3回連続失敗ではなく1回の呼び出しのみで即時GH_ERRORとする。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(db_id=2, head_sha="sha2")]
        calls = {"n": 0}

        def job_list_fn(run):
            if run["databaseId"] != 2:
                return []
            calls["n"] += 1
            raise wait_ci.RunListError("follow jobs failure")

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: ["sha2"],
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_GH_ERROR
        )
        assert calls["n"] == 1

    def test_follow_completion_polling_timeout_with_non_consecutive_job_failures(self):
        """後続run完了待ちで断続的なジョブ取得失敗（連続3回未満）が発生してもタイムアウトへ至る。

        `registration_grace=0.0`でも登録猶予フェーズは1回のスナップショット取得を経由するため、
        `calls["n"] == 1`（猶予フェーズの初回取得）は成功させ、以降の完了待ちフェーズでのみ断続的に失敗させる。
        """
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(status="in_progress", conclusion=None, db_id=2, head_sha="sha2")]
        calls = {"n": 0}

        def job_list_fn(run):
            if run["databaseId"] != 2:
                return []
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            if calls["n"] % 2 == 0:
                raise wait_ci.RunListError("intermittent follow jobs failure")
            return []

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                registration_grace=0.0,
                timeout=8.0,
                follow_shas_fn=lambda _b: ["sha2"],
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_TIMEOUT
        )

    def test_follow_completion_partial_multi_run_snapshot_does_not_reset_failure_counter(self):
        """後続run完了待ちで、複数runのうち1runのジョブ取得だけが3poll連続失敗しても、
        別runの取得成功でカウンターがリセットされない。

        両runを同一後続SHA上に配置し、`run_list_fn`が返すリスト順（wf2が先）で
        `job_list_fn`の呼び出し順を決定的にする。登録猶予フェーズの1回のスナップショット
        取得でwf2・wf3の双方が呼ばれるため、最初の2呼び出しは猶予フェーズとして成功させ、
        完了待ちフェーズに入ってからwf3のみ失敗させる。
        """
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [
            _run(name="wf2", db_id=2, head_sha="sha2"),
            _run(name="wf3", db_id=3, head_sha="sha2"),
        ]
        total_calls = {"n": 0}
        job_calls = {2: 0, 3: 0}

        def job_list_fn(run):
            run_id = run["databaseId"]
            if run_id not in (2, 3):
                # 元shaのcancelled run（db_id=1）に対する呼び出しはカウント対象外とする
                return []
            total_calls["n"] += 1
            if total_calls["n"] <= 2:
                return []
            job_calls[run_id] += 1
            if run_id == 3:
                raise wait_ci.RunListError("wf3 jobs failure")
            return []

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: ["sha2"],
                job_list_fn=job_list_fn,
            )
            == wait_ci.EXIT_GH_ERROR
        )
        assert job_calls[3] == 3
        assert job_calls[2] >= 1

    def test_follow_cancelled_returns_success_when_follow_succeeds(self):
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(conclusion="success", db_id=2, head_sha="sha2")]
        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                follow_shas_fn=lambda _b: ["sha2"],
            )
            == wait_ci.EXIT_SUCCESS
        )

    def test_all_cancelled_without_flag_returns_ci_failed(self):
        cancelled = [_run(conclusion="cancelled")]
        assert _run_wait(lambda _s: cancelled) == wait_ci.EXIT_CI_FAILED

    def test_follow_cancelled_returns_ci_failed_when_follow_fails(self):
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(conclusion="failure", db_id=2, head_sha="sha2")]
        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                follow_shas_fn=lambda _b: ["sha2"],
            )
            == wait_ci.EXIT_CI_FAILED
        )

    def test_follow_cancelled_rejects_non_ancestor_sha(self):
        cancelled = [_run(conclusion="cancelled")]
        assert (
            _run_wait(
                lambda _s: cancelled,
                follow_cancelled=True,
                ancestor_check_fn=lambda _a: False,
            )
            == wait_ci.EXIT_GH_ERROR
        )

    def test_no_follow_commit_generated_times_out(self):
        """後続コミットが生成されないまま`remaining_timeout`が経過した場合。"""
        cancelled = [_run(conclusion="cancelled")]
        assert (
            _run_wait(
                lambda _s: cancelled,
                follow_cancelled=True,
                timeout=3.0,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: [],
            )
            == wait_ci.EXIT_TIMEOUT
        )

    def test_follow_commit_exists_but_no_run_registered_times_out(self):
        """後続コミットは検出済みだが後続run自体が未登録のまま経過した場合。"""
        cancelled = [_run(conclusion="cancelled")]
        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {}),
                follow_cancelled=True,
                timeout=3.0,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: ["sha2"],
            )
            == wait_ci.EXIT_TIMEOUT
        )

    def test_delayed_follow_commit_registration_still_succeeds(self):
        """後続コミットの検出が数回遅延しても、検出後は正常に追跡へ移行する。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(conclusion="success", db_id=2, head_sha="sha2")]
        calls = {"n": 0}

        def delayed_follow_shas(_b):
            calls["n"] += 1
            return ["sha2"] if calls["n"] >= 3 else []

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                registration_grace=0.0,
                follow_shas_fn=delayed_follow_shas,
            )
            == wait_ci.EXIT_SUCCESS
        )

    def test_staged_follow_sha_registration_waits_for_grace(self):
        """複数の後続SHAが登録猶予期間内に段階的に出現しても全件を追跡対象へ含める。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow_sha2 = [_run(name="wf2", conclusion="success", db_id=2, head_sha="sha2")]
        follow_sha3 = [_run(name="wf3", conclusion="success", db_id=3, head_sha="sha3")]
        calls = {"n": 0}

        def staged_follow_shas(_b):
            calls["n"] += 1
            return ["sha2", "sha3"] if calls["n"] >= 2 else ["sha2"]

        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow_sha2, "sha3": follow_sha3}),
                follow_cancelled=True,
                registration_grace=5.0,
                follow_shas_fn=staged_follow_shas,
            )
            == wait_ci.EXIT_SUCCESS
        )

    def test_multiple_workflows_register_progressively_on_same_follow_sha(self):
        """同一後続SHA上で複数workflowのrunが段階的に登録されても全件完了まで待つ。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        calls = {"n": 0}

        def staged_run_list(sha):
            if sha == "sha1":
                return cancelled
            calls["n"] += 1
            wf1 = _run(name="wf1", conclusion="success", db_id=2, head_sha="sha2")
            wf2_pending = _run(name="wf2", conclusion=None, status="in_progress", db_id=3, head_sha="sha2")
            wf2_done = _run(name="wf2", conclusion="success", db_id=3, head_sha="sha2")
            if calls["n"] == 1:
                return [wf1]  # wf2はまだ登録前
            if calls["n"] == 2:
                return [wf1, wf2_pending]  # wf2が新規登録され未完了
            return [wf1, wf2_done]

        assert (
            _run_wait(
                staged_run_list,
                follow_cancelled=True,
                registration_grace=2.0,
                follow_shas_fn=lambda _b: ["sha2"],
            )
            == wait_ci.EXIT_SUCCESS
        )
        assert calls["n"] >= 3

    def test_follow_phase_incomplete_run_times_out(self):
        """後続runが未完了のまま`remaining_timeout`が経過した場合。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(conclusion=None, status="in_progress", db_id=2, head_sha="sha2")]
        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow}),
                follow_cancelled=True,
                timeout=3.0,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: ["sha2"],
            )
            == wait_ci.EXIT_TIMEOUT
        )

    def test_follow_run_fetch_error_returns_gh_error(self):
        """後続SHAのrun取得で`gh`呼び出しが失敗した場合。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]

        def failing_run_list(sha):
            if sha == "sha1":
                return cancelled
            raise wait_ci.RunListError("mock follow failure")

        assert (
            _run_wait(
                failing_run_list,
                follow_cancelled=True,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: ["sha2"],
            )
            == wait_ci.EXIT_GH_ERROR
        )

    def test_multiple_follow_shas_all_must_succeed(self):
        """複数の後続SHAに跨るrunが全て成功して初めてEXIT_SUCCESSとなる。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow_sha2 = [_run(name="wf2", conclusion="success", db_id=2, head_sha="sha2")]
        follow_sha3 = [_run(name="wf3", conclusion="success", db_id=3, head_sha="sha3")]
        assert (
            _run_wait(
                self._run_list_dispatch(cancelled, {"sha2": follow_sha2, "sha3": follow_sha3}),
                follow_cancelled=True,
                registration_grace=0.0,
                follow_shas_fn=lambda _b: ["sha2", "sha3"],
            )
            == wait_ci.EXIT_SUCCESS
        )


class TestExplicitFollowSourceRef:
    """cancelled後のローカル探索がdestination refではなくsource refを使うことを確認する。"""

    def test_ancestor_check_uses_explicit_source_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        assert _is_ancestor_of_ref("base", "HEAD", 1.0) is True
        assert calls == [["git", "merge-base", "--is-ancestor", "base", "HEAD"]]

    def test_follow_sha_query_uses_explicit_source_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="next\n", stderr="")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        assert _follow_shas("base", "HEAD", 1.0) == ["next"]
        assert calls == [["git", "log", "base..HEAD", "--format=%H"]]

    def test_renamed_push_uses_local_source_ref_when_destination_ref_is_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        local_refs: list[tuple[str, str]] = []

        def ancestor_check(ancestor: str, ref: str, _timeout: float) -> bool:
            local_refs.append(("ancestor", ref))
            return ancestor == "sha1"

        def follow_shas(base: str, ref: str, _timeout: float) -> list[str]:
            local_refs.append(("follow", ref))
            return ["sha2"] if base == "sha1" else []

        def run_list(sha: str):
            if sha == "sha1":
                return [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
            return [_run(conclusion="success", db_id=2, head_sha="sha2")]

        monkeypatch.setattr(wait_ci, _is_ancestor_of_ref.__name__, ancestor_check)
        monkeypatch.setattr(wait_ci, _follow_shas.__name__, follow_shas)
        times = iter(t * 1.0 for t in range(100))
        result = wait_ci.wait_for_ci(
            "sha1",
            900.0,
            20.0,
            0.0,
            True,
            10.0,
            repository="owner/repository",
            ref="refs/heads/release",
            source_ref="HEAD",
            baseline_ids=frozenset(),
            sleep_fn=lambda _seconds: None,
            now_fn=lambda: next(times),
            run_list_fn=run_list,
            job_list_fn=lambda _run_record: [],
        )

        assert result == wait_ci.EXIT_SUCCESS
        assert local_refs == [("ancestor", "HEAD"), ("follow", "HEAD")]


class TestSignalHandling:
    """実プロセスへシグナルを送信し、`_install_signal_handlers`の実挙動を確認する。"""

    @pytest.mark.skipif(sys.platform == "win32", reason="WindowsのSIGTERMはPythonハンドラを呼び出さない")
    def test_sigterm_exits_with_interrupted_code(self, tmp_path: pathlib.Path, git_repository: tuple[pathlib.Path, str]):
        """SIGTERM受信時に`EXIT_INTERRUPTED`を返すことを実プロセスで確認する。

        ハンドラ登録は`main`冒頭で行われるため、送信までの待機が起動所要時間を下回ると
        既定動作で終了し`-SIGTERM`が返る。待機時間を延ばしながら最大3回試行し、
        起動が遅い実行環境でも登録後の挙動を判定できるようにする。

        併せてstderrへ`reentrant call`が出ないことを確認する。ハンドラが`print`で
        書くと`sys.stderr`の`BufferedWriter`へ再入し得るため、その回帰を検出する。

        子プロセスは`--sha`を`git rev-parse`で解決するため、テスト用repositoryを作業ディレクトリに
        与える。共通fixtureがgitのglobal・system設定を遮断するので、本リポジトリの作業ツリーを
        使うと所有者が異なる実行環境で解決に失敗する。
        """
        repository, baseline_sha = git_repository
        baseline_path = tmp_path / "baseline.json"
        _write_test_baseline(baseline_path, sha=baseline_sha)
        arguments = [
            "--baseline",
            str(baseline_path),
            "--forge=github",
            "--repo=owner/repository",
            "--ref=refs/heads/main",
            "--source-ref=HEAD",
            f"--sha={baseline_sha}",
            "--poll-interval=5",
            "--registration-grace=30",
            "--timeout=60",
            "--subprocess-timeout=2",
        ]
        child_code = (
            "import time, wait_ci\n"
            "wait_ci._gh_run_list = lambda *_args, **_kwargs: time.sleep(60) or []\n"
            f"raise SystemExit(wait_ci.main({arguments!r}))\n"
        )
        returncode = None
        for warmup_sec in (1.0, 3.0, 6.0):
            with subprocess.Popen(
                [sys.executable, "-c", child_code],
                cwd=repository,
                env={**os.environ, "PYTHONPATH": str(pathlib.Path(__file__).parent)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as proc:
                stderr_text = ""
                try:
                    time.sleep(warmup_sec)
                    proc.send_signal(signal.SIGTERM)
                    _, stderr_text = proc.communicate(timeout=15)
                    returncode = proc.returncode
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
            assert "reentrant call" not in stderr_text, f"シグナルハンドラが再入した: {stderr_text}"
            if returncode == wait_ci.EXIT_INTERRUPTED:
                break
            assert returncode == -signal.SIGTERM, f"想定外の終了コード: {returncode}"
        assert returncode == wait_ci.EXIT_INTERRUPTED


class TestMainEntrypoint:
    """公開エントリ`main`経由の引数解析・SHA解決・シナリオ確認。"""

    def test_sha_resolution_failure_returns_gh_error(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="", returncode=1, stderr="")):
            assert wait_ci.main(_main_args(baseline) + ["--timeout", "1"]) == wait_ci.EXIT_GH_ERROR

    def test_explicit_sha_success_path(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd, **_kwargs):
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return mock.Mock(stdout="a" * 40, returncode=0, stderr="")
            if cmd[:3] == ["gh", "run", "list"]:
                return mock.Mock(stdout=json.dumps([_run()]).encode(), returncode=0, stderr=b"")
            if cmd[:2] == ["gh", "api"]:
                return mock.Mock(stdout=b"[]", returncode=0, stderr=b"")
            raise AssertionError(cmd)

        with mock.patch("subprocess.run", side_effect=fake_run):
            assert wait_ci.main(_main_args(baseline) + ["--registration-grace", "0"]) == wait_ci.EXIT_SUCCESS

    def test_subprocess_timeout_surfaces_as_gh_error(self, tmp_path: pathlib.Path):
        """内部subprocess呼び出しのタイムアウトが`main`経由でGH_ERRORに現れる。"""
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 10.0)):
            assert wait_ci.main(_main_args(baseline) + ["--registration-grace", "0", "--timeout", "1"]) == wait_ci.EXIT_GH_ERROR

    def test_write_baseline_records_context_and_existing_run_ids(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"

        def fake_run(cmd, **_kwargs):
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return mock.Mock(stdout=_FULL_SHA, returncode=0, stderr="")
            if cmd[:3] == ["gh", "run", "list"]:
                return mock.Mock(stdout=json.dumps([_run(db_id=11), _run(db_id=12)]).encode(), returncode=0, stderr=b"")
            raise AssertionError(cmd)

        args = [
            "--write-baseline",
            str(baseline),
            "--forge=github",
            "--repo=owner/repository",
            "--ref=refs/heads/main",
            "--source-ref=HEAD",
            "--sha=abc123",
        ]
        with mock.patch("subprocess.run", side_effect=fake_run):
            assert wait_ci.main(args) == wait_ci.EXIT_SUCCESS

        payload = json.loads(baseline.read_text(encoding="utf-8"))
        assert payload == {
            "version": 2,
            "forge": "github",
            "repository": "owner/repository",
            "ref": "refs/heads/main",
            "source_ref": "HEAD",
            "sha": _FULL_SHA,
            "run_ids": [11, 12],
        }

    @pytest.mark.parametrize("source_ref", ["source-branch", "lightweight-tag", "annotated-tag"])
    def test_write_baseline_resolves_source_ref_to_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        git_repository: tuple[pathlib.Path, str],
        source_ref: str,
    ) -> None:
        repository, commit_sha = git_repository
        baseline = tmp_path / f"{source_ref}.json"
        monkeypatch.chdir(repository)
        monkeypatch.setattr(wait_ci, "_default_run_list_fn", lambda *_args, **_kwargs: lambda _sha: [])

        result = wait_ci.main(
            [
                "--write-baseline",
                str(baseline),
                "--forge=github",
                "--repo=owner/repository",
                "--ref=refs/heads/main",
                "--source-ref",
                source_ref,
            ]
        )

        assert result == wait_ci.EXIT_SUCCESS
        assert json.loads(baseline.read_text(encoding="utf-8"))["sha"] == commit_sha

    def test_write_baseline_rejects_source_ref_that_cannot_peel_to_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: pathlib.Path,
        git_repository: tuple[pathlib.Path, str],
    ) -> None:
        repository, _commit_sha = git_repository
        baseline = tmp_path / "baseline.json"
        monkeypatch.chdir(repository)
        monkeypatch.setattr(wait_ci, "_default_run_list_fn", lambda *_args, **_kwargs: lambda _sha: [])

        result = wait_ci.main(
            [
                "--write-baseline",
                str(baseline),
                "--forge=github",
                "--repo=owner/repository",
                "--ref=refs/heads/main",
                "--source-ref=non-commit-tag",
            ]
        )

        assert result == wait_ci.EXIT_GH_ERROR
        assert not baseline.exists()
        assert "non-commit-tag" in capsys.readouterr().err

    def test_wait_uses_saved_sha_after_source_ref_advances(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        git_repository: tuple[pathlib.Path, str],
    ) -> None:
        repository, baseline_sha = git_repository
        baseline = tmp_path / "baseline.json"
        monkeypatch.chdir(repository)
        monkeypatch.setattr(wait_ci, "_default_run_list_fn", lambda *_args, **_kwargs: lambda _sha: [])
        write_args = [
            "--write-baseline",
            str(baseline),
            "--forge=github",
            "--repo=owner/repository",
            "--ref=refs/heads/main",
            "--source-ref=HEAD",
        ]
        assert wait_ci.main(write_args) == wait_ci.EXIT_SUCCESS
        (repository / "tracked.txt").write_text("second\n", encoding="utf-8")
        _git(repository, "add", "tracked.txt")
        _git(repository, "commit", "-m", "second")
        assert _git(repository, "rev-parse", "HEAD").stdout.strip() != baseline_sha
        captured: dict[str, str] = {}

        def fake_wait_for_ci(sha: str, *_args: object, **_kwargs: object) -> int:
            captured["sha"] = sha
            return wait_ci.EXIT_SUCCESS

        monkeypatch.setattr(wait_ci, "wait_for_ci", fake_wait_for_ci)
        result = wait_ci.main(_main_args(baseline, sha=None))

        assert result == wait_ci.EXIT_SUCCESS
        assert captured["sha"] == baseline_sha

    def test_explicit_sha_must_match_saved_sha(self, tmp_path: pathlib.Path) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        different_sha = "b" * 40

        def fake_run(cmd, **_kwargs):
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "different^{commit}"]
            return mock.Mock(stdout=different_sha, returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = wait_ci.main(_main_args(baseline, sha="different"))

        assert result == wait_ci.EXIT_GH_ERROR

    def test_baseline_context_mismatch_fails_before_ci_query(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd, **_kwargs):
            assert cmd[:3] == ["git", "rev-parse", "--verify"]
            return mock.Mock(stdout=_FULL_SHA, returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = wait_ci.main(_main_args(baseline) + ["--repo=different/repository"])

        assert result == wait_ci.EXIT_GH_ERROR

    def test_source_ref_mismatch_fails_before_ci_query(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline, source_ref="HEAD")

        def fake_run(cmd, **_kwargs):
            assert cmd[:3] == ["git", "rev-parse", "--verify"]
            return mock.Mock(stdout=_FULL_SHA, returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = wait_ci.main(_main_args(baseline, source_ref="refs/heads/local"))

        assert result == wait_ci.EXIT_GH_ERROR

    @pytest.mark.parametrize("flag", ["--timeout", "--poll-interval", "--registration-grace", "--subprocess-timeout"])
    @pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf"])
    def test_non_finite_numeric_args_rejected(self, flag, bad_value):
        """`nan`/`inf`/`-inf`は境界比較をすり抜けるため明示的に拒否する。"""
        with pytest.raises(SystemExit) as exc_info:
            wait_ci.main([flag, bad_value])
        assert exc_info.value.code == 2

    @pytest.mark.parametrize("flag", ["--timeout", "--poll-interval", "--subprocess-timeout"])
    def test_non_positive_args_rejected(self, flag):
        with pytest.raises(SystemExit) as exc_info:
            wait_ci.main([flag, "0"])
        assert exc_info.value.code == 2

    def test_negative_registration_grace_rejected(self):
        with pytest.raises(SystemExit) as exc_info:
            wait_ci.main(["--registration-grace", "-1"])
        assert exc_info.value.code == 2


class TestResolveShaViaMain:
    """`--sha`明示指定時も完全形式へ解決してから`wait_for_ci`へ渡す。"""

    def test_explicit_short_sha_is_resolved_to_full_form(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        captured: dict[str, object] = {}
        resolved_sha = "17561bd376c5fb8ace04153871b2a2d6993c380d"
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline, sha=resolved_sha)

        def fake_wait_for_ci(sha: str, *args: object, **kwargs: object) -> int:
            del args, kwargs
            captured["sha"] = sha
            return wait_ci.EXIT_SUCCESS

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del kwargs
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "17561bd^{commit}"]
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{resolved_sha}\n", stderr="")

        monkeypatch.setattr(wait_ci, "wait_for_ci", fake_wait_for_ci)
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = wait_ci.main(_main_args(baseline, sha="17561bd"))
        assert result == wait_ci.EXIT_SUCCESS
        assert captured["sha"] == resolved_sha

    def test_explicit_sha_resolution_failure_is_distinguished_from_no_runs(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del cmd, kwargs
            return subprocess.CompletedProcess([], returncode=128, stdout="", stderr="fatal: bad revision")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = wait_ci.main(_main_args(baseline, sha="deadbee"))
        assert result == wait_ci.EXIT_GH_ERROR
        err = capsys.readouterr().err
        assert "deadbee" in err
        assert "commit解決に失敗" in err

    def test_missing_git_returns_sha_resolution_failure(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        monkeypatch.setattr(subprocess, "run", mock.Mock(side_effect=FileNotFoundError("git")))

        result = wait_ci.main(_main_args(baseline, sha="17561bd"))

        assert result == wait_ci.EXIT_GH_ERROR
        assert "commit解決に失敗" in capsys.readouterr().err

    def test_option_like_sha_is_passed_after_end_of_options(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del kwargs
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "--not-an-option^{commit}"]
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: bad revision")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = wait_ci.main(_main_args(baseline, sha="--not-an-option"))

        assert result == wait_ci.EXIT_GH_ERROR


class TestResolveForge:
    """forge判別の分岐を検証する。"""

    def test_explicit_value_is_returned_as_is(self) -> None:
        """明示指定時はリモート照会をしない。"""
        assert _resolve_forge("gitlab", "owner/repository") == "gitlab"
        assert _resolve_forge("github", "owner/repository") == "github"

    def test_auto_detects_github_from_repository_host(self) -> None:
        """ホスト名のラベルがgithubと一致する場合はgithubと判定する（SSH短縮形式）。"""
        assert _resolve_forge("auto", "git@github.com:o/r.git") == "github"

    def test_auto_detects_github_enterprise_host(self) -> None:
        """GitHub Enterprise Serverの標準的なホスト名もgithubと判定する。"""
        assert _resolve_forge("auto", "https://github.example.com/o/r.git") == "github"

    def test_auto_detects_gitlab_for_self_hosted_host(self) -> None:
        """ホスト名のラベルがgitlabと一致する私設ホストはgitlabと判定する（サブグループ付きHTTPS形式）。"""
        assert _resolve_forge("auto", "https://gitlab.example.com/group/sub/repo.git") == "gitlab"

    def test_auto_detects_gitlab_from_ssh_uri(self) -> None:
        """ポート付きSSH URI形式からもホスト名を抽出する。"""
        assert _resolve_forge("auto", "ssh://git@gitlab.example.com:2222/group/repo.git") == "gitlab"

    def test_auto_does_not_match_host_label_by_substring(self) -> None:
        """ラベルの部分一致では判定せず、無関係なホストを誤分類しない。"""
        assert _resolve_forge("auto", "https://notgithub.example.com/o/r.git") is None

    @pytest.mark.parametrize("repository", ["owner/repository", "git@git.example.com:o/r.git", "invalid"])
    def test_auto_returns_none_without_recognized_host(self, repository: str) -> None:
        assert _resolve_forge("auto", repository) is None


class TestNormalizeGitlabPipeline:
    """GitLabパイプラインの正規化を検証する。"""

    def test_success_maps_to_completed_success(self) -> None:
        """successは完了かつconclusion=successへ写像される。"""
        record = _normalize_gitlab_pipeline({"id": 7, "status": "success", "name": "build", "sha": "abc"})
        assert record["status"] == "completed"
        assert record["conclusion"] == "success"
        assert record["databaseId"] == 7
        assert record["headSha"] == "abc"

    def test_canceled_spelling_is_converted(self) -> None:
        """GitLabのcanceledをGitHub綴りのcancelledへ変換する。"""
        record = _normalize_gitlab_pipeline({"id": 8, "status": "canceled"})
        assert record["conclusion"] == "cancelled"
        assert _all_cancelled([record]) is True

    def test_manual_is_completed_and_not_success(self) -> None:
        """manualは完了かつ非成功として扱う。"""
        record = _normalize_gitlab_pipeline({"id": 9, "status": "manual"})
        assert record["status"] == "completed"
        assert _all_success([record]) is False

    def test_pending_statuses_are_incomplete(self) -> None:
        """進行中系ステータスは未完了として扱う。"""
        for status in ("created", "pending", "running", "canceling", "preparing", "scheduled"):
            record = _normalize_gitlab_pipeline({"id": 1, "status": status})
            assert record["status"] != "completed", status

    def test_empty_name_falls_back_to_identifier(self) -> None:
        """nameが空の場合は識別子を含む代替表示にする。"""
        record = _normalize_gitlab_pipeline({"id": 42, "status": "success", "name": ""})
        assert "42" in str(record["name"])

    def test_unknown_status_is_completed_and_not_success(self) -> None:
        """未知のステータスは完了かつ非成功として扱う。"""
        record = _normalize_gitlab_pipeline({"id": 5, "status": "unknown_future_state"})
        assert record["status"] == "completed"
        assert _all_success([record]) is False


class TestRunJsonCommand:
    """forge CLIのJSON出力をbytes境界でUTF-8厳格復号する。"""

    def test_decodes_utf8_from_binary_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CP932ロケールでもbytes境界からUTF-8の非ASCII JSONを取得する。"""
        code = (
            "import json, sys; "
            "value = chr(0x65e5) + chr(0x672c) + chr(0x8a9e) + chr(0x2014); "
            "sys.stdout.buffer.write(json.dumps({'name': value}, ensure_ascii=False).encode('utf-8'))"
        )
        original_run = subprocess.run
        monkeypatch.setattr(locale, "getencoding", lambda: "cp932")

        def run_binary(
            command: list[str],
            *,
            capture_output: bool,
            check: bool,
            timeout: float,
        ) -> subprocess.CompletedProcess[bytes]:
            return original_run(
                command,
                capture_output=capture_output,
                check=check,
                timeout=timeout,
            )

        monkeypatch.setattr(wait_ci.subprocess, "run", run_binary)

        payload = _run_json_command([sys.executable, "-c", code], 5.0, "test command")

        assert payload == {"name": "日本語—"}

    def test_non_utf8_output_raises_run_list_error(self) -> None:
        """不正UTF-8をJSON型エラーへ変質させず境界エラーとして返す。"""
        code = "import sys; sys.stdout.buffer.write(bytes([0xff]))"

        with pytest.raises(wait_ci.RunListError, match="UTF-8") as exc_info:
            _run_json_command([sys.executable, "-c", code], 5.0, "test command")

        assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)


class TestGhRunList:
    """GitHub一覧取得がrepository・ref・SHAの全てを明示することを確認する。"""

    @pytest.mark.parametrize(
        ("repository", "ref", "short_ref"),
        [
            ("owner/first", "refs/heads/main", "main"),
            ("owner/second", "refs/heads/release/v2", "release/v2"),
        ],
    )
    def test_command_targets_requested_repository_and_ref(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repository: str,
        ref: str,
        short_ref: str,
    ) -> None:
        calls: list[tuple[list[str], float, str]] = []

        def fake_run_json(command: list[str], timeout: float, description: str) -> list[object]:
            calls.append((command, timeout, description))
            return []

        monkeypatch.setattr(wait_ci, "_run_forge_json_command", fake_run_json)
        assert _gh_run_list(repository, ref, "deadbeef", 1.0) == []
        command, timeout, description = calls[0]
        assert command[command.index("--repo") + 1] == repository
        assert command[command.index("--branch") + 1] == short_ref
        assert command[command.index("--commit") + 1] == "deadbeef"
        assert timeout == 1.0
        assert description == "gh run list"


class TestGlabPipelineList:
    """glab呼び出しと応答検証を確認する。"""

    def test_command_uses_sha_and_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """glab ci listへSHAとJSON出力指定を渡す。"""
        calls: list[tuple[list[str], float, str]] = []

        def fake_run_json(command: list[str], timeout: float, description: str) -> list[dict[str, object]]:
            calls.append((command, timeout, description))
            return [{"id": 1, "status": "success"}]

        monkeypatch.setattr(wait_ci, "_run_forge_json_command", fake_run_json)
        records = _glab_pipeline_list("group/repository", "refs/heads/main", "deadbeef", 1.0)
        command, timeout, description = calls[0]
        assert command[:3] == ["glab", "ci", "list"]
        assert command[command.index("--repo") + 1] == "group/repository"
        assert command[command.index("--ref") + 1] == "main"
        assert "--sha" in command and "deadbeef" in command
        assert command[command.index("-F") + 1] == "json"
        assert timeout == 1.0
        assert description == "glab ci list"
        assert records[0]["conclusion"] == "success"

    def test_non_zero_exit_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非ゼロ終了はRunListErrorとして扱う。"""
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom"),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_pipeline_list("group/repository", "refs/heads/main", "deadbeef", 1.0)

    def test_unexpected_shape_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """配列以外の応答はRunListErrorとして扱う。"""
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=b'{"message":"x"}', stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_pipeline_list("group/repository", "refs/heads/main", "deadbeef", 1.0)


class TestGhJobList:
    def test_fetches_latest_jobs_from_all_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []
        payload = [
            {"jobs": [{"id": 11, "name": "build", "status": "completed", "conclusion": "success", "html_url": "u1"}]},
            {"jobs": [{"id": 12, "name": "test", "status": "completed", "conclusion": "failure", "html_url": "u2"}]},
        ]

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload).encode(), stderr=b"")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        jobs = _gh_job_list("owner/repository", _run(db_id=7), 1.0)
        command = calls[0]
        assert "repos/owner/repository/actions/runs/7/jobs?filter=latest&per_page=100" in command
        assert "--paginate" in command
        assert "--slurp" in command
        assert "--jq" not in command
        assert [job["databaseId"] for job in jobs] == [11, 12]

    def test_custom_host_and_repository_are_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=b"[]", stderr=b"")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        assert not _gh_job_list("https://github.example.com/owner/repository.git", _run(db_id=7), 1.0)
        assert "repos/owner/repository/actions/runs/7/jobs?filter=latest&per_page=100" in calls[0]
        assert calls[0][calls[0].index("--hostname") + 1] == "github.example.com"

    @pytest.mark.parametrize(
        "payload",
        ["{}", '[{"jobs":{}}]', '[{"other":[]}]', '[{"jobs":[1]}]', "not-json"],
    )
    def test_invalid_response_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=payload.encode(), stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", _run(), 1.0)

    def test_invalid_run_id_raises_run_list_error(self) -> None:
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", {"databaseId": None}, 1.0)

    @pytest.mark.parametrize(
        "side_effect",
        [subprocess.TimeoutExpired("gh", 1.0), FileNotFoundError("gh")],
    )
    def test_command_exception_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch, side_effect: Exception) -> None:
        monkeypatch.setattr(wait_ci.subprocess, "run", mock.Mock(side_effect=side_effect))
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", _run(), 1.0)

    def test_non_zero_exit_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom"),
        )
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", _run(), 1.0)


class TestGlabJobList:
    def test_fetches_all_non_retried_jobs_without_fixed_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []
        payload = [
            {
                "id": 21,
                "name": "build",
                "status": "failed",
                "allow_failure": False,
                "web_url": "u1",
            },
            {
                "id": 22,
                "name": "lint",
                "status": "canceled",
                "allow_failure": True,
                "web_url": "u2",
            },
        ]

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload).encode(), stderr=b"")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        jobs = _glab_job_list("group/sub/repository", _run(db_id=8), 1.0)
        command = calls[0]
        assert command[:2] == ["glab", "api"]
        assert "projects/group%2Fsub%2Frepository/pipelines/8/jobs?include_retried=false&per_page=100" in command
        assert "--paginate" in command
        assert not any(item.startswith("--hostname") for item in command)
        assert jobs[0]["conclusion"] == "failure"
        assert jobs[1]["status"] == "cancelled"

    @pytest.mark.parametrize("allow_failure", [None, 0, "false"])
    def test_missing_or_non_bool_allow_failure_raises(self, monkeypatch: pytest.MonkeyPatch, allow_failure: object) -> None:
        item: dict[str, object] = {"id": 21, "name": "build", "status": "failed"}
        if allow_failure is not None:
            item["allow_failure"] = allow_failure
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([item]).encode(), stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_job_list("group/repository", _run(), 1.0)

    def test_invalid_json_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=b"not-json", stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_job_list("group/repository", _run(), 1.0)

    def test_unexpected_shape_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=b'{"message":"x"}', stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_job_list("group/repository", _run(), 1.0)
