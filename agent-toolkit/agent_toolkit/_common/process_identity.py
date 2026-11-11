"""時計補正とPID再利用を区別する、保存可能なプロセス識別。"""

import dataclasses
import pathlib
import sys
from typing import Any

import psutil

_PROC_ROOT = pathlib.Path("/proc")


def start_id(process: psutil.Process) -> str | None:
    """Linuxではboot IDと起動ticksを返す。他OSではpsutilの世代識別を使う。"""
    if sys.platform.startswith("linux"):
        if not process.is_running():
            raise psutil.NoSuchProcess(process.pid)
        boot = (_PROC_ROOT / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        try:
            stat = (_PROC_ROOT / str(process.pid) / "stat").read_bytes()
        except FileNotFoundError as error:
            raise psutil.NoSuchProcess(process.pid) from error
        # commには空白と括弧を含められるため、最後の閉じ括弧の後をfield 3として数える。
        ticks = int(stat.rsplit(b")", 1)[1].split()[19])
        if not process.is_running():
            raise psutil.NoSuchProcess(process.pid)
        return f"linux:{boot}:{ticks}"
    if sys.platform == "darwin":
        # psutil自身もmacOSのPID世代判定にこの単調時刻を用いる。非公開APIへの依存はここに閉じる。
        if not process.is_running():
            raise psutil.NoSuchProcess(process.pid)
        created = process._proc.create_time(monotonic=True)  # pylint: disable=protected-access
        if not process.is_running():
            raise psutil.NoSuchProcess(process.pid)
        return f"darwin:{created!r}"
    return None


def matches(process: psutil.Process, created: float, saved_start_id: str | None = None, *, conservative: bool = False) -> bool:
    """保存した世代と同じプロセスかを返す。旧記録の不確定な不一致は、保持判定に限り生存として扱う。

    Linux/macOSの旧形式には時計補正とPID再利用を区別する情報が無い。
    conservativeは資源の保持にだけ使い、シグナル送信の認可には使わない。
    """
    if saved_start_id is not None:
        if not isinstance(saved_start_id, str) or not saved_start_id:
            raise ValueError("プロセスのstart_idが不正")
        return start_id(process) == saved_start_id
    if process.create_time() == created:
        return True
    return conservative and sys.platform != "win32"


def key(process: psutil.Process) -> tuple[int, str | float]:
    """プロセス内の集合でも同じ不変識別を使う。"""
    return process.pid, start_id(process) or process.create_time()


def start_order(process: psutil.Process) -> float:
    """同じ起動中の親子の前後関係を、壁時計の補正によらず比較する。"""
    token = start_id(process)
    return float(token.rsplit(":", 1)[1]) if token is not None else process.create_time()


def parent(process: psutil.Process) -> psutil.Process | None:
    """PPIDと不変な起動順序で親を取得し、壁時計を使うpsutil.parentを避ける。"""
    try:
        observed = key(process)
        ppid = process.ppid()
        if ppid <= 0 or ppid == process.pid:
            return None
        candidate = psutil.Process(ppid)
        if start_order(candidate) > start_order(process):
            return None
        # 読取中のPID再利用と親の付替えは、その時点の祖先関係として採用しない。
        if key(psutil.Process(process.pid)) != observed or process.ppid() != ppid:
            return None
        return candidate
    except psutil.NoSuchProcess:
        return None


def parents(process: psutil.Process) -> list[psutil.Process]:
    """同じ起動中の祖先を、直近の親から順に返す。"""
    found: list[psutil.Process] = []
    seen = {process.pid}
    while (ancestor := parent(process)) is not None and ancestor.pid not in seen:
        found.append(ancestor)
        seen.add(ancestor.pid)
        process = ancestor
    return found


@dataclasses.dataclass(frozen=True, eq=False)
class ProcessIdentity:
    """互換用の壁時計時刻と、時計補正に依存しない世代識別を保持する。"""

    pid: int
    create_time: float
    start_id: str | None = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProcessIdentity):
            return NotImplemented
        if self.pid != other.pid:
            return False
        if self.start_id is not None or other.start_id is not None:
            return self.start_id == other.start_id
        return self.create_time == other.create_time

    def __hash__(self) -> int:
        return hash(self.pid)

    def compatible_with(self, other: ProcessIdentity) -> bool:
        """旧形式との受渡しでは、双方が持つ世代情報か、旧形式の正確な時刻一致を確認する。"""
        if self.start_id is not None and other.start_id is not None:
            return self == other
        return self.pid == other.pid and self.create_time == other.create_time

    @classmethod
    def capture(cls, process: psutil.Process) -> ProcessIdentity:
        """取得済みのプロセス観測から識別を保存する。"""
        token = start_id(process)
        return cls(process.pid, process.create_time(), token)

    @classmethod
    def from_payload(cls, value: dict[str, Any], *, stable: dict[str, Any] | None = None) -> ProcessIdentity:
        """旧版が更新できる記録に、対応する追加の世代情報だけを復元する。"""
        identity = cls(**value)
        if stable is not None and not isinstance(stable, dict):
            raise ValueError("追加のプロセス識別がobjectではない")
        if stable is not None and (stable.get("pid"), stable.get("create_time")) == (identity.pid, identity.create_time):
            return cls(**stable)
        return identity

    @classmethod
    def of(cls, pid: int | None) -> ProcessIdentity | None:
        """PIDが実在し識別を取得できる場合に返す。"""
        if pid is None:
            return None
        try:
            return cls.capture(psutil.Process(pid))
        except psutil.Error:
            return None

    def matches(self, process: psutil.Process, *, conservative: bool = False) -> bool:
        """現在の観測が保存した世代と一致するかを返す。"""
        return process.pid == self.pid and matches(process, self.create_time, self.start_id, conservative=conservative)

    def exists(self) -> bool:
        """生存を否定できない間は、使用中の資源を保持する。"""
        try:
            process = psutil.Process(self.pid)
            return self.matches(process, conservative=True) and process.status() not in (
                psutil.STATUS_ZOMBIE,
                psutil.STATUS_DEAD,
            )
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            return False
        except psutil.Error, OSError, ValueError:
            return True

    def payload(self, *, legacy: bool = False) -> dict[str, Any]:
        """旧版でも読める項目に世代識別を追加する。"""
        result: dict[str, Any] = {"pid": self.pid, "create_time": self.create_time}
        if not legacy and self.start_id is not None:
            result["start_id"] = self.start_id
        return result
