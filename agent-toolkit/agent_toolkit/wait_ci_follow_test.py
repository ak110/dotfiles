"""CI待機の主題別テスト。"""

from __future__ import annotations

import subprocess

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._atk import wait_ci_polling
from agent_toolkit._testing.wait_ci_support import (
    _follow_shas,
    _is_ancestor_of_ref,
    _job,
    _run,
    _run_wait,
)
from agent_toolkit._testing.wait_ci_support import (
    _restore_signal_handlers as _restore_wait_ci_signal_handlers,  # noqa: F401  # pytestがfixtureとして収集する。
)


class TestUnifiedCompletionLoop:
    """主SHA・後続SHAが共有する完了待機ループを同一条件で検証する。"""

    def _run_completion(self, fetch, follow_mode: bool, now_fn):
        """共通の待機条件でsnapshotの取得と時刻だけを差し替える。"""
        return wait_ci._wait_for_completion(  # pylint: disable=protected-access  # noqa: SLF001
            start=0.0,
            timeout=10.0,
            poll_interval=1.0,
            expected_ids={1},
            fetch_fn=fetch,
            select_fn=lambda candidates: candidates,
            forge="github",
            repository="owner/repository",
            sleep_fn=lambda _seconds: None,
            now_fn=now_fn,
            consecutive_failures=0,
            follow_mode=follow_mode,
            reported=set(),
            save=None,
        )

    @pytest.mark.parametrize("follow_mode", [False, True])
    def test_snapshot_failure_recovers(self, follow_mode: bool) -> None:
        """一時的な取得失敗後の成功を両方の待機処理で受理する。"""
        calls = 0

        def fetch() -> tuple[list[wait_ci.RunRecord], list[wait_ci.JobRecord], set[int]]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise wait_ci.RunListError("transient completion failure")
            return [_run()], [], set()

        result, runs, _ = self._run_completion(fetch, follow_mode, lambda: float(calls))

        assert result == wait_ci.EXIT_SUCCESS
        assert runs == [_run()]

    @pytest.mark.parametrize("follow_mode", [False, True])
    def test_three_snapshot_failures_return_gh_error(self, follow_mode: bool) -> None:
        """3回連続取得失敗を両方の待機処理で同じ終了コードへ変換する。"""

        def fetch() -> tuple[list[wait_ci.RunRecord], list[wait_ci.JobRecord], set[int]]:
            raise wait_ci.RunListError("completion failure")

        result, runs, _ = self._run_completion(fetch, follow_mode, lambda: 0.0)

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
            == wait_ci.EXIT_NEW_FAILURE
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
            == wait_ci.EXIT_NEW_FAILURE
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
        """後続SHA登録猶予末の直近ジョブ取得失敗は、1回の呼び出しの失敗だけで即時GH_ERRORとする。"""
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
    """cancelled後のローカル探索がsource refを使うことを確認する。"""

    def test_ancestor_check_uses_explicit_source_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _is_ancestor_of_ref("base", "HEAD", 1.0) is True
        assert calls == [["git", "merge-base", "--is-ancestor", "base", "HEAD"]]

    def test_follow_sha_query_uses_explicit_source_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="next\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
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

        monkeypatch.setattr(wait_ci_polling, _is_ancestor_of_ref.__name__, ancestor_check)
        monkeypatch.setattr(wait_ci_polling, _follow_shas.__name__, follow_shas)
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
