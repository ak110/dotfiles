import json
import os
import pathlib
import stat
import subprocess
import sys
from collections.abc import Callable
from typing import NoReturn

import pytest

from pytools import agent_launcher

# 計画で指定された分岐条件を、外部プロセスを起動せず直接検証する。
# pylint: disable=protected-access

type Entrypoint = Callable[..., NoReturn]


def _capture_command(commands: list[list[str]]) -> Callable[[list[str]], int]:
    def run(arguments: list[str]) -> int:
        commands.append(arguments)
        return 0

    return run


@pytest.mark.parametrize(
    ("entrypoint", "model_args"),
    [
        (
            agent_launcher.main_sonnet,
            ["--permission-mode=auto", "--model=sonnet[1m]"],
        ),
        (
            agent_launcher.main_opus,
            ["--permission-mode=auto", "--model=opus[1m]"],
        ),
        (
            agent_launcher.main_fable,
            [
                "--permission-mode=auto",
                "--model=fable",
                "--fallback-model=opus[1m]",
            ],
        ),
        (
            agent_launcher.main_poteto_mode,
            [
                "--permission-mode=auto",
                "--model=opus[1m]",
                '--settings={"enabledPlugins":{"agent-basics@ak110-dotfiles":false,'
                '"agent-toolkit@ak110-dotfiles":false,"agent-basics@synced":false,'
                '"agent-toolkit@synced":false,"pstack@pstack-claude":true}}',
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
            which=lambda _name: None,
            resolve=lambda _name, _preferred: pathlib.Path("claude.exe"),
            run=run,
            isatty=lambda _: True,
        )

    assert commands == [["claude.exe", *model_args, "--version", "追加引数"]]


@pytest.mark.parametrize(
    ("argv", "return_code", "stdout_tty", "stderr_tty", "clear_bin"),
    [
        ([], 0, True, True, "c"),
        (["prompt"], 0, True, True, "c"),
        (["--help"], 0, True, True, "c"),
        ([], 1, True, True, "c"),
        ([], 0, False, True, "c"),
        ([], 0, True, False, "c"),
        ([], 0, True, True, None),
    ],
)
def test_launch_does_not_clear_terminal(
    argv: list[str],
    return_code: int,
    stdout_tty: bool,
    stderr_tty: bool,
    clear_bin: str | None,
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

    result = agent_launcher._run_claude(
        ("--model=test",),
        argv,
        os_name="nt",
        home=tmp_path,
        which=which,
        resolve=lambda _name, _preferred: pathlib.Path("claude"),
        run=run,
        isatty=isatty,
    )

    assert result == return_code
    assert commands == [["claude", "--model=test", *argv]]


def test_resolve_claude_bin_prefers_user_install_on_all_platforms(
    tmp_path: pathlib.Path,
) -> None:
    preferred = tmp_path / ".local" / "bin" / "claude"
    calls: list[tuple[str, tuple[pathlib.Path, ...]]] = []

    def resolve(name: str, directories: tuple[pathlib.Path, ...]) -> pathlib.Path:
        calls.append((name, directories))
        return preferred

    assert agent_launcher._resolve_claude_bin("posix", tmp_path, resolve) == str(preferred)
    assert agent_launcher._resolve_claude_bin("nt", tmp_path, resolve) == str(preferred)
    assert calls == [("claude", (preferred.parent,)), ("claude", (preferred.parent,))]


def test_missing_claude_returns_127(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    result = agent_launcher._run_claude(
        ("--model=test",),
        [],
        os_name="nt",
        home=tmp_path,
        which=lambda _: None,
        resolve=lambda _name, _preferred: None,
        run=lambda _: pytest.fail("コマンドは実行されない"),
        isatty=lambda _: True,
    )

    assert result == 127
    assert capsys.readouterr().err == (
        "claudeコマンドが見つかりません。~/.local/binとPATHの導入先を確認し、update-dotfilesを再実行してください。\n"
    )


@pytest.mark.parametrize(
    ("entrypoint", "model_args", "command_name"),
    [
        (agent_launcher.main_sonnet, ["--permission-mode=auto", "--model=sonnet[1m]"], "claude"),
        (agent_launcher.main_opus, ["--permission-mode=auto", "--model=opus[1m]"], "claude"),
        (
            agent_launcher.main_fable,
            ["--permission-mode=auto", "--model=fable", "--fallback-model=opus[1m]"],
            "claude",
        ),
    ],
)
def test_posix_launch_replaces_process_with_command_name(
    entrypoint: Entrypoint,
    model_args: list[str],
    command_name: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    command_path = tmp_path / command_name

    def execv(path: str, arguments: list[str]) -> NoReturn:
        calls.append((path, arguments))
        raise SystemExit(0)

    monkeypatch.setattr(agent_launcher.os, "execv", execv)
    with pytest.raises(SystemExit, match="0"):
        entrypoint(
            ["--version", "追加引数"],
            os_name="posix",
            home=tmp_path,
            resolve=lambda _name, _preferred: command_path,
            run=lambda _: pytest.fail("子プロセスは起動されない"),
        )

    assert calls == [(str(command_path), [command_name, *model_args, "--version", "追加引数"])]


@pytest.mark.parametrize(
    ("entrypoint", "family"),
    [
        (agent_launcher.main_astra, "astra"),
        (agent_launcher.main_sol, "sol"),
        (agent_launcher.main_terra, "terra"),
    ],
)
def test_codex_entrypoint_uses_latest_visible_model_and_same_startup_context(
    entrypoint: Entrypoint,
    family: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "isatty", lambda _fd: False)
    commands: list[list[str]] = []
    runtime = str(tmp_path / "codex.exe")
    arguments = ["-C", str(tmp_path), "-c", 'model_reasoning_effort="medium"', "--", "--model"]

    def list_models(command: tuple[str, ...], *, cwd: str | None) -> list[dict[str, object]]:
        assert command == (runtime, "-c", 'model_reasoning_effort="medium"', "app-server", "--stdio")
        assert cwd == str(tmp_path)
        return [
            {"id": f"gpt-6.9-{family}"},
            {"model": f"gpt-6.12-{family}"},
            {"id": f"gpt-7-{family}", "hidden": True},
        ]

    def run(command: list[str]) -> int:
        commands.append(command)
        return 23

    monkeypatch.setattr(agent_launcher.codex_models, "list_models_at", list_models)
    with pytest.raises(SystemExit, match="23"):
        entrypoint(
            ["--full", *arguments],
            os_name="nt",
            home=tmp_path,
            resolve=lambda _name, _preferred: pathlib.Path(runtime),
            run=run,
        )

    assert commands == [[runtime, "-m", f"gpt-6.12-{family}", *arguments]]


@pytest.mark.parametrize(
    ("arguments", "defaults"),
    [
        (["--profile=work", "--model", "explicit"], []),
        (["-pwork"], []),
        (["--profile", "work"], []),
        (["--profile=work"], []),
        (["--model=explicit"], []),
        (["exec", "-m", "explicit", "prompt"], []),
        (["-mexplicit", "--model=second"], []),
        (["--version"], []),
        (["login", "status"], []),
        (["exec", "help"], []),
        (["e", "--json", "help"], []),
        (["exec", "--color", "always", "help"], []),
        (["exec", "--color=always", "help"], []),
        (["--oss"], []),
        (["-c", 'model_provider="custom"'], []),
        (["--config='model_provider' = custom", "-m", "explicit"], []),
    ],
)
def test_codex_launch_without_catalog(
    arguments: list[str], defaults: list[str], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "isatty", lambda _fd: False)
    commands: list[list[str]] = []
    runtime = tmp_path / "codex.exe"
    monkeypatch.setattr(agent_launcher.codex_models, "list_models_at", lambda *_, **__: pytest.fail("一覧は不要"))
    with pytest.raises(SystemExit, match="0"):
        agent_launcher.main_sol(
            ["--full", *arguments], os_name="nt", home=tmp_path, resolve=lambda *_: runtime, run=_capture_command(commands)
        )
    assert commands == [[str(runtime), *defaults, *arguments]]


@pytest.mark.parametrize(
    "entrypoint",
    [agent_launcher.main_sonnet, agent_launcher.main_opus, agent_launcher.main_fable, agent_launcher.main_poteto_mode],
)
@pytest.mark.parametrize("explicit", [["--model", "haiku"], ["--model=haiku"]])
def test_claude_explicit_model_is_not_duplicated(entrypoint: Entrypoint, explicit: list[str], tmp_path: pathlib.Path) -> None:
    commands: list[list[str]] = []
    runtime = tmp_path / "claude.exe"
    with pytest.raises(SystemExit, match="0"):
        entrypoint(explicit, os_name="nt", home=tmp_path, resolve=lambda *_: runtime, run=_capture_command(commands))
    assert commands[0][-len(explicit) :] == explicit
    assert not any(arg.startswith("--model=") for arg in commands[0][: -len(explicit)])
    if entrypoint is agent_launcher.main_fable:
        assert "--fallback-model=opus[1m]" in commands[0]
    if entrypoint is agent_launcher.main_poteto_mode:
        assert any(arg.startswith("--settings=") for arg in commands[0])


@pytest.mark.parametrize("arguments", [["--", "--model"], ["--append-system-prompt", "--model"], ["-n", "--model"]])
def test_claude_model_literal_does_not_remove_default(arguments: list[str], tmp_path: pathlib.Path) -> None:
    commands: list[list[str]] = []
    runtime = tmp_path / "claude.exe"
    with pytest.raises(SystemExit, match="0"):
        agent_launcher.main_opus(
            ["--full", *arguments], os_name="nt", home=tmp_path, resolve=lambda *_: runtime, run=_capture_command(commands)
        )
    assert commands == [[str(runtime), "--permission-mode=auto", "--model=opus[1m]", *arguments]]


def test_codex_explicit_help_prompt_and_openai_override_still_use_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(agent_launcher.codex_models, "list_models_at", lambda *_a, **_kw: [{"model": "gpt-7-sol"}])
    arguments = ["-cmodel_provider=custom", "-c", "model_provider=openai", "exec", "--color", "always", "--", "help"]
    monkeypatch.setattr(os, "isatty", lambda _fd: False)
    commands: list[list[str]] = []
    runtime = tmp_path / "codex.exe"
    with pytest.raises(SystemExit, match="0"):
        agent_launcher.main_sol(
            ["--full", *arguments], os_name="nt", home=tmp_path, resolve=lambda *_: runtime, run=_capture_command(commands)
        )
    assert commands == [[str(runtime), "-m", "gpt-7-sol", *arguments]]


@pytest.mark.parametrize("missing_series", [False, True])
def test_codex_selection_failure_is_distinguished_and_safe(
    missing_series: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    def list_models(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        if missing_series:
            return [{"model": "gpt-7-astra"}]
        raise ValueError("private catalog error")

    monkeypatch.setattr(agent_launcher.codex_models, "list_models_at", list_models)
    with pytest.raises(SystemExit, match="1"):
        agent_launcher.main_sol(
            [], os_name="nt", home=tmp_path, resolve=lambda *_: pathlib.Path("codex"), run=lambda _: pytest.fail("起動しない")
        )
    error = capsys.readouterr().err
    assert ("sol系列がありません" if missing_series else "モデル一覧を取得できません") in error
    assert "--modelで完全なID" in error and "private catalog error" not in error


def test_codex_missing_returns_127(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    with pytest.raises(SystemExit, match="127"):
        agent_launcher.main_astra(
            [],
            os_name="nt",
            home=tmp_path,
            resolve=lambda _name, _preferred: None,
            run=lambda _: pytest.fail("コマンドは実行されない"),
        )

    assert capsys.readouterr().err == (
        "codexコマンドが見つかりません。~/.local/binとPATHの導入先を確認し、update-dotfilesを再実行してください。\n"
    )


def test_executable_search_keeps_symlink_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "binary"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    link = tmp_path / "claude"
    link.symlink_to(binary)
    monkeypatch.setattr(agent_launcher.common, "mise_shim_directories", set)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert agent_launcher._resolve_executable("claude", ()) == link


@pytest.mark.parametrize(
    ("name", "command", "entrypoint"),
    [
        ("sonnet", "claude", agent_launcher.main_sonnet),
        ("opus", "claude", agent_launcher.main_opus),
        ("fable", "claude", agent_launcher.main_fable),
        ("poteto-mode", "claude", agent_launcher.main_poteto_mode),
        ("astra", "codex", agent_launcher.main_astra),
        ("sol", "codex", agent_launcher.main_sol),
        ("terra", "codex", agent_launcher.main_terra),
    ],
)
@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_wrapper_help_without_resolving_cli(name, command, entrypoint, flag, capsys):
    def unexpected(*_args):
        pytest.fail("ヘルプ表示に本体CLIは不要")

    with pytest.raises(SystemExit, match="0"):
        entrypoint([flag], resolve=unexpected, run=unexpected)
    output = capsys.readouterr().out
    assert f"使い方: {name}" in output
    assert "既定:" in output
    assert f"`{command} --help`" in output


def test_get_claude_options_extracts_long_options() -> None:
    result = subprocess.CompletedProcess(
        args=["claude", "--help"],
        returncode=0,
        stdout="  -p, --print  Print\n  --model <model>\n  --model <model>\n",
        stderr="",
    )

    assert agent_launcher._get_claude_options("claude", run_help=lambda _: result) == ["--model", "--print"]


@pytest.mark.parametrize(
    "result",
    [None, subprocess.CompletedProcess(args=[], returncode=1, stdout="--model", stderr="")],
)
def test_get_claude_options_returns_empty_on_help_failure(
    result: subprocess.CompletedProcess[str] | None,
) -> None:
    assert agent_launcher._get_claude_options("claude", run_help=lambda _: result) == []


@pytest.mark.skipif(os.name == "nt", reason="bash補完の疎通確認はPOSIX専用")
def test_argcomplete_writes_claude_options_to_fd8(tmp_path: pathlib.Path) -> None:
    claude_bin = tmp_path / ".local" / "bin" / "claude"
    claude_bin.parent.mkdir(parents=True)
    claude_bin.write_text(
        ("#!/bin/sh\nprintf '  --model <model>\\n  --permission-mode <mode>\\n'\n"),
        encoding="utf-8",
    )
    claude_bin.chmod(claude_bin.stat().st_mode | stat.S_IXUSR)

    launcher = tmp_path / "sonnet"
    launcher.write_text(
        (f"#!{sys.executable}\nfrom pytools.agent_launcher import main_sonnet\nmain_sonnet()\n"),
        encoding="utf-8",
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)

    completion_output = tmp_path / "completion-output"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
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


@pytest.mark.parametrize("wrapper", ["opus", "sonnet", "fable"])
@pytest.mark.parametrize(
    ("state", "status", "display"),
    [
        ("exiting", 0, True),
        ("requested", 0, False),
        ("missing", 0, False),
        ("other-launch", 0, False),
        ("exiting", 17, False),
    ],
)
def test_claude_completion_after_child_exit(
    wrapper: str,
    state: str,
    status: int,
    display: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    child_hint = "Resume this session with:\nclaude --resume session-a"
    actual_wait = agent_launcher._wait_for_claude
    events: list[dict] = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
                        "id": call,
                        "input": {"kind": "completion", "message": message},
                    },
                ]
            },
        }
        for call, message in [("call-a", "完了報告\n\n本文"), ("call-b", "別の誤った報告")]
    ]
    events.extend(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": call, "content": json.dumps({"ok": ok}), "is_error": not ok},
                ]
            },
        }
        for call, ok in [("call-a", True), ("call-b", False)]
    )
    record = "".join(json.dumps(event) + "\n" for event in events)
    fake = tmp_path / "fake_claude.py"
    fake.write_text(
        f"""import json, os, pathlib, sys
root = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"])
launch = os.environ["AGENT_TOOLKIT_COMPLETION_LAUNCH_ID"]
state = {state!r}
if state != "missing":
    directory = root / "agent-toolkit-function-hooks"
    directory.mkdir()
    if state == "other-launch":
        launch, state = "a" * 32, "exiting"
    path = directory / f"completion-exit-{{launch}}.txt"
    path.write_text(json.dumps({{"session": "session-a", "call": "call-a", "status": state}}), encoding="utf-8")
path = root / "projects" / "test" / "session-a.jsonl"
path.parent.mkdir(parents=True)
path.write_text({record!r}, encoding="utf-8")
print({child_hint!r})
sys.exit({status})
""",
        encoding="utf-8",
    )

    def wait(command: list[str], env: dict[str, str]) -> int:
        assert command[-2:] == ["--resume", "session-a"]
        return actual_wait([sys.executable, str(fake)], env)

    monkeypatch.setattr(agent_launcher, "_wait_for_claude", wait)
    with pytest.raises(SystemExit) as error:
        getattr(agent_launcher, f"main_{wrapper}")(
            ["--resume", "session-a"],
            home=tmp_path,
            resolve=lambda *_: pathlib.Path("claude"),
            isatty=lambda _: True,
        )
    assert error.value.code == status
    output = capfd.readouterr().out
    assert output.startswith(child_hint + "\n")
    if display:
        assert "完了報告\n\n本文\n" in output
        assert output.splitlines()[-2] == "同じ起動方法で再開:"
        command = output.splitlines()[-1]
        if os.name == "nt":
            assert command == f'cmd /d /c "set CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1&& {wrapper} --resume session-a"'
        else:
            assert command == f"CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1 {wrapper} --resume session-a"
            launcher = tmp_path / wrapper
            launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$CLAUDE_CODE_EXPERIMENTAL_CODE_MODE" "$@"\n', encoding="utf-8")
            launcher.chmod(0o755)
            resumed = subprocess.run(
                ["/bin/sh", "-c", command],
                env={**os.environ, "PATH": str(tmp_path)},
                capture_output=True,
                text=True,
                check=True,
            )
            assert resumed.stdout.splitlines() == ["1", "--resume", "session-a"]
            print(output, end="")
            print(resumed.stdout, end="")
        assert "別の誤った報告" not in output
        assert not list((tmp_path / "agent-toolkit-function-hooks").iterdir())
    else:
        assert output == child_hint + "\n"


@pytest.mark.parametrize("entrypoint", [agent_launcher.main_opus, agent_launcher.main_sonnet, agent_launcher.main_fable])
@pytest.mark.parametrize(
    "args",
    [
        ["--print", "hello"],
        ["-p", "hello"],
        ["--resume", "--print", "hello"],
        ["-r", "-p", "hello"],
        ["--resume", "session-a", "-p"],
        ["--version"],
    ],
)
def test_claude_entrypoints_keep_noninteractive_path(
    entrypoint: Entrypoint,
    args: list[str],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(command: list[str]) -> int:
        assert command[-len(args) :] == args
        print("本体出力")
        return 19

    with pytest.raises(SystemExit) as error:
        entrypoint(
            args,
            home=tmp_path,
            os_name="nt",
            resolve=lambda *_: pathlib.Path("claude"),
            isatty=lambda _: True,
            run=run,
        )
    assert error.value.code == 19
    assert capsys.readouterr().out == "本体出力\n"


@pytest.mark.parametrize("entrypoint", [agent_launcher.main_opus, agent_launcher.main_sonnet, agent_launcher.main_fable])
@pytest.mark.parametrize(
    "args",
    [
        [],
        ["hello"],
        ["--model", "sonnet"],
        ["--model=sonnet", "hello"],
        ["--model", "sonnet", "--resume", "session-a"],
        ["--model", "sonnet", "-r"],
        ["--model=sonnet", "--continue"],
        ["--resume"],
        ["-r", "session-a"],
        ["--continue"],
        ["two words"],
        ["--", "hello"],
        ["--", "--print"],
    ],
)
@pytest.mark.parametrize("fds", [{0, 1}, {0}, {1}])
def test_claude_resume_checks_both_terminal_descriptors(
    entrypoint: Entrypoint,
    args: list[str],
    fds: set[int],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = []
    monkeypatch.setattr(agent_launcher, "_run_completion_launcher", lambda *_: child.append(True) or 21)
    with pytest.raises(SystemExit) as error:
        entrypoint(
            args,
            home=tmp_path,
            os_name="nt",
            resolve=lambda *_: pathlib.Path("claude"),
            isatty=lambda fd: fd in fds,
            run=lambda _: 19,
        )
    assert error.value.code == (21 if fds == {0, 1} else 19)
    assert bool(child) is (fds == {0, 1})


@pytest.mark.parametrize(
    "args",
    [
        ["mcp", "list"],
        ["auth", "status"],
        ["auto-mode"],
        ["plugins"],
        ["--bg", "hello"],
        ["--background", "hello"],
        ["--desktop", "--resume", "session-a"],
        ["--debug", "hooks", "plugins", "list"],
        ["--plugin-url", "https://example.invalid/plugin.zip", "mcp", "list"],
        ["--unknown-launch-mode"],
        ["--unknown-launch-mode=value", "--continue"],
        ["--unknown-launch-mode", "value", "mcp", "list"],
        ["new-management-command"],
    ],
)
def test_claude_unclassified_launch_does_not_exit_or_append_on_its_own(
    args: list[str],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_toolkit._common.completion_exit import LAUNCH_ENV

    def wait(command: list[str], env: dict[str, str]) -> int:
        assert command[-len(args) :] == args
        assert env[LAUNCH_ENV]
        print("本体出力")
        return 19

    monkeypatch.setattr(agent_launcher, "_wait_for_claude", wait)
    with pytest.raises(SystemExit) as error:
        agent_launcher.main_opus(args, home=tmp_path, resolve=lambda *_: pathlib.Path("claude"), isatty=lambda _: True)
    assert error.value.code == 19
    assert capsys.readouterr().out == "本体出力\n"
