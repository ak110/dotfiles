"""SessionEndでの共有状態の回収と、会話破棄時の削除を検証するテスト。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

import _fork_runner
import pytest
from conftest import SESSION_STATE_FILENAME_TEMPLATE

_SCRIPT = pathlib.Path(__file__).parent / "claude_hook.py"
_STALE_AGE_SECONDS = 15 * 24 * 60 * 60


def _run(payload_text: str, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)})
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("session_end_cleanup",),
        input=payload_text,
        env=env,
    )


def _write_state(state_dir: pathlib.Path, session_id: str, *, age_seconds: float = 0.0) -> pathlib.Path:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path.write_text('{"active": true}', encoding="utf-8")
    _set_age(path, age_seconds)
    return path


def _write_lock(state_dir: pathlib.Path, session_id: str, *, age_seconds: float = 0.0) -> pathlib.Path:
    filename = SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path = state_dir / f"{filename}.lock"
    path.write_text("", encoding="utf-8")
    _set_age(path, age_seconds)
    return path


def _set_age(path: pathlib.Path, age_seconds: float) -> None:
    if age_seconds <= 0:
        return
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))


def _session_end(session_id: str, reason: str | None = None) -> str:
    payload: dict[str, object] = {"hook_event_name": "SessionEnd", "session_id": session_id}
    if reason is not None:
        payload["reason"] = reason
    return json.dumps(payload)


def test_state_is_preserved_when_session_can_resume(tmp_path: pathlib.Path) -> None:
    """会話破棄以外の理由では、自セッションの状態を残す。"""
    target = _write_state(tmp_path, "target")

    result = _run(_session_end("target", reason="prompt_input_exit"), tmp_path)

    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert target.exists()


def test_state_is_preserved_when_reason_is_absent(tmp_path: pathlib.Path) -> None:
    """理由が無い場合も削除しない。"""
    target = _write_state(tmp_path, "target")

    result = _run(_session_end("target"), tmp_path)

    assert result.returncode == 0
    assert target.exists()


def test_state_is_deleted_when_conversation_is_cleared(tmp_path: pathlib.Path) -> None:
    """会話が破棄された場合は自セッションの状態を削除し、他セッションは残す。"""
    target = _write_state(tmp_path, "target")
    other = _write_state(tmp_path, "other")

    result = _run(_session_end("target", reason="clear"), tmp_path)

    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert not target.exists()
    assert other.exists()


def test_stale_state_and_its_lock_are_collected(tmp_path: pathlib.Path) -> None:
    """期限を過ぎた状態ファイルは対のロックとともに回収する。"""
    stale = _write_state(tmp_path, "stale", age_seconds=_STALE_AGE_SECONDS)
    stale_lock = _write_lock(tmp_path, "stale", age_seconds=_STALE_AGE_SECONDS)
    fresh = _write_state(tmp_path, "fresh")
    fresh_lock = _write_lock(tmp_path, "fresh")

    result = _run(_session_end("other", reason="logout"), tmp_path)

    assert result.returncode == 0
    assert not stale.exists()
    assert not stale_lock.exists()
    assert fresh.exists()
    assert fresh_lock.exists()


def test_fresh_state_keeps_its_old_lock(tmp_path: pathlib.Path) -> None:
    """状態ファイルが期限内なら、対のロックが古くても残す。"""
    fresh = _write_state(tmp_path, "resumed")
    old_lock = _write_lock(tmp_path, "resumed", age_seconds=_STALE_AGE_SECONDS)

    result = _run(_session_end("other", reason="logout"), tmp_path)

    assert result.returncode == 0
    assert fresh.exists()
    assert old_lock.exists()


def test_orphan_lock_is_collected_only_after_expiry(tmp_path: pathlib.Path) -> None:
    """状態ファイルの無いロックは、期限を過ぎた場合だけ回収する。"""
    starting = _write_lock(tmp_path, "starting")
    abandoned = _write_lock(tmp_path, "abandoned", age_seconds=_STALE_AGE_SECONDS)

    result = _run(_session_end("other", reason="logout"), tmp_path)

    assert result.returncode == 0
    assert starting.exists()
    assert not abandoned.exists()


def test_missing_state_is_success(tmp_path: pathlib.Path) -> None:
    result = _run(_session_end("missing", reason="clear"), tmp_path)
    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr


@pytest.mark.parametrize(
    "payload_text",
    [
        "{",
        "[]",
        json.dumps({"hook_event_name": "Other", "session_id": "sid", "reason": "clear"}),
        json.dumps({"hook_event_name": "SessionEnd", "session_id": "", "reason": "clear"}),
    ],
)
def test_invalid_payload_is_ignored(tmp_path: pathlib.Path, payload_text: str) -> None:
    state = _write_state(tmp_path, "sid")
    result = _run(payload_text, tmp_path)
    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert state.exists()


def test_delete_failure_is_reported_and_fails_open(tmp_path: pathlib.Path) -> None:
    # 状態パスをディレクトリにしてunlinkを失敗させる。
    state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="sid")
    state_path.mkdir()
    result = _run(_session_end("sid", reason="clear"), tmp_path)
    assert result.returncode == 0
    assert not result.stdout
    assert "削除できませんでした" in result.stderr
