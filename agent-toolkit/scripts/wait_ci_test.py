"""`wait_ci`モジュールのテスト。公開API（`main`・`wait_for_ci`）経由で境界条件を網羅する。
private helper（`_gh_run_list`・`_resolve_sha`等）は原則として直接テストせず、`main`経由の
シナリオテストで挙動を確認する（`coding-standards/references/testing.md`
「private関数の直接テスト禁止」に従う）。
例外はforge判別・GitLab応答正規化（`_resolve_forge`・`_normalize_gitlab_pipeline`・
`_glab_pipeline_list`）とし、入力形態ごとの網羅を`main`経由で行うと
1形態あたり複数の外部コマンド応答を組み立てる必要があり、判定対象の入出力関係が読み取れなくなるため
最小限の範囲で直接テストする。
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

# 直接テスト対象のprivate helperはモジュール冒頭で別名束縛し、抑制コメントを1箇所へ集約する。
# 理由はモジュールdocstringの「例外」記述に従う。
_resolve_forge = wait_ci._resolve_forge  # pylint: disable=protected-access
_normalize_gitlab_pipeline = wait_ci._normalize_gitlab_pipeline  # pylint: disable=protected-access
_glab_pipeline_list = wait_ci._glab_pipeline_list  # pylint: disable=protected-access
_all_cancelled = wait_ci._all_cancelled  # pylint: disable=protected-access
_all_success = wait_ci._all_success  # pylint: disable=protected-access


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


class TestSignalHandling:
    """実プロセスへシグナルを送信し、`_install_signal_handlers`の実挙動を確認する。"""

    def test_sigterm_exits_with_interrupted_code(self):
        """SIGTERM受信時に`EXIT_INTERRUPTED`を返すことを実プロセスで確認する。

        ハンドラ登録は`main`冒頭で行われるため、送信までの待機が起動所要時間を下回ると
        既定動作で終了し`-SIGTERM`が返る。待機時間を延ばしながら最大3回試行し、
        起動が遅い実行環境でも登録後の挙動を判定できるようにする。

        併せてstderrへ`reentrant call`が出ないことを確認する。ハンドラが`print`で
        書くと`sys.stderr`の`BufferedWriter`へ再入し得るため、その回帰を検出する。
        """
        script_path = pathlib.Path(__file__).parent / "wait_ci.py"
        returncode = None
        for warmup_sec in (1.0, 3.0, 6.0):
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
    """公開エントリ`main`経由の引数解析・HEAD解決・シナリオ確認。"""

    def test_head_resolution_failure_returns_gh_error(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="", returncode=1, stderr="")):
            assert wait_ci.main(["--timeout", "1"]) == wait_ci.EXIT_GH_ERROR

    def test_explicit_sha_success_path(self):
        payload = json.dumps([_run()])
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=payload, returncode=0, stderr="")):
            assert wait_ci.main(["--sha", "abc123", "--registration-grace", "0", "--forge=github"]) == wait_ci.EXIT_SUCCESS

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


class TestResolveShaViaMain:
    """`--sha`明示指定時も完全形式へ解決してから`wait_for_ci`へ渡す。"""

    def test_explicit_short_sha_is_resolved_to_full_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_wait_for_ci(sha: str, *args: object, **kwargs: object) -> int:
            del args, kwargs
            captured["sha"] = sha
            return wait_ci.EXIT_SUCCESS

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del kwargs
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "17561bd"]
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="17561bd376c5fb8ace04153871b2a2d6993c380d\n", stderr=""
            )

        monkeypatch.setattr(wait_ci, "wait_for_ci", fake_wait_for_ci)
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = wait_ci.main(["--sha", "17561bd", "--forge=github"])
        assert result == wait_ci.EXIT_SUCCESS
        assert captured["sha"] == "17561bd376c5fb8ace04153871b2a2d6993c380d"

    def test_explicit_sha_resolution_failure_is_distinguished_from_no_runs(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del cmd, kwargs
            return subprocess.CompletedProcess([], returncode=128, stdout="", stderr="fatal: bad revision")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = wait_ci.main(["--sha", "deadbee"])
        assert result == wait_ci.EXIT_GH_ERROR
        err = capsys.readouterr().err
        assert "deadbee" in err
        assert "sha解決に失敗" in err

    def test_missing_git_returns_sha_resolution_failure(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(subprocess, "run", mock.Mock(side_effect=FileNotFoundError("git")))

        result = wait_ci.main(["--sha", "17561bd"])

        assert result == wait_ci.EXIT_GH_ERROR
        assert "sha解決に失敗" in capsys.readouterr().err

    def test_option_like_sha_is_passed_after_end_of_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del kwargs
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "--not-an-option"]
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: bad revision")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = wait_ci.main(["--sha=--not-an-option"])

        assert result == wait_ci.EXIT_GH_ERROR


def _patch_git(monkeypatch: pytest.MonkeyPatch, remote: str, toplevel: str | None = None) -> None:
    """`git remote get-url origin`と`git rev-parse --show-toplevel`の応答を固定するヘルパー。"""

    def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
        if cmd[1:] == ["rev-parse", "--show-toplevel"]:
            if toplevel is None:
                return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: not a git repository")
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{toplevel}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=remote, stderr="")

    monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)


class TestResolveForge:
    """forge判別の分岐を検証する。"""

    def test_explicit_value_is_returned_as_is(self) -> None:
        """明示指定時はリモート照会をしない。"""
        assert _resolve_forge("gitlab", 1.0) == "gitlab"
        assert _resolve_forge("github", 1.0) == "github"

    def test_auto_detects_github_from_remote_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ホスト名のラベルがgithubと一致する場合はgithubと判定する（SSH短縮形式）。"""
        _patch_git(monkeypatch, "git@github.com:o/r.git\n")
        assert _resolve_forge("auto", 1.0) == "github"

    def test_auto_detects_github_enterprise_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GitHub Enterprise Serverの標準的なホスト名もgithubと判定する。"""
        _patch_git(monkeypatch, "https://github.example.com/o/r.git\n")
        assert _resolve_forge("auto", 1.0) == "github"

    def test_auto_detects_gitlab_for_self_hosted_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ホスト名のラベルがgitlabと一致する私設ホストはgitlabと判定する（サブグループ付きHTTPS形式）。"""
        _patch_git(monkeypatch, "https://gitlab.example.com/group/sub/repo.git\n")
        assert _resolve_forge("auto", 1.0) == "gitlab"

    def test_auto_detects_gitlab_from_ssh_uri(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ポート付きSSH URI形式からもホスト名を抽出する。"""
        _patch_git(monkeypatch, "ssh://git@gitlab.example.com:2222/group/repo.git\n")
        assert _resolve_forge("auto", 1.0) == "gitlab"

    def test_auto_does_not_match_host_label_by_substring(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """ラベルの部分一致では判定せず、無関係なホストを誤分類しない。"""
        _patch_git(monkeypatch, "https://notgithub.example.com/o/r.git\n", toplevel=str(tmp_path))
        assert _resolve_forge("auto", 1.0) is None

    def test_auto_returns_none_for_ambiguous_host_without_gitlab_ci(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """種別ラベルを持たないホスト名で`.gitlab-ci.yml`が無ければNoneを返す。"""
        _patch_git(monkeypatch, "git@git.example.com:o/r.git\n", toplevel=str(tmp_path))
        assert _resolve_forge("auto", 1.0) is None

    def test_auto_finds_gitlab_ci_at_worktree_root_from_subdirectory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """サブディレクトリから起動しても作業ツリールートの`.gitlab-ci.yml`を検出する。"""
        (tmp_path / ".gitlab-ci.yml").write_text("stages: []\n", encoding="utf-8")
        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        _patch_git(monkeypatch, "git@git.example.com:o/r.git\n", toplevel=str(tmp_path))
        assert _resolve_forge("auto", 1.0) == "gitlab"

    def test_auto_returns_none_when_remote_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """リモートURLを取得できない場合はNoneを返す。"""
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: no such remote"),
        )
        assert _resolve_forge("auto", 1.0) is None


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


class TestGlabPipelineList:
    """glab呼び出しと応答検証を確認する。"""

    def test_command_uses_sha_and_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """glab ci listへSHAとJSON出力指定を渡す。"""
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='[{"id":1,"status":"success"}]', stderr="")

        monkeypatch.setattr(wait_ci.subprocess, "run", fake_run)
        records = _glab_pipeline_list("deadbeef", 1.0)
        assert calls[0][:3] == ["glab", "ci", "list"]
        assert "--sha" in calls[0] and "deadbeef" in calls[0]
        assert calls[0][calls[0].index("-F") + 1] == "json"
        assert records[0]["conclusion"] == "success"

    def test_non_zero_exit_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非ゼロ終了はRunListErrorとして扱う。"""
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom"),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_pipeline_list("deadbeef", 1.0)

    def test_unexpected_shape_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """配列以外の応答はRunListErrorとして扱う。"""
        monkeypatch.setattr(
            wait_ci.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout='{"message":"x"}', stderr=""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_pipeline_list("deadbeef", 1.0)
