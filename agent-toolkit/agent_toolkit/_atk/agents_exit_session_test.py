"""atk agents-exit-sessionの本人識別と安全な停止を検証する。"""

import json
import os
import pathlib
import sys
import time
import types
import typing

import psutil
import pytest

from agent_toolkit._atk import agents_exit_session


@pytest.mark.parametrize(
    "argv",
    [
        ["codex"],
        ["codex", "--model", "o3", "--search", "inspect"],
        ["codex", "--no-daemon", "--model", "o3", "inspect"],
        ["codex", "-mo3", "--", "inspect"],
        ["codex", "resume", "--last"],
        ["codex", "resume", "session-id", "inspect"],
        ["codex", "resume", "--no-daemon", "session-id", "inspect"],
    ],
)
def test_interactive_codex_argv_is_accepted(argv: list[str]) -> None:
    assert agents_exit_session.is_interactive_codex(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["codex", "exec", "inspect"],
        ["codex", "exec", "--no-daemon", "inspect"],
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


@pytest.mark.parametrize(
    "argv",
    [
        ["agy"],
        ["agy", "--model", "gemini-3.8-flash", "--effort", "high", "--mode", "auto", "-i", "inspect"],
        ["agy", "--conversation", "cc963364", "-i", "resume"],
        ["agy", "--mode=auto", "--dangerously-skip-permissions"],
    ],
)
def test_interactive_agy_argv_is_accepted(argv: list[str]) -> None:
    assert agents_exit_session.is_interactive_agy(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["agy", "-p", "inspect", "--output-format", "stream-json"],
        ["agy", "--print", "inspect"],
        ["agy", "--prompt=inspect"],
        ["agy", "acp"],
        ["agy", "--help"],
        ["agy", "-i"],
        ["agy", "--unknown"],
    ],
)
def test_noninteractive_or_ambiguous_agy_argv_is_rejected(argv: list[str]) -> None:
    assert not agents_exit_session.is_interactive_agy(argv)


def test_unsupported_host_reports_invocation_without_stopping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: None)
    monkeypatch.setattr(agents_exit_session.os, "kill", lambda *_args: pytest.fail("停止してはならない"))

    assert agents_exit_session.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "unsupported"
    assert "対話CLIで実行している場合は" in captured.err
    assert "/exit" in captured.err


def test_changed_target_names_rerun_and_exit_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
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
    host_os_name = os.name
    monkeypatch.setattr(
        agents_exit_session,
        "os",
        types.SimpleNamespace(name="posix", environ=os.environ, kill=lambda pid, sig: killed.append((pid, sig))),
    )
    assert os.name == host_os_name

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
    """Function hooksの読込目印が現在のセッションにあるClaude Codeでは、停止シグナルを送らずターン後の終了を要求する。"""
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
    result = json.loads(output.out)
    assert result["status"] == "exit_requested"
    assert "ターンを終えると/exit" in result["next_action"]
    assert "report-completion" in result["next_action"]
    assert "送達済み本文を再掲せず" in result["next_action"]
    assert not output.err
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
    def __init__(self, pid: int, argv: list[str], executable: pathlib.Path, terminal: str | None = None) -> None:
        self.pid = pid
        self._argv = argv
        self._executable = executable
        self._terminal = terminal

    def cmdline(self) -> list[str]:
        return self._argv

    def exe(self) -> str:
        return str(self._executable)

    def create_time(self) -> float:
        return 1.0

    def terminal(self) -> str | None:
        return self._terminal


def _identify(argv: list[str], executable: pathlib.Path, terminal: str | None = None) -> agents_exit_session.Target | None:
    process = typing.cast(psutil.Process, _FakeProcess(123, argv, executable, terminal))
    return agents_exit_session._target(process)  # pylint: disable=protected-access  # noqa: SLF001


def test_versioned_executable_with_claude_argv_is_identified(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ネイティブ導入の実体が版数名でも、argv[0]からClaude Code本体を識別する。"""
    executable = tmp_path / "2.1.269"
    executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(agents_exit_session.process_identity, "start_id", lambda process, **kwargs: "test-generation")
    target = _identify(["claude"], executable)

    assert target is not None
    assert (target.host, target.pid) == ("claude", 123)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Codexの終了対象はLinuxに限る")
def test_codex_native_alias_requires_real_codex_and_terminal(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex = tmp_path / "codex"
    other = tmp_path / "other"
    for executable in (codex, other):
        executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(agents_exit_session.process_identity, "start_id", lambda process, **kwargs: "test-generation")
    alias = str(tmp_path / ".dotfiles-codex-native")
    tty = "/dev/pts/3"

    target = _identify([alias, "--no-daemon", "inspect"], codex, tty)
    assert target is not None and target.host == "codex"
    assert _identify([alias, "--no-daemon", "inspect"], other, tty) is None
    assert _identify([alias, "--no-daemon", "inspect"], codex) is None
    assert _identify([alias, "exec", "inspect"], codex, tty) is None
    assert _identify([alias, "--remote", "server"], codex, tty) is None
    assert _identify(["different-alias", "inspect"], codex, tty) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="agyの終了対象はLinuxに限る")
def test_interactive_agy_with_terminal_is_identified(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "agy"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(agents_exit_session.process_identity, "start_id", lambda process, **kwargs: "test-generation")
    interactive = ["agy", "--mode", "auto", "-i", "inspect"]

    target = _identify(interactive, executable, "/dev/pts/3")

    assert target is not None
    assert (target.host, target.pid) == ("agy", 123)
    assert _identify(interactive, executable, None) is None
    assert _identify(["agy", "-p", "inspect"], executable, "/dev/pts/3") is None


def test_unrelated_process_is_not_identified(tmp_path: pathlib.Path) -> None:
    executable = tmp_path / "python3"
    executable.write_text("", encoding="utf-8")

    assert _identify(["python3", "app.py"], executable) is None


@pytest.mark.parametrize("clear", [False, True])
def test_sweep_function_hook_files_includes_recheck(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, clear: bool
) -> None:
    """受渡しを含む現行会話の記録を保持し、期限切れとclear対象だけを回収する。"""
    directory = tmp_path / "agent-toolkit-function-hooks"
    directory.mkdir()
    now = 10_000_000.0
    expired = now - agents_exit_session._STATE_MAX_AGE_SECONDS - 1  # pylint: disable=protected-access  # noqa: SLF001
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(agents_exit_session.time, "time", lambda: now)
    for prefix in ("marker", "request", "recheck"):
        for session_id, modified_at in (("own", expired), ("old", expired), ("fresh", now)):
            path = directory / f"{prefix}-{session_id}.txt"
            path.write_text("consumed", encoding="utf-8")
            os.utime(path, (modified_at, modified_at))
    unrelated = directory / "notes-old.txt"
    unrelated.write_text("保持する", encoding="utf-8")
    os.utime(unrelated, (expired, expired))

    agents_exit_session.sweep_function_hook_files(keep_session_id="own", clear=clear)

    for prefix in ("marker", "request", "recheck"):
        assert (directory / f"{prefix}-own.txt").exists() is not clear
        assert not (directory / f"{prefix}-old.txt").exists()
        assert (directory / f"{prefix}-fresh.txt").is_file()
    assert unrelated.is_file()


@pytest.mark.parametrize(
    ("case", "requested"),
    [
        ("ready", True),
        ("no-launch", False),
        ("no-marker", False),
        ("stale-marker", False),
        ("disabled", False),
        ("delegated", False),
        ("previous-turn", False),
        ("delivery-error", False),
    ],
)
def test_request_completion_exit_is_fail_closed(
    case: str,
    requested: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_toolkit._common import completion_exit

    session, launch = "session-a", "a" * 32
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session)
    monkeypatch.delenv(completion_exit.DISABLE_ENV, raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    if case != "no-launch":
        monkeypatch.setenv(completion_exit.LAUNCH_ENV, launch)
    else:
        monkeypatch.delenv(completion_exit.LAUNCH_ENV, raising=False)
    if case == "disabled":
        monkeypatch.setenv(completion_exit.DISABLE_ENV, "1")
    if case == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.setattr(agents_exit_session, "identify_current_host", lambda: _claude_target(tmp_path))
    monkeypatch.setattr(agents_exit_session, "request_termination", lambda **_: pytest.fail("停止してはならない"))
    paths = agents_exit_session.function_hook_paths(session)
    assert paths is not None
    marker, _ = paths
    marker.parent.mkdir()
    if case != "no-marker":
        marker.write_text("ready", encoding="utf-8")
    if case == "stale-marker":
        os.utime(marker, (1, 1))
    record = tmp_path / "projects" / "repo" / f"{session}.jsonl"
    record.parent.mkdir(parents=True)
    entries = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-a",
                        "name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
                        "input": {"kind": "completion", "message": "今回の報告"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-a",
                        "is_error": case == "delivery-error",
                        "content": [{"type": "text", "text": '{"ok": true}'}],
                    }
                ]
            },
        },
    ]
    if case == "previous-turn":
        entries.append({"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": "新しい依頼"}})
    record.write_text("".join(json.dumps(event) + "\n" for event in entries), encoding="utf-8")

    assert agents_exit_session.request_completion_exit() == 0
    output = capsys.readouterr().out
    if requested:
        assert "追加のツールを呼ばず、通常どおりターンを終えてください。" in output
    else:
        assert output.startswith("自動終了しない: ")
    state = completion_exit.state_path(launch, root=tmp_path)
    assert state is not None
    assert state.exists() is requested
    if requested:
        assert completion_exit.read_state(state) == {"session": session, "call": "call-a", "status": "requested"}
