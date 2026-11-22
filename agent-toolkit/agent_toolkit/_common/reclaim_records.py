"""停止を確認できなかった記録済み実行の所有対象を、起点の消滅後も再試行できるよう保存する。

記録済み実行の起点（委譲先CLIや`atk run-skill`の子）が終了しても、子孫が残るか生成停止を確認できない場合がある。
起点が消えると子孫は親を失い、起点のPIDから辿れなくなる。
停止で一度所有した対象をPIDと起動時刻の組で残し、同じ起点の次の停止でその対象を回収し直す。
記録は`recorded_process`が書き、登録簿（`session_registry`）が回収待ちの委譲先CLIを実行中として扱う判定に読む。
両者の循環参照を避けるため、記録の読み書きだけを本モジュールへ分ける。
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import pathlib

import psutil

from agent_toolkit._common import process_identity, state_paths
from agent_toolkit._common.atomic_file import atomic_write

_DIRECTORY = "recorded-process-reclaim"


@dataclasses.dataclass(frozen=True)
class ReclaimRecord:
    """起点の停止で回収できなかった所有対象と、生成停止を確認できなかった理由。

    `unreaped`は回収（親による待機）を確認できなかった直接の子である。スレッドグループのリーダーだけが
    終了した多スレッドの子はzombieと表示されたまま他のスレッドが動くため、`targets`と同じzombie除外で
    終了済みと扱わず、同じPID世代が残る間は回収待ちとする。
    """

    targets: tuple[process_identity.ProcessIdentity, ...]
    unresolved: tuple[str, ...]
    unreaped: tuple[process_identity.ProcessIdentity, ...]

    def live_targets(self, *, conservative: bool = False) -> list[psutil.Process]:
        processes: list[psutil.Process] = []
        for identity in self.targets:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.ZombieProcess):
                process = psutil.Process(identity.pid)
                if identity.matches(process, conservative=conservative) and process.status() not in (
                    psutil.STATUS_ZOMBIE,
                    psutil.STATUS_DEAD,
                ):
                    processes.append(process)
        return processes

    def held_unreaped(self, *, conservative: bool = False) -> list[psutil.Process]:
        processes: list[psutil.Process] = []
        for identity in self.unreaped:
            with contextlib.suppress(psutil.NoSuchProcess):
                process = psutil.Process(identity.pid)
                if identity.matches(process, conservative=conservative):
                    processes.append(process)
        return processes


def path(pid: int, create_time: float) -> pathlib.Path:
    return state_paths.state_dir() / _DIRECTORY / f"{pid}-{create_time!r}.json"


def load(pid: int, create_time: float) -> ReclaimRecord | None:
    target = path(pid, create_time)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return ReclaimRecord(
            tuple(process_identity.ProcessIdentity.parse(item) for item in raw["targets"]),
            tuple(str(reason) for reason in raw["unresolved"]),
            tuple(process_identity.ProcessIdentity.parse(item) for item in raw["unreaped"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"回収待ちの記録を読めない: {target}: {error}") from error


def save(
    pid: int,
    create_time: float,
    processes: list[psutil.Process],
    unresolved: list[str],
    unreaped: list[psutil.Process],
) -> None:
    target = path(pid, create_time)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        target,
        json.dumps(
            {"targets": _identities(processes), "unresolved": unresolved, "unreaped": _identities(unreaped)}, ensure_ascii=False
        ),
        fsync=True,
    )


def _identities(processes: list[psutil.Process]) -> list[dict[str, object]]:
    identities = []
    for process in processes:
        with contextlib.suppress(psutil.Error):
            identities.append(process_identity.ProcessIdentity.capture(process).payload())
    return identities


def delete(pid: int, create_time: float) -> None:
    path(pid, create_time).unlink(missing_ok=True)


def pending(pid: int, create_time: float) -> bool:
    try:
        record = load(pid, create_time)
    except ValueError:
        return True
    return record is not None and (
        bool(record.unresolved) or bool(record.live_targets(conservative=True)) or bool(record.held_unreaped(conservative=True))
    )
