"""管理サービスの受付世代をOS排他下で切り替え、遅れた準備の逆行を防ぐ。"""

from __future__ import annotations

import contextlib
import json
import pathlib
from collections.abc import Generator
from typing import Any

from agent_toolkit._agents_server import service_transport
from agent_toolkit._common import file_lock, private_directory
from agent_toolkit._common.atomic_file import atomic_write


class GenerationRegistry:
    """実行ジョブを複製せず、世代・起動元・受付先だけを保存する。"""

    def __init__(self, directory: pathlib.Path) -> None:
        self.directory = directory

    @contextlib.contextmanager
    def _locked(self, *, write: bool = True) -> Generator[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=private_directory.MODE)
        with (self.directory / "generations.lock").open("a+", encoding="utf-8") as handle:
            file_lock.acquire_lock(handle)
            try:
                path = self.directory / "generations.json"
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("generations"), dict):
                        raise ValueError(f"管理サービスの世代索引が不正: {path}")
                else:
                    data = {"schema": 1, "next_epoch": 1, "current": None, "generations": {}}
                yield data
                if write:
                    atomic_write(path, json.dumps(data, ensure_ascii=False))
            finally:
                file_lock.release_lock(handle)

    def reserve(self, generation: str, root: pathlib.Path, version: str) -> dict[str, Any]:
        """同版の準備を合流し、既知の旧版接続で現行受付先を戻さない。"""
        with self._locked() as data:
            entries = data["generations"]
            current = entries.get(data["current"])
            if generation not in entries:
                requested = tuple(int(part) for part in version.split("."))
                accepted = tuple(int(part) for part in current["version"].split(".")) if current else ()
                if accepted > requested:
                    return dict(current)
                entries[generation] = {
                    "generation": generation,
                    "epoch": data["next_epoch"],
                    "root": str(root),
                    "version": version,
                    "ready": False,
                }
                data["next_epoch"] += 1
            entry = entries[generation]
            if current is not None and current["epoch"] > entry["epoch"]:
                return dict(current)
            return dict(entry)

    def publish(
        self,
        generation: str,
        *,
        endpoint: str,
        process: str,
        server_instance: str | None = None,
        root: pathlib.Path | None = None,
    ) -> dict[str, Any]:
        """readyになった世代だけを受付候補にし、先に確定した新世代を上書きしない。"""
        with self._locked() as data:
            entries = data["generations"]
            entry = entries[generation]
            if entry.get("process") != process:
                entry.pop("handover_source", None)
                entry.pop("handover_confirmed", None)
            entry.update(ready=True, endpoint=endpoint, process=process)
            if server_instance is not None:
                entry["server_instance"] = server_instance
            if root is not None:
                entry["root"] = str(root)
            current = entries.get(data["current"])
            if current is None or entry["epoch"] >= current["epoch"]:
                if current is not None and current is not entry:
                    entry["handover_source"] = [current["generation"], current["process"]]
                    entry.pop("handover_confirmed", None)
                data["current"] = generation
                current = entry
            return dict(current)

    def confirm_handover(self, generation: str, process: str) -> None:
        """旧世代が会話と資源を持たないと確認した結果を、今回の受付先に残す。"""
        with self._locked() as data:
            current = data["generations"].get(data["current"])
            if current is not None and current.get("handover_source") == [generation, process]:
                current["handover_confirmed"] = True

    def handover_confirmed(self, target: dict, previous: dict) -> bool:
        """今回の受付先と移転元の起動単位が両方一致する確定記録だけを使う。"""
        with self._locked(write=False) as data:
            entry = data["generations"].get(target["generation"], {})
            return (
                entry.get("process") == target["process"]
                and entry.get("handover_source") == [previous["generation"], previous["process"]]
                and entry.get("handover_confirmed") is True
            )

    def current(self) -> dict[str, Any] | None:
        """現行受付先を読み、接続先の生存・認証は通信境界で確かめる。"""
        with self._locked(write=False) as data:
            entry = data["generations"].get(data["current"])
            return dict(entry) if entry is not None else None

    def restore(self) -> dict[str, Any] | None:
        """受付先のプロセスが終了していれば、生存する最新の公開済み世代へ受付を戻し、戻した後の受付先を返す。

        各世代は受付先のepochが下がらない前提で遅れた通知を捨てる。戻す世代へ終了した受付先のepochを引き継がせ、
        どの世代からも新しい受付先として観測できるようにする。
        終了した世代は未公開へ戻して次のepochを与え、同じ配布物の次の起動を、戻した世代より新しい候補として公開できるようにする。
        それより後に準備された未公開の候補は順序を保って繰り下げ、終了した世代の再起動より新しい順序に保つ。
        生存する公開済み世代が無ければ変更しない。転送側が受付先のrootから同じ世代を起動し直す。

        2026-11-18、公開直後に終了した新世代を受付先が指し続け、生存していた旧世代の状態保存とユーザーへの送達が失敗した。
        """
        current = self.current()
        if current is None or service_transport.process_alive(current["process"]):
            return current
        with self._locked() as data:
            entries = data["generations"]
            exited = entries.get(data["current"])
            if exited is None or service_transport.process_alive(exited["process"]):
                return dict(exited) if exited is not None else None
            survivors = [
                entry
                for entry in entries.values()
                if entry is not exited
                and entry.get("ready")
                and not entry.get("retired")
                and service_transport.process_alive(entry["process"])
            ]
            if not survivors:
                return dict(exited)
            survivor = max(survivors, key=lambda entry: entry["epoch"])
            released = exited["epoch"]
            for entry in entries.values():
                if entry["epoch"] > released:
                    entry["epoch"] += 1
            survivor["epoch"], exited["epoch"] = released, released + 1
            data["next_epoch"] += 1
            exited["ready"] = False
            data["current"] = survivor["generation"]
            return dict(survivor)

    def generations(self) -> dict[str, dict[str, Any]]:
        """準備順と資源の所有元を、世代ごとの既存索引から読む。"""
        with self._locked(write=False) as data:
            return {name: dict(entry) for name, entry in data["generations"].items()}

    def retire(self, generation: str, process: str) -> None:
        """自分の旧世代だけを退出させ、消えた配布物と同一checkoutの古い索引を回収する。"""
        with self._locked() as data:
            entries = data["generations"]
            entry = entries.get(generation)
            if entry is None or entry.get("process") != process or data["current"] == generation:
                return
            entry.update(ready=False, retired=True)
            for name, value in tuple(entries.items()):
                if name == data["current"] or not value.get("retired"):
                    continue
                obsolete = not pathlib.Path(value["root"]).exists() or any(
                    other["root"] == value["root"] and other["epoch"] > value["epoch"] for other in entries.values()
                )
                if obsolete:
                    for suffix in (".json", ".stderr.log"):
                        try:
                            (self.directory / (name + suffix)).unlink(missing_ok=True)
                        except PermissionError:
                            # Windowsの終了診断writerが閉じるまでは、次の起動時の回収へ残す。
                            break
                    else:
                        entries.pop(name, None)

    def retired(self, generation: str, process: str) -> bool:
        """世代の起動単位が終了の前に退役を記録したかを返す。

        退役を記録するのは`retire`だけであり、索引から世代を除くのも退役を記録した世代の回収に限る。
        引き継ぐ会話・資源を持ったまま終了した起動単位と、強制終了された起動単位は、退役を記録しないまま索引に残る。
        """
        with self._locked(write=False) as data:
            entry = data["generations"].get(generation)
            return entry is None or (bool(entry.get("retired")) and entry.get("process") == process)
