"""候補選択を偽のCLIと保存ロールアウトで検証する。"""

import os
import pathlib
import stat
import sys
import types

import pytest

from agent_toolkit._atk import orchestrator
from agent_toolkit._common import mcp_environment


def test_child_env_strips_conversation_context_and_preserves_explicit_session_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """全起動で親contextを除き、接続設定と除去後に指定する委譲印を維持する。"""
    for name in mcp_environment._NON_INHERITABLE_NAMES:  # pylint: disable=protected-access
        monkeypatch.setenv(name.upper(), "parent")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("DROP_SETTING", "remove")
    environment = orchestrator.child_env(drop=("DROP_SETTING",))
    assert not {name.casefold() for name in environment} & mcp_environment._NON_INHERITABLE_NAMES  # pylint: disable=protected-access
    assert environment["ANTHROPIC_AUTH_TOKEN"] == "test-token"
    assert "DROP_SETTING" not in environment
    environment["AGENT_TOOLKIT_DELEGATED_SESSION"] = "1"
    session = orchestrator.session_env(environment, "claude")
    assert session["AGENT_TOOLKIT_DELEGATED_SESSION"] == "1"
    assert session["CLAUDE_CODE_RETRY_WATCHDOG"] == "1"


@pytest.mark.parametrize("mode", ["fallback", "retry", "failure", "earliest"])
def test_codex_probe_fallback_and_all_candidates_retry(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """次候補を優先し、全候補が失敗した上限だけを待って先頭から再判定する。"""
    binary = tmp_path / "codex"
    calls = tmp_path / "calls"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "assert '--json' in sys.argv\n"
        "model = sys.argv[sys.argv.index('--model') + 1]\n"
        "calls = pathlib.Path(os.environ['PROBE_CALLS'])\n"
        "previous = calls.read_text().splitlines() if calls.exists() else []\n"
        "calls.write_text('\\n'.join([*previous, model]))\n"
        "mode = os.environ['PROBE_MODE']\n"
        "if len(previous) >= 2 or mode == 'fallback' and model == 'second': sys.exit(0)\n"
        "if mode == 'failure' or model == 'second' and mode != 'earliest': sys.exit(7)\n"
        "thread = '01234567-89ab-cdef-0123-456789abcdef'\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': thread}))\n"
        "directory = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions/2026/10/16'\n"
        "directory.mkdir(parents=True, exist_ok=True)\n"
        "events = [\n"
        " {'type': 'event_msg', 'payload': {'type': 'token_count', 'rate_limits': {'primary': {\n"
        "  'used_percent': 100, 'resets_at': 1010 if model == 'first' else 1005}}}},\n"
        " {'type': 'event_msg', 'payload': {'type': 'task_complete', 'error': {'codex_error_info': 'usage_limit_exceeded'}}}\n"
        "]\n"
        "(directory / ('rollout-test-' + thread + '.jsonl')).write_text('\\n'.join(json.dumps(e) for e in events))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "PROBE_CALLS": str(calls),
        "PROBE_MODE": mode,
        "CODEX_HOME": str(tmp_path / "codex-home"),
    }
    sleeps: list[float] = []
    monkeypatch.setattr(orchestrator, "time", types.SimpleNamespace(time=lambda: 1000, sleep=sleeps.append))
    candidates = [("codex", "first", "high"), ("codex", "second", "low")]
    context = orchestrator.ProbeContext(title="test", prompt="OK")
    if mode == "failure":
        with pytest.raises(orchestrator.NoAvailableCandidateError) as exc:
            orchestrator.select_available(candidates, env, tmp_path, context)
        assert exc.value.returncode == 7
        assert calls.read_text().splitlines() == ["first", "second"]
        assert not sleeps
    else:
        selected = orchestrator.select_available(candidates, env, tmp_path, context)
        assert selected == candidates[1 if mode == "fallback" else 0]
        assert calls.read_text().splitlines() == (["first", "second"] if mode == "fallback" else ["first", "second", "first"])
        assert sleeps == ([] if mode == "fallback" else [5 if mode == "earliest" else 10])
