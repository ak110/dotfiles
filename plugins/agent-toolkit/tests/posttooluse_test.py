"""plugins/agent-toolkit/scripts/posttooluse.py のテスト。

PostToolUse セッション状態記録のテスト。
subprocess で起動し exit code・状態ファイルの内容を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class TestTestExecution:
    """テスト実行検出。"""

    @pytest.mark.parametrize(
        "command",
        [
            "pytest",
            "uv run pytest -v",
            "python -m pytest tests/",
            "make test",
            "pyfltr run --output-format=jsonl",
            "uv run pyfltr ci",
            "npm test",
            "pnpm test",
            "pnpm run test",
            "cargo test",
        ],
    )
    def test_test_commands_detected(self, tmp_path: pathlib.Path, command: str):
        sid = "test-exec-detect"
        result = _run(
            {"session_id": sid, "tool_input": {"command": command}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is True, f"command={command!r} not detected"

    def test_unrelated_command_no_change(self, tmp_path: pathlib.Path):
        sid = "test-unrelated"
        _run(
            {"session_id": sid, "tool_input": {"command": "echo hello"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("test_executed") is not True


class TestGitStatusCheck:
    """Git 状態確認検出。"""

    @pytest.mark.parametrize("command", ["git status", "git log --decorate --oneline -5", "git diff"])
    def test_git_commands_detected(self, tmp_path: pathlib.Path, command: str):
        sid = "test-git-status"
        _run(
            {"session_id": sid, "tool_input": {"command": command}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("git_status_checked") is True


class TestCodexResume:
    """codex exec resume 検出。"""

    def test_resume_increments_count(self, tmp_path: pathlib.Path):
        sid = "test-codex-resume"
        for _ in range(3):
            _run(
                {"session_id": sid, "tool_input": {"command": "codex exec resume --dangerously-bypass abc123 prompt"}},
                state_dir=tmp_path,
            )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_resume_count") == 3

    def test_initial_exec_not_counted(self, tmp_path: pathlib.Path):
        sid = "test-codex-initial"
        _run(
            {"session_id": sid, "tool_input": {"command": "codex exec --dangerously-bypass plan.md prompt"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_resume_count", 0) == 0


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_exits_zero(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0

    def test_missing_session_id(self, tmp_path: pathlib.Path):
        result = _run({"tool_input": {"command": "pytest"}}, state_dir=tmp_path)
        assert result.returncode == 0

    def test_missing_command(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "x", "tool_input": {}}, state_dir=tmp_path)
        assert result.returncode == 0

    def test_silent_output(self, tmp_path: pathlib.Path):
        """PostToolUse は stdout に何も出さない。"""
        result = _run(
            {"session_id": "silent", "tool_input": {"command": "pytest"}},
            state_dir=tmp_path,
        )
        assert result.stdout == ""
