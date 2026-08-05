"""SessionEndで親セッション状態を破棄するテスト。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest

_SCRIPT = pathlib.Path(__file__).parent / "claude_hook.py"


def _run(payload_text: str, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)})
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("session_end_cleanup",),
        input=payload_text,
        env=env,
    )


def _write_state(state_dir: pathlib.Path, session_id: str) -> pathlib.Path:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text('{"active": true}', encoding="utf-8")
    return path


def test_target_session_is_deleted_and_other_session_is_preserved(tmp_path: pathlib.Path) -> None:
    target = _write_state(tmp_path, "target")
    other = _write_state(tmp_path, "other")
    payload = json.dumps({"hook_event_name": "SessionEnd", "session_id": "target"})

    result = _run(payload, tmp_path)

    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
    assert not target.exists()
    assert other.exists()


def test_missing_state_is_success(tmp_path: pathlib.Path) -> None:
    result = _run(json.dumps({"hook_event_name": "SessionEnd", "session_id": "missing"}), tmp_path)
    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr


@pytest.mark.parametrize(
    "payload_text",
    [
        "{",
        "[]",
        json.dumps({"hook_event_name": "Other", "session_id": "sid"}),
        json.dumps({"hook_event_name": "SessionEnd", "session_id": ""}),
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
    state_path = tmp_path / "claude-agent-toolkit-sid.json"
    state_path.mkdir()
    result = _run(json.dumps({"hook_event_name": "SessionEnd", "session_id": "sid"}), tmp_path)
    assert result.returncode == 0
    assert not result.stdout
    assert "削除できませんでした" in result.stderr
