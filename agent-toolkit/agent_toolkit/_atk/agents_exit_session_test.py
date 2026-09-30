"""atk agents-exit-sessionの本人識別と安全な停止を検証する。"""

import json
import os
import pathlib
import time
import typing

import psutil
import pytest

from agent_toolkit._atk import agents_exit_session


@pytest.mark.parametrize(
    "argv",
    [
        ["codex"],
        ["codex", "--model", "o3", "--search", "inspect"],
        ["codex", "-mo3", "--", "inspect"],
        ["codex", "resume", "--last"],
        ["codex", "resume", "session-id", "inspect"],
    ],
)
def test_interactive_codex_argv_is_accepted(argv: list[str]) -> None:
    assert agents_exit_session.is_interactive_codex(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["codex", "exec", "inspect"],
        ["codex", "app-server"],
        ["codex", "remote-control"],
        ["codex", "--remote", "server"],
        ["codex", "--help"],
        ["codex", "--model"],
        ["codex", "-zvalue"],
    ],
)
def test_noninteractive_or_ambiguous_codex_argv_is_rejected(argv: list[str]) -> None:
    assert not agents_exit_session.is_interactive_codex(argv)


def test_unsupported_host_reports_invocation_without_stopping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: None)
    monkeypatch.setattr(agents_exit_session.os, "kill", lambda *_args: pytest.fail("停止してはならない"))

    assert agents_exit_session.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "unsupported"
    assert "次の操作: /exit" in captured.err


def test_changed_target_names_rerun_and_exit_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    """識別後に終了対象が変化した場合は停止せず、再実行か/exitの入力を案内する。"""
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: _claude_target(tmp_path))
    monkeypatch.setattr(agents_exit_session, "_same_process", lambda _target: False)
    monkeypatch.setattr(agents_exit_session.os, "kill", lambda *_args: pytest.fail("停止してはならない"))

    assert agents_exit_session.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "changed"
    next_action_lines = [line for line in captured.err.splitlines() if line.startswith("次の操作: ")]
    assert len(next_action_lines) == 1
    assert "atk agents-exit-session" in next_action_lines[0]
    assert "/exit" in next_action_lines[0]


def test_rechecked_target_is_terminated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    executable = tmp_path / "codex"
    executable.write_text("", encoding="utf-8")
    stat = executable.stat()
    target = agents_exit_session.Target(123, "codex", 1.0, executable, stat.st_dev, stat.st_ino)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: target)
    monkeypatch.setattr(agents_exit_session, "_same_process", lambda _target: True)
    monkeypatch.setattr(agents_exit_session.os, "name", "posix")
    monkeypatch.setattr(agents_exit_session.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert agents_exit_session.main() == 0
    assert killed and killed[0][0] == 123
    assert json.loads(capsys.readouterr().out)["status"] == "terminating"


def _claude_target(tmp_path: pathlib.Path) -> agents_exit_session.Target:
    executable = tmp_path / "claude"
    executable.write_text("", encoding="utf-8")
    stat = executable.stat()
    return agents_exit_session.Target(321, "claude", time.time() - 60, executable, stat.st_dev, stat.st_ino)


def test_exit_requested_with_current_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    """読込済みのClaude Codeでは停止シグナルを送らずターン後の終了を要求する。"""
    target = _claude_target(tmp_path)
    directory = tmp_path / "agent-toolkit-function-hooks"
    directory.mkdir()
    (directory / "marker-session-1.txt").write_text("ready", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-1")
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: target)
    monkeypatch.setattr(agents_exit_session, "_same_process", lambda _target: True)
    monkeypatch.setattr(agents_exit_session.os, "kill", lambda *_args: pytest.fail("停止してはならない"))

    assert agents_exit_session.main() == 0

    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "exit_requested"
    assert "ターンを終えると/exit" in output.err
    assert (directory / "request-session-1.txt").read_text(encoding="utf-8") == "requested"


@pytest.mark.parametrize("session_id", [None, "session-2"])
def test_missing_marker_or_session_id_keeps_signal_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path, session_id: str | None
) -> None:
    """目印かIDを欠くClaude Codeには従来のSIGTERMを送る。"""
    target = _claude_target(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    if session_id is None:
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session_id)
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: target)
    monkeypatch.setattr(agents_exit_session, "_same_process", lambda _target: True)
    killed: list[int] = []
    monkeypatch.setattr(agents_exit_session.os, "kill", lambda pid, _sig: killed.append(pid))

    assert agents_exit_session.main() == 0

    assert killed == [target.pid]
    assert json.loads(capsys.readouterr().out)["status"] == "terminating"


def test_old_marker_from_resume_keeps_signal_path(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """再開前の目印だけが残るセッションではFunction hooksを有効と見なさない。"""
    target = _claude_target(tmp_path)
    directory = tmp_path / "agent-toolkit-function-hooks"
    directory.mkdir()
    marker = directory / "marker-session-3.txt"
    marker.write_text("ready", encoding="utf-8")
    os.utime(marker, (target.create_time - 1, target.create_time - 1))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-3")
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: target)
    monkeypatch.setattr(agents_exit_session, "_same_process", lambda _target: True)
    killed: list[int] = []
    monkeypatch.setattr(agents_exit_session.os, "kill", lambda pid, _sig: killed.append(pid))

    assert agents_exit_session.main() == 0
    assert killed == [target.pid]
    assert not (directory / "request-session-3.txt").exists()


class _FakeProcess:
    """`_target`が参照する属性だけを返す検査用のプロセス。"""

    def __init__(self, pid: int, argv: list[str], executable: pathlib.Path) -> None:
        self.pid = pid
        self._argv = argv
        self._executable = executable

    def cmdline(self) -> list[str]:
        return self._argv

    def exe(self) -> str:
        return str(self._executable)

    def create_time(self) -> float:
        return 1.0


def _identify(argv: list[str], executable: pathlib.Path) -> agents_exit_session.Target | None:
    process = typing.cast(psutil.Process, _FakeProcess(123, argv, executable))
    return agents_exit_session._target(process)  # pylint: disable=protected-access  # noqa: SLF001


def test_versioned_executable_with_claude_argv_is_identified(tmp_path: pathlib.Path) -> None:
    """ネイティブ導入の実体が版数名でも、argv[0]からClaude Code本体を識別する。"""
    executable = tmp_path / "2.1.269"
    executable.write_text("", encoding="utf-8")

    target = _identify(["claude"], executable)

    assert target is not None
    assert (target.host, target.pid) == ("claude", 123)


def test_unrelated_process_is_not_identified(tmp_path: pathlib.Path) -> None:
    """対話CLIのいずれにも一致しないプロセスは識別しない。"""
    executable = tmp_path / "python3"
    executable.write_text("", encoding="utf-8")

    assert _identify(["python3", "app.py"], executable) is None
