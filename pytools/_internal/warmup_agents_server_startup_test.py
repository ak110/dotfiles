import asyncio
import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any, cast

import psutil
import pytest
from agent_toolkit._agents_server import service_forward, service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import delegated_session, plugin_environment, process_tree, windows_job
from agent_toolkit._common.runtime_environment import CallerContext
from agent_toolkit._testing import isolation
from agent_toolkit._testing.codex_plugin_support import _REAL_MCP_PREPARE_TIMEOUT
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment

from pytools import post_apply
from pytools._internal import common as _common
from pytools._internal import warmup_agents_server as _warmup
from pytools._internal.warmup_agents_server import _check_startup as _CHECK_STARTUP

_PLUGIN_ROOT = pathlib.Path(plugin_environment.__file__).resolve().parents[2]


@pytest.fixture(name="service_environment", scope="module")
def _service_environment(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    environment = {
        key: value
        for key, value in isolation.build_host_environ().items()
        if key not in {"AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "VIRTUAL_ENV", "CLAUDECODE", "UV_FROZEN"}
    }
    # WindowsのDLL読み込みが260文字のパス上限を超えないよう、短い領域へ準備する。  # pyfltr: allow-comment
    environment["AGENT_TOOLKIT_RUNTIME_DATA"] = str(tmp_path_factory.mktemp("rt"))
    prepare_plugin_environment(_PLUGIN_ROOT, environment, timeout=_REAL_MCP_PREPARE_TIMEOUT)
    return environment


def _stop_services(directory: pathlib.Path) -> None:
    for path in directory.glob("*.json"):
        if path.name == "generations.json":
            continue
        identity = json.loads(path.read_text(encoding="utf-8"))["process"]
        if not service_transport.process_alive(identity):
            continue
        with contextlib.suppress(psutil.NoSuchProcess):
            service = psutil.Process(int(identity.split(":", 1)[0]))
            owners = [process for process in service.parents() if _serves(process, directory)]
            result = process_tree.stop_tree(owners[-1] if owners else service)
            assert not result.residual and not result.unresolved, result.descriptions()
        assert not service_transport.process_alive(identity)


def _serves(process: psutil.Process, directory: pathlib.Path) -> bool:
    with contextlib.suppress(psutil.Error):
        return str(directory) in process.cmdline()
    return False


def _launch_command(root: pathlib.Path) -> list[str]:
    return [
        sys.executable,
        str(_PLUGIN_ROOT / "scripts" / "launch.py"),
        "--project",
        str(root),
        str(root / "agent_toolkit" / "agents_server_mcp.py"),
    ]


@pytest.mark.windows
@pytest.mark.timeout(240)
@pytest.mark.cpu_limit(
    3.0,
    reason="実のlauncher・転送側・管理サービスを起動し、MCPのinitializeとtools/listを通して世代を公開する。",
)
def test_published_service_outlives_startup_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, service_environment: dict[str, str]
) -> None:
    """公開した世代は確認用の接続と子の終了後も生存し、次の版が受付を引き継ぐ。  pyfltr: allow-comment

    WindowsのMCP SDKは確認用の子プロセスを離脱不可のKILL_ON_JOB_CLOSEのJobへ入れ、接続を閉じるとJobの全プロセスを止める。
    2026-11-18、確認用の転送側が起動した新世代が確認の終了とともに終了し、受付先が終了した世代を指し続けた。
    """
    environment = dict(
        service_environment,
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        LOCALAPPDATA=str(tmp_path / "state"),
        AGENT_TOOLKIT_PRIVATE_NOTES=str(tmp_path / "notes"),
    )
    environment.pop(delegated_session.CALLER_PLUGIN_ROOT_ENV, None)
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(_warmup, "_STARTUP_TIMEOUT", 120.0)

    previous_root = tmp_path / "previous" / "agent-toolkit"
    updated = tmp_path / "updated" / "agent-toolkit"
    ignore = shutil.ignore_patterns(".venv", "__pycache__", "node_modules", "*_test.py")
    shutil.copytree(_PLUGIN_ROOT, previous_root, ignore=ignore)
    shutil.copytree(previous_root, updated, ignore=ignore)
    with (updated / "agent_toolkit" / "agents_server_mcp.py").open("a", encoding="utf-8") as script:
        script.write("# updated\n")
    repo_id = "github.com/ak110/dotfiles"
    config = _warmup.config_settings.config_file_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    config_text = json.dumps({"repositories": json.dumps({repo_id: {"path": str(updated.parent)}})})
    config.write_text(config_text, encoding="utf-8")
    notes = tmp_path / "notes"
    notes.mkdir()
    shared = notes / _warmup.config_settings.SHARED_SETTINGS_FILENAME
    shared_text = json.dumps({"repositories": json.dumps({repo_id: {"edit": "allow"}})})
    shared.write_text(shared_text, encoding="utf-8")
    monkeypatch.setenv(
        _warmup.config_settings.config_env_name("repositories"),
        json.dumps({repo_id: {"edit": "allow", "path": str(previous_root.parent)}}),
    )
    monkeypatch.setattr(_common, "find_dotfiles_root", lambda: updated.parent)
    monkeypatch.setattr(_warmup, "_targets", lambda: [updated / "agent_toolkit" / "agents_server_mcp.py"])
    monkeypatch.setattr(_warmup.plugin_warmup, "_find_python", lambda _uv: pathlib.Path(sys.executable))
    monkeypatch.setattr(_warmup.windows_security, "is_elevated_above_user_processes", lambda: False)
    monkeypatch.setattr(_warmup.plugin_warmup, "cleanup_prepared", lambda *_args, **_kwargs: None)
    registry = GenerationRegistry(service_transport.namespace(CallerContext(environment, tmp_path)))

    def published(target: pathlib.Path) -> dict:
        outcome = asyncio.run(_CHECK_STARTUP(_launch_command(target)))
        assert outcome.ok, outcome.message
        current = registry.current()
        assert current is not None and current["generation"] == service_forward.code_identity(target, registry.directory)[0]
        assert service_transport.process_alive(current["process"])
        return current

    try:
        previous = published(previous_root)
        results, _ = post_apply.run([("agents_server", _warmup.run)])
        assert results[0].ok, results[0].reason
        current = registry.current()
        assert current is not None and current["generation"] == service_forward.code_identity(updated, registry.directory)[0]
        assert current["generation"] != previous["generation"] and service_transport.process_alive(current["process"])
        assert config.read_text(encoding="utf-8") == config_text and shared.read_text(encoding="utf-8") == shared_text

        deadline = time.monotonic() + 30
        while service_transport.process_alive(previous["process"]):
            assert time.monotonic() < deadline, "旧世代が退出しない"
            time.sleep(0.1)
    finally:
        _stop_services(registry.directory)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Jobの終了が所属プロセスを止める条件を実APIで確かめる")
@pytest.mark.timeout(240)
@pytest.mark.cpu_limit(1.5, reason="実Windows Job・管理サービスと資源回収を通す。sthenoの所有CPU実測は1.079秒。")
def test_service_outlives_killing_job_of_its_launcher(tmp_path: pathlib.Path, service_environment: dict[str, str]) -> None:
    """離脱を許すKILL_ON_JOB_CLOSEのJobの中から起動した管理サービスは、そのJobの終了後も生存する。  pyfltr: allow-comment

    SSHのセッションは所属プロセスをこの種類のJobへ入れ、venvのpython.exeは子を自動で離脱させるJobをその内側に作る。
    2026-11-19、SSHのセッションから起動した管理サービスが外側のJobに残り、セッションの終了とともに終了した。
    """
    environment = dict(service_environment, XDG_STATE_HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    registry = GenerationRegistry(service_transport.namespace(CallerContext(environment, tmp_path)))
    try:
        with windows_job.WindowsJob() as job:

            def member(command: list[str]) -> subprocess.Popen[bytes]:
                process = subprocess.Popen(  # pylint: disable=consider-using-with
                    command, stdin=subprocess.DEVNULL, env=environment, creationflags=windows_job.CREATE_SUSPENDED
                )
                # _handleはWindows版CPythonの属性であり、Linux上の型検査からは解決できない。  # pyfltr: allow-comment
                job.assign_suspended(int(cast(Any, process)._handle), process.pid)  # pylint: disable=protected-access,no-member
                return process

            launcher = member(_launch_command(_PLUGIN_ROOT))
            unbroken = member([sys.executable, "-c", "import time; time.sleep(600)"])
            assert launcher.wait(timeout=120) == 0
            current = registry.current()
            assert current is not None and service_transport.process_alive(current["process"])

            service = psutil.Process(int(current["process"].split(":", 1)[0]))
            owners = [process for process in service.parents() if _serves(process, registry.directory)]
            assert owners

        unbroken.wait(timeout=30)
        exited, _running = psutil.wait_procs([service, *owners], timeout=3)
        assert not exited
        assert service_transport.process_alive(current["process"])
    finally:
        _stop_services(registry.directory)
