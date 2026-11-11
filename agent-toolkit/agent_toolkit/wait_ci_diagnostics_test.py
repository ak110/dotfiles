"""CI待機の主題別テスト。"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import time
from unittest import mock

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._testing.wait_ci_support import (
    _git_repository,
    _job,
    _main_args,
    _next_action,
    _run,
    _run_wait,
    _write_test_baseline,
)
from agent_toolkit._testing.wait_ci_support import (
    _restore_signal_handlers as _restore_wait_ci_signal_handlers,  # noqa: F401  # pytestがfixtureとして収集する。
)

__all__ = ["_git_repository"]


class TestSignalHandling:
    """実プロセスへシグナルを送信し、`_install_signal_handlers`の実挙動を確認する。"""

    @pytest.mark.skipif(sys.platform == "win32", reason="WindowsのSIGTERMはPythonハンドラを呼び出さない")
    def test_sigterm_exits_with_interrupted_code(self, tmp_path: pathlib.Path, git_repository: tuple[pathlib.Path, str]):
        """SIGTERM受信時に`EXIT_INTERRUPTED`を返すことを実プロセスで確認する。

        CI照会へ到達した通知を受けてからシグナルを送る。
        ハンドラ登録前の中断と照会中の中断を分けて検証する。

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
        ready = tmp_path / "query.ready"
        child_code = (
            "import pathlib, threading, wait_ci\n"
            "from agent_toolkit._atk import wait_ci_forge\n"
            "def query(*_args, **_kwargs):\n"
            f"    pathlib.Path({str(ready)!r}).touch()\n"
            "    assert threading.Event().wait(30), '照会の保持上限'\n"
            "    return []\n"
            "wait_ci_forge._gh_run_list = query\n"
            f"raise SystemExit(wait_ci.main({arguments!r}))\n"
        )
        with subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=repository,
            env={**os.environ, "PYTHONPATH": str(pathlib.Path(__file__).parent)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as proc:
            try:
                deadline = time.monotonic() + 30
                while not ready.exists():
                    assert proc.poll() is None and time.monotonic() < deadline
                    time.sleep(0.01)
                proc.send_signal(signal.SIGTERM)
                _, stderr_text = proc.communicate(timeout=15)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        assert "reentrant call" not in stderr_text, stderr_text
        assert proc.returncode == wait_ci.EXIT_INTERRUPTED
        assert "次の操作: 同じ引数で再実行すると待機を再開できる" in stderr_text


class TestNextActionOnNonZeroExit:
    """非0の終了では、失敗の種類ごとに実在するコマンドやオプションを名指す次の操作を標準エラーへ書く。"""

    @pytest.mark.parametrize(
        ("kwargs", "expected_code", "expected_names"),
        [
            (
                {"run_list_fn": lambda _s: [_run(conclusion="failure")]},
                wait_ci.EXIT_CI_FAILED,
                ["gh run view 1", "--repo owner/repository", "--json jobs", "actions/jobs/<job ID>/logs"],
            ),
            ({"run_list_fn": lambda _s: [], "registration_grace": 100.0, "timeout": 1.0}, wait_ci.EXIT_TIMEOUT, ["--timeout"]),
            (
                {
                    "run_list_fn": lambda _s: [_run(status="in_progress", conclusion=None)],
                    "registration_grace": 0.0,
                    "timeout": 3.0,
                },
                wait_ci.EXIT_TIMEOUT,
                ["--timeout"],
            ),
            ({"run_list_fn": lambda _s: [], "registration_grace": 5.0}, wait_ci.EXIT_NO_RUNS, ["--registration-grace"]),
            (
                {"run_list_fn": mock.Mock(side_effect=wait_ci.RunListError("boom"))},
                wait_ci.EXIT_GH_ERROR,
                ["gh auth status", "glab auth status"],
            ),
            ({"run_list_fn": lambda _s: [_run(conclusion="cancelled")]}, wait_ci.EXIT_CI_FAILED, ["--follow-cancelled"]),
            (
                {
                    "run_list_fn": lambda _s: [_run(conclusion="cancelled")],
                    "follow_cancelled": True,
                    "ancestor_check_fn": lambda _a: False,
                },
                wait_ci.EXIT_GH_ERROR,
                ["--follow-cancelled", "--source-ref"],
            ),
        ],
    )
    def test_wait_paths_write_next_action(
        self,
        capsys: pytest.CaptureFixture[str],
        kwargs: dict,
        expected_code: int,
        expected_names: list[str],
    ) -> None:
        assert _run_wait(**kwargs) == expected_code
        next_action = _next_action(capsys.readouterr().err)
        assert all(name in next_action for name in expected_names)

    def test_successful_wait_writes_no_next_action(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run_wait(lambda _s: [_run()]) == wait_ci.EXIT_SUCCESS
        assert "次の操作: " not in capsys.readouterr().err

    @pytest.mark.parametrize("follow", [False, True])
    @pytest.mark.parametrize(("grace", "fail_at"), [(100.0, 1), (0.0, 2)])
    @pytest.mark.parametrize("record_type", ["job", "run"])
    def test_github_failure_guidance_matches_detected_target(
        self, capsys: pytest.CaptureFixture[str], follow: bool, grace: float, fail_at: int, record_type: str
    ) -> None:
        """各待機段階で検出したjobとrunを取り違えず、終了待機を増やさず案内する。"""
        calls = 0

        def run_list(sha: str) -> list[dict]:
            nonlocal calls
            if follow and sha == "sha1":
                return [_run(conclusion="cancelled")]
            calls += 1
            failed = calls >= fail_at and record_type == "run"
            records = [_run(db_id=303, head_sha=sha, status="in_progress", conclusion=None)]
            if failed:
                records[0].update(status="completed", conclusion="failure")
                records.append(_run(db_id=304, name="pending", head_sha=sha, status="in_progress", conclusion=None))
            return records

        def job_list(run: dict) -> list[dict]:
            if run["databaseId"] == 303 and calls >= fail_at and record_type == "job":
                return [_job(db_id=904, conclusion="failure")]
            return []

        assert (
            _run_wait(
                run_list,
                repository="https://github.example.com/team/project.git",
                registration_grace=grace,
                follow_cancelled=follow,
                job_list_fn=job_list,
            )
            == wait_ci.EXIT_NEW_FAILURE
        )
        action = _next_action(capsys.readouterr().err)
        assert calls == fail_at
        if record_type == "job":
            assert "repos/team/project/actions/jobs/904/logs" in action
            assert "--hostname github.example.com" in action
            assert "--allow-escape-sequences" in action
            assert "managed-tempの中のファイルへ保存" in action
            assert "--log-failed" not in action
        else:
            assert "gh run view 303 --repo github.example.com/team/project --json jobs" in action
            assert "repos/team/project/actions/jobs/<job ID>/logs" in action
            assert "--log-failed" not in action

    def test_terminal_non_success_guidance_uses_failed_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """早期失敗に含まれない終了状態でも成功runのIDを案内しない。"""
        assert _run_wait(lambda _sha: [_run(db_id=301), _run(db_id=302, conclusion="skipped")]) == wait_ci.EXIT_CI_FAILED
        assert "gh run view 302 --repo owner/repository --json jobs" in _next_action(capsys.readouterr().err)

    def test_gitlab_failure_guidance_uses_job_trace(self, capsys: pytest.CaptureFixture[str]) -> None:
        """GitLabの失敗jobには検出したjob IDでtrace取得を案内する。"""
        assert (
            _run_wait(
                lambda _sha: [_run(db_id=303, status="in_progress", conclusion=None)],
                forge="gitlab",
                job_list_fn=lambda _run: [_job(db_id=904, status="failed", conclusion="failure")],
            )
            == wait_ci.EXIT_NEW_FAILURE
        )
        assert "glab ci trace 904 --repo owner/repository" in _next_action(capsys.readouterr().err)

    def test_sha_resolution_failure_names_git_fetch(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="", returncode=1, stderr="")):
            assert wait_ci.main(_main_args(baseline) + ["--timeout", "1"]) == wait_ci.EXIT_GH_ERROR
        assert "`git fetch`" in _next_action(capsys.readouterr().err)

    def test_baseline_mismatch_names_write_baseline_and_wait_sha(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        arguments = _main_args(baseline, sha=None)
        arguments[arguments.index("refs/heads/main")] = "refs/heads/other"
        assert wait_ci.main(arguments) == wait_ci.EXIT_GH_ERROR
        next_action = _next_action(capsys.readouterr().err)
        assert "`--write-baseline`" in next_action
        assert "`--wait-sha`" in next_action
