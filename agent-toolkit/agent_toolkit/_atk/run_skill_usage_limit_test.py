"""run skill usage limitの契約を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import types

import pytest

from agent_toolkit._atk import orchestrator, run_skill
from agent_toolkit._common import claude_usage_limit
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment
from agent_toolkit._testing.run_skill_support import _engine_env, _Env, _log_files, _records, _run, _startup_input

__all__ = ["_engine_env"]


def test_usage_limit_retries_same_claude_candidate(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """可用性判定がClaudeの利用上限を返した場合は次の候補へ進まず、解除を待って同じ候補を判定し直す。"""
    set_orchestrate_environment(monkeypatch, "claude:opus/high,codex:gpt-5.6-sol/low")
    monkeypatch.setenv("FAKE_PROBE_REJECT_COUNT", "1")
    sleeps: list[float] = []
    monkeypatch.setattr(orchestrator, "time", types.SimpleNamespace(sleep=sleeps.append, time=time.time))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert [record["name"] for record in _records(engine_env, "probe")] == ["claude", "claude"]
    assert sleeps == [claude_usage_limit.RECHECK_SECONDS]
    assert _records(engine_env, "session")[0]["name"] == "claude"
    assert "利用上限の解除待ち: claude:opus/high" in _log_files(engine_env)[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("engine,limit_type", [("claude", "five_hour"), ("claude", "seven_day"), ("codex", "")])
@pytest.mark.parametrize("progress", [False, True])
def test_usage_limit_resumes_work_conversation_outside_timeout(
    engine_env: _Env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    limit_type: str,
    progress: bool,
) -> None:
    """公開CLIは表示設定によらず本作業の上限を待ち、同じ会話と実行時間予算を引き継ぐ。

    CLIイベントとrolloutは20261014-210511-001/002の実現性で観測された形を使う。
    """
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{engine.upper()}_USAGE_LIMIT_WAIT", "always")
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/high")
    monkeypatch.setenv("FAKE_SESSION_LIMIT_COUNT", "2")
    monkeypatch.setenv("FAKE_LIMIT_TYPE", limit_type)
    monkeypatch.setenv("CODEX_HOME", str(engine_env.state / "codex"))
    monkeypatch.setattr(sys.stderr, "isatty", lambda: progress)
    sleeps: list[float] = []
    clock = [1000.0]

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(
        run_skill,
        "time",
        types.SimpleNamespace(
            time=lambda: clock[0],
            monotonic=lambda: clock[0],
            sleep=sleep,
        ),
    )
    code, out, err = _run(["--target-repo", str(engine_env.repo), "--timeout", "2", "resume-skill"], capsys)
    assert code == 0, err
    assert out.startswith("成功: ") and len(out.splitlines()) == 1
    assert sleeps == [10, 300]
    sessions = _records(engine_env, "session")
    assert len(sessions) == 3
    if engine == "claude":
        assert all(session["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1" for session in sessions)
    first = sessions[0]["argv"]
    for session in sessions[1:]:
        argv = session["argv"]
        if engine == "claude":
            assert argv[argv.index("--resume") + 1] == first[first.index("--session-id") + 1]
            assert "--session-id" not in argv
        else:
            assert argv[:3] == ["codex", "exec", "resume"]
            assert argv[-2] == "01234567-89ab-cdef-0123-456789abcdef"
        assert argv[-1].startswith('<atk-auto source="run-skill" kind="usage-limit-resume">')
        assert argv[argv.index("--model") + 1] == "test-model"
        data = _startup_input(session, monkeypatch, capsys, source="resume")
        assert data == json.loads(sessions[0]["env"]["AGENT_TOOLKIT_SESSION_START_INPUT"])
    log = _log_files(engine_env)[0].read_text(encoding="utf-8")
    assert log.count("利用上限の解除待ち:") == 2
    run_ids = {session["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"] for session in sessions}
    assert len(run_ids) == 1 and None not in run_ids
    reports = list((pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"]) / "2026/10").glob("*.md"))
    assert len(reports) == 1
    saved = reports[0].read_text(encoding="utf-8")
    assert next(iter(run_ids)) in saved
    assert saved.count("再開後の報告更新") == 2


@pytest.mark.parametrize(
    "engine,limit_type,policy",
    [("claude", "five_hour", "never"), ("claude", "seven_day", "five-hour"), ("codex", "", "never")],
)
def test_usage_limit_outside_wait_policy_returns_failure(
    engine_env: _Env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    limit_type: str,
    policy: str,
) -> None:
    """解除を待たない設定は公開CLIから元の失敗を返し、会話の自動再開を行わない。"""
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/high")
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{engine.upper()}_USAGE_LIMIT_WAIT", policy)
    monkeypatch.setenv("FAKE_SESSION_LIMIT_COUNT", "1")
    monkeypatch.setenv("FAKE_LIMIT_TYPE", limit_type)
    monkeypatch.setenv("CODEX_HOME", str(engine_env.state / "codex"))
    sleeps: list[float] = []
    monkeypatch.setattr(run_skill, "time", types.SimpleNamespace(time=time.time, monotonic=time.monotonic, sleep=sleeps.append))
    code, out, err = _run(["--target-repo", str(engine_env.repo), "nonwaiting-skill"], capsys)
    assert code == 1 and not out
    assert "異常終了" in err
    assert len(_records(engine_env, "session")) == 1
    assert not sleeps
