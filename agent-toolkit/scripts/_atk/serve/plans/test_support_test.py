# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
"""`atk serve`の計画ファイル画面の処理のテスト。"""

# pylint: disable=protected-access

import asyncio
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import typing

import pytest

from _atk.serve import plans


@pytest.fixture(name="index_path")
def _index_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """作成日時インデックスを一時ディレクトリへ隔離する。"""
    path = tmp_path / "cache" / "index.json"
    monkeypatch.setattr(plans, "_CREATION_TIME_INDEX_PATH", path)
    monkeypatch.setattr(plans, "_ctime_epoch", lambda st: float(st.st_mtime))
    return path


def _legacy_cache_path(index_path: pathlib.Path, host: str, rel: str) -> pathlib.Path:
    """旧形式（1エントリ1ファイル）のキャッシュパスを組み立てる。"""
    digest = hashlib.sha256(f"{host}\0{rel}".encode()).hexdigest()
    return index_path.parent / f"{digest}.json"


def _write_legacy_cache(path: pathlib.Path, host: str, rel: str, ctime_epoch: float) -> None:
    """旧形式のキャッシュを出力する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"host": host, "path": rel, "ctime_epoch": ctime_epoch}), encoding="utf-8")


def _plan(root: pathlib.Path, rel: str, body: str = "x", *, mtime: float = 2_000.0) -> pathlib.Path:
    """計画ファイルを1件作成する。"""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _context(root: pathlib.Path, **kwargs: typing.Any) -> plans.PlansContext:
    """単一rootの計画ファイル画面のコンテキストを生成する。"""
    return plans.create_context(root=root, hostname="local-host", **kwargs)


class _FakeWatcher:
    """常駐SSH接続のRPCを差し替える検体。"""

    def __init__(self, *, connected: bool, response: typing.Any) -> None:
        self._connected = connected
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def is_connected(self) -> bool:
        """接続状態を返す。"""
        return self._connected

    async def request(self, op: str, args: dict[str, str]) -> dict[str, typing.Any]:
        """RPC応答を返すか、設定された例外を送出する。"""
        self.calls.append((op, args))
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


def _read_payload(text: str, mtime: float | None = 1_000.0) -> dict[str, typing.Any]:
    """リモートヘルパーの`read`応答を組み立てる。"""
    payload: dict[str, typing.Any] = {"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    if mtime is not None:
        payload["mtime_epoch"] = mtime
    return payload


def _runner_returning(payload: dict[str, typing.Any]) -> tuple[plans.SshRunner, list[tuple[str, str, list[str]]]]:
    """単発SSHの呼び出しを記録するrunnerと、その記録先を返す。"""
    calls: list[tuple[str, str, list[str]]] = []

    async def runner(host: str, op: str, args: list[str]) -> str:
        calls.append((host, op, args))
        return json.dumps(payload)

    return runner, calls


def _failed_ssh(returncode: int, stderr: bytes) -> typing.Callable[..., subprocess.CompletedProcess[bytes]]:
    """指定した終了コードと標準エラー出力を返す`subprocess.run`の代用を組み立てる。"""

    def run(*args: typing.Any, **kwargs: typing.Any) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=b"", stderr=stderr)

    return run


__all__ = [
    "_FakeWatcher",
    "_context",
    "_failed_ssh",
    "_index_path",
    "_legacy_cache_path",
    "_plan",
    "_read_payload",
    "_runner_returning",
    "_write_legacy_cache",
]
