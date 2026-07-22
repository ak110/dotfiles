"""`wait_ci`モジュールのテスト。公開API（`main`・`wait_for_ci`）経由で境界条件を網羅する。
private helper（`_gh_run_list`・`_resolve_sha`等）は直接テストせず、`main`経由の
シナリオテストで挙動を確認する（`coding-standards/references/testing.md`
「private関数の直接テスト禁止」に従う）。
"""

from __future__ import annotations

import json
import pathlib
import signal
import subprocess
import sys
import time
from unittest import mock

import pytest
import wait_ci


def _run_wait(
    run_list_fn,
    *,
    timeout=900.0,
    poll_interval=20.0,
    registration_grace=60.0,
    follow_cancelled=False,
    ancestor_check_fn=None,
    follow_shas_fn=None,
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
        sleep_fn=lambda _s: None,
        now_fn=lambda: next(times),
        run_list_fn=run_list_fn,
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


class TestGhErrorHandling:
    def test_consecutive_gh_failures_return_gh_error(self):
        def run_list_fn(_s):
            raise wait_ci.GhListError("mock failure")

        assert _run_wait(run_list_fn) == wait_ci.EXIT_GH_ERROR

    def test_intermittent_failure_recovers(self):
        state = {"n": 0}

        def run_list_fn(_s):
            state["n"] += 1
            if state["n"] == 1:
                raise wait_ci.GhListError("transient")
            return [_run()]

        assert _run_wait(run_list_fn) == wait_ci.EXIT_SUCCESS


class TestFollowCancelled:
    def _run_list_dispatch(self, cancelled_runs, follow_map):
        """sha別にrun一覧を返すスタブ。cancelled_runs=元sha, follow_map={後続sha: [run]}。"""

        def _fn(sha):
            if sha == "sha1":
                return cancelled_runs
            return follow_map.get(sha, [])

        return _fn

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
            raise wait_ci.GhListError("mock follow failure")

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


class TestSignalHandling:
    """実プロセスへシグナルを送信し、`_install_signal_handlers`の実挙動を確認する。"""

    def test_sigterm_exits_with_interrupted_code(self):
        script_path = pathlib.Path(__file__).parent / "wait_ci.py"
        with subprocess.Popen(
            [
                sys.executable,
                str(script_path),
                "--sha=0000000000000000000000000000000000000000",
                "--poll-interval=5",
                "--registration-grace=30",
                "--timeout=60",
                "--subprocess-timeout=2",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            try:
                time.sleep(0.5)
                proc.send_signal(signal.SIGTERM)
                returncode = proc.wait(timeout=15)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        assert returncode == wait_ci.EXIT_INTERRUPTED


class TestMainEntrypoint:
    """公開エントリ`main`経由の引数解析・HEAD解決・シナリオ確認。"""

    def test_head_resolution_failure_returns_gh_error(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="", returncode=1, stderr="")):
            assert wait_ci.main(["--timeout", "1"]) == wait_ci.EXIT_GH_ERROR

    def test_explicit_sha_success_path(self):
        payload = json.dumps([_run()])
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=payload, returncode=0, stderr="")):
            assert wait_ci.main(["--sha", "abc123", "--registration-grace", "0"]) == wait_ci.EXIT_SUCCESS

    def test_subprocess_timeout_surfaces_as_gh_error(self):
        """内部subprocess呼び出しのタイムアウトが`main`経由でGH_ERRORに現れる。"""
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 10.0)):
            assert wait_ci.main(["--sha", "abc123", "--registration-grace", "0", "--timeout", "1"]) == wait_ci.EXIT_GH_ERROR

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
