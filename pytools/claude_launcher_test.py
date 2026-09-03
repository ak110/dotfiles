import os
import pathlib
import stat
import subprocess
import sys
from collections.abc import Callable
from typing import NoReturn

import pytest

from pytools import claude_launcher

# 計画で指定された分岐条件を、外部プロセスに依存せず直接検証する。
# pylint: disable=protected-access

type Entrypoint = Callable[..., NoReturn]


@pytest.mark.parametrize(
    ("entrypoint", "model_args"),
    [
        (
            claude_launcher.main_sonnet,
            ["--permission-mode=auto", "--model=sonnet[1m]"],
        ),
        (
            claude_launcher.main_opus,
            ["--permission-mode=auto", "--model=opus[1m]"],
        ),
        (
            claude_launcher.main_fable,
            [
                "--permission-mode=auto",
                "--model=fable",
                "--fallback-model=opus[1m]",
            ],
        ),
    ],
)
def test_model_entrypoint_forwards_arguments_unchanged(
    entrypoint: Entrypoint, model_args: list[str], tmp_path: pathlib.Path
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> int:
        commands.append(command)
        return 23

    with pytest.raises(SystemExit, match="23"):
        entrypoint(
            ["--version", "追加引数"],
            os_name="nt",
            home=tmp_path,
            which=lambda name: "claude.exe" if name == "claude" else None,
            run=run,
            isatty=lambda _: True,
        )

    assert commands == [["claude.exe", *model_args, "--version", "追加引数"]]


@pytest.mark.parametrize(
    ("argv", "return_code", "stdout_tty", "stderr_tty", "clear_bin", "clears"),
    [
        ([], 0, True, True, "c", True),
        (["prompt"], 0, True, True, "c", True),
        (["--help"], 0, True, True, "c", False),
        ([], 1, True, True, "c", False),
        ([], 0, False, True, "c", False),
        ([], 0, True, False, "c", False),
        ([], 0, True, True, None, False),
    ],
)
def test_clear_conditions(
    argv: list[str],
    return_code: int,
    stdout_tty: bool,
    stderr_tty: bool,
    clear_bin: str | None,
    clears: bool,
    tmp_path: pathlib.Path,
) -> None:
    commands: list[list[str]] = []

    def which(name: str) -> str | None:
        if name == "claude":
            return "claude"
        return clear_bin

    def run(command: list[str]) -> int:
        commands.append(command)
        return 0 if command == ["c"] else return_code

    def isatty(fd: int) -> bool:
        return stdout_tty if fd == 1 else stderr_tty

    result = claude_launcher._run_claude(
        ("--model=test",),
        argv,
        os_name="nt",
        home=tmp_path,
        which=which,
        run=run,
        isatty=isatty,
    )

    assert result == return_code
    assert (["c"] in commands) is clears


def test_resolve_claude_bin_prefers_posix_user_install_only(
    tmp_path: pathlib.Path,
) -> None:
    preferred = tmp_path / ".local" / "bin" / "claude"
    preferred.parent.mkdir(parents=True)
    preferred.write_text("#!/bin/sh\n", encoding="utf-8")
    preferred.chmod(preferred.stat().st_mode | stat.S_IXUSR)

    assert claude_launcher._resolve_claude_bin("posix", tmp_path, lambda _: "path-claude") == str(preferred)
    assert claude_launcher._resolve_claude_bin("nt", tmp_path, lambda _: "path-claude") == "path-claude"


def test_missing_claude_returns_127(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    result = claude_launcher._run_claude(
        ("--model=test",),
        [],
        os_name="nt",
        home=tmp_path,
        which=lambda _: None,
        run=lambda _: pytest.fail("コマンドは実行されない"),
        isatty=lambda _: True,
    )

    assert result == 127
    assert capsys.readouterr().err == "claudeコマンドが見つかりません。\n"


def test_get_claude_options_extracts_long_options() -> None:
    result = subprocess.CompletedProcess(
        args=["claude", "--help"],
        returncode=0,
        stdout="  -p, --print  Print\n  --model <model>\n  --model <model>\n",
        stderr="",
    )

    assert claude_launcher._get_claude_options("claude", run_help=lambda _: result) == ["--model", "--print"]


@pytest.mark.parametrize(
    "result",
    [None, subprocess.CompletedProcess(args=[], returncode=1, stdout="--model", stderr="")],
)
def test_get_claude_options_returns_empty_on_help_failure(
    result: subprocess.CompletedProcess[str] | None,
) -> None:
    assert claude_launcher._get_claude_options("claude", run_help=lambda _: result) == []


@pytest.mark.skipif(os.name == "nt", reason="bash補完の疎通確認はPOSIX専用")
def test_argcomplete_writes_claude_options_to_fd8(tmp_path: pathlib.Path) -> None:
    claude_bin = tmp_path / "claude"
    claude_bin.write_text(
        "#!/bin/sh\nprintf '  --model <model>\\n  --permission-mode <mode>\\n'\n",
        encoding="utf-8",
    )
    claude_bin.chmod(claude_bin.stat().st_mode | stat.S_IXUSR)

    launcher = tmp_path / "sonnet"
    launcher.write_text(
        f"#!{sys.executable}\nfrom pytools.claude_launcher import main_sonnet\nmain_sonnet()\n",
        encoding="utf-8",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)

    completion_output = tmp_path / "completion-output"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "_ARGCOMPLETE": "1",
        "_ARGCOMPLETE_IFS": "\v",
        "COMP_LINE": "sonnet --",
        "COMP_POINT": str(len("sonnet --")),
        "COMP_TYPE": "9",
        "COMP_WORDBREAKS": " ",
    }
    subprocess.run(
        [
            "bash",
            "-c",
            'exec 8>"$1"; exec "$2"',
            "bash",
            str(completion_output),
            str(launcher),
        ],
        check=True,
        env=env,
    )

    candidates = completion_output.read_text(encoding="utf-8").split("\v")
    assert "--model" in candidates
    assert "--permission-mode" in candidates
