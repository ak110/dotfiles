# pylint: disable=protected-access

import base64
import hashlib
import json
import os
import pathlib
import typing

from agent_toolkit._atk.serve import plans
from agent_toolkit._atk.serve.plans import roots as plans_roots


def _legacy_cache_path(index_path: pathlib.Path, host: str, rel: str) -> pathlib.Path:
    digest = hashlib.sha256(f"{host}\0{rel}".encode()).hexdigest()
    return index_path.parent / f"{digest}.json"


def _write_legacy_cache(path: pathlib.Path, host: str, rel: str, ctime_epoch: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"host": host, "path": rel, "ctime_epoch": ctime_epoch}), encoding="utf-8")


def _plan(root: pathlib.Path, rel: str, body: str = "x", *, mtime: float = 2_000.0) -> pathlib.Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _context(root: pathlib.Path, **kwargs: typing.Any) -> plans.PlansContext:
    return plans.create_context(root=root, hostname="local-host", **kwargs)


class _FakeWatcher:
    def __init__(self, *, connected: bool, response: typing.Any) -> None:
        self._connected = connected
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def is_connected(self) -> bool:
        return self._connected

    async def request(self, op: str, args: dict[str, str]) -> dict[str, typing.Any]:
        self.calls.append((op, args))
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


def _read_payload(text: str) -> dict[str, typing.Any]:
    return {"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def _runner_returning(payload: dict[str, typing.Any]) -> tuple[plans_roots.SshRunner, list[tuple[str, str, list[str]]]]:
    calls: list[tuple[str, str, list[str]]] = []

    async def runner(host: str, op: str, args: list[str]) -> str:
        calls.append((host, op, args))
        return json.dumps(payload)

    return runner, calls


def _failed_ssh(returncode: int, stderr: bytes) -> typing.Callable[..., typing.Awaitable[tuple[int, bytes, bytes]]]:

    async def run(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        del cmd, timeout
        return returncode, b"", stderr

    return run
