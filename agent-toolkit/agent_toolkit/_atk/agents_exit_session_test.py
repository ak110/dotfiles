"""atk agents-exit-sessionの本人識別と安全な停止を検証する。"""

import json
import pathlib
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
