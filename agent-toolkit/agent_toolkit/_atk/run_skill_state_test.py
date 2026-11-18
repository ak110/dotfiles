"""run-skillの実行記録の互換読込と、読めない記録があっても起動を止めない契約を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import time
import typing

import pytest

from agent_toolkit._atk import outcome, run_skill, run_skill_state
from agent_toolkit._testing.run_skill_support import _engine_env, _Env, _records, _run

__all__ = ["_engine_env"]

_OLD = time.time() - run_skill.LOG_RETENTION_SECONDS - 3600


def _write_record(env: _Env, run_id: str, connection: dict[str, typing.Any] | None) -> pathlib.Path:
    """30日より前のログを参照する中断済みの記録を、保存された形のJSONで置く。"""
    root = env.state / "agent-toolkit" / "run-skill"
    (root / "runs").mkdir(parents=True, exist_ok=True)
    log_path = root / f"20261109-000000-agent-toolkit_review-session-{run_id[:8]}.log"
    log_path.write_text("old\n", encoding="utf-8")
    os.utime(log_path, (_OLD, _OLD))
    payload = {
        "input": {
            "source": "run-skill",
            "run_id": run_id,
            "skill": "agent-toolkit:review-session",
            "arguments": "batch",
            "mode": "batch",
            "free_text": None,
            "accepted_scopes": ["agent-toolkit:review-session"],
            "target_repo": None,
            "batch_manifest": None,
        },
        "work_dir": str(env.repo),
        "log_path": str(log_path),
        "engine": "claude",
        "model": "sonnet",
        "effort": "high",
        "session_id": None,
        "connection": connection,
        "remaining": 60.0,
        "deadline": None,
        "executor": None,
        "status": "interrupted",
    }
    (root / "runs" / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return log_path


def test_legacy_isolation_env_file_record_starts_new_run(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """`isolation_env_file`を保存した旧形式の記録を読み替え、新しい実行の起動とその記録のログ保護を続ける。"""
    unset = "0" * 32
    path_valued = "1" * 32
    unset_log = _write_record(engine_env, unset, {"kind": "subscription", "api_env_file": None, "isolation_env_file": None})
    path_log = _write_record(
        engine_env, path_valued, {"kind": "subscription", "api_env_file": None, "isolation_env_file": "/secrets/credits.env"}
    )

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert outcome.WARNING_PREFIX not in err
    assert len(_records(engine_env, "session")) == 1
    assert unset_log.exists() and path_log.exists()
    unset_connection = run_skill_state.load(unset).connection
    path_connection = run_skill_state.load(path_valued).connection
    assert unset_connection is not None and unset_connection.isolation_env_files == ()
    assert path_connection is not None and path_connection.isolation_env_files == ("/secrets/credits.env",)


def test_unreadable_record_does_not_block_new_run(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """読めない記録は診断して残し、参照先を特定できないログを回収せずに新しい実行を起動する。"""
    root = engine_env.state / "agent-toolkit" / "run-skill"
    (root / "runs").mkdir(parents=True)
    broken = root / "runs" / f"{'3' * 32}.json"
    broken.write_text("{", encoding="utf-8")
    log_path = root / "20261109-000000-my-skill-unknown.log"
    log_path.write_text("old\n", encoding="utf-8")
    os.utime(log_path, (_OLD, _OLD))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert len(_records(engine_env, "session")) == 1
    assert str(broken) in err and "次の操作:" in err
    assert broken.exists() and log_path.exists()
