"""通常CLIから外部peerを起動し、同じ仕事と子の復旧・明示中止・承認を検証する。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import psutil
import pytest
from agent_toolkit._common import process_tree

pytestmark = pytest.mark.windows

# 実装はそのまま読み、外部Codexの実行ファイルだけをプロセス境界で代用する。
_RUNNER = r"""
import asyncio
import os
import pathlib
import sys
import types
from pytools import codex_launcher

peer = os.environ['CODEX_PEER_HELPER']
binary = os.environ['CODEX_PEER_BINARY']
real_create = asyncio.create_subprocess_exec
real_run = codex_launcher.common.run_subprocess
real_exec = os.execv
real_native_run = codex_launcher.process_tree.run_with_timeout

async def create(program, *arguments, **kwargs):
    if str(program) == binary:
        return await real_create(sys.executable, peer, *arguments, **kwargs)
    return await real_create(program, *arguments, **kwargs)

def run(command, **kwargs):
    if str(command[0]) == binary:
        return real_run([sys.executable, peer, *command[1:]], **kwargs)
    return real_run(command, **kwargs)

def execute(program, arguments):
    if os.environ.get('CODEX_PEER_WINDOWS') == '1':
        raise AssertionError('Windowsの非対話呼出にexecvを使用した')
    if str(program) == binary:
        return real_exec(sys.executable, [sys.executable, peer, *arguments[1:]])
    return real_exec(program, arguments)

def native_run(command, **kwargs):
    if str(command[0]) == binary:
        return real_native_run([sys.executable, peer, *command[1:]], **kwargs)
    return real_native_run(command, **kwargs)

asyncio.create_subprocess_exec = create
codex_launcher.common.run_subprocess = run
codex_launcher.codex_runtime.find_runtime_launcher = lambda **_kwargs: pathlib.Path(binary)
os.execv = execute
codex_launcher.process_tree.run_with_timeout = native_run
class Terminal:
    def isatty(self): return True
sys.stdin = Terminal()

if os.environ['CODEX_PEER_MODE'] == 'partial':
    original_generation = codex_launcher.RecoveryBridge._generation
    def observed_generation(self):
        try:
            return original_generation(self)
        finally:
            path = self.home / 'app-server-daemon' / 'daemon.pid'
            if path.exists() and path.read_bytes() == b'':
                (self.home / 'metadata-observed').touch()
    codex_launcher.RecoveryBridge._generation = observed_generation

if os.environ['CODEX_PEER_MODE'] == 'dead':
    original_generation = codex_launcher.RecoveryBridge._generation
    def generation_after_tui_exit(self):
        current = original_generation(self)
        # このケースはTUI終了後の再開を検証する。接続中のbridge復旧は他ケースが担当する。
        if self._authenticated and self.connected_generation is not None:
            return self.connected_generation
        return current
    codex_launcher.RecoveryBridge._generation = generation_after_tui_exit

if os.environ.get('CODEX_PEER_WINDOWS') == '1':
    codex_launcher.sys = types.SimpleNamespace(**(vars(sys) | {'platform': 'win32'}))
sys.exit(codex_launcher.main())
"""


def test_public_wrapper_uses_installed_runtime_without_environment_setup(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """設定取得の標準入出力と終了状態を保ち、起動時のuv依存導入を行わない。"""
    runtime = tmp_path / "runtime.py"
    runtime.write_text(
        "import json,sys; print(json.dumps({'args':sys.argv[1:],'stdin':sys.stdin.read()})); sys.exit(7)",
        encoding="utf-8",
    )
    command = tmp_path / ("dotfiles-codex.cmd" if sys.platform == "win32" else "dotfiles-codex")
    if sys.platform == "win32":
        command.write_bytes(f'@"{sys.executable}" "{runtime}" %*\r\n'.encode("cp932"))
    else:
        command.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(runtime))} "$@"\n', encoding="utf-8"
        )
        command.chmod(0o755)
    root = pathlib.Path(__file__).resolve().parent.parent
    wrapper = root / "bin" / ("codex.cmd" if sys.platform == "win32" else "codex")
    environment = host_environ()
    environment.update(
        PATH=str(tmp_path) + os.pathsep + environment.get("PATH", ""),
        UV_OFFLINE="1",
        UV_PROJECT_ENVIRONMENT=str(tmp_path / "unused-environment"),
        UV_CACHE_DIR=str(tmp_path / "unused-cache"),
    )
    arguments = ["mcp", "get", "claude", "--json"]
    result = process_tree.run_with_timeout(
        [str(wrapper), *arguments], input="input\n", capture_output=True, encoding="utf-8", env=environment, timeout=20
    )
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == {"args": arguments, "stdin": "input\n"}
    assert not (tmp_path / "unused-environment").exists()
    assert not (tmp_path / "unused-cache").exists()


@dataclasses.dataclass(frozen=True)
class _Peer:
    root: pathlib.Path
    work: pathlib.Path
    runner: pathlib.Path
    environment: dict[str, str]

    async def run(
        self, arguments: list[str], *, mode: str, actors: int = 1, windows: bool = False, pid_format: str = "linux"
    ) -> tuple[int, str, str]:
        environment = dict(self.environment, CODEX_PEER_MODE=mode, CODEX_PEER_ACTORS=str(actors))
        environment["CODEX_PEER_PID_FORMAT"] = pid_format
        environment["CODEX_PEER_WINDOWS"] = "1" if windows else "0"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self.runner),
            *arguments,
            cwd=str(self.work),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        identity = None
        try:
            identity = psutil.Process(process.pid)
            identity.create_time()
            stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
            assert process.returncode is not None
            return process.returncode, stdout.decode("utf-8"), stderr.decode("utf-8")
        finally:
            if process.returncode is None:
                await asyncio.to_thread(process_tree.stop_tree, identity)
                await asyncio.wait_for(process.wait(), process_tree.RECLAIM_TIMEOUT_SECONDS)

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in (self.root / "threads").glob("*.json")]


@pytest.fixture(name="peer")
def _peer(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> _Peer:
    root, work = tmp_path / "peer", tmp_path / "work"
    root.mkdir()
    work.mkdir()
    runner = tmp_path / "runner.py"
    runner.write_text(_RUNNER, encoding="utf-8")
    environment = host_environ()
    environment.update(
        CODEX_PEER_ROOT=str(root),
        CODEX_HOME=str(root),
        CODEX_PEER_BINARY=str(tmp_path / "codex-peer"),
        CODEX_PEER_HELPER=str(pathlib.Path(__file__).with_name("codex_test_helpers.py")),
        DOTFILES_CODEX_RECOVERY_TIMEOUT_SECONDS="3",
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        APPDATA=str(tmp_path / "roaming"),
        LOCALAPPDATA=str(tmp_path / "local"),
        AGENT_TOOLKIT_PRIVATE_NOTES=str(tmp_path / "notes"),
    )
    environment.pop("AGENT_TOOLKIT_OWNER_SESSION", None)
    environment.pop("CODEX_THREAD_ID", None)
    return _Peer(root, work, runner, environment)


@pytest.mark.parametrize(
    ("mode", "effort", "expected"),
    [
        ("active", "medium", 0),
        ("active", "xhigh", 0),
        ("dead", "xhigh", 0),
        ("completed", "medium", 0),
        ("cancel", "medium", 130),
        ("approval", "medium", 0),
        ("lost-response", "medium", 0),
        ("partial", "medium", 0),
        ("error", "medium", 1),
        ("corrupt", "medium", 1),
        ("mcp-error", "medium", 1),
        ("elicitation", "medium", 0),
        ("elicitation-cancel", "medium", 130),
    ],
)
@pytest.mark.asyncio
async def test_public_launcher_preserves_work_and_stop_contract(peer: _Peer, mode: str, effort: str, expected: int) -> None:
    """CLI→実peerプロセス→世代交代→自動再開→所有する成果物の確認までを接続する。"""
    arguments = ["--no-alt-screen", "--cd", str(peer.work), "--config", f'model_reasoning_effort="{effort}"']
    if mode == "dead":
        arguments.append("--worktree")
    arguments.append("original owned work")
    code, stdout, stderr = await peer.run(arguments, mode=mode)
    assert code == expected, (stdout, stderr, peer.records())
    records = peer.records()
    assert len(records) == 1
    record = records[0]
    assert record["starts"] == (
        1 if mode in {"completed", "cancel", "error", "corrupt", "mcp-error", "elicitation-cancel"} else 2
    )
    assert record["effort"] == effort
    if mode == "elicitation-cancel":
        assert not tuple((peer.root / "results").glob("*.json"))
        return
    if mode in {"error", "corrupt", "mcp-error"}:
        assert {"error": "thread/resume", "corrupt": "JSONDecodeError", "mcp-error": "子孫復旧"}[mode] in stderr
        assert not tuple((peer.root / "results").glob("*.json"))
        return
    output = json.loads(stdout)
    assert output["id"] == record["id"] and output["effort"] == effort
    if mode not in {"completed", "cancel"}:
        assert output["cwd"] == record["cwd"]
        assert record["turns"][-1]["status"] == "completed"
        child = json.loads((peer.root / "children" / f"{record['id']}.json").read_text(encoding="utf-8"))
        assert child["direct_parent"] == record["id"]
    if mode == "partial":
        assert (peer.root / "metadata-observed").exists()
    if mode == "lost-response":
        assert json.loads((peer.root / "config-write.json").read_text(encoding="utf-8"))["count"] == 1
        error = json.loads((peer.root / "lost-request-error.json").read_text(encoding="utf-8"))
        assert "config/value/write" in error["message"]
        assert "入力欄" in error["message"] and "実行履歴" in error["message"]
    actors = [json.loads(path.read_text(encoding="utf-8")) for path in (peer.root / "actors").glob("*.json")]
    assert len(actors) == (2 if mode == "dead" else 1)
    if mode == "dead":
        resumed = next(value for value in actors if "resume" in value)
        assert "--worktree" not in resumed
        assert resumed[resumed.index("--cd") + 1] == record["cwd"]


@pytest.mark.asyncio
async def test_windows_daemon_record_without_process_identity_keeps_recovery(peer: _Peer) -> None:
    """processIdentityを書かないWindowsの世代記録でも終了せず、世代交代後に同じ仕事を再開する。"""
    code, stdout, stderr = await peer.run(
        ["--cd", str(peer.work), "--config", 'model_reasoning_effort="medium"', "owned work"],
        mode="active",
        pid_format="windows",
    )
    assert code == 0, (stdout, stderr)
    assert "ValueError" not in stderr
    assert peer.records()[0]["starts"] == 2


@pytest.mark.asyncio
async def test_parallel_public_launchers_keep_distinct_threads(peer: _Peer) -> None:
    """同じcwdの2会話を同時に再開しても、IDと起動条件を互いへ転用しない。"""
    results = await asyncio.gather(
        *(
            peer.run(
                ["--cd", str(peer.work), "--config", f'model_reasoning_effort="{effort}"', "owned work"],
                mode="active",
                actors=2,
            )
            for effort in ("medium", "xhigh")
        )
    )
    assert all(code == 0 for code, _stdout, _stderr in results), results
    outputs = [json.loads(stdout) for _code, stdout, _stderr in results]
    assert len({value["id"] for value in outputs}) == 2
    assert [value["effort"] for value in outputs] == ["medium", "xhigh"]
    assert all(record["starts"] == 2 for record in peer.records())


@pytest.mark.parametrize(
    "arguments",
    [
        ["plugin", "marketplace", "list", "--json"],
        ["--version"],
        ["completion", "bash"],
        ["exec", "--json", "literal spaces ; $(not-executed) & %NAME%"],
    ],
)
@pytest.mark.parametrize("windows", [False, True])
def test_noninteractive_commands_skip_recovery_dependencies(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], arguments: list[str], windows: bool
) -> None:
    """反復利用される非対話コマンドは、復旧用の依存を読み込まずに同じ実行体へ引き渡す。"""
    probe = tmp_path / "probe.py"
    probe.write_text(_NONINTERACTIVE_PROBE, encoding="utf-8")
    environment = host_environ()
    environment["CODEX_PROBE_WINDOWS"] = "1" if windows else "0"
    result = process_tree.run_with_timeout(
        [sys.executable, str(probe), *arguments],
        capture_output=True,
        encoding="utf-8",
        env=environment,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["arguments"] == arguments
    assert observed["loaded"] == []


_NONINTERACTIVE_PROBE = r"""
import json
import os
import pathlib
import sys
import types
from pytools import codex_launcher

_HEAVY = ("psutil", "pydantic", "pytools._internal.codex_bridge", "pytools._internal.codex_recovery", "asyncio")
binary = pathlib.Path(sys.executable)
codex_launcher.codex_runtime.find_runtime_launcher = lambda **_kwargs: binary


def record(arguments):
    loaded = sorted(name for name in sys.modules if name.startswith(_HEAVY))
    print(json.dumps({"arguments": list(arguments), "loaded": loaded}))
    sys.stdout.flush()
    os._exit(0)


def execute(program, arguments):
    record(arguments[1:])


class _Result:
    returncode = 0


def native_run(command, **_kwargs):
    record(command[1:])
    return _Result()


os.execv = execute
# 模擬するplatformを実行ホストによらず固定する。実Windowsで非Windows経路を模擬する場合も
# 分岐が実物のprocess_treeへ進まず、差し替えたexecvへ到達するようにする。
windows = os.environ["CODEX_PROBE_WINDOWS"] == "1"
codex_launcher.sys = types.SimpleNamespace(**(vars(sys) | {"platform": "win32" if windows else "linux"}))
if windows:
    codex_launcher.process_tree = types.SimpleNamespace(run_with_timeout=native_run)
sys.exit(codex_launcher.main(sys.argv[1:]))
"""


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.asyncio
async def test_noninteractive_arguments_and_exit_status_pass_through(peer: _Peer, windows: bool) -> None:
    """専用App Serverを使う既存CLIの引数・標準出力・終了状態を保持する。"""
    arguments = ["exec", "--json", "literal spaces ; $(not-executed) & %NAME%"]
    code, stdout, stderr = await peer.run(arguments, mode="active", windows=windows)
    assert code == 7, stderr
    assert json.loads(stdout)["passthrough"] == arguments
    assert not (peer.root / "app-server-daemon" / "daemon.pid").exists()


@pytest.mark.asyncio
async def test_unrelated_failure_after_completed_recovery_is_not_restarted(peer: _Peer) -> None:
    """復旧済みの古い世代交代を、その後の別の仕事の異常終了へ転用しない。"""
    code, _stdout, _stderr = await peer.run(
        ["--cd", str(peer.work), "--config", 'model_reasoning_effort="medium"', "owned work"], mode="late-error"
    )
    assert code == 1
    assert len(tuple((peer.root / "actors").glob("*.json"))) == 1
    assert peer.records()[0]["starts"] == 3
