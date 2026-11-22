"""登録簿の要求の列挙が、登録簿の件数に比例する仕事を監視の毎巡で繰り返さないことを確かめる。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import time
import types

import pytest

from agent_toolkit._common import effort_requests, registry_requests, session_registry, session_release


class _CountingScandir:
    """列挙で読んだディレクトリ項目の数を数える。"""

    def __init__(self) -> None:
        self.entries = 0

    @contextlib.contextmanager
    def __call__(self, path):
        with os.scandir(path) as entries:
            yield self._count(entries)

    def _count(self, entries):
        for entry in entries:
            self.entries += 1
            yield entry


def _registry(tmp_path: pathlib.Path, count: int) -> pathlib.Path:
    directory = session_registry.registry_directory(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (directory / f"{index:032x}.json").touch()
    return directory


def _settle(directory: pathlib.Path, seconds_ago: int) -> None:
    """登録簿の最後の変更から時刻の刻み（ext3は1秒）より十分に時間が過ぎた状態にする。"""
    settled = (time.time_ns() // 1_000_000_000 - seconds_ago) * 1_000_000_000
    os.utime(directory, ns=(settled, settled))


def _monitor_cycle(tmp_path: pathlib.Path) -> None:
    """agents_serverの監視の1巡と同じく、effort要求と解放要求を探す。"""
    assert not effort_requests.pending(state_root=tmp_path)
    asyncio.run(session_release.process_requests({}, _unexpected_stop, state_root=tmp_path))


async def _unexpected_stop(*_args, **_kwargs) -> dict:
    raise AssertionError("要求が無いのにstopが呼ばれた")


def test_monitor_cycles_read_no_entries_while_registry_is_unchanged(tmp_path: pathlib.Path, monkeypatch) -> None:
    """登録簿の件数を倍にしても、変化の無い間の監視の1巡で読む項目数は0のままで、新しい要求は次の巡で見つかる。"""
    scandir = _CountingScandir()
    monkeypatch.setattr(registry_requests, "os", types.SimpleNamespace(stat=os.stat, fspath=os.fspath, scandir=scandir))
    directory = _registry(tmp_path, 300)
    _settle(directory, 60)

    per_cycle = []
    for _cycle in range(3):
        scandir.entries = 0
        _monitor_cycle(tmp_path)
        per_cycle.append(scandir.entries)
    assert per_cycle == [300, 0, 0]

    for index in range(300, 600):
        (directory / f"{index:032x}.json").touch()
    _settle(directory, 30)
    per_cycle = []
    for _cycle in range(3):
        scandir.entries = 0
        _monitor_cycle(tmp_path)
        per_cycle.append(scandir.entries)
    assert per_cycle == [600, 0, 0]

    request = directory / "token.effort-request"
    request.write_text("{}", encoding="utf-8")
    assert registry_requests.request_paths(directory, registry_requests.EFFORT_REQUEST_SUFFIX) == [request]
    assert registry_requests.request_paths(directory, registry_requests.RELEASE_REQUEST_SUFFIX) == []


def test_request_written_within_the_same_timestamp_tick_is_found(tmp_path: pathlib.Path, monkeypatch) -> None:
    """更新時刻の刻みが粗いファイルシステムで、列挙と同じ刻みの中に置かれた要求を次の巡で見つける。"""
    directory = _registry(tmp_path, 3)
    tick = time.time_ns() // 1_000_000_000 * 1_000_000_000

    def coarse_stat(path):
        status = os.stat(path)
        return types.SimpleNamespace(st_ino=status.st_ino, st_mtime_ns=tick)

    monkeypatch.setattr(registry_requests, "os", types.SimpleNamespace(stat=coarse_stat, fspath=os.fspath, scandir=os.scandir))

    assert registry_requests.request_paths(directory, registry_requests.RELEASE_REQUEST_SUFFIX) == []
    request = directory / "token.release-request"
    request.write_text("{}", encoding="utf-8")
    assert registry_requests.request_paths(directory, registry_requests.RELEASE_REQUEST_SUFFIX) == [request]


@pytest.mark.parametrize("state", ["missing", "empty"])
def test_absent_or_empty_registry_has_no_requests(tmp_path: pathlib.Path, state: str) -> None:
    directory = session_registry.registry_directory(tmp_path)
    if state == "empty":
        directory.mkdir(parents=True)
    assert registry_requests.request_paths(directory, registry_requests.EFFORT_REQUEST_SUFFIX) == []


@pytest.mark.parametrize("kind", ["effort", "release"])
def test_completion_stays_with_request_even_after_waiting_ends(tmp_path, kind):
    request = tmp_path / f"token.{kind}-request"
    old_response = request.with_suffix(f".{kind}-response")
    registry_requests.write_request(request, {"version": 1, "deadline": time.monotonic() + 60})
    assert registry_requests.pending_payload(request, old_response) is not None
    registry_requests.write_request(request, {"version": 1, "deadline": 0})
    assert registry_requests.pending_payload(request, old_response) is None

    registry_requests.respond(request, old_response, {"observed": "完了"})
    assert not old_response.exists() and not request.exists()
    text, denial = registry_requests.read_result(request, old_response)
    assert text is not None and json.loads(text) == {"observed": "完了"} and denial is None
    registry_requests.respond(request, old_response, {"observed": "別の結果"})
    assert registry_requests.read_result(request, old_response) == (text, None)


def test_request_accepts_legacy_result_and_legacy_sender_still_gets_result(tmp_path):
    request = tmp_path / "legacy.effort-request"
    response = request.with_suffix(".effort-response")
    request.write_text('{"version":1}', encoding="utf-8")
    registry_requests.respond(request, response, {"observed": "完了"})
    assert json.loads(response.read_text(encoding="utf-8")) == {"observed": "完了"}

    registry_requests.write_request(request, {"version": 1, "deadline": time.monotonic() + 60})
    assert registry_requests.read_result(request, response) == (response.read_text(encoding="utf-8"), None)
    assert registry_requests.pending_payload(request, response) is None


def test_inaccessible_result_can_be_read_again(tmp_path, monkeypatch):
    request = tmp_path / "busy.effort-request"
    response = request.with_suffix(".effort-response")
    registry_requests.write_request(request, {"version": 1, "deadline": time.monotonic() + 60})
    registry_requests.respond(request, response, {"observed": "完了"})
    original = pathlib.Path.read_text

    def denied(path, **kwargs):
        if path.name.endswith("-result"):
            raise PermissionError("一時的な共有拒否")
        return original(path, **kwargs)

    with monkeypatch.context() as attempt:
        attempt.setattr(pathlib.Path, "read_text", denied)
        text, denial = registry_requests.read_result(request, response)
    assert text is None and isinstance(denial, PermissionError)
    text, denial = registry_requests.read_result(request, response)
    assert text is not None and json.loads(text) == {"observed": "完了"} and denial is None
