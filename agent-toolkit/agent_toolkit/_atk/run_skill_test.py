"""`atk run-skill`の公開契約を、偽の`claude`・`codex`実行ファイルをPATHへ置いた起動で検証する。

偽の実行ファイルは可用性判定（Claudeは`--output-format`付き、Codexは`availability-probe`の入力）と本作業の
セッションを引数で見分け、起動ごとに引数、作業ディレクトリと環境印の有無を記録ディレクトリへ書く。
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import time
import types
import typing

import psutil
import pytest

from agent_toolkit import atk
from agent_toolkit._atk import orchestrator, run_skill
from agent_toolkit._common import claude_usage_limit

_FAKE_ENGINE = """\
#!{python}
import json, os, pathlib, subprocess, sys, time, uuid
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
record_dir = pathlib.Path(os.environ["FAKE_RECORD_DIR"])
probe = "--output-format" in args if name == "claude" else "availability-probe" in args[-1]
kind = "probe" if probe else "session"
record = {{
    "name": name,
    "kind": kind,
    "argv": [name, *args],
    "cwd": os.getcwd(),
    "env": {{key: os.environ.get(key) for key in (
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_RESTART_SPEC",
    )}},
}}
(record_dir / f"{{time.time_ns()}}-{{uuid.uuid4().hex}}.json").write_text(json.dumps(record), encoding="utf-8")
if probe:
    counter = record_dir / "probe-count"
    count = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(count + 1))
    if count < int(os.environ.get("FAKE_PROBE_REJECT_COUNT", "0")):
        for event in (
            {{"type": "system", "subtype": "init", "session_id": "probe"}},
            {{
                "type": "rate_limit_event",
                "rate_limit_info": {{"status": "rejected", "rateLimitType": "five_hour", "resetsAt": None}},
            }},
            {{"type": "result", "is_error": True, "result": "You've hit your limit"}},
        ):
            print(json.dumps(event))
        sys.exit(1)
    print(json.dumps({{"type": "result", "is_error": False}}))
    sys.exit(0)
print("session stdout line")
print("session stderr line", file=sys.stderr)
sys.stdout.flush()
if os.environ.get("FAKE_SESSION_STARTED_FILE"):
    pathlib.Path(os.environ["FAKE_SESSION_STARTED_FILE"]).write_text("started")
if os.environ.get("FAKE_SESSION_SPAWN_PID_FILE"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pathlib.Path(os.environ["FAKE_SESSION_SPAWN_PID_FILE"]).write_text(str(child.pid))
    time.sleep(120)
if os.environ.get("FAKE_SESSION_DETACH_PID_FILE"):
    # 出力のパイプを継承したまま残る子孫を起動し、自身は直ちに終わる。
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pathlib.Path(os.environ["FAKE_SESSION_DETACH_PID_FILE"]).write_text(str(child.pid))
if os.environ.get("FAKE_SESSION_HOLD_FILE"):
    while not pathlib.Path(os.environ["FAKE_SESSION_HOLD_FILE"]).exists():
        time.sleep(0.05)
sys.exit(int(os.environ.get("FAKE_SESSION_EXIT", "0")))
"""


class _Env(typing.NamedTuple):
    repo: pathlib.Path
    records: pathlib.Path
    state: pathlib.Path
    bin_dir: pathlib.Path


@pytest.fixture(name="engine_env")
def _engine_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    """偽の実行ファイル、隔離した状態ディレクトリと対象リポジトリを用意する。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex"):
        path = bin_dir / name
        path.write_text(_FAKE_ENGINE.format(python=sys.executable), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    records = tmp_path / "records"
    records.mkdir()
    state = tmp_path / "state"
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, text=True, encoding="utf-8")
    monkeypatch.setenv("PATH", os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))))
    monkeypatch.setenv("FAKE_RECORD_DIR", str(records))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", "claude:sonnet/high")
    for name in ("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "AGENT_TOOLKIT_RESTART_SPEC"):
        monkeypatch.delenv(name, raising=False)
    return _Env(repo=repo, records=records, state=state, bin_dir=bin_dir)


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["run-skill", *argv])
    captured = capsys.readouterr()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code, captured.out, captured.err


def _records(env: _Env, kind: str) -> list[dict[str, typing.Any]]:
    return [
        record
        for path in sorted(env.records.glob("*.json"))
        if (record := json.loads(path.read_text(encoding="utf-8")))["kind"] == kind
    ]


def _log_files(env: _Env) -> list[pathlib.Path]:
    return sorted((env.state / "agent-toolkit" / "run-skill").glob("*.log"))


def test_claude_session_runs_skill_with_goal_and_reports_log(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Claude候補を採用した起動は非対話の`/goal`付きで対象リポジトリのrootから始まり、成功行でログの場所を示す。

    目的文に報告用UWIの保存と過去の実行のUWIの適用が欠けると、定期実行の結果がユーザーへ届かず、
    回答済みの対応も実施されない。
    """
    code, out, err = _run(
        ["--target-repo", str(engine_env.repo / "sub"), "--args", "対象: web", "example-plugin:check-logs"], capsys
    )

    assert code == 0, err
    sessions = _records(engine_env, "session")
    assert len(sessions) == 1
    argv = sessions[0]["argv"]
    assert argv[:2] == ["claude", "-p"]
    for option, value in (
        ("--permission-mode", "auto"),
        ("--permission-prompts", "none"),
        ("--model", "sonnet"),
        ("--effort", "high"),
    ):
        assert argv[argv.index(option) + 1] == value
    session_id = argv[argv.index("--session-id") + 1]
    goal = argv[-1]
    assert goal.startswith('/goal <atk-auto source="run-skill" kind="goal">')
    for expected in (
        "example-plugin:check-logs",
        "スキルへ渡す引数: 対象: web",
        "`agent-toolkit:completion-report`の報告用UWI",
        "「`atk run-skill`の過去の実行のUWI」",
    ):
        assert expected in goal
    assert pathlib.Path(sessions[0]["cwd"]).resolve() == engine_env.repo.resolve()
    lines = out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("成功: ")
    log_path = _log_files(engine_env)[0]
    assert str(log_path) in lines[0]
    log = log_path.read_text(encoding="utf-8")
    for expected in (
        "採用した候補: claude:sonnet/high",
        f"Claudeのセッション識別子: {session_id}",
        "[stdout] session stdout line",
        "[stderr] session stderr line",
        "終了状態: exit code 0",
    ):
        assert expected in log


def test_codex_session_receives_goal_without_slash_command(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexは非対話起動で`/goal`を目標として扱わないため、`atk-auto`要素の目的文だけを渡す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", "codex:gpt-5.6-sol/low")

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    sessions = _records(engine_env, "session")
    assert sessions[0]["argv"][:6] == ["codex", "exec", "--model", "gpt-5.6-sol", "-c", "model_reasoning_effort=low"]
    assert sessions[0]["argv"][-1].startswith('<atk-auto source="run-skill" kind="goal">')


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

    出力の終端を待ち続けると、子孫が終わるまで`--timeout`の外で終了コードを返さない。
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


def test_same_skill_is_not_started_twice_but_other_skill_runs(
    engine_env: _Env, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ対象リポジトリと同じスキルの実行中は起動せず1で終わり、別のスキルは並行して起動する。"""
    started = tmp_path / "started"
    release = tmp_path / "release"
    env = {**os.environ, "FAKE_SESSION_STARTED_FILE": str(started), "FAKE_SESSION_HOLD_FILE": str(release)}
    agent_toolkit_root = pathlib.Path(run_skill.__file__).resolve().parents[2]
    env["PYTHONPATH"] = os.pathsep.join((str(agent_toolkit_root), env.get("PYTHONPATH", "")))
    first = subprocess.Popen(  # pylint: disable=consider-using-with
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
    )
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
    assert pid_file.exists(), "子孫プロセスが時間上限より前に起動しなかった"
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while psutil.pid_exists(grandchild) and psutil.Process(grandchild).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline, "子孫プロセスが終了しなかった"
        time.sleep(0.05)
    assert "時間上限（3秒）を超えたため終了させた" in _log_files(engine_env)[0].read_text(encoding="utf-8")


def test_usage_limit_retries_same_claude_candidate(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """可用性判定がClaudeの利用上限を返した場合は次の候補へ進まず、解除を待って同じ候補を判定し直す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", "claude:opus/high,codex:gpt-5.6-sol/low")
    monkeypatch.setenv("FAKE_PROBE_REJECT_COUNT", "1")
    sleeps: list[float] = []
    monkeypatch.setattr(orchestrator, "time", types.SimpleNamespace(sleep=sleeps.append, time=time.time))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert [record["name"] for record in _records(engine_env, "probe")] == ["claude", "claude"]
    assert sleeps == [claude_usage_limit.RECHECK_SECONDS]
    assert _records(engine_env, "session")[0]["name"] == "claude"
    assert "利用上限の解除待ち: claude:opus/high" in _log_files(engine_env)[0].read_text(encoding="utf-8")


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
