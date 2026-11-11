"""CI待機の主題別テスト。"""

from __future__ import annotations

import json
import pathlib
from unittest import mock

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._testing.wait_ci_support import (
    _FULL_SHA,
    _job,
    _main_args,
    _run,
    _run_wait,
    _write_test_baseline,
)
from agent_toolkit._testing.wait_ci_support import (
    _restore_signal_handlers as _restore_wait_ci_signal_handlers,  # noqa: F401  # pytestがfixtureとして収集する。
)


class TestSuccessAndFailurePaths:
    def test_all_success_returns_exit_success(self):
        assert _run_wait(lambda _s: [_run()]) == wait_ci.EXIT_SUCCESS


@pytest.mark.parametrize("registration_grace", [0.0, 60.0])
def test_invalid_failed_job_id_returns_monitoring_error(registration_grace, capsys):
    """登録中と通常待機のどちらでも、不正な失敗IDを監視不能として返す。"""
    calls = 0

    def jobs(_run_record):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else [_job(conclusion="failure", db_id=-1)]

    result = _run_wait(
        lambda _sha: [_run(status="in_progress", conclusion=None)],
        job_list_fn=jobs,
        registration_grace=registration_grace,
    )
    assert result == wait_ci.EXIT_GH_ERROR
    assert "databaseIdが不正" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("field", "value"),
    [("sha", "b" * 40), ("run_ids", [99]), ("reported_failures", ["invalid"]), ("reported_failures", True)],
)
def test_wait_sha_rejects_incompatible_failure_record(tmp_path, capsys, field, value):
    """対象の異なる記録や不正な失敗集合から待機を再開しない。"""
    record = tmp_path / "failure.json"
    _write_test_baseline(record)
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload[field] = value
    record.write_text(json.dumps(payload), encoding="utf-8")
    args = _main_args(record, sha=None)
    args[:2] = ["--wait-sha", _FULL_SHA, "--failure-record", str(record)]
    with mock.patch.object(wait_ci, "wait_for_ci") as waiter:
        assert wait_ci.main(args) == wait_ci.EXIT_GH_ERROR
    waiter.assert_not_called()
    assert "待機記録の準備に失敗" in capsys.readouterr().err


@pytest.mark.parametrize("forge", ["github", "gitlab"])
@pytest.mark.parametrize("mode", ["baseline", "wait-sha"])
@pytest.mark.parametrize("follow", [False, True])
def test_main_resumes_after_reported_failure_and_returns_all_failures(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], forge: str, mode: str, follow: bool
) -> None:
    """同じ引数で再開し、既報失敗の再通知を省き新規失敗と全体終了を順に受け取る。"""
    record = tmp_path / "wait.json"
    if mode == "baseline":
        _write_test_baseline(record)
        payload = json.loads(record.read_text(encoding="utf-8"))
        payload["forge"] = forge
        record.write_text(json.dumps(payload), encoding="utf-8")
        args = _main_args(record, sha=None)
        args[2] = f"--forge={forge}"
    else:
        args = [
            "--wait-sha",
            _FULL_SHA,
            "--failure-record",
            str(record),
            f"--forge={forge}",
            "--repo",
            "owner/repository",
            "--ref",
            "refs/heads/main",
            "--source-ref",
            "HEAD",
        ]
    args += ["--registration-grace", "0"]
    if follow:
        args.append("--follow-cancelled")
    current = {"stage": 0, "poll": 0}

    def runs(sha):
        if follow and sha == _FULL_SHA:
            return [_run(conclusion="cancelled", head_sha=sha)]
        current["poll"] += 1
        completed = current["stage"] == 2
        return [
            _run(
                name="CI",
                db_id=2,
                head_sha=sha,
                status="completed" if completed else "in_progress",
                conclusion="failure" if completed else None,
            )
        ]

    def jobs(run):
        if run["databaseId"] == 1:
            return []

        def failed(name, identifier):
            return _job(
                name=name, db_id=identifier, conclusion="failure", status="failed" if forge == "gitlab" else "completed"
            )

        result = [failed("failure-A", 10)]
        if current["stage"] == 2 or (current["stage"] > 0 and current["poll"] > 1):
            result.append(failed("failure-B", 11))
        else:
            result.append(_job(name="B", status="in_progress", conclusion=None, db_id=11))
        return result

    original_wait = wait_ci.wait_for_ci

    def invoke(*positional, **keywords):
        clock = iter(range(10000))
        return original_wait(
            *positional,
            **keywords,
            run_list_fn=runs,
            job_list_fn=jobs,
            now_fn=lambda: float(next(clock)),
            sleep_fn=lambda _: None,
            ancestor_check_fn=lambda _: True,
            follow_shas_fn=lambda _: ["b" * 40],
        )

    with mock.patch.object(wait_ci, "wait_for_ci", side_effect=invoke):
        assert wait_ci.main(args) == 6
        first = capsys.readouterr()
        assert "job failure-A:" in first.out
        assert "同じ引数" in first.err
        current.update(stage=1, poll=0)
        assert wait_ci.main(args) == 6
        second = capsys.readouterr()
        assert current["poll"] > 1
        assert "job failure-B:" in second.out
        assert "job failure-A:" not in second.out
        current.update(stage=2, poll=0)
        assert wait_ci.main(args) == 1
        final = capsys.readouterr()
        assert "job failure-A:" in final.out
        assert "job failure-B:" in final.out
        assert "run CI:" in final.out


class TestFailureClassifications:
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
            == wait_ci.EXIT_NEW_FAILURE
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
        assert _run_wait(
            lambda _s: [_run()],
            registration_grace=0.0,
            job_list_fn=lambda _r: [_job(status=status, conclusion=conclusion)],
        ) == (wait_ci.EXIT_TIMEOUT if status == "in_progress" else wait_ci.EXIT_SUCCESS)

    @pytest.mark.parametrize("conclusion", ["startup_failure", "stale"])
    def test_github_failed_completed_run_returns_before_other_run(self, conclusion):
        runs = [
            _run(name="failed", conclusion=conclusion, db_id=1),
            _run(name="pending", status="in_progress", conclusion=None, db_id=2),
        ]
        assert _run_wait(lambda _s: runs, registration_grace=100.0) == wait_ci.EXIT_NEW_FAILURE

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
            == wait_ci.EXIT_NEW_FAILURE
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
        assert _run_wait(
            lambda _s: [_run()],
            forge="gitlab",
            registration_grace=0.0,
            job_list_fn=lambda _r: [_job(status=status, conclusion=status, allow_failure=allow_failure)],
        ) == (wait_ci.EXIT_TIMEOUT if status == "running" else wait_ci.EXIT_SUCCESS)


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
            == wait_ci.EXIT_NEW_FAILURE
        )


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

    def test_dynamic_dependabot_failure_does_not_fail_push_success(self):
        runs = [
            _run(name="dependabot", workflow_name="Dependabot Updates", event="dynamic", conclusion="failure"),
            _run(name="ci", db_id=2),
        ]
        assert _run_wait(lambda _sha: runs, registration_grace=0.0) == wait_ci.EXIT_SUCCESS

    def test_manual_dependabot_failure_is_not_excluded(self):
        runs = [
            _run(
                name="dependabot",
                workflow_name="Dependabot Updates",
                event="workflow_dispatch",
                conclusion="failure",
            ),
            _run(name="ci", db_id=2),
        ]
        assert _run_wait(lambda _sha: runs, registration_grace=0.0) == wait_ci.EXIT_CI_FAILED

    def test_other_dynamic_workflow_failure_is_not_excluded(self):
        runs = [_run(name="dynamic-ci", workflow_name="CI", event="dynamic", conclusion="failure")]
        assert _run_wait(lambda _sha: runs, registration_grace=0.0) == wait_ci.EXIT_CI_FAILED

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
            == wait_ci.EXIT_NEW_FAILURE
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

        assert _run_wait(run_list_fn, registration_grace=0.0, job_list_fn=job_list_fn) == wait_ci.EXIT_NEW_FAILURE
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


class TestSupersededCancelledRuns:
    """同じworkflow名・同じcommitへ重複登録され、後続runへ置き換えられて打ち切られたrunの扱いを検証する。

    保証する契約は、打ち切られた残骸runを成否判定の対象へ含めないことと、
    そのrunを期待run集合へ残したまま欠落待機を継続しないこととする。
    """

    def _superseded_pair_dispatch(self, final_conclusion: str):
        """登録猶予中は2runとも実行中、以降は先行runがcancelled・後続runが指定の結論となる一覧を返す。"""
        calls = {"n": 0}

        def _fn(_sha):
            calls["n"] += 1
            if calls["n"] == 1:
                return [
                    _run(status="in_progress", conclusion=None, db_id=1),
                    _run(status="in_progress", conclusion=None, db_id=2),
                ]
            return [_run(conclusion="cancelled", db_id=1), _run(conclusion=final_conclusion, db_id=2)]

        return _fn

    def test_superseded_cancelled_run_does_not_block_success(self, capsys: pytest.CaptureFixture[str]) -> None:
        """後続runが成功した入力を成功と判定し、期待run集合の欠落待機を発生させない。"""
        assert _run_wait(self._superseded_pair_dispatch("success"), registration_grace=1.0) == wait_ci.EXIT_SUCCESS
        assert "期待run集合の一部が取得結果から欠落" not in capsys.readouterr().err

    def test_superseded_cancelled_run_keeps_later_failure(self) -> None:
        """後続runが失敗した入力は従来どおり失敗と判定する。"""
        assert _run_wait(self._superseded_pair_dispatch("failure"), registration_grace=1.0) == wait_ci.EXIT_CI_FAILED

    def test_single_cancelled_run_still_follows_later_commits(self) -> None:
        """同一commitのrunが1件だけcancelledである場合は、後続SHA追跡の振る舞いを変えない。"""
        cancelled = [_run(conclusion="cancelled", db_id=1, head_sha="sha1")]
        follow = [_run(db_id=2, head_sha="sha2")]

        def _fn(sha):
            return cancelled if sha == "sha1" else follow

        assert (
            _run_wait(_fn, follow_cancelled=True, registration_grace=0.0, follow_shas_fn=lambda _b: ["sha2"])
            == wait_ci.EXIT_SUCCESS
        )
