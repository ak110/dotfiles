"""run skill lifecycleの契約を検証する。"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys
import time
import typing

import psutil
import pytest

from agent_toolkit._atk import run_skill
from agent_toolkit._common import process_tree, reclaim_records, session_review
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment
from agent_toolkit._testing.run_skill_support import _engine_env, _Env, _log_files, _records, _run

__all__ = ["_engine_env"]


def test_child_session_does_not_inherit_process_loop_markers(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """起動元がprocess-loopの子でも、本作業のセッションへprocess-loopの環境印と再起動要求の受け渡し先を渡さない。

    環境印が渡ると、対話型のCLI本体を終了させる工程を求めるStopの判定が働き、
    最終応答で終わる非対話セッションが時間上限まで終われなくなる。
    再起動要求の受け渡し先が渡ると、セッションの子孫が親のprocess-loopへ再起動対象を書き込める。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "parent-session")
    monkeypatch.setenv("AGENT_TOOLKIT_RESTART_SPEC", str(engine_env.records / "restart-spec.json"))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    env = _records(engine_env, "session")[0]["env"]
    assert env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION"] is None
    assert env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"] is None
    assert env["AGENT_TOOLKIT_DELEGATED_SESSION"] is None
    assert env["AGENT_TOOLKIT_RESTART_SPEC"] is None


def test_descendant_holding_output_does_not_delay_result(
    engine_env: _Env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """子セッションの終了後に出力のパイプを保持する子孫が残っても、上限付きの待ちの後に結果を返す。

    出力の終了を待ち続けると、子孫が終わるまで`--timeout`の外で終了コードを返さない。
    """
    monkeypatch.setattr(run_skill, "OUTPUT_DRAIN_SECONDS", 0.5)
    pid_file = tmp_path / "detached.pid"
    monkeypatch.setenv("FAKE_SESSION_DETACH_PID_FILE", str(pid_file))
    try:
        started = time.monotonic()
        code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)
        elapsed = time.monotonic() - started

        assert code == 0, err
        assert out.startswith("成功: ")
        assert pid_file.exists(), "出力を保持する子孫が起動しなかった"
        assert psutil.pid_exists(int(pid_file.read_text(encoding="utf-8"))), "子孫が先に終わり、待ちの上限を検証できない"
        assert elapsed < 60
        log = _log_files(engine_env)[0].read_text(encoding="utf-8")
        assert "[stdout] session stdout line" in log
        assert "出力の読み取りを打ち切った" in log
    finally:
        if pid_file.exists():
            with contextlib.suppress(psutil.Error):
                psutil.Process(int(pid_file.read_text(encoding="utf-8"))).kill()


def test_abnormal_session_exit_propagates_code_with_failure_line(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """子セッションの非0終了はその終了コードと、ログの場所を含む失敗行を標準エラーへ返す。"""
    monkeypatch.setenv("FAKE_SESSION_EXIT", "3")

    code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 3
    assert not out
    assert err.startswith("失敗: ")
    assert str(_log_files(engine_env)[0]) in err
    assert "原因を確かめ、解消してから`atk run-skill`を元の引数で再実行する" in err
    assert "次回の定期実行" not in err


def test_same_skill_is_not_started_twice_but_other_skill_runs(
    engine_env: _Env, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ対象リポジトリと同じスキルの実行中は起動せず1で終わり、別のスキルは並行して起動する。"""
    started = tmp_path / "started"
    release = tmp_path / "release"
    env = {**os.environ, "FAKE_SESSION_STARTED_FILE": str(started), "FAKE_SESSION_HOLD_FILE": str(release)}
    agent_toolkit_root = pathlib.Path(run_skill.__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join((str(agent_toolkit_root), env.get("PYTHONPATH", "")))
    with process_tree.process_scope(
        [
            sys.executable,
            "-c",
            "import sys; from agent_toolkit import atk; atk.main(sys.argv[1:])",
            "run-skill",
            "--target-repo",
            str(engine_env.repo),
            "busy-skill",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as first:
        try:
            deadline = time.monotonic() + 60
            while not started.exists():
                assert first.poll() is None, first.communicate()
                assert time.monotonic() < deadline, "1回目のセッションが開始しなかった"
                time.sleep(0.05)

            code, out, err = _run(["--target-repo", str(engine_env.repo), "busy-skill"], capsys)
            assert code == run_skill.CONCURRENT_EXIT_CODE
            assert not out
            assert err.startswith("失敗: ")
            assert "進行中の実行の終了を確認してから、`atk run-skill`を元の引数で再実行する" in err
            assert "次回の定期実行" not in err
            assert len(_records(engine_env, "session")) == 1

            release.write_text("release")
            other_code, _other_out, other_err = _run(["--target-repo", str(engine_env.repo), "other-skill"], capsys)
            assert other_code == 0, other_err
            assert len(_records(engine_env, "session")) == 2
        finally:
            release.write_text("release")
            first.communicate(timeout=60)
    assert first.returncode == 0


def test_timeout_terminates_session_and_descendants(
    engine_env: _Env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """時間上限を超えると子セッションと子孫を終了させ、124と失敗行で終わる。

    子孫が残ると、定期実行のたびに終わらないプロセスが積み上がる。
    """
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_SESSION_SPAWN_PID_FILE", str(pid_file))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "--timeout", "3", "my-skill"], capsys)

    assert code == run_skill.TIMEOUT_EXIT_CODE
    assert err.startswith("失敗: ")
    assert "ログで進行状況を確かめ、必要なら`--timeout`を延ばして" in err
    assert "`atk run-skill`を元の対象・スキル・スキル引数で再実行する" in err
    assert "次回の定期実行" not in err
    assert pid_file.exists(), "子孫プロセスが時間上限より前に起動しなかった"
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while psutil.pid_exists(grandchild) and psutil.Process(grandchild).status() not in (
        psutil.STATUS_ZOMBIE,
        psutil.STATUS_DEAD,
    ):
        assert time.monotonic() < deadline, "子孫プロセスが終了しなかった"
        time.sleep(0.05)
    assert "時間上限（3秒）を超えたため終了させた" in _log_files(engine_env)[0].read_text(encoding="utf-8")


def test_unreclaimed_timeout_is_failure_and_resume_reclaims_same_descendant(
    engine_env: _Env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """時間上限で子孫を回収できなければ通常の時間上限と分けて伝え、同じ実行の再開前に同じ子孫を回収し直す。

    子が終了すると残った子孫は子から辿れないため、所有対象を引き継がないと再開時の停止は回収済みと判定する。
    """
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_SESSION_SPAWN_PID_FILE", str(pid_file))
    real_stop = process_tree.stop_tree_by_pid

    def leave_grandchild(pid: int, **kwargs: typing.Any) -> process_tree.TreeStopResult | None:
        # 子だけを終了させ、停止できない孫が残った停止結果を返す（停止の失敗の注入）。
        del kwargs
        root = psutil.Process(pid)
        grandchild = psutil.Process(int(pid_file.read_text(encoding="utf-8")))
        root.kill()
        return process_tree.TreeStopResult([root, grandchild], [grandchild], [])

    monkeypatch.setattr(process_tree, "stop_tree_by_pid", leave_grandchild)
    code, _out, err = _run(["--target-repo", str(engine_env.repo), "--timeout", "3", "my-skill"], capsys)
    monkeypatch.setattr(process_tree, "stop_tree_by_pid", real_stop)
    grandchild = psutil.Process(int(pid_file.read_text(encoding="utf-8")))
    reclaim_dir = reclaim_records.path(0, 0.0).parent
    try:
        assert code == 1
        assert "子プロセスの停止を確認できない" in err and "--resume" in err
        assert grandchild.is_running()
        assert len(list(reclaim_dir.glob("*.json"))) == 1
        run_id = _records(engine_env, "session")[0]["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]

        _resumed_code, _resumed_out, resumed_err = _run(["--resume", run_id], capsys)

        # 孫の親は終了済みで、回収はinitなどが行う。停止処理と同じ解放判定で、zombieは資源を保持しないとみなす。
        deadline = time.monotonic() + 10
        # pylint: disable-next=protected-access
        while process_tree._holds_resources(grandchild):  # noqa: SLF001
            assert time.monotonic() < deadline, f"再開前の停止が残った孫を回収しなかった: {resumed_err}"
            time.sleep(0.05)
        assert not list(reclaim_dir.glob("*.json"))
    finally:
        with contextlib.suppress(psutil.Error):
            grandchild.kill()


def test_logs_older_than_retention_are_removed(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """各実行の開始時に保持期間（30日）を超えたログだけを削除する。"""
    log_dir = engine_env.state / "agent-toolkit" / "run-skill"
    log_dir.mkdir(parents=True)
    now = time.time()
    old = log_dir / "old.log"
    recent = log_dir / "recent.log"
    for path, age_days in ((old, 31), (recent, 29)):
        path.write_text("過去の実行\n", encoding="utf-8")
        mtime = now - age_days * 24 * 60 * 60
        os.utime(path, (mtime, mtime))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert not old.exists()
    assert recent.exists()


def test_public_runner_stops_limited_cli_and_switches_same_conversation(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """上限通知後も終了しない実CLIを停止し、別接続で同じ会話と報告実行を続行する。"""
    api = tmp_path / "payg.env"
    api.write_text("ANTHROPIC_API_KEY=payg-test-key\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription-test")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(api))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "always")
    monkeypatch.setenv("FAKE_CONNECTION_LIMIT", "1")
    code, _out, error = _run(["--target-repo", str(engine_env.repo), "--timeout", "5", "switch-skill"], capsys)
    assert code == 0, error
    sessions = _records(engine_env, "session")
    assert [record["connection"] for record in sessions] == ["subscription", "payg"]
    first, resumed = (record["argv"] for record in sessions)
    assert resumed[resumed.index("--resume") + 1] == first[first.index("--session-id") + 1]
    assert len({record["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"] for record in sessions}) == 1
    assert not psutil.pid_exists(sessions[0]["pid"])
    assert [record["connection"] for record in _records(engine_env, "probe")] == ["subscription", "subscription", "payg"]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_public_resume_restores_execution_after_launcher_process_exit(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    host_environ: typing.Callable[[], dict[str, str]],
    engine: str,
    batch: bool,
) -> None:
    """異なる起動元プロセスの公開resumeが、会話・入力・報告実行と残予算を保持する。"""
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/high")
    environment = {
        **host_environ(),
        **os.environ,
        "FAKE_SESSION_EXIT": "7",
        "FAKE_REPORT_MODE": "missing",
        "FAKE_RECORD_THREAD": "1",
    }
    if engine == "claude":
        api = tmp_path / "payg.env"
        api.write_text("ANTHROPIC_API_KEY=payg-test-key\n", encoding="utf-8")
        environment.update(
            {
                "CLAUDE_CODE_OAUTH_TOKEN": "subscription-test",
                "AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE": str(api),
                "AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS": "subscription,payg",
            }
        )
    launcher = [sys.executable, "-c", "import sys; from agent_toolkit import atk; atk.main(sys.argv[1:])", "run-skill"]
    options = ["--target-repo", str(engine_env.repo), "--timeout", "10", "--args", "元の追加指示", "resume-skill"]
    if batch:
        transcript = tmp_path / "resume-source.jsonl"
        transcript.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "元の要求"}}) + "\n", encoding="utf-8"
        )
        session_review.record(
            {
                "hook_event_name": "SessionStart",
                "session_id": "resume-source",
                "transcript_path": str(transcript),
                "cwd": str(engine_env.repo),
            },
            host="claude",
            target_repo="https://example.com/team/repo",
        )
        options = ["--timeout", "10", "--args", "batch auto 元の追加指示", "agent-toolkit:review-session"]
    first = subprocess.run(
        [*launcher, *options],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert first.returncode == 7, first.stderr
    original = _records(engine_env, "session")[0]
    run_id = original["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    record_path = engine_env.state / "agent-toolkit/run-skill/runs" / f"{run_id}.json"
    before = json.loads(record_path.read_text(encoding="utf-8"))
    old_time = time.time() - 35 * 24 * 60 * 60
    for path in (record_path, pathlib.Path(before["log_path"])):
        os.utime(path, (old_time, old_time))
    environment.update(
        FAKE_SESSION_EXIT="0", FAKE_REPORT_MODE="saved", FAKE_PROBE_REJECT_COUNT="2" if engine == "claude" else "0"
    )
    ready = tmp_path / "resumed-ready"
    release = tmp_path / "resume-release"
    environment.update(FAKE_SESSION_STARTED_FILE=str(ready), FAKE_SESSION_HOLD_FILE=str(release))
    with process_tree.process_scope(
        [*launcher, "--resume", run_id],
        env=environment,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as resumed:
        try:
            deadline = time.monotonic() + 15
            while not ready.exists():
                assert resumed.poll() is None, resumed.communicate()
                assert time.monotonic() < deadline, "再開したCLIが実行状態へ到達しない"
                time.sleep(0.01)
            active = json.loads(record_path.read_text(encoding="utf-8"))
            assert active["status"] == "running" and active["remaining"] == before["remaining"]
            assert active["input"] == before["input"]
            release.touch()
            _out, error = resumed.communicate(timeout=20)
            assert resumed.returncode == 0, error
        finally:
            release.touch()
    sessions = _records(engine_env, "session")
    assert len(sessions) == 2
    assert json.loads(sessions[1]["env"]["AGENT_TOOLKIT_SESSION_START_INPUT"]) == json.loads(
        original["env"]["AGENT_TOOLKIT_SESSION_START_INPUT"]
    )
    assert sessions[1]["cwd"] == original["cwd"]
    if engine == "claude":
        argv = sessions[1]["argv"]
        assert argv[argv.index("--resume") + 1] == original["argv"][original["argv"].index("--session-id") + 1]
        assert sessions[1]["connection"] == "payg"
    else:
        assert sessions[1]["argv"][-2] == "01234567-89ab-cdef-0123-456789abcdef"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "completed" and 0 < record["remaining"] < 10
    if batch:
        manifest = pathlib.Path(record["input"]["batch_manifest"])
        value = session_review.read(manifest)
        assert value["status"] == "completed"
        assert [prepared["session_id"] for prepared in value["prepared"]] == ["resume-source"]
        assert not session_review.pending()
