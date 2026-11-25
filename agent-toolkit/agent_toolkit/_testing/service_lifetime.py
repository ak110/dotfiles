from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import psutil

from agent_toolkit._common import process_identity, process_tree, windows_job

_RECOVERY_SECONDS = 40


@contextlib.contextmanager
def guard(state_base: Path) -> Generator[None]:
    with subprocess.Popen(
        [sys.executable, "-m", "agent_toolkit._testing.service_lifetime", str(state_base.resolve())],
        stdin=subprocess.PIPE,
        close_fds=True,
        start_new_session=os.name != "nt",
        creationflags=windows_job.HIDDEN_DETACHED_CREATIONFLAGS if os.name == "nt" else 0,
    ) as janitor:
        assert janitor.stdin is not None
        try:
            yield
        finally:
            janitor.stdin.close()
            try:
                status = janitor.wait(timeout=_RECOVERY_SECONDS)
            except subprocess.TimeoutExpired:
                janitor.kill()
                raise
            assert status == 0, f"試験用サービスの回収に失敗: {state_base} (終了コード: {status})"


def _targets(state_base: Path) -> list[tuple[int, psutil.Process, process_identity.ProcessIdentity]]:
    services = state_base / "agent-toolkit" / "services"
    state_variable = "LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"
    found = []
    for listed in psutil.process_iter():
        try:
            process = psutil.Process(listed.pid)
            arguments = process.cmdline()
            if not arguments or process.status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
                continue
            if len(arguments) >= 3 and (
                Path(arguments[-3]).parts[-2:] == ("_agents_server", "service.py")
                or arguments[-3] == "agent_toolkit/_agents_server/service.py"
            ):
                namespace = Path(arguments[-2])
                if namespace.parent == services and namespace.name in {
                    "agents-server",
                    "agents-server-elevated",
                    "agents-server-standard",
                }:
                    priority = 1 if "--agents-service" in arguments else 2
                    found.append((priority, process, process_identity.ProcessIdentity.capture(process)))
            elif any(Path(arg).name == "agents_server_mcp.py" for arg in arguments[1:]) and process.environ().get(
                state_variable
            ) == str(state_base):
                found.append((0, process, process_identity.ProcessIdentity.capture(process)))
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
    return sorted(found, key=lambda target: target[0])


def _recover(state_base: Path) -> None:
    # pyfltr: allow-comment 転送側とランチャーを先に止め、後続の走査で直前に起動されたサービス本体も検出する。
    for priority in range(3):
        for order, process, identity in _targets(state_base):
            if order != priority:
                continue
            try:
                if identity.matches(process):
                    result = process_tree.stop_tree(process)
                    if result.residual or result.unresolved:
                        raise RuntimeError(result.descriptions())
            except psutil.NoSuchProcess:
                continue
    remaining = _targets(state_base)
    assert not remaining, f"回収後も試験用サービスが残っている: {[identity for _, _, identity in remaining]}"


if __name__ == "__main__":
    sys.stdin.buffer.read()
    _recover(Path(sys.argv[1]))
