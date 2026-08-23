"""SessionEndでの共有状態の回収と、会話破棄時の削除を検証するテスト。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

import _fork_runner
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE

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


def _write_title_state(state_dir: pathlib.Path, session_id: str) -> tuple[pathlib.Path, pathlib.Path]:
    directory = state_dir / "claude-agent-toolkit-session-title"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    path.write_text(json.dumps({"last_hook_session_title": "plan"}), encoding="utf-8")
    lock = path.with_name(path.name + ".lock")
    lock.write_text("", encoding="utf-8")
    return path, lock


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
    title, title_lock = _write_title_state(tmp_path, "target")

    result = _run(_session_end("target", reason="prompt_input_exit"), tmp_path)

    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert target.exists()
    assert title.exists()
    assert title_lock.exists()


def test_state_is_preserved_when_reason_is_absent(tmp_path: pathlib.Path) -> None:
    """理由が無い場合も削除しない。"""
    target = _write_state(tmp_path, "target")

    result = _run(_session_end("target"), tmp_path)

    assert result.returncode == 0
    assert target.exists()


def test_state_is_deleted_when_conversation_is_cleared(tmp_path: pathlib.Path) -> None:
    """会話破棄時は自セッションの両状態を削除するが対応するロックは残し、他セッションを残す。"""
    target = _write_state(tmp_path, "target")
    target_lock = _write_lock(tmp_path, "target")
    title, title_lock = _write_title_state(tmp_path, "target")
    other = _write_state(tmp_path, "other")

    result = _run(_session_end("target", reason="clear"), tmp_path)

    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert not target.exists()
    assert target_lock.exists()
    assert not title.exists()
    assert title_lock.exists()
    assert other.exists()


def test_stale_state_is_collected_but_its_lock_is_kept(tmp_path: pathlib.Path) -> None:
    """期限を過ぎた状態ファイルは回収するが、対応するロックは削除しない。"""
    stale = _write_state(tmp_path, "stale", age_seconds=_STALE_AGE_SECONDS)
    stale_lock = _write_lock(tmp_path, "stale", age_seconds=_STALE_AGE_SECONDS)
    fresh = _write_state(tmp_path, "fresh")
    fresh_lock = _write_lock(tmp_path, "fresh")
    stale_title, stale_title_lock = _write_title_state(tmp_path, "stale")

    result = _run(_session_end("other", reason="logout"), tmp_path)

    assert result.returncode == 0
    assert not stale.exists()
    assert stale_lock.exists()
    assert fresh.exists()
    assert fresh_lock.exists()
    assert stale_title.exists()
    assert stale_title_lock.exists()


def test_fresh_state_keeps_its_old_lock(tmp_path: pathlib.Path) -> None:
    """状態ファイルが期限内なら、対のロックが古くても残す。"""
    fresh = _write_state(tmp_path, "resumed")
    old_lock = _write_lock(tmp_path, "resumed", age_seconds=_STALE_AGE_SECONDS)

    result = _run(_session_end("other", reason="logout"), tmp_path)

    assert result.returncode == 0
    assert fresh.exists()
    assert old_lock.exists()


def test_orphan_lock_is_never_collected(tmp_path: pathlib.Path) -> None:
    """状態ファイルの無いロックは、期限をいくら過ぎても回収しない。"""
    starting = _write_lock(tmp_path, "starting")
    abandoned = _write_lock(tmp_path, "abandoned", age_seconds=_STALE_AGE_SECONDS)

    result = _run(_session_end("other", reason="logout"), tmp_path)

    assert result.returncode == 0
    assert starting.exists()
    assert abandoned.exists()


def test_codex_other_reason_collects_stale_and_keeps_own_state(tmp_path: pathlib.Path) -> None:
    """Codexの`reason: other`では期限切れ状態だけを回収し、当該セッションの状態は残す。"""
    own = _write_state(tmp_path, "codex-session")
    stale = _write_state(tmp_path, "stale", age_seconds=_STALE_AGE_SECONDS)
    stale_lock = _write_lock(tmp_path, "stale", age_seconds=_STALE_AGE_SECONDS)
    payload = json.dumps(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "codex-session",
            "reason": "other",
            "turn_id": "turn-1",
        }
    )

    result = _run(payload, tmp_path)

    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert own.exists()
    assert not stale.exists()
    assert stale_lock.exists()


def test_codex_other_reason_keeps_own_expired_state(tmp_path: pathlib.Path) -> None:
    """当該セッションの状態は期限を過ぎていても残し、別セッションの期限切れは回収する。"""
    own = _write_state(tmp_path, "codex-session", age_seconds=_STALE_AGE_SECONDS)
    own_lock = _write_lock(tmp_path, "codex-session", age_seconds=_STALE_AGE_SECONDS)
    stale = _write_state(tmp_path, "stale", age_seconds=_STALE_AGE_SECONDS)
    payload = json.dumps(
        {
            "hook_event_name": "SessionEnd",
            "session_id": "codex-session",
            "reason": "other",
            "turn_id": "turn-1",
        }
    )

    result = _run(payload, tmp_path)

    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert own.exists()
    assert own_lock.exists()
    assert not stale.exists()


def test_own_expired_state_is_deleted_when_conversation_is_cleared(tmp_path: pathlib.Path) -> None:
    """会話破棄時は、当該セッションの期限切れ状態も削除する。"""
    own = _write_state(tmp_path, "target", age_seconds=_STALE_AGE_SECONDS)

    result = _run(_session_end("target", reason="clear"), tmp_path)

    assert result.returncode == 0
    assert not result.stderr
    assert not own.exists()


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
