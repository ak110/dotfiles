"""マシン全体の同時稼働数の上限で、起動が待たされ空きで進み、親子の組が止まらないことを確かめる。"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import subprocess
import sys
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
    assert admission.resolve_limit(setting, cpu_count=cpus).value == expected


@pytest.mark.parametrize("value", ["0", "-1x", "0x", "abc", "2.5", "nanx", ""])
def test_invalid_settings_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        config_settings.parse_agents_concurrency(value)


def test_default_setting_limits_by_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission.os, "cpu_count", lambda: 4)
    assert admission.configured_limit().value == 10


def test_working_parents_are_counted_and_only_parents_waiting_for_admission_are_not(tmp_path: pathlib.Path) -> None:
    _write_status(tmp_path, "root", "w1", None, {"parent": "running", "leaf": "running", "done": "completed"})
    _write_status(tmp_path, "root", "w2", "parent", {"child": "running"})
    assert admission.observe(tmp_path).working == 3
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


def test_reset_records_are_not_sessions_but_invalid_real_sessions_remain_diagnostic(tmp_path: pathlib.Path) -> None:
    _write_status(tmp_path, "real-root", "root", None, {"worker": "running"})
    reset = tmp_path / "agents-server" / "codex-auto-reset" / "account.json"
    reset.parent.mkdir(parents=True)
    reset.write_text('{"used": 1}', encoding="utf-8")
    observed = admission.observe(tmp_path)
    assert observed.working_sessions == ("worker",)
    assert not observed.failures
    broken = _write_status(tmp_path, "real-root", "broken", None, {})
    broken.write_text('{"sessions": "invalid"}', encoding="utf-8")
    observed = admission.observe(tmp_path)
    assert observed.working_sessions == ("worker",)
    assert len(observed.failures) == 1 and f"real-root/{broken.name}" in observed.failures[0]


async def _until(condition, *, timeout: float = 10.0) -> None:
    """状態の成立を条件で待ち、上限までに成立しなければ失敗させる。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("状態が上限時間内に成立しない")
        await asyncio.sleep(0.01)


def test_launches_beyond_limit_wait_and_proceed_in_request_order(tmp_path: pathlib.Path) -> None:
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
        assert order == ["a", "b"]
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
    limit = admission.Limit(2, "test")
    _write_status(tmp_path, "root", "w0", None, {"p1": "running", "p2": "running"})

    async def child_of(parent: str) -> str:
        ticket = await admission.acquire(parent, limit=limit, state_root=tmp_path, sleep=lambda _s: asyncio.sleep(0.01))
        _write_status(tmp_path, "root", f"w-{parent}", parent, {f"{parent}-child": "running"})
        ticket.release()

        _write_status(tmp_path, "root", f"w-{parent}", parent, {f"{parent}-child": "completed"})
        return f"{parent}-child"

    async def scenario() -> list[str]:
        return list(await asyncio.wait_for(asyncio.gather(child_of("p1"), child_of("p2")), timeout=5))

    assert asyncio.run(scenario()) == ["p1-child", "p2-child"]


@pytest.mark.parametrize("waiters", [2, 6])
def test_one_poll_round_reads_all_roots_once_regardless_of_waiters(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, waiters: int
) -> None:
    limit = admission.Limit(1, "test")
    _write_status(tmp_path, "root", "w", None, {"busy": "running"})
    scans = 0
    load_root = admission.status_reader.load_root

    def counting_load_root(*args, **kwargs):
        nonlocal scans
        scans += 1
        return load_root(*args, **kwargs)

    monkeypatch.setattr(admission.status_reader, "load_root", counting_load_root)

    async def scenario() -> None:
        polled = 0
        hold = asyncio.Event()

        async def poll_once(_seconds: float) -> None:
            nonlocal polled
            polled += 1
            await hold.wait()

        tasks = [
            asyncio.create_task(admission.acquire(None, limit=limit, state_root=tmp_path, sleep=poll_once))
            for _ in range(waiters)
        ]
        await _until(lambda: polled == waiters)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
    assert scans == 1
    assert not any(admission.admission_directory(tmp_path).glob("*.json"))


def test_disabled_limit_never_waits(tmp_path: pathlib.Path) -> None:
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


_HELD_LOCKS: list[subprocess.Popen[bytes]] = []


def _record_wait(state_root: pathlib.Path, root: str, writer: str, status: str = "running", *, alive: bool = True) -> None:
    """`atk agents wait`が書込主体の状態ファイルごとに保存する待機runと、待機中に保持する排他を再現する。"""
    directory = state_root / "agents-server" / root / "wait-results" / f"{writer}.json"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "current.json").write_text(json.dumps({"run_id": "run1"}), encoding="utf-8")
    (directory / "run1.json").write_text(json.dumps({"run_id": "run1", "status": status, "targets": []}), encoding="utf-8")
    if alive:
        # 待機プロセスが保持する排他を別プロセスで保持し、同じプロセス内のロックの再取得と区別する。
        locks = state_root / "agents-server" / root / "wait-locks"
        locks.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-c", _HOLD_LOCK, str(locks / f"{writer}.json.lock")], stdout=subprocess.PIPE
        )
        assert holder.stdout is not None and holder.stdout.readline().strip() == b"held"
        _HELD_LOCKS.append(holder)


_HOLD_LOCK = (
    "import sys\n"
    "from agent_toolkit._common import file_lock\n"
    "handle = open(sys.argv[1], 'a+b')\n"
    "file_lock.acquire_lock(handle)\n"
    "print('held', flush=True)\n"
    "import time\n"
    "time.sleep(60)\n"
)


@pytest.fixture(autouse=True)
def _release_held_locks():
    yield
    while _HELD_LOCKS:
        holder = _HELD_LOCKS.pop()
        holder.kill()
        holder.wait()
        if holder.stdout is not None:
            holder.stdout.close()


def test_abandoned_running_wait_run_is_not_waiting(tmp_path: pathlib.Path) -> None:
    """待機プロセスが中断して実行中のrunだけが残った委譲元は、待機ではなく作業中に数える。"""
    _write_status(tmp_path, "root", "w0", None, {"parent": "running"})
    _write_status(tmp_path, "root", "w1", "parent", {"child": "running"})
    _record_wait(tmp_path, "root", "w1", alive=False)
    assert admission.observe(tmp_path).working_sessions == ("child", "parent")


def test_parent_waiting_on_children_is_not_counted_but_working_parent_is(tmp_path: pathlib.Path) -> None:
    """待機runが実行中の委譲元は子を待つだけとして数えず、待機していない委譲元は数える。"""
    _write_status(tmp_path, "root", "w0", None, {"waiter": "running", "worker": "running"})
    _write_status(tmp_path, "root", "w1", "waiter", {"child": "running"})
    _write_status(tmp_path, "root", "w2", "worker", {"other-child": "running"})
    _record_wait(tmp_path, "root", "w1")
    assert admission.observe(tmp_path).working_sessions == ("child", "other-child", "worker")


@pytest.mark.parametrize(
    ("action", "run_status", "waiting"),
    [
        ('Bash: command=rg -n "agents wait" agent-toolkit', "running", True),
        ("Bash: command=cd /repo && /x/scripts/atk agents wait", None, False),
        ('Bash: command=echo "command=atk agents wait"', None, False),
        ("Bash: command=atk agents wait", "published", False),
    ],
)
def test_wait_detection_uses_wait_run_record_not_command_text(
    tmp_path: pathlib.Path, action: str, run_status: str | None, waiting: bool
) -> None:
    """待機の判定は最後の操作の文字列ではなく待機runの記録で行い、終わった待機は待機に数えない。"""
    _write_status(tmp_path, "root", "w0", None, {"parent": "running"}, {"parent": action})
    _write_status(tmp_path, "root", "w1", "parent", {"child": "completed"})
    if run_status is not None:
        _record_wait(tmp_path, "root", "w1", run_status)
    assert ("parent" not in admission.observe(tmp_path).working_sessions) is waiting


def test_grandchild_starts_while_grandparent_waits_and_unrelated_work_finishes(tmp_path: pathlib.Path) -> None:
    """上限2で祖父が子を待ち、別の担当が作業中でも、子は孫を起動できる。祖父が作業中なら待機へ入るまで待つ。"""
    limit = admission.Limit(2, "test")
    _write_status(tmp_path, "root", "w0", None, {"grandparent": "running", "other": "running"})
    _write_status(tmp_path, "root", "w1", "grandparent", {"child": "running"})

    async def blocked_then_released() -> None:
        task = asyncio.create_task(
            admission.acquire("child", limit=limit, state_root=tmp_path, sleep=lambda _s: asyncio.sleep(0.01))
        )
        await _until(lambda: admission.observe(tmp_path).waiting == 1)
        await asyncio.sleep(0.05)
        assert not task.done()
        # 祖父が子の待機へ入ると、別担当が作業中のままでも孫の起動が許可される。
        _record_wait(tmp_path, "root", "w1")
        (await asyncio.wait_for(task, timeout=5)).release()

    asyncio.run(blocked_then_released())


def test_requester_is_not_counted_while_waiting_and_counts_again_after_admission(tmp_path: pathlib.Path) -> None:
    limit = admission.Limit(1, "test")
    _write_status(tmp_path, "root", "w0", None, {"parent": "running"})
    ticket = asyncio.run(admission.acquire("parent", limit=limit, state_root=tmp_path))
    ticket.release()
    _write_status(tmp_path, "root", "w1", "parent", {"child": "running"})
    assert admission.observe(tmp_path).working == 2
    _record_wait(tmp_path, "root", "w1")
    assert admission.observe(tmp_path).working == 1  # 親が待機へ入ると上限内へ戻る
