"""登録全体ループのmise更新契機と、共有更新コマンドの契約を検証する。"""

import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_mise as mise
from agent_toolkit._atk.wi import process_loop_update as update
from agent_toolkit._atk.wi import process_loop_watch as watch
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    isolate_process_loop_commands(monkeypatch, tmp_path)


@pytest.mark.parametrize(
    ("flags", "times", "expected"),
    [
        ([], [0.0], 1),
        (["--internal-mise-refreshed"], [0.0], 0),
        (["--no-update"], [0.0], 0),
        ([], [0.0, 86399.0, 86400.0, 86401.0], 2),
    ],
)
def test_registered_dotfiles_refresh_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, flags: list[str], times: list[float], expected: int
) -> None:
    """初回印、更新無効、24時間未満と到達を区別し、登録全体で一度だけ更新する。"""
    _setup_notes(tmp_path)
    repository = git_repository.init_repository(tmp_path / "dotfiles", origin="https://github.com/ak110/dotfiles.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(repository)]))
    monkeypatch.setattr(update, "resolve_dotfiles_root", lambda: repository)
    clock = [times[0]]
    monkeypatch.setattr(loop.time, "monotonic", lambda: clock[0])
    remaining = iter(times[1:])
    refreshes: list[pathlib.Path] = []

    def refresh(root: pathlib.Path) -> bool:
        refreshes.append(root)
        return True

    def wait(*_args: object, **_kwargs: object) -> bool:
        try:
            clock[0] = next(remaining)
        except StopIteration:
            raise KeyboardInterrupt from None
        return True

    monkeypatch.setattr(mise, "refresh_mise_tools", refresh)
    monkeypatch.setattr(watch, "wait_for_changes", wait)
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "process-loop", "--no-alerts", *flags], home=tmp_path)
    assert exc.value.code == 0
    assert refreshes == [repository] * expected


def test_mise_command_uses_dotfiles_root_and_quiet_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """共有mise処理は対象root、上限、lockfileを保持する設定で起動する。"""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mise.process_tree, "run_with_timeout", run)
    assert mise.refresh_mise_tools(tmp_path)
    command, kwargs = calls[0]
    assert command == ["/resolved/mise", "install", "--quiet"]
    assert kwargs["cwd"] == tmp_path and kwargs["timeout"] == 600 and kwargs["capture_output"] is True
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["MISE_LOCKED"] == "1" and environment["MISE_LOCKED_SCOPES"] == "project"


@pytest.mark.parametrize(("failure", "detail"), [("nonzero", "exit code 7"), ("timeout", "途中出力")])
def test_mise_failure_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], failure: str, detail: str
) -> None:
    """非0終了とtimeoutは警告へ変換し、登録ループの独立した処理を継続する。"""

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 600, stderr="途中出力")
        return subprocess.CompletedProcess(command, 7, "", "registry error")

    monkeypatch.setattr(mise.process_tree, "run_with_timeout", run)
    assert not mise.refresh_mise_tools(tmp_path)
    warning = capsys.readouterr().err
    assert "process-loopを継続します" in warning and detail in warning
