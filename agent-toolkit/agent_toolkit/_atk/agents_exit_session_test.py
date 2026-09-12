"""atk agents-exit-sessionの本人識別と安全な停止を検証する。"""

import json
import pathlib

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
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"


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
