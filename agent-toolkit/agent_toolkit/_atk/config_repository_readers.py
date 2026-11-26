from __future__ import annotations

import contextlib
import json
import os
import pathlib
from typing import Any

from agent_toolkit._common import agents_generation_index, plugin_root_leases, state_paths
from agent_toolkit._common.next_action import ActionableError


def require_compatible_readers() -> None:
    prepare_separated_format(required=True)


def prepare_separated_format(*, required: bool = False) -> bool:
    if _prepare():
        return True
    if required:
        raise ActionableError(
            "旧版の委譲先がリポジトリ登録を利用できるか確かめられないため、登録の変更を保留しました",
            next_action="管理サービスの新版を受付先にし、旧版の作業の終了または引継ぎ後に登録操作を再実行する",
        )
    return False


def _prepare() -> bool:
    try:
        with contextlib.ExitStack() as stack:
            snapshots = [
                (directory, stack.enter_context(agents_generation_index.repository_reader_boundary(directory)))
                for directory in _registry_directories()
            ]
            if all(_fenced(data) for _, data in snapshots):
                return True
            if any(_legacy_in_use(directory, entry) for directory, data in snapshots for entry in _legacy_entries(data)):
                return False
            anchor = next(
                (
                    current
                    for _, data in snapshots
                    if (current := _compatible_current(data)) is not None
                    and current.get("ready")
                    and agents_generation_index.generation_process_alive(current["process"])
                ),
                None,
            )
            if anchor is None:
                return False
            for directory, data in snapshots:
                if not _fenced(data):
                    _fence(data, anchor)
                    agents_generation_index.save_locked_index(directory, data)
    except OSError, ValueError, KeyError, TypeError:
        return False
    return True


def _registry_directories() -> list[pathlib.Path]:
    services = state_paths.state_dir() / "services"
    directories = [services / "agents-server"]
    if os.name == "nt":
        directories.extend((services / "agents-server-standard", services / "agents-server-elevated"))
    return directories


def _legacy_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in data["generations"].values()
        if not agents_generation_index.reads_separated_repositories(entry["version"])
    ]


def _compatible_current(data: dict[str, Any]) -> dict[str, Any] | None:
    current = data["generations"].get(data["current"])
    if current is None or not agents_generation_index.reads_separated_repositories(current["version"]):
        return None
    return current


def _fenced(data: dict[str, Any]) -> bool:
    return (
        data.get("repository_reader_minimum") == list(agents_generation_index.REPOSITORY_READER_MINIMUM)
        and _compatible_current(data) is not None
    )


def _fence(data: dict[str, Any], anchor: dict[str, Any]) -> None:
    legacy = _legacy_entries(data)
    current = _compatible_current(data)
    if current is None:
        # 旧版も参照する復旧候補だけを確保し、別の名前空間の生存・接続先は流用しない。pyfltr: allow-comment
        generation = anchor["generation"]
        current = {
            "generation": generation,
            "version": anchor["version"],
            "root": anchor["root"],
            "epoch": data["next_epoch"],
            "ready": False,
            "process": "",
        }
        data["generations"][generation] = current
        data["current"] = generation
        data["next_epoch"] += 1
    else:
        # 旧版が切替のロック前に取得した予約も、現行受付より前に置く。pyfltr: allow-comment
        old_epoch = current["epoch"]
        required_epoch = max((entry["epoch"] + 1 for entry in legacy), default=old_epoch)
        if (shift := required_epoch - old_epoch) > 0:
            for entry in data["generations"].values():
                if entry["epoch"] >= old_epoch and entry not in legacy:
                    entry["epoch"] += shift
            data["next_epoch"] += shift
    for entry in legacy:
        entry.update(epoch=0, ready=False, retired=True)
    data["repository_reader_minimum"] = list(agents_generation_index.REPOSITORY_READER_MINIMUM)


def _legacy_in_use(directory: pathlib.Path, entry: dict[str, Any]) -> bool:
    if agents_generation_index.generation_process_alive(entry.get("process")):
        return True
    try:
        discovery = json.loads((directory / f"{entry['generation']}.json").read_bytes())
    except FileNotFoundError:
        discovery = {}
    if not isinstance(discovery, dict) or agents_generation_index.generation_process_alive(discovery.get("process")):
        return True
    root = pathlib.Path(entry["root"])
    return plugin_root_leases.completed_plugin_copy(root.parent) == root and plugin_root_leases.plugin_root_in_use(root.parent)
