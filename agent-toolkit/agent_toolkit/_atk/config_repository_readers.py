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
    services = state_paths.state_dir() / "services"
    directories = [services / "agents-server"]
    if os.name == "nt":
        directories.extend((services / "agents-server-standard", services / "agents-server-elevated"))
    try:
        with contextlib.ExitStack() as stack:
            snapshots = [
                (directory, stack.enter_context(agents_generation_index.repository_reader_boundary(directory)))
                for directory in directories
                if _has_registry(directory)
            ]
            if not all(_can_cut_over(directory, data) for directory, data in snapshots):
                return False
            for _, data in snapshots:
                if not data["generations"]:
                    continue
                data["repository_reader_minimum"] = list(agents_generation_index.REPOSITORY_READER_MINIMUM)
                for entry in data["generations"].values():
                    if not agents_generation_index.reads_separated_repositories(entry["version"]):
                        # 旧版のreserve/restoreも旧世代を選ばない形にする。pyfltr: allow-comment
                        entry.update(epoch=0, ready=False, retired=True)
    except OSError, ValueError, KeyError, TypeError:
        return False
    return True


def _has_registry(directory: pathlib.Path) -> bool:
    try:
        (directory / "generations.json").stat()
    except FileNotFoundError:
        return False
    return True


def _can_cut_over(directory: pathlib.Path, data: dict[str, Any]) -> bool:
    entries = data["generations"]
    legacy = [entry for entry in entries.values() if not agents_generation_index.reads_separated_repositories(entry["version"])]
    if not legacy or data.get("repository_reader_minimum") == list(agents_generation_index.REPOSITORY_READER_MINIMUM):
        return True
    current = entries.get(data["current"])
    if (
        current is None
        or not agents_generation_index.reads_separated_repositories(current["version"])
        or not current.get("ready")
        or not agents_generation_index.generation_process_alive(current["process"])
    ):
        return False
    return not any(_legacy_in_use(directory, entry) for entry in legacy)


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
