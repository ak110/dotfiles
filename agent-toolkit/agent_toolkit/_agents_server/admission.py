"""マシン全体で作業中の委譲先の数を数え、上限に達した起動を空きが出るまで待たせる。

同時数を役割や担当ごとに分けて定めると合計を止める主体がなく、CPUが飽和して全操作の応答が遅れた
（2026年11月15日の巡回で作業中のCodexセッションが39まで増え、hookの所要が6倍を超えた）。
このため起動経路そのものでエンジン・役割・起動ディレクトリを問わず合算する。

作業中の数は既存の状態ファイル（30秒周期のheartbeat付き）から求め、新しい常駐の監視は作らない。
子を持つ委譲元も自身が作業を続けていれば作業中に数える。
`atk agents wait`で子を待っている最中の委譲元（待機runの記録が実行中）と、子の起動の許可を待っている委譲元は、
子を待つだけとして数えない。
待つだけの親が枠を持つと子や孫を起動できずに全体が止まるためである。
上限の判定は起動の許可時点で行う。許可を待つ間は依頼元を数えないため、許可後に依頼元が待機へ入らず作業を続けると、
依頼元1件につき最大1件だけ一時的に上限を超える（2026年11月17日の統括の技術判断。起動を待たせたまま即時に返す方式は
識別子をバックエンドが起動時に払い出すため採らない）。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import math
import os
import pathlib
import time
import uuid
from collections.abc import Callable, Generator

import psutil

from agent_toolkit._agents_server import shared_layout, status_reader
from agent_toolkit._atk import config_settings
from agent_toolkit._common import file_lock
from agent_toolkit._common import state_paths as _state_paths

CONFIG_KEY = "agents_concurrency"
DEFAULT_SETTING = "2.5x"
POLL_SECONDS = 1.0

ADMITTED_GRACE_SECONDS = 60.0
# 待機中の記録は毎回の照会で更新する。長く更新の無い記録は、共有サービスのように生存し続けるプロセス内で
# 待機が失われたものとして削除し、依頼順の先頭を塞がないようにする。
WAITING_STALE_SECONDS = 30.0
_WORKING_STATUSES = frozenset({"starting", "running"})


def _waiting_on_children(root_session_id: str, owner_status_file: str, state_root: pathlib.Path | None) -> bool:
    """書込主体の委譲元が`atk agents wait`で子を待っている最中かを、待機runの記録から返す。

    `atk agents wait`は待機中、ルートの`wait-results/<状態ファイル名>/current.json`が指すrunを`running`に保ち、
    `wait-locks/<状態ファイル名>.lock`の排他を保持する。runが実行中で排他が保持されている場合だけ待機中とする。
    コマンド文字列や表示用の要約ではなく実際の待機の記録を使い、検索語・引用・要約の省略による誤判定を避ける。
    """
    directory = shared_layout.status_directory(root_session_id, state_root) / "wait-results" / owner_status_file
    try:
        current = json.loads((directory / "current.json").read_text(encoding="utf-8"))
        run = json.loads((directory / f"{current['run_id']}.json").read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError, KeyError, TypeError:
        return False
    if not isinstance(run, dict) or run.get("status") != "running":
        return False
    # 実行中のrunは待機プロセスが中断した後も回復のために残るため、待機プロセスが保持する排他で生存を確かめる。
    # 取得できた場合は待機が終わっているので直ちに解放する。取得は非ブロッキングで行い、待機中の側を待たせない。
    lock_path = shared_layout.status_directory(root_session_id, state_root) / "wait-locks" / f"{owner_status_file}.lock"
    try:
        with lock_path.open("a+b") as handle:
            try:
                file_lock.acquire_lock(handle, blocking=False)
            except OSError:
                return True
            file_lock.release_lock(handle)
    except OSError:
        return False
    return False


@dataclasses.dataclass(frozen=True)
class Limit:
    value: int | None
    source: str


def resolve_limit(setting: str, cpu_count: int | None = None) -> Limit:
    """論理CPU数に比例する上限を求める。CPU数を取得できない環境は1CPUとして扱う。"""
    parsed = config_settings.parse_agents_concurrency(setting)
    if parsed is None:
        return Limit(None, f"{CONFIG_KEY}=off")
    kind, number = parsed
    if kind == "absolute":
        return Limit(int(number), f"{CONFIG_KEY}={setting}")
    cpus = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return Limit(max(1, math.floor(cpus * number)), f"{CONFIG_KEY}={setting}（論理CPU {cpus}）")


def configured_limit() -> Limit:
    """`atk config`の設定（環境変数を含む）から上限を求める。

    旧設定の移行を伴う`config.resolve_mutable_setting`は使わず、保存値を読むだけにする。
    起動のたびに設定ファイルの書換えを起こさず、イベントループ上で書込を行わないためである。
    """
    return resolve_limit(config_settings.raw_mutable_setting(CONFIG_KEY))


def admission_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    root = _state_paths.state_dir() if state_root is None else state_root
    return root / "agents-server" / "admission"


@dataclasses.dataclass(frozen=True)
class Observation:
    working: int
    admitted: int
    waiting: int
    failures: tuple[str, ...] = ()
    working_sessions: tuple[str, ...] = ()


def _live(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except psutil.Error, OSError:
        return True


def _read_tickets(directory: pathlib.Path, now: float) -> list[tuple[pathlib.Path, dict[str, object]]]:
    tickets: list[tuple[pathlib.Path, dict[str, object]]] = []
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return tickets
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except OSError, UnicodeError, ValueError:
            payload = None
        admitted_at = payload.get("admitted_at") if isinstance(payload, dict) else None
        refreshed_at = payload.get("requested_at") if isinstance(payload, dict) else None
        expired = (isinstance(admitted_at, (int, float)) and now - admitted_at > ADMITTED_GRACE_SECONDS) or (
            admitted_at is None and isinstance(refreshed_at, (int, float)) and now - refreshed_at > WAITING_STALE_SECONDS
        )
        if not isinstance(payload, dict) or not _live(payload.get("pid")) or expired:
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        tickets.append((path, payload))
    return tickets


def observe(state_root: pathlib.Path | None = None, *, now: float | None = None) -> Observation:
    now = time.time() if now is None else now
    tickets = _read_tickets(admission_directory(state_root), now)
    waiting_hosts = {
        str(payload["host_session_id"])
        for _path, payload in tickets
        if payload.get("admitted_at") is None and isinstance(payload.get("host_session_id"), str)
    }
    sessions: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    waiting_parents: set[str] = set()
    cutoff = status_reader.heartbeat_cutoff()
    for root_session_id in shared_layout.list_root_session_ids(state_root):
        snapshot = status_reader.load_root(root_session_id, state_root, cutoff=cutoff)
        sessions.update(snapshot.sessions)
        failures.extend(snapshot.failures)
        owners = {
            (str(value["owner_status_file"]), str(value["host_session_id"]))
            for value in snapshot.sessions.values()
            if isinstance(value.get("owner_status_file"), str) and isinstance(value.get("host_session_id"), str)
        }
        waiting_parents.update(host for owner, host in owners if _waiting_on_children(root_session_id, owner, state_root))
    active = {key: value for key, value in sessions.items() if value.get("status") in _WORKING_STATUSES}
    working_sessions = tuple(
        sorted(key for key, value in active.items() if key not in waiting_hosts and key not in waiting_parents)
    )

    admitted = sum(1 for _path, payload in tickets if payload.get("admitted_at") is not None)
    waiting = sum(1 for _path, payload in tickets if payload.get("admitted_at") is None)
    return Observation(len(working_sessions), admitted, waiting, tuple(failures), working_sessions)


@dataclasses.dataclass
class Admission:
    path: pathlib.Path | None
    waited_seconds: float
    limit: Limit

    def release(self) -> None:
        if self.path is not None:
            with contextlib.suppress(OSError):
                self.path.unlink()

    def response_fields(self) -> dict[str, object]:
        if self.waited_seconds < POLL_SECONDS:
            return {}
        return {"admission_wait_seconds": round(self.waited_seconds, 1), "admission_limit": self.limit.source}


def _atomic_write(path: pathlib.Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


@contextlib.contextmanager
def _locked(directory: pathlib.Path) -> Generator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as handle:
        file_lock.acquire_lock(handle)
        try:
            yield
        finally:
            file_lock.release_lock(handle)


async def acquire(
    host_session_id: str | None,
    *,
    limit: Limit | None = None,
    state_root: pathlib.Path | None = None,
    sleep: Callable[[float], object] | None = None,
) -> Admission:
    limit = await asyncio.to_thread(configured_limit) if limit is None else limit
    if limit.value is None:
        return Admission(None, 0.0, limit)
    directory = admission_directory(state_root)
    started = time.monotonic()
    ticket = directory / f"{time.time_ns():020d}-{os.getpid()}-{uuid.uuid4().hex}.json"
    with _locked(directory):
        _atomic_write(ticket, {"pid": os.getpid(), "host_session_id": host_session_id, "requested_at": time.time()})
    try:
        while True:
            with _locked(directory):
                now = time.time()
                waiting = sorted(path for path, payload in _read_tickets(directory, now) if payload.get("admitted_at") is None)
                # 許可されうるのは依頼順の先頭だけなので、全rootの状態ファイルは先頭の待機だけが読む。
                # 待機の全員が毎秒読むと、待つ起動の数だけ全rootの走査が重なる。
                if waiting and waiting[0] == ticket:
                    observation = observe(state_root, now=now)
                    if observation.working + observation.admitted < limit.value:
                        _atomic_write(
                            ticket,
                            {"pid": os.getpid(), "host_session_id": host_session_id, "admitted_at": now},
                        )
                        return Admission(ticket, time.monotonic() - started, limit)
                # 待機の継続を示す時刻を更新する。外から消された場合も依頼順を保てるよう、元の名前で書き直す。
                _atomic_write(ticket, {"pid": os.getpid(), "host_session_id": host_session_id, "requested_at": now})
            result = (sleep or asyncio.sleep)(POLL_SECONDS)
            if asyncio.iscoroutine(result):
                await result
    except BaseException:
        with contextlib.suppress(OSError):
            ticket.unlink()
        raise
