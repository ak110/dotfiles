"""共有検証の受入試験が起動する、保持者・公開CLI・実serveの独立プロセス。"""

from __future__ import annotations

import asyncio
import json
import pathlib
import runpy
import socket
import sys
import threading
import time
from typing import Any

import watchdog.utils
from pyfltr.command import execution_slot

from agent_toolkit._atk.serve import app, config, plans, sessions, state, wi_operations
from agent_toolkit._common.atomic_file import atomic_write


def _wait_release(root: pathlib.Path, label: str) -> None:
    deadline = time.monotonic() + 40
    while not (root / f"{label}.release").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"解放の指示が届かない: {label}")
        time.sleep(0.01)


def _entry(root: pathlib.Path, label: str, argv: list[str]) -> None:
    original = execution_slot.hold

    def observed_hold(*, on_wait: Any = None, **kwargs: Any) -> Any:
        def waiting(owner: execution_slot.SlotOwner | None) -> None:
            (root / f"{label}.waiting").touch()
            if on_wait is not None:
                on_wait(owner)

        return original(on_wait=waiting, **kwargs)

    execution_slot.hold = observed_hold
    if argv[0] == "-m":
        sys.argv = argv[1:]
        runpy.run_module(argv[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = argv
        runpy.run_path(argv[0], run_name="__main__")


class _OfflineOperations(wi_operations.Operations):
    """一時データの配信と監視を実行し、外部Gitとの同期を省く。"""

    def sync(self) -> bool:
        return True

    def background_sync(self) -> bool:
        return False


async def _serve(root: pathlib.Path, failure: bool) -> None:
    notes = root / "notes"
    plan_root = root / "plans"
    plan_root.mkdir()
    watched = state.ServeState(notes)
    observed_threads: list[threading.Thread] = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = int(reserved.getsockname()[1])
    try:
        async with watched:
            application = app.create_app(
                notes,
                config.ServeConfig("127.0.0.1", port),
                watched,
                operations=_OfflineOperations(notes),
                plans_context=plans.create_context(root=plan_root, hostname="acceptance"),
                sessions_context=sessions.create_context(
                    hostname="acceptance", claude_home=root / "claude", codex_home=root / "codex"
                ),
            )

            async def shutdown() -> None:
                observed_threads.extend(
                    thread for thread in threading.enumerate() if isinstance(thread, watchdog.utils.BaseThread)
                )
                assert all(thread.is_alive() for thread in observed_threads)
                atomic_write(root / "serve.ready", json.dumps({"port": port}))
                deadline = asyncio.get_running_loop().time() + 40
                while not (root / "serve.release").exists():
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError("serveの停止指示が届かない")
                    await asyncio.sleep(0.01)
                watched.request_shutdown()

            await application.run_task("127.0.0.1", port, shutdown_trigger=shutdown)
            if failure:
                raise ValueError("受入観測後の失敗")
    finally:
        stopped = [{"name": thread.name, "alive": thread.is_alive()} for thread in observed_threads]
        (root / "serve.stopped").write_text(json.dumps(stopped), encoding="utf-8")
        assert stopped and not any(item["alive"] for item in stopped)


def main() -> None:
    """試験が指定した独立実行を、実行枠の所有を保って完了する。"""
    mode, root_value, label, *argv = sys.argv[1:]
    root = pathlib.Path(root_value)
    if mode == "entry":
        _entry(root, label, argv)
        return
    with execution_slot.hold(label=label):
        if mode == "hold":
            (root / f"{label}.ready").touch()
            _wait_release(root, label)
        elif mode == "serve":
            asyncio.run(_serve(root, failure=label == "failure"))
        else:
            raise ValueError(f"試験のmodeが不正: {mode}")


if __name__ == "__main__":
    main()
