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

from agent_toolkit._common import state_paths
from agent_toolkit._common.atomic_file import atomic_write

_DIRECTORY = "recorded-process-reclaim"


@dataclasses.dataclass(frozen=True)
class ReclaimRecord:
    """起点の停止で回収できなかった所有対象と、生成停止を確認できなかった理由。"""

    targets: tuple[tuple[int, float], ...]
    unresolved: tuple[str, ...]

    def live_targets(self) -> list[psutil.Process]:
        """記録した起動時刻と一致し、資源を保持するプロセスを返す。PIDの再利用先は含めない。"""
        processes: list[psutil.Process] = []
        for pid, created in self.targets:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.ZombieProcess):
                process = psutil.Process(pid)
                if process.create_time() == created and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    processes.append(process)
        return processes


def path(pid: int, create_time: float) -> pathlib.Path:
    """起点の識別に対応する記録の保存先を返す。"""
    return state_paths.state_dir() / _DIRECTORY / f"{pid}-{create_time!r}.json"


def load(pid: int, create_time: float) -> ReclaimRecord | None:
    """記録を読む。記録が無ければNoneを返し、読めない記録は`ValueError`で伝える。"""
    target = path(pid, create_time)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return ReclaimRecord(
            tuple((int(item["pid"]), float(item["create_time"])) for item in raw["targets"]),
            tuple(str(reason) for reason in raw["unresolved"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"回収待ちの記録を読めない: {target}: {error}") from error


def save(pid: int, create_time: float, processes: list[psutil.Process], unresolved: list[str]) -> None:
    """所有対象の識別と未確認の理由を保存する。起動時刻を取得できない対象は既に終了しているため除く。"""
    targets = []
    for process in processes:
        with contextlib.suppress(psutil.Error):
            targets.append({"pid": process.pid, "create_time": process.create_time()})
    target = path(pid, create_time)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, json.dumps({"targets": targets, "unresolved": unresolved}, ensure_ascii=False), fsync=True)


def delete(pid: int, create_time: float) -> None:
    """回収を確認した起点の記録を消す。"""
    path(pid, create_time).unlink(missing_ok=True)


def pending(pid: int, create_time: float) -> bool:
    """回収待ちの対象が残るかを返す。読めない記録は回収を確認できないため残るとみなす。"""
    try:
        record = load(pid, create_time)
    except ValueError:
        return True
    return record is not None and bool(record.live_targets())
