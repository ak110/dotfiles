"""停滞検知完了記録CLIのテスト。"""

from __future__ import annotations

import pathlib

from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import _read_state

_SCRIPT = pathlib.Path(__file__).with_name("record_stall_detection.py")


def test_records_task_id_in_session_state(tmp_path: pathlib.Path) -> None:
    """正しい入力を対象別の時刻記録として保存する。"""
    result = _fork_runner.run_script(
        _SCRIPT,
        argv=("--session-id", "session-1", "--task-id", "task-1"),
        env={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert result.returncode == 0
    assert isinstance(_read_state(tmp_path, "session-1")["stall_detection_completed_at_by_task"]["task-1"], float)


def test_rejects_whitespace_in_identifier(tmp_path: pathlib.Path) -> None:
    """空白を含む対象識別子を受理しない。"""
    result = _fork_runner.run_script(
        _SCRIPT,
        argv=("--session-id", "session-1", "--task-id", "bad task"),
        env={"TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )
    assert result.returncode == 2
    assert "空白と制御文字" in result.stderr
