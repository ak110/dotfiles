"""hook計測の入力非保存と有限保持を検証する。"""

import importlib
import json
import time

import pytest

from agent_toolkit._atk import run_skill_batch
from agent_toolkit._atk.managed_temp import paths
from agent_toolkit._hooks import rules_context, timing
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


def test_new_session_start_records_internal_stages(tmp_path, monkeypatch, capsys):
    """領域がまだ無い新規セッションでも、初回の計測を保存する。"""
    monkeypatch.setattr(paths, "_temp_root_path", lambda: tmp_path / "temp")
    monkeypatch.setattr(paths, "_state_root_path", lambda: tmp_path / "state")
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(run_skill_batch, "hook_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rules_context, "is_delegated", lambda _env: False)
    monkeypatch.setattr(rules_context, "_conversation_identity_notice", lambda *_args: "")
    assert timing.managed_temp.find_session_temp("new-timing-session") is None
    assert (
        rules_context.main(
            json.dumps({"session_id": "new-timing-session", "hook_event_name": "SessionStart", "source": "startup"})
        )
        == 0
    )
    capsys.readouterr()
    directory = timing.managed_temp.find_session_temp("new-timing-session")
    assert directory is not None
    rows = [json.loads(line) for line in (directory / "hook-timings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["stage"] for row in rows} >= {"normative-content", "review-session-request"}


def test_timing_records_only_operational_metadata(tmp_path, monkeypatch):
    """入力本文とセッション識別子をログへ保存しない。"""
    monkeypatch.setattr(timing.managed_temp, "find_session_temp", lambda _session: tmp_path)
    timing.record(
        {"session_id": "secret-id", "hook_event_name": "PreToolUse", "prompt": "secret-input"},
        "pretooluse",
        time.monotonic(),
        "ok",
    )
    body = (tmp_path / "hook-timings.jsonl").read_text(encoding="utf-8")
    row = json.loads(body)
    assert set(row) == {"event", "operation", "stage", "seconds", "status"}
    assert row["event"] == "PreToolUse" and row["status"] == "ok" and row["seconds"] >= 0
    assert "secret" not in body


def test_timing_retains_recent_complete_records_within_limit(tmp_path, monkeypatch):
    """上限到達後も最新行と完全なJSON Linesを保持する。"""
    monkeypatch.setattr(timing.managed_temp, "find_session_temp", lambda _session: tmp_path)
    monkeypatch.setattr(timing, "_MAX_BYTES", 400)
    for index in range(20):
        timing.record(
            {"session_id": "s", "hook_event_name": "SessionStart"},
            "rules_context",
            time.monotonic(),
            str(index),
            stage="normative-content",
        )
    path = tmp_path / "hook-timings.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert path.stat().st_size <= 400
    assert rows[-1]["status"] == "19"
    assert len(rows) < 20


@pytest.mark.parametrize("failure", [False, True])
def test_public_hook_records_event_and_result(tmp_path, monkeypatch, failure):
    """公開entrypointの正常処理と読込障害を、保存した診断の成否へ対応付ける。"""
    monkeypatch.setattr(timing.managed_temp, "find_session_temp", lambda _session: tmp_path)
    original = importlib.import_module

    def load(name, *args, **kwargs):
        if failure and name == "agent_toolkit._hooks.pretooluse":
            raise ImportError("計測用の故障注入")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", load)
    command = "pretooluse" if failure else "permissionrequest"
    result = _run_hook_in_process(
        command,
        {"session_id": "timing-public", "hook_event_name": "PreToolUse" if failure else "PermissionRequest"},
    )
    assert result.returncode == 0 and not result.stderr
    rows = [json.loads(line) for line in (tmp_path / "hook-timings.jsonl").read_text(encoding="utf-8").splitlines()]
    total = [row for row in rows if row["operation"] == command and row["stage"] == "total"]
    assert total[-1]["status"] == ("import-error" if failure else "returned-zero")
    assert total[-1]["event"] == ("PreToolUse" if failure else "PermissionRequest")
    assert total[-1]["seconds"] >= 0
