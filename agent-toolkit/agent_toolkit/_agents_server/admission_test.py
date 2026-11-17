"""マシン全体の同時稼働数の上限で、起動が待たされ空きで進み、親子の組が止まらないことを確かめる。"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import time

import pytest

from agent_toolkit._agents_server import admission
from agent_toolkit._atk import config_settings


def _write_status(state_root: pathlib.Path, root: str, writer: str, host: str | None, sessions: dict[str, str]) -> pathlib.Path:
    """1つの書込主体の状態ファイルを、heartbeatが有効な状態で書く。"""
    directory = state_root / "agents-server" / root
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{writer}.json"
    now = datetime.datetime.now(datetime.UTC).isoformat()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "host_session_id": host,
                "heartbeat_at": now,
                "sessions": [
                    {"session_id": session_id, "status": status, "updated_at": now} for session_id, status in sessions.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("setting", "cpus", "expected"),
    [
        ("2.5x", 6, 15),
        ("2.5x", 12, 30),
        ("1x", 1, 1),
        ("0.1x", 2, 1),
        ("8", 64, 8),
        ("off", 6, None),
    ],
)
def test_limit_scales_with_cpu_count_and_settings(setting: str, cpus: int, expected: int | None) -> None:
    """係数はCPU数に比例し、絶対値はCPU数によらず、offは制限しない。最小値は1。"""
    assert admission.resolve_limit(setting, cpu_count=cpus).value == expected


@pytest.mark.parametrize("value", ["0", "-1x", "0x", "abc", "2.5", "nanx", ""])
def test_invalid_settings_are_rejected(value: str) -> None:
    """意味の定まらない値は初期値へ置き換えずに拒否する。"""
    with pytest.raises(ValueError):
        config_settings.parse_agents_concurrency(value)


def test_default_setting_limits_by_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定が無い場合は論理CPU数×2.5で制限する。"""
    monkeypatch.setattr(admission.os, "cpu_count", lambda: 4)
    assert admission.configured_limit().value == 10


def test_waiting_parents_and_parents_with_live_children_are_not_counted(tmp_path: pathlib.Path) -> None:
    """子を持つsessionと、子の起動を待つsessionは作業中に数えない。"""
    _write_status(tmp_path, "root", "w1", None, {"parent": "running", "leaf": "running", "done": "completed"})
    _write_status(tmp_path, "root", "w2", "parent", {"child": "running"})
    assert admission.observe(tmp_path).working == 2  # leafとchild
    tickets = admission.admission_directory(tmp_path)
    tickets.mkdir(parents=True)
    (tickets / "0001.json").write_text(
        json.dumps({"pid": os.getpid(), "host_session_id": "leaf", "requested_at": time.time()}), encoding="utf-8"
    )
    observed = admission.observe(tmp_path)
    assert (observed.working, observed.waiting) == (1, 1)


def test_tickets_of_dead_processes_release_slots(tmp_path: pathlib.Path) -> None:
    """異常終了したプロセスの許可・待機は枠を持ち続けない。"""
    tickets = admission.admission_directory(tmp_path)
    tickets.mkdir(parents=True)
    dead = tickets / "0001.json"
    dead.write_text(json.dumps({"pid": 2**22 + 12345, "admitted_at": 1e12}), encoding="utf-8")
    assert admission.observe(tmp_path).admitted == 0
    assert not dead.exists()


def test_launches_beyond_limit_wait_and_proceed_in_request_order(tmp_path: pathlib.Path) -> None:
    """上限を超えた起動は待たされ、空きが出た順に依頼順で進む。作業中は上限を超えない。"""
    limit = admission.Limit(2, "test")
    running: dict[str, str] = {}
    order: list[str] = []
    peak = 0

    async def scenario() -> None:
        nonlocal peak

        async def launch(name: str) -> None:
            nonlocal peak
            ticket = await admission.acquire(None, limit=limit, state_root=tmp_path, sleep=lambda _s: asyncio.sleep(0.01))
            running[name] = "running"
            _write_status(tmp_path, "root", "w", None, running)
            ticket.release()
            order.append(name)
            peak = max(peak, admission.observe(tmp_path).working)

        tasks = []
        for name in ("a", "b", "c", "d"):
            tasks.append(asyncio.create_task(launch(name)))
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        assert order == ["a", "b"]  # c・dは待っている
        assert admission.observe(tmp_path).waiting == 2
        running["a"] = "completed"
        _write_status(tmp_path, "root", "w", None, running)
        await asyncio.sleep(0.2)
        assert order == ["a", "b", "c"]
        running["b"] = "completed"
        _write_status(tmp_path, "root", "w", None, running)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    asyncio.run(scenario())
    assert order == ["a", "b", "c", "d"]
    assert peak <= 2


def test_parent_waiting_for_child_does_not_deadlock_at_full_limit(tmp_path: pathlib.Path) -> None:
    """上限いっぱいの親がそれぞれ子を起動しても、待つ親を数えないため子が起動して結果が戻る。"""
    limit = admission.Limit(2, "test")
    _write_status(tmp_path, "root", "w0", None, {"p1": "running", "p2": "running"})

    async def child_of(parent: str) -> str:
        ticket = await admission.acquire(parent, limit=limit, state_root=tmp_path, sleep=lambda _s: asyncio.sleep(0.01))
        _write_status(tmp_path, "root", f"w-{parent}", parent, {f"{parent}-child": "running"})
        ticket.release()
        # 子が終わり、親へ結果が戻る。
        _write_status(tmp_path, "root", f"w-{parent}", parent, {f"{parent}-child": "completed"})
        return f"{parent}-child"

    async def scenario() -> list[str]:
        return list(await asyncio.wait_for(asyncio.gather(child_of("p1"), child_of("p2")), timeout=5))

    assert asyncio.run(scenario()) == ["p1-child", "p2-child"]


def test_disabled_limit_never_waits(tmp_path: pathlib.Path) -> None:
    """無効化した設定では作業中の数によらず即座に許可し、待ちを応答へ加えない。"""
    _write_status(tmp_path, "root", "w", None, {f"s{index}": "running" for index in range(50)})
    ticket = asyncio.run(admission.acquire(None, limit=admission.Limit(None, "off"), state_root=tmp_path))
    assert ticket.response_fields() == {}


def test_stale_waiting_ticket_does_not_block_request_order(tmp_path: pathlib.Path) -> None:
    """更新の止まった待機記録は、生存中のプロセスのものでも依頼順の先頭を塞がない。"""
    tickets = admission.admission_directory(tmp_path)
    tickets.mkdir(parents=True)
    stale = tickets / "00000000000000000001-lost.json"
    stale.write_text(json.dumps({"pid": os.getpid(), "requested_at": time.time() - 3600}), encoding="utf-8")
    ticket = asyncio.run(admission.acquire(None, limit=admission.Limit(1, "test"), state_root=tmp_path))
    ticket.release()
    assert not stale.exists()
