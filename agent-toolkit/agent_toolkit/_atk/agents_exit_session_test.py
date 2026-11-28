"""atk agents-exit-sessionの本人識別と安全な停止を検証する。"""

import json
import os
import pathlib
import subprocess
import sys
import time
import types
import typing

import psutil
import pytest

from agent_toolkit._atk import agents_exit_session, lane
from agent_toolkit._common import completion_exit
from agent_toolkit._testing.helpers import repl_send_to_user_entries


def test_function_hook_paths_does_not_load_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from agent_toolkit._atk.agents_exit_session import function_hook_paths; "
            "paths = function_hook_paths('path-only'); assert paths is not None; "
            "assert 'agent_toolkit._atk.lane' not in sys.modules; print(paths[0].name, paths[1].name)",
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.split() == ["marker-path-only.txt", "request-path-only.txt"]


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


@pytest.mark.parametrize("registered, orphan", [(True, False), (False, True), (True, True)])
def test_exit_cleanup_warns_with_the_retry_command_for_each_resource(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
    *,
    registered: bool,
    orphan: bool,
) -> None:
    monkeypatch.setattr(lane, "current_session_id", lambda: "session-test")
    monkeypatch.setattr(
        lane,
        "cleanup_session",
        lambda *_args, **_kwargs: {"retained": ["lane-01"] if registered else [], "result_path": "/saved-result"},
    )
    monkeypatch.setattr(agents_exit_session, "is_delegated", lambda _environ: False)

    monkeypatch.setattr(agents_exit_session.lane_cleanup, "exit_orphan_target", lambda *_args: (tmp_path, "refs/heads/main"))

    def cleanup_orphans(_identifier, _target, _cwd, cleanup):
        cleanup["orphans"] = {"retained": ["orphan"] if orphan else []}

    monkeypatch.setattr(agents_exit_session.lane_cleanup, "cleanup_exit_orphans", cleanup_orphans)
    monkeypatch.setattr(agents_exit_session, "request_termination", lambda **_kwargs: ("unsupported", None))

    assert agents_exit_session.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "unsupported"
    assert ("--session-id session-test" in captured.err) is registered
    assert ("--list --repo <統合先worktree>" in captured.err) is orphan


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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
        ("delivery-error-other-call", False),
        ("ordinary-message-error", False),
        ("no-report", False),
        ("repl", True),
        ("repl-error", False),
        ("repl-not-completion", False),
        ("repl-previous-turn", False),
        ("repl-unwritten", False),
        ("repl-unwritten-with-old-success", False),
        ("repl-body-ref", True),
        ("repl-body-ref-unrecorded", False),
        ("repl-body-ref-other-session", False),
        ("repl-body-ref-other-id", False),
        ("repl-body-ref-error", False),
        ("repl-body-ref-previous-turn", False),
    ],
)
def test_request_completion_exit_is_fail_closed(
    case: str,
    requested: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    entries: list[dict] = [
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
                        "tool_use_id": "call-b" if case == "delivery-error-other-call" else "call-a",
                        "is_error": case in {"delivery-error", "delivery-error-other-call", "ordinary-message-error"},
                        "content": [{"type": "text", "text": '{"ok": true, "detail": "私的なエラー詳細"}'}],
                    }
                ]
            },
        },
    ]
    if case == "ordinary-message-error":
        entries[0]["message"]["content"][0]["input"].pop("kind")
    if case == "no-report":
        entries.clear()
    if case.startswith("repl"):
        entries = repl_send_to_user_entries(
            "call-a",
            {"kind": "completion", "message": "今回の報告"} if case != "repl-not-completion" else {"message": "今回の報告"},
            ok=case != "repl-error",
        )
    if case == "repl-unwritten":
        entries = [entry for entry in entries if not entry.get("isVirtual")]
    if case == "repl-unwritten-with-old-success":
        entries = [
            *repl_send_to_user_entries("call-old", {"kind": "completion", "message": "前回の報告"}, ok=True),
            {"type": "user", "message": {"content": "新しい依頼"}},
            *(entry for entry in entries if not entry.get("isVirtual") or entry["type"] == "assistant"),
        ]
    if case.startswith("repl-body-ref"):
        call = next(entry for entry in entries if entry.get("isVirtual") and entry["type"] == "assistant")
        call["message"]["content"][0]["input"] = {"kind": "completion", "body_ref": "existing-draft"}
        receipt = next(entry for entry in entries if entry.get("isVirtual") and entry["type"] == "user")
        receipt["toolUseResult"] = json.dumps({"ok": not case.endswith("-error"), "id": "a" * 32})
        if not case.endswith("-unrecorded"):
            assert completion_exit.save_receipt(
                "other-session" if case.endswith("-other-session") else session,
                "b" * 32 if case.endswith("-other-id") else "a" * 32,
                "送達済みの本文。",
                root=tmp_path,
            )
    if case.endswith("previous-turn"):
        entries.append({"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": "新しい依頼"}})
    record.write_text("".join(json.dumps(event) + "\n" for event in entries), encoding="utf-8")

    assert agents_exit_session.request_completion_exit() == 0
    output = capsys.readouterr().out
    if requested:
        assert "追加のツールを呼ばず、通常どおりターンを終えてください。" in output
    else:
        assert output.startswith("自動終了しない: ")
    if case in {"delivery-error", "repl-error", "repl-body-ref-error"}:
        assert "このターンの完了報告の送達に失敗" in output
        assert "エラーを解消し" in output and "ok: true" in output
        assert "同じREPL" not in output and "前のターン" not in output
    if case.endswith("previous-turn"):
        assert "前のターンの完了報告では自動終了できない" in output
        assert "このターンの完了報告が未送信なら送信" in output and "別のツール呼び出し" in output
        assert "同じREPL" not in output and "送達に失敗" not in output
    if case in {
        "repl-unwritten",
        "repl-unwritten-with-old-success",
        "repl-not-completion",
        "no-report",
        "delivery-error-other-call",
        "ordinary-message-error",
    }:
        assert "記録では送達の成功を確認できない" in output
        assert "同じREPLスクリプトに置いた場合" in output and "別のツール呼び出し" in output
        assert "送達に失敗" not in output and "前のターン" not in output
    if case in {"repl-body-ref-unrecorded", "repl-body-ref-other-session", "repl-body-ref-other-id"}:
        assert "送達は成功" in output and "本文の写し" in output and "本文は再送しない" in output
        assert "同じREPL" not in output and "送達に失敗" not in output
    assert "私的なエラー詳細" not in output
    state = completion_exit.state_path(launch, root=tmp_path)
    assert state is not None
    assert state.exists() is requested
    if requested:
        assert completion_exit.read_state(state) == {"session": session, "call": "call-a", "status": "requested"}
