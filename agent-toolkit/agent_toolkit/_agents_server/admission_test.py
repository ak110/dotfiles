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


def _write_status(
    state_root: pathlib.Path,
    root: str,
    writer: str,
    host: str | None,
    sessions: dict[str, str],
    actions: dict[str, str] | None = None,
) -> pathlib.Path:
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
                    {
                        "session_id": session_id,
                        "status": status,
                        "updated_at": now,
                        "last_action": (actions or {}).get(session_id, ""),
                    }
                    for session_id, status in sessions.items()
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


def test_working_parents_are_counted_and_only_parents_waiting_for_admission_are_not(tmp_path: pathlib.Path) -> None:
    """子を持つ委譲元も作業中なら数え、子の起動の許可を待っている委譲元だけを除く。"""
    _write_status(tmp_path, "root", "w1", None, {"parent": "running", "leaf": "running", "done": "completed"})
    _write_status(tmp_path, "root", "w2", "parent", {"child": "running"})
    assert admission.observe(tmp_path).working == 3  # parent・leaf・child
    tickets = admission.admission_directory(tmp_path)
    tickets.mkdir(parents=True)
    (tickets / "0001.json").write_text(
        json.dumps({"pid": os.getpid(), "host_session_id": "leaf", "requested_at": time.time()}), encoding="utf-8"
    )
    observed = admission.observe(tmp_path)
    assert (observed.working, observed.waiting) == (2, 1)


def test_tickets_of_dead_processes_release_slots(tmp_path: pathlib.Path) -> None:
    """異常終了したプロセスの許可・待機は枠を持ち続けない。"""
    tickets = admission.admission_directory(tmp_path)
    tickets.mkdir(parents=True)
    dead = tickets / "0001.json"
    dead.write_text(json.dumps({"pid": 2**22 + 12345, "admitted_at": 1e12}), encoding="utf-8")
    assert admission.observe(tmp_path).admitted == 0
    assert not dead.exists()


async def _until(condition, *, timeout: float = 10.0) -> None:
    """状態の成立を条件で待ち、上限までに成立しなければ失敗させる。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("状態が上限時間内に成立しない")
        await asyncio.sleep(0.01)


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
        for index, name in enumerate(("a", "b", "c", "d")):
            tasks.append(asyncio.create_task(launch(name)))
            # 依頼順を固定するため、前の依頼が許可されるか待機として登録されるまで次を依頼しない。
            await _until(lambda index=index: len(order) + admission.observe(tmp_path).waiting > index)
        assert order == ["a", "b"]  # c・dは待っている
        assert admission.observe(tmp_path).waiting == 2
        running["a"] = "completed"
        _write_status(tmp_path, "root", "w", None, running)
        await _until(lambda: order == ["a", "b", "c"])
        assert admission.observe(tmp_path).waiting == 1
        running["b"] = "completed"
        _write_status(tmp_path, "root", "w", None, running)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)

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


def test_parent_waiting_on_children_is_not_counted_but_working_parent_is(tmp_path: pathlib.Path) -> None:
    """最後の操作が`atk agents wait`の委譲元は子を待つだけとして数えず、作業中の委譲元は数える。"""
    _write_status(
        tmp_path,
        "root",
        "w1",
        None,
        {"waiter": "running", "worker": "running"},
        {"waiter": "Bash: atk agents wait", "worker": "Read: plan.md"},
    )
    _write_status(tmp_path, "root", "w2", "waiter", {"child": "running"})
    assert admission.observe(tmp_path).working_sessions == ("child", "worker")


def test_grandchild_starts_while_unrelated_work_finishes(tmp_path: pathlib.Path) -> None:
    """上限2で祖父が子を待ち、別の担当が作業中でも、子は孫を起動できる。別担当の終了を待つ場合も止まらない。"""
    limit = admission.Limit(2, "test")
    _write_status(
        tmp_path, "root", "w0", None, {"grandparent": "running", "other": "running"}, {"grandparent": "Bash: atk agents wait"}
    )
    _write_status(tmp_path, "root", "w1", "grandparent", {"child": "running"})

    async def scenario() -> None:
        ticket = await asyncio.wait_for(
            admission.acquire("child", limit=limit, state_root=tmp_path, sleep=lambda _s: asyncio.sleep(0.01)), timeout=5
        )
        ticket.release()

    asyncio.run(scenario())
    # 祖父が待機を示さず作業中に見える場合も、別担当が終われば系列だけになり許可される。
    _write_status(tmp_path, "root", "w0", None, {"grandparent": "running", "other": "running"})

    async def blocked_then_released() -> None:
        task = asyncio.create_task(
            admission.acquire("child", limit=limit, state_root=tmp_path, sleep=lambda _s: asyncio.sleep(0.01))
        )
        # 待機として登録され、その後の判定を数回経ても許可されないことを条件で確かめる。
        await _until(lambda: admission.observe(tmp_path).waiting == 1)
        await asyncio.sleep(0.05)
        assert not task.done()
        _write_status(tmp_path, "root", "w0", None, {"grandparent": "running", "other": "completed"})
        (await asyncio.wait_for(task, timeout=5)).release()

    asyncio.run(blocked_then_released())


def test_limit_is_exceeded_only_when_every_working_session_is_the_requester_lineage(tmp_path: pathlib.Path) -> None:
    """上限1で作業中が依頼元の親だけなら行き止まりを避けて許可し、無関係な作業中があれば待たせる。"""
    limit = admission.Limit(1, "test")
    _write_status(tmp_path, "root", "w0", None, {"parent": "running"})
    ticket = asyncio.run(admission.acquire("parent", limit=limit, state_root=tmp_path))
    ticket.release()
    _write_status(tmp_path, "root", "w0", None, {"parent": "running", "unrelated": "running"})

    async def waits() -> bool:
        task = asyncio.create_task(
            admission.acquire("parent", limit=limit, state_root=tmp_path, sleep=lambda _s: asyncio.sleep(0.01))
        )
        await _until(lambda: admission.observe(tmp_path).waiting == 1)
        await asyncio.sleep(0.05)
        done = task.done()
        task.cancel()
        return done

    assert asyncio.run(waits()) is False
