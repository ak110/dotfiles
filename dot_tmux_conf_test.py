"""tmux設定原本のウィンドウ書式を検証する。"""

import collections.abc
import os
import pathlib
import shlex
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent
TMUX_CONF = REPO_ROOT / ".chezmoi-source" / "dot_tmux.conf"
WINDOW_FORMAT_NAMES = (
    "@catppuccin_window_current_text",
    "@catppuccin_window_text",
)


def _read_window_formats() -> dict[str, str]:
    values: dict[str, list[str]] = {name: [] for name in WINDOW_FORMAT_NAMES}
    for line in TMUX_CONF.read_text(encoding="utf-8").splitlines():
        fields = shlex.split(line, comments=False)
        if len(fields) != 4 or fields[:2] != ["set", "-g"]:
            continue
        name = fields[2]
        if name in values:
            values[name].append(fields[3])

    assert all(len(entries) == 1 for entries in values.values()), values
    return {name: entries[0] for name, entries in values.items()}


def _run_tmux(socket_name: str, *args: str) -> str:
    completed = subprocess.run(
        ["tmux", "-L", socket_name, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.removesuffix("\n")


def _render_window(socket_name: str, window_id: str, format_value: str) -> str:
    return _run_tmux(socket_name, "display-message", "-t", window_id, "-p", format_value)


def _set_cmd_running(socket_name: str, pane_id: str, value: str) -> None:
    _run_tmux(socket_name, "set-option", "-p", "-t", pane_id, "-q", "@cmd_running", value)


def _unset_cmd_running(socket_name: str, pane_id: str) -> None:
    _run_tmux(socket_name, "set-option", "-p", "-t", pane_id, "-q", "-u", "@cmd_running")


@pytest.fixture(name="tmux_server")
def tmux_server_fixture(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> collections.abc.Iterator[tuple[str, str]]:
    """ホーム設定を読まない隔離tmuxサーバーを用意する。"""
    if shutil.which("tmux") is None:
        pytest.skip("tmuxコマンドが存在しない")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TMUX_TMPDIR", "/tmp")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    socket_name = f"u002-{os.getpid()}-{tmp_path.name}"
    session_name = "u002"
    try:
        started = subprocess.run(
            [
                "tmux",
                "-L",
                socket_name,
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                session_name,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert started.returncode == 0, started.stderr
        yield socket_name, session_name
    finally:
        subprocess.run(
            ["tmux", "-L", socket_name, "kill-server"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def test_window_formats_share_pane_aggregate_condition() -> None:
    """両ウィンドウ書式がペイン集約の同じ実行状態条件を持つ。"""
    values = _read_window_formats()
    current_value = values["@catppuccin_window_current_text"]

    assert current_value == values["@catppuccin_window_text"]
    assert "#{m:*1*," in current_value
    assert "#{P:#{@cmd_running}}" in current_value
    assert "#W" in current_value


@pytest.mark.parametrize("format_name", WINDOW_FORMAT_NAMES)
def test_window_format_evaluates_running_panes(
    tmux_server: tuple[str, str],
    format_name: str,
) -> None:
    """各ウィンドウ書式がペインの実行状態を描画へ反映する。"""
    socket_name, session_name = tmux_server
    format_value = _read_window_formats()[format_name]
    _run_tmux(socket_name, "set-option", "-g", "@thm_peach", "colour123")
    _run_tmux(socket_name, "set-option", "-g", "automatic-rename", "off")

    first_window = _run_tmux(
        socket_name,
        "list-windows",
        "-t",
        session_name,
        "-F",
        "#{window_id}",
    ).splitlines()[0]
    _run_tmux(socket_name, "rename-window", "-t", first_window, "first")
    first_pane = _run_tmux(
        socket_name,
        "list-panes",
        "-t",
        first_window,
        "-F",
        "#{pane_id}",
    ).splitlines()[0]
    running_pane = _run_tmux(
        socket_name,
        "split-window",
        "-d",
        "-t",
        first_window,
        "-P",
        "-F",
        "#{pane_id}",
    )
    second_window = _run_tmux(
        socket_name,
        "new-window",
        "-d",
        "-t",
        session_name,
        "-n",
        "second",
        "-P",
        "-F",
        "#{window_id}",
    )
    other_pane = _run_tmux(
        socket_name,
        "list-panes",
        "-t",
        second_window,
        "-F",
        "#{pane_id}",
    ).splitlines()[0]

    assert _render_window(socket_name, first_window, format_value) == " first"
    assert _render_window(socket_name, second_window, format_value) == " second"

    _set_cmd_running(socket_name, first_pane, "0")
    assert _render_window(socket_name, first_window, format_value) == " first"

    _set_cmd_running(socket_name, first_pane, "1")
    assert _render_window(socket_name, first_window, format_value) == " #[fg=colour123]#[bold]first"
    assert _render_window(socket_name, second_window, format_value) == " second"

    _set_cmd_running(socket_name, running_pane, "1")
    _unset_cmd_running(socket_name, first_pane)
    assert _render_window(socket_name, first_window, format_value) == " #[fg=colour123]#[bold]first"

    _run_tmux(socket_name, "kill-pane", "-t", running_pane)
    assert _render_window(socket_name, first_window, format_value) == " first"

    _set_cmd_running(socket_name, other_pane, "1")
    assert _render_window(socket_name, first_window, format_value) == " first"
    assert _render_window(socket_name, second_window, format_value) == " #[fg=colour123]#[bold]second"
