"""マシン全体で作業中の委譲先の数を数え、上限に達した起動を空きが出るまで待たせる。

同時数を役割や担当ごとに分けて定めると合計を止める主体がなく、CPUが飽和して全操作の応答が遅れた
（2026年11月15日の巡回で作業中のCodexセッションが39まで増え、hookの所要が6倍を超えた）。
このため起動経路そのものでエンジン・役割・起動ディレクトリを問わず合算する。

作業中の数は既存の状態ファイル（30秒周期のheartbeat付き）から求め、新しい常駐の監視は作らない。
子の終了を待つだけの親が枠を持ち続けると、その子を起動できずに全体が止まるため、
稼働中の子を持つsessionと、子の起動を待っているsessionは数えない。
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
# 許可した起動が状態ファイルへ`running`として現れるまでの猶予。この間は許可を枠として数える。
ADMITTED_GRACE_SECONDS = 60.0
# 待機中の記録は毎回の照会で更新する。長く更新の無い記録は、共有サービスのように生存し続けるプロセス内で
# 待機が失われたものとして削除し、依頼順の先頭を塞がないようにする。
WAITING_STALE_SECONDS = 30.0
_WORKING_STATUSES = frozenset({"starting", "running"})


@dataclasses.dataclass(frozen=True)
class Limit:
    """設定から求めた上限。`value`がNoneなら制限しない。"""

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
    """`atk config`の設定（環境変数を含む）から上限を求める。"""
    from agent_toolkit._atk import config  # pylint: disable=import-outside-toplevel

    return resolve_limit(config.resolve_mutable_setting(CONFIG_KEY))


def admission_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """起動待ちと許可直後の起動を記録するディレクトリを返す。"""
    root = _state_paths.state_dir() if state_root is None else state_root
    return root / "agents-server" / "admission"


@dataclasses.dataclass(frozen=True)
class Observation:
    """作業中の数とその内訳。点検や起動結果へそのまま渡す。"""

    working: int
    admitted: int
    waiting: int
    failures: tuple[str, ...] = ()


def _live(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except psutil.Error, OSError:
        return True


def _read_tickets(directory: pathlib.Path, now: float) -> list[tuple[pathlib.Path, dict[str, object]]]:
    """生存中のプロセスが持つ記録だけを返し、異常終了したプロセスの記録は削除して枠を解放する。"""
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
    """全ルートの状態ファイルと起動記録から、作業中の数を求める。

    作業中は`starting`か`running`のsessionとする。ただし、稼働中の子を持つsession（状態ファイルの委譲元）と、
    子の起動を待っているsessionは子を待つだけの親として数えない。
    """
    now = time.time() if now is None else now
    tickets = _read_tickets(admission_directory(state_root), now)
    waiting_hosts = {
        str(payload["host_session_id"])
        for _path, payload in tickets
        if payload.get("admitted_at") is None and isinstance(payload.get("host_session_id"), str)
    }
    sessions: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    cutoff = status_reader.heartbeat_cutoff()
    for root_session_id in shared_layout.list_root_session_ids(state_root):
        snapshot = status_reader.load_root(root_session_id, state_root, cutoff=cutoff)
        sessions.update(snapshot.sessions)
        failures.extend(snapshot.failures)
    active = {key: value for key, value in sessions.items() if value.get("status") in _WORKING_STATUSES}
    parents = {value.get("host_session_id") for value in active.values()} | waiting_hosts
    working = sum(1 for key in active if key not in parents)
    # 起動処理中の許可は状態ファイルへ現れる前から数える。現れた後の短い重複は上限を下げる側へ倒れるだけである。
    admitted = sum(1 for _path, payload in tickets if payload.get("admitted_at") is not None)
    waiting = sum(1 for _path, payload in tickets if payload.get("admitted_at") is None)
    return Observation(working, admitted, waiting, tuple(failures))


@dataclasses.dataclass
class Admission:
    """許可済みの起動。起動処理を終えたら`release`で記録を消す。"""

    path: pathlib.Path | None
    waited_seconds: float
    limit: Limit

    def release(self) -> None:
        if self.path is not None:
            with contextlib.suppress(OSError):
                self.path.unlink()

    def response_fields(self) -> dict[str, object]:
        """起動を依頼した側が既存の起動結果から待ちを知るための項目。待ちが無ければ空。"""
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
    """上限に空きが出るまで待ち、依頼順に許可する。

    待っている間は依頼元（`host_session_id`）を子を待つ親として数えないため、親の枠が子の起動を塞がない。
    """
    limit = configured_limit() if limit is None else limit
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
                observation = observe(state_root, now=now)
                waiting = sorted(path for path, payload in _read_tickets(directory, now) if payload.get("admitted_at") is None)
                first = bool(waiting) and waiting[0] == ticket
                if first and observation.working + observation.admitted < limit.value:
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
