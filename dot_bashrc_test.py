"""dot_bashrcのPATH追加契約を検証する。"""

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
