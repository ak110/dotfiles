"""共有状態ディレクトリから、保持期限を過ぎたルート、session登録簿とコンパクションの計測記録を除く掃引。"""

from __future__ import annotations

import datetime
import logging
import pathlib
import shutil

from agent_toolkit._agents_server import compaction_metrics, session_registry
from agent_toolkit._agents_server.shared_layout import RESERVED_DIRECTORY_NAMES, valid_session_id
from agent_toolkit._common import state_paths as _state_paths

STALE_SHARED_STATE_SECONDS = 7 * 24 * 60 * 60


def sweep_stale_shared_state(
    *,
    keep_root_session_id: str | None,
    state_root: pathlib.Path | None = None,
    now: float | None = None,
) -> None:
    """7日を超えて更新されていない共有状態を個別失敗で停止せず回収する。"""
    root = _state_paths.state_dir() if state_root is None else state_root
    cutoff = datetime.datetime.now(datetime.UTC).timestamp() if now is None else now
    cutoff -= STALE_SHARED_STATE_SECONDS
    base = root / "agents-server"
    try:
        entries = tuple(base.iterdir())
    except OSError:
        return
    for directory in entries:
        if (
            not directory.is_dir()
            or directory.name in RESERVED_DIRECTORY_NAMES
            or directory.name == keep_root_session_id
            or not valid_session_id(directory.name)
        ):
            continue
        try:
            files = tuple(path for path in directory.rglob("*") if path.is_file())
            latest = max((path.stat().st_mtime for path in files), default=directory.stat().st_mtime)
            if latest < cutoff:
                shutil.rmtree(directory)
        except OSError as exc:
            _LOG.warning("stale root状態を回収できませんでした: path=%s error=%s", directory, exc)
    _sweep_stale_files(session_registry.registry_directory(root), cutoff)
    _sweep_stale_compaction(compaction_metrics.record_directory(root), cutoff)


def _sweep_stale_files(directory: pathlib.Path, cutoff: float) -> None:
    """ディレクトリ直下の期限切れファイルを個別失敗で停止せず削除する。"""
    try:
        paths = tuple(directory.iterdir())
    except OSError:
        return
    for path in paths:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError as exc:
            _LOG.warning("期限切れ共有状態を回収できませんでした: path=%s error=%s", path, exc)


def _sweep_stale_compaction(directory: pathlib.Path, cutoff: float) -> None:
    """期限切れJSONLと対応するlockを対として削除する。"""
    try:
        paths = tuple(directory.iterdir())
    except OSError:
        return
    records = {
        path.with_name(path.name.removesuffix(".lock")) if path.name.endswith(".jsonl.lock") else path
        for path in paths
        if path.name.endswith((".jsonl", ".jsonl.lock"))
    }
    for record in records:
        lock = record.with_name(f"{record.name}.lock")
        try:
            members = [path for path in (record, lock) if path.exists()]
            if members and max(path.stat().st_mtime for path in members) < cutoff:
                for path in members:
                    path.unlink(missing_ok=True)
        except OSError as exc:
            _LOG.warning("期限切れコンパクション記録を回収できませんでした: path=%s error=%s", record, exc)


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")
