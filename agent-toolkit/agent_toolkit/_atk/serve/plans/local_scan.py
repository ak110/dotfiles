"""ローカルのrootにある計画ファイルの走査と全文検索、変更の監視。

対象判定、走査と検索の処理はSSH先のリモートヘルパーと共有する`_plan.viewer_files`が持ち、
本モジュールは一覧の項目への変換、停止要求の伝達とローカルの変更監視を担う。
"""

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


# --------------------------------------------------------------------------------------
# ローカル走査
# --------------------------------------------------------------------------------------


class PlansEventHandler(watchdog.events.FileSystemEventHandler):
    """watchdogのイベントを受信してSSE購読者へ通知するハンドラ。

    watchdogコールバックはwatchdog側のスレッドで実行されるため、
    asyncioループへ`run_coroutine_threadsafe`でブリッジする。
    """

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
        """ファイルシステムイベントをフィルタリングして購読者へ通知する。"""
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
            # 起動直後にループ参照が未設定のイベントは取りこぼしてよい（直後のイベントで再通知される）。
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
    """`root`を走査し、作成日時の降順の一覧とroot単位の警告を返す。

    走査と警告の扱いは`viewer_files.scan_root`が定める。
    `stop`が設定されると走査の途中で`ServeStopping`を送出し、途中までの観測でインデックスを更新しない。
    """
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
    """`root`から一覧対象の計画ファイルを再帰的に探し、作成日時の降順で返す。"""
    entries, _ = scan_files(root, host, source_id)
    return entries


def search_files(root: pathlib.Path, query: str, source_id: str = "", *, stop: threading.Event | None = None) -> set[str]:
    """本文へ検索語が部分一致する計画ファイルの相対パス集合を返す。

    `stop`が設定されるとファイル1件ごとの確認で`ServeStopping`を送出して打ち切る。
    """
    return _viewer_files.search_root(root, query, source_id, check_stop=lambda: _atk_serve_remote.raise_if_stopping(stop))


def read_pygments_css() -> str:
    """Pygmentsのスタイルシートを返す。

    pygmentsの基本ルール（`.codehilite { background: ...; color: ... }`）は除外し、
    トークン別カラールール（`.codehilite .k`等）のみを返す。
    背景と、トークンごとに指定しない文字色はapp.css側の`pre code`ルールで定め、
    `<pre>`の背景上に異色矩形が出現する事象を防ぐ。
    """
    raw = _PYGMENTS_FORMATTER.get_style_defs(f".{_PYGMENTS_CSS_CLASS}")
    base_selector = f".{_PYGMENTS_CSS_CLASS}"
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{base_selector} {{") or stripped.startswith(f"{base_selector}{{"):
            continue
        kept.append(line)
    return "\n".join(kept)
