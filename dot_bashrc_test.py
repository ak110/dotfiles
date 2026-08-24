"""dot_bashrcの起動時契約全般を検証する。"""

import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent
BASHRC = REPO_ROOT / ".chezmoi-source" / "dot_bashrc"


def test_fixed_paths_are_ordered_and_idempotent(tmp_path: pathlib.Path) -> None:
    """固定PATH要素の順序を保ち、2回読み込んでも重複させない。"""
    home = tmp_path / "home"
    fixed_home_paths = [
        home / ".cargo" / "bin",
        home / ".local" / "bin",
        home / "bin",
        home / "dotfiles" / "bin",
        home / "dotfiles" / "agent-toolkit" / "bin",
        home / ".poetry" / "bin",
    ]
    for path in fixed_home_paths:
        path.mkdir(parents=True)

    initial_parts = [
        "/external-a",
        "/external-dup",
        "",
        str(home / ".local" / "bin"),
        "/external-dup",
        "/external-b",
    ]
    script = '. "$1"\nfirst=$PATH\n. "$1"\nprintf "%s\\n%s\\n" "$first" "$PATH"\n'
    completed = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", script, "bash", str(BASHRC)],
        check=True,
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": ":".join(initial_parts), "TERM": "dumb"},
    )
    first, second = completed.stdout.splitlines()

    expected_parts = [
        str(home / ".poetry" / "bin"),
        str(home / "bin"),
        str(home / ".cargo" / "bin"),
    ]
    cuda_bin = pathlib.Path("/usr/local/cuda/bin")
    if cuda_bin.is_dir():
        expected_parts.append(str(cuda_bin))
    expected_parts.extend(initial_parts)
    expected_parts.extend(
        [
            str(home / "dotfiles" / "bin"),
            str(home / "dotfiles" / "agent-toolkit" / "bin"),
        ]
    )
    expected = ":".join(expected_parts)

    assert first == expected
    assert second == expected
    assert first.split(":").count("/external-dup") == 2
    assert "" in first.split(":")


def test_enable_pyenv_keeps_existing_path_position(tmp_path: pathlib.Path) -> None:
    """pyenv有効化を2回実行しても既存PATHの位置と件数を保つ。"""
    home = tmp_path / "home"
    pyenv_bin = home / ".pyenv" / "bin"
    pyenv_bin.mkdir(parents=True)
    fake_bin = home / "fake-bin"
    fake_bin.mkdir()
    call_log = home / "pyenv-calls"
    pyenv_stub = fake_bin / "pyenv"
    pyenv_stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$PYENV_CALL_LOG"\n',
        encoding="utf-8",
    )
    pyenv_stub.chmod(0o755)

    initial_parts = ["/external-a", str(pyenv_bin), str(fake_bin), "/external-b"]
    script = '. "$1"\nfirst=$PATH\nenable-pyenv\nprintf "%s\\n%s\\n" "$first" "$PATH"\n'
    completed = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-i", "-c", script, "bash", str(BASHRC)],
        check=True,
        capture_output=True,
        text=True,
        cwd=home,
        env={
            "HOME": str(home),
            "PATH": ":".join(initial_parts),
            "PYENV_CALL_LOG": str(call_log),
            "TERM": "dumb",
        },
    )
    first, second = completed.stdout.splitlines()

    expected_parts: list[str] = []
    cuda_bin = pathlib.Path("/usr/local/cuda/bin")
    if cuda_bin.is_dir():
        expected_parts.append(str(cuda_bin))
    expected_parts.extend(initial_parts)
    expected = ":".join(expected_parts)

    assert first == expected
    assert second == expected
    assert second.split(":").count(str(pyenv_bin)) == 1
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        "init -",
        "virtualenv-init -",
        "--version",
        "versions",
        "init -",
        "virtualenv-init -",
        "--version",
        "versions",
    ]


def test_tmux_command_state_uses_calling_pane_and_tracks_commands(
    tmp_path: pathlib.Path,
) -> None:
    """tmux内ではコマンド開始とプロンプト復帰を呼び出し元ペインへ通知する。"""
    home = tmp_path / "home"
    fake_bin = home / "fake-bin"
    fake_bin.mkdir(parents=True)
    call_log = home / "tmux-calls"
    tmux_stub = fake_bin / "tmux"
    tmux_stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TMUX_CALL_LOG"\n',
        encoding="utf-8",
    )
    tmux_stub.chmod(0o755)

    pane = "%known-pane"
    script = """\
. "$1"
_tmux_cmd_idle
printf 'first\\n'; printf 'second\\n'
_tmux_cmd_idle
printf 'third\\n'; printf 'fourth\\n'
_tmux_cmd_idle
"""
    subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-i", "-c", script, "bash", str(BASHRC)],
        check=True,
        capture_output=True,
        text=True,
        cwd=home,
        env={
            "HOME": str(home),
            "PATH": str(fake_bin),
            "TERM": "dumb",
            "TMUX": "/tmp/tmux-test,1,0",
            "TMUX_PANE": pane,
            "TMUX_CALL_LOG": str(call_log),
        },
    )

    assert call_log.read_text(encoding="utf-8").splitlines() == [
        f"set-option -p -t {pane} -q @cmd_running 1",
        f"set-option -p -t {pane} -qu @cmd_running",
        f"set-option -p -t {pane} -q @cmd_running 1",
        f"set-option -p -t {pane} -qu @cmd_running",
        f"set-option -p -t {pane} -q @cmd_running 1",
        f"set-option -p -t {pane} -qu @cmd_running",
    ]


def test_tmux_command_state_prompt_hook_is_unique_and_gated(
    tmp_path: pathlib.Path,
) -> None:
    """tmux内のPROMPT_COMMANDを末尾へ一度だけ追加し、tmux外では追加しない。"""
    home = tmp_path / "home"
    fake_bin = home / "fake-bin"
    fake_bin.mkdir(parents=True)
    call_log = home / "tmux-calls"
    tmux_stub = fake_bin / "tmux"
    tmux_stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TMUX_CALL_LOG"\n',
        encoding="utf-8",
    )
    tmux_stub.chmod(0o755)

    prompt_script = """\
. "$1"
. "$1"
printf 'PROMPT:%s\\n' "$PROMPT_COMMAND"
"""
    completed = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-i",
            "-c",
            prompt_script,
            "bash",
            str(BASHRC),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=home,
        env={
            "HOME": str(home),
            "PATH": str(fake_bin),
            "PROMPT_COMMAND": "",
            "TERM": "dumb",
            "TMUX": "/tmp/tmux-test,1,0",
            "TMUX_PANE": "%known-pane",
            "TMUX_CALL_LOG": str(call_log),
        },
    )
    prompt_line = next(line for line in completed.stdout.splitlines() if line.startswith("PROMPT:"))
    prompt_command = prompt_line.removeprefix("PROMPT:")

    assert prompt_command.startswith("history -a;_show_status;")
    assert prompt_command.endswith("_tmux_cmd_idle;")
    assert prompt_command.count("_tmux_cmd_idle;") == 1

    outside_call_log = home / "tmux-outside-calls"
    outside_script = """\
. "$1"
printf 'PROMPT:%s\\n' "$PROMPT_COMMAND"
printf 'DEBUG:%s\\n' "$(trap -p DEBUG)"
"""
    outside = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-i",
            "-c",
            outside_script,
            "bash",
            str(BASHRC),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=home,
        env={
            "HOME": str(home),
            "PATH": str(fake_bin),
            "PROMPT_COMMAND": "",
            "TERM": "dumb",
            "TMUX_CALL_LOG": str(outside_call_log),
        },
    )
    outside_lines = outside.stdout.splitlines()

    assert next(line for line in outside_lines if line.startswith("PROMPT:")).endswith("history -a;_show_status;")
    assert next(line for line in outside_lines if line.startswith("DEBUG:")) == "DEBUG:"
    assert not outside_call_log.exists()
