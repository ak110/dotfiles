import asyncio
import os
import pathlib
import stat
import subprocess
import sys
from collections.abc import Callable
from typing import NoReturn

import pytest
from agent_toolkit._common import codex_models

from pytools import agent_launcher, codex_launcher
from pytools._internal.codex_transport import RpcChannel

# 計画で指定された分岐条件を、外部プロセスを起動せず直接検証する。
# pylint: disable=protected-access

type Entrypoint = Callable[..., NoReturn]


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
            ["--", "追加引数"],
            os_name="nt",
            home=tmp_path,
            which=lambda _name: None,
            resolve=lambda _name, _preferred: pathlib.Path("claude.exe"),
            run=run,
            isatty=lambda _: True,
        )

    assert commands == [["claude.exe", *model_args, "--", "追加引数"]]


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
    expected = [] if argv == ["--help"] else ["--model=test"]
    assert commands == [["claude", *expected, *argv]]


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
            ["--", "追加引数"],
            os_name="posix",
            home=tmp_path,
            resolve=lambda _name, _preferred: command_path,
            run=lambda _: pytest.fail("子プロセスは起動されない"),
        )

    assert calls == [(str(command_path), [command_name, *model_args, "--", "追加引数"])]


@pytest.mark.parametrize(
    ("entrypoint", "family"),
    [
        (agent_launcher.main_astra, "astra"),
        (agent_launcher.main_sol, "sol"),
        (agent_launcher.main_terra, "terra"),
    ],
)
def test_codex_entrypoint_uses_shared_config_and_runtime(
    entrypoint: Entrypoint,
    family: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "codex"
    args = ["-c", "model_provider=provider", "--cd", str(tmp_path), "--", "--model"]
    calls: list[tuple[pathlib.Path, list[str]]] = []

    def list_models(command: tuple[str, ...], *, cwd: str | None) -> list[dict[str, str]]:
        assert command == (str(binary), "-c", "model_provider=provider", "app-server", "--stdio")
        assert cwd == str(tmp_path)
        return [{"model": f"gpt-5.7-{family}"}, {"model": f"gpt-9.2-{family}"}]

    def launch(runtime: pathlib.Path, argv: list[str]) -> int:
        calls.append((runtime, argv))
        return 23

    monkeypatch.setattr(agent_launcher.codex_runtime, "find_runtime_launcher", lambda *, windows: binary)
    monkeypatch.setattr(codex_models, "list_models_at", list_models)
    monkeypatch.setattr(agent_launcher.codex_launcher, "launch_resolved", launch)
    with pytest.raises(SystemExit, match="23"):
        entrypoint(args)
    assert calls == [(binary, ["-m", f"gpt-9.2-{family}", *args])]


@pytest.mark.parametrize(
    "args",
    [
        ["--help"],
        ["--version"],
        ["login"],
        ["-a", "never", "login", "status"],
        ["plugin", "list"],
        ["--model=exact"],
        ["--model", "exact"],
        ["-m", "exact"],
        ["exec", "--model", "exact", "prompt"],
        ["--oss"],
    ],
)
def test_codex_catalog_not_needed(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    binary = pathlib.Path("codex").absolute()
    monkeypatch.setattr(agent_launcher.codex_runtime, "find_runtime_launcher", lambda *, windows: binary)
    monkeypatch.setattr(codex_models, "list_models_at", lambda *a, **kw: pytest.fail("一覧は不要"))
    calls: list[tuple[pathlib.Path, list[str]]] = []

    def launch(runtime: pathlib.Path, arguments: list[str]) -> int:
        calls.append((runtime, arguments))
        return 0

    monkeypatch.setattr(agent_launcher.codex_launcher, "launch_resolved", launch)
    with pytest.raises(SystemExit, match="0"):
        agent_launcher.main_sol(args)
    assert calls == [(binary, args)]


def test_codex_catalog_failure_points_to_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binary = pathlib.Path("codex").absolute()
    monkeypatch.setattr(agent_launcher.codex_runtime, "find_runtime_launcher", lambda *, windows: binary)

    def fail(*args: object, **kwargs: object) -> list[dict[str, str]]:
        raise ValueError("ここは非公開の疑似診断")

    monkeypatch.setattr(codex_models, "list_models_at", fail)
    monkeypatch.setattr(agent_launcher.codex_launcher, "launch_resolved", lambda *args: pytest.fail("起動してはいけない"))
    with pytest.raises(SystemExit, match="1"):
        agent_launcher.main_sol([])
    diagnostic = capsys.readouterr().err
    assert "モデル一覧を取得できません" in diagnostic
    assert "--model" in diagnostic
    assert "--model-type" not in diagnostic
    assert "非公開の疑似診断" not in diagnostic


@pytest.mark.parametrize("args", [["-p", "work"], ["exec", "--ignore-user-config", "prompt"]])
def test_codex_catalog_never_replaces_requested_settings(
    args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(agent_launcher.codex_runtime, "find_runtime_launcher", lambda *, windows: pathlib.Path("codex"))
    monkeypatch.setattr(codex_models, "list_models_at", lambda *a, **kw: pytest.fail("異なる設定に変えない"))
    monkeypatch.setattr(agent_launcher.codex_launcher, "launch_resolved", lambda *args: pytest.fail("起動してはいけない"))
    with pytest.raises(SystemExit, match="1"):
        agent_launcher.main_sol(args)
    assert "--model" in capsys.readouterr().err


def test_codex_runtime_disappears_before_launch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(agent_launcher.codex_runtime, "find_runtime_launcher", lambda *, windows: pathlib.Path("gone"))

    def disappeared(*args: object) -> int:
        raise FileNotFoundError

    monkeypatch.setattr(agent_launcher.codex_launcher, "launch_resolved", disappeared)
    with pytest.raises(SystemExit, match="127"):
        agent_launcher.main_sol(["--model=exact"])
    assert "update-dotfiles" in capsys.readouterr().err


def test_codex_completion_uses_same_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    binary = tmp_path / "native-codex"
    monkeypatch.setattr(agent_launcher.codex_runtime, "find_runtime_launcher", lambda *, windows: binary)

    def options(path: str) -> list[str]:
        assert path == str(binary)
        return ["--model", "--profile"]

    monkeypatch.setattr(agent_launcher, "_get_claude_options", options)
    assert agent_launcher._complete_codex_options("--m") == ["--model"]


@pytest.mark.asyncio
async def test_codex_remote_catalog_uses_the_same_authenticated_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "catalog-fixture-token"
    monkeypatch.setenv("TEST_CODEX_REMOTE_BEARER", token)
    finished = asyncio.Event()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            channel = RpcChannel(reader, writer, client=False)
            await channel.handshake(token=token)
            initialize = await channel.receive()
            assert initialize["method"] == "initialize"
            await channel.send({"id": initialize["id"], "result": {}})
            assert (await channel.receive())["method"] == "initialized"
            request = await channel.receive()
            assert request["method"] == "model/list"
            await channel.send({"id": request["id"], "result": {"data": [{"model": "gpt-12-sol"}]}})
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    async with await asyncio.start_server(serve, "127.0.0.1", 0) as server:
        assert server.sockets
        port = server.sockets[0].getsockname()[1]
        catalog = await codex_launcher._remote_model_catalog(
            pathlib.Path("not-used"), f"ws://127.0.0.1:{port}/catalog", "TEST_CODEX_REMOTE_BEARER"
        )
        await asyncio.wait_for(finished.wait(), 3)
    assert codex_models.latest_model("sol", catalog) == "gpt-12-sol"


def test_codex_missing_returns_127(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_launcher.codex_runtime, "find_runtime_launcher", lambda *, windows: None)
    with pytest.raises(SystemExit, match="127"):
        agent_launcher.main_astra([])
    assert "codexコマンドが見つかりません" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--model=exact"], True),
        (["--model", "exact"], True),
        (["--resume", "--model", "exact"], True),
        (["--resume", "session", "--model", "exact"], True),
        (["--settings", "--model", "--", "prompt"], False),
        (["--", "--model"], False),
        (["--help"], True),
        (["plugin", "list"], True),
    ],
)
def test_claude_default_model_is_only_removed_for_options(args: list[str], expected: bool) -> None:
    assert agent_launcher._suppress_claude_default_model(args) is expected


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
