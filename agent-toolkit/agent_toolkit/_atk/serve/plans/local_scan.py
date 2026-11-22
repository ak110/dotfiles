from __future__ import annotations

import asyncio
import pathlib
import threading
import typing

import watchdog.events
import watchdog.observers
import watchdog.observers.api

from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve.plans.roots import (
    _PYGMENTS_CSS_CLASS,
    _PYGMENTS_FORMATTER,
    _WATCHED_EVENT_TYPES,
    BroadcastState,
    FileEntry,
    make_file_entry,
    schedule_broadcast,
)
from agent_toolkit._plan import viewer_files as _viewer_files

_STATIC_DIR = pathlib.Path(__file__).with_name("static")

# リモート側で実行する短いPython bootstrap。組み立ての制約は`_atk_serve_remote`が定める。
REMOTE_BOOTSTRAP = _atk_serve_remote.remote_bootstrap("atk_serve_plans_remote_helper.py")


class PlansEventHandler(watchdog.events.FileSystemEventHandler):
    def __init__(
        self,
        root: pathlib.Path,
        state: BroadcastState,
        source_id: str = "",
    ) -> None:
        super().__init__()
        self.root = root
        self.state = state
        self.source_id = source_id

    @typing.override
    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        if not isinstance(event, _WATCHED_EVENT_TYPES):
            return
        if event.is_directory:
            return
        # src_pathはwatchdog型定義上bytes|strだが実行時はstr。str変換でPath型エラーを回避する。
        src = pathlib.Path(str(event.src_path))
        # atomic-write保存では`FileMovedEvent(src_path="plan.md.tmp", dest_path="plan.md")`となるため、
        # src_pathとdest_pathの両方を確認する。
        if isinstance(event, watchdog.events.FileMovedEvent):
            dest = pathlib.Path(str(event.dest_path))
            if not (
                _viewer_files.is_target_path(src, self.root, self.source_id)
                or _viewer_files.is_target_path(dest, self.root, self.source_id)
            ):
                return
        elif not _viewer_files.is_target_path(src, self.root, self.source_id):
            return
        loop = self.state.loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(schedule_broadcast(self.state), loop)


def scan_files(
    root: pathlib.Path,
    host: str,
    source_id: str = "",
    *,
    migrate_legacy_ctime: bool | None = None,
    stop: threading.Event | None = None,
) -> tuple[list[FileEntry], str | None]:
    items, warning = _viewer_files.scan_root(
        root,
        host,
        source_id,
        migrate_legacy_ctime=migrate_legacy_ctime,
        check_stop=lambda: _atk_serve_remote.raise_if_stopping(stop),
    )
    collected = [make_file_entry(host, {**item, "source_id": source_id}) for item in items]
    collected.sort(key=lambda entry: (entry.ctime_epoch, entry.path), reverse=True)
    return collected, warning


def list_files(root: pathlib.Path, host: str, source_id: str = "") -> list[FileEntry]:
    entries, _ = scan_files(root, host, source_id)
    return entries


def search_files(root: pathlib.Path, query: str, source_id: str = "", *, stop: threading.Event | None = None) -> set[str]:
    return _viewer_files.search_root(root, query, source_id, check_stop=lambda: _atk_serve_remote.raise_if_stopping(stop))


def read_pygments_css() -> str:
    raw = _PYGMENTS_FORMATTER.get_style_defs(f".{_PYGMENTS_CSS_CLASS}")
    base_selector = f".{_PYGMENTS_CSS_CLASS}"
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{base_selector} {{") or stripped.startswith(f"{base_selector}{{"):
            continue
        kept.append(line)
    return "\n".join(kept)
