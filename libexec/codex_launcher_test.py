"""通常CLIから外部peerを起動し、同じ仕事と子の復旧・明示中止・承認を検証する。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import pathlib
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
sys.path.insert(0, os.environ['CODEX_PEER_LIBEXEC'])
import codex_launcher

peer = os.environ['CODEX_PEER_HELPER']
binary = os.environ['CODEX_PEER_BINARY']
real_create = asyncio.create_subprocess_exec
real_run = codex_launcher.common.run_subprocess
real_exec = os.execv

async def create(program, *arguments, **kwargs):
    if str(program) == binary:
        return await real_create(sys.executable, peer, *arguments, **kwargs)
    return await real_create(program, *arguments, **kwargs)

def run(command, **kwargs):
    if str(command[0]) == binary:
        return real_run([sys.executable, peer, *command[1:]], **kwargs)
    return real_run(command, **kwargs)

def execute(program, arguments):
    if str(program) == binary:
        return real_exec(sys.executable, [sys.executable, peer, *arguments[1:]])
    return real_exec(program, arguments)

asyncio.create_subprocess_exec = create
codex_launcher.common.run_subprocess = run
codex_launcher.setup_codex_cli.find_runtime_launcher = lambda **_kwargs: pathlib.Path(binary)
os.execv = execute
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

sys.exit(codex_launcher.main())
"""


@dataclasses.dataclass(frozen=True)
class _Peer:
    root: pathlib.Path
    work: pathlib.Path
    runner: pathlib.Path
    environment: dict[str, str]

    async def run(self, arguments: list[str], *, mode: str, actors: int = 1) -> tuple[int, str, str]:
        environment = dict(self.environment, CODEX_PEER_MODE=mode, CODEX_PEER_ACTORS=str(actors))
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
        CODEX_PEER_HELPER=str(pathlib.Path(__file__).with_name("_codex_test_peer.py")),
        CODEX_PEER_LIBEXEC=str(pathlib.Path(__file__).parent),
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
    assert code == expected, (stdout, stderr)
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


@pytest.mark.asyncio
async def test_noninteractive_arguments_and_exit_status_pass_through(peer: _Peer) -> None:
    """専用App Serverを使う既存CLIの引数・標準出力・終了状態を保持する。"""
    arguments = ["exec", "--json", "literal spaces ; $(not-executed) & %NAME%"]
    code, stdout, stderr = await peer.run(arguments, mode="active")
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
