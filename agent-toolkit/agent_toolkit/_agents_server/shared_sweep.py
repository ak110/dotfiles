"""共有状態ディレクトリから、保持期限を過ぎたルート、session登録簿、コンパクションの計測記録と起動単位の終了診断を除く掃引。"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import shutil
from collections.abc import Callable

from agent_toolkit._agents_server import compaction_metrics, logging_config
from agent_toolkit._agents_server.shared_layout import RESERVED_DIRECTORY_NAMES, valid_session_id
from agent_toolkit._common import session_registry
from agent_toolkit._common import state_paths as _state_paths

STALE_SHARED_STATE_SECONDS = 7 * 24 * 60 * 60


def sweep_stale_shared_state(
    *,
    keep_root_session_id: str | None,
    state_root: pathlib.Path | None = None,
    now: float | None = None,
    instance_max_bytes: int = logging_config.INSTANCE_DIRECTORY_MAX_BYTES,
) -> None:
    """7日を超えて更新されていない共有状態を個別失敗で停止せず回収する。

    起動単位の終了診断は、登録簿と終了時の結果の掃引の後に残る参照元が指すものを保持する。
    """
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
            _LOG.warning(
                "stale root状態を回収できませんでした: path=%s error=%s。"
                "次の操作: root状態の使用状況と書込権限を確認し、使用中なら保持する。"
                "解消できない場合はこのログを添えて管理者へ報告する。",
                directory,
                exc,
            )
    for path in session_registry.registry_directory(root).glob("*.json"):
        if not valid_session_id(path.stem):
            continue
        try:
            with session_registry.registry_lock(path.stem, state_root=root):
                if path.is_file() and path.stat().st_mtime < cutoff and not _registry_record_is_running(path, root):
                    path.unlink()
        except OSError as exc:
            _LOG.warning(
                "期限切れ登録簿を回収できませんでした: path=%s error=%s。"
                "次の操作: 対象の使用状況と書込権限を確認し、使用中なら保持する。"
                "解消できない場合はこのログを添えて管理者へ報告する。",
                path,
                exc,
            )
    _sweep_stale_files(
        session_registry.registry_directory(root),
        cutoff,
        keep=lambda path: path.suffix == ".json" and valid_session_id(path.stem),
    )
    _sweep_stale_compaction(compaction_metrics.record_directory(root), cutoff)
    referenced = session_registry.recorded_server_instances(root) | _result_server_instances(base)
    logging_config.sweep_instance_diagnostics(
        state_root=root, cutoff=cutoff, referenced=referenced, max_total_bytes=instance_max_bytes
    )


def _result_server_instances(base: pathlib.Path) -> set[str]:
    """各rootの終了時の結果ファイルが中断の診断参照で指す起動単位を返す。読めないファイルは除く。"""
    instances: set[str] = set()
    try:
        paths = tuple(base.glob("*/results/*.json"))
    except OSError:
        return instances
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError, UnicodeError, json.JSONDecodeError:
            continue
        error = payload.get("error") if isinstance(payload, dict) else None
        diagnostics = error.get("diagnostics") if isinstance(error, dict) else None
        instance = diagnostics.get("server_instance") if isinstance(diagnostics, dict) else None
        if isinstance(instance, str) and instance:
            instances.add(instance)
    return instances


def _registry_record_is_running(path: pathlib.Path, state_root: pathlib.Path) -> bool:
    """登録簿の記録が終了しておらず、記録プロセスのいずれかが実在するかを返す。

    更新時刻は所有側の公開の契機でしか進まないため、7日を超えて1つのturnを続ける委譲先の記録も期限切れと判定される。
    削除すると以後の照会は不在と解決するため、記録プロセスが実在する間は保持する。
    """
    if path.suffix != ".json" or not valid_session_id(path.stem):
        return False
    resolution = session_registry.resolve(path.stem, state_root=state_root)
    return resolution.state is session_registry.Resolution.RUNNING and (
        resolution.owner_process is not None or bool(resolution.executor_processes)
    )


def _sweep_stale_files(directory: pathlib.Path, cutoff: float, *, keep: Callable[[pathlib.Path], bool] | None = None) -> None:
    """ディレクトリ直下の期限切れファイルを個別失敗で停止せず削除する。`keep`が真を返すファイルは保持する。"""
    try:
        paths = tuple(directory.iterdir())
    except OSError:
        return
    for path in paths:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff and (keep is None or not keep(path)):
                path.unlink()
        except OSError as exc:
            _LOG.warning(
                "期限切れ共有状態を回収できませんでした: path=%s error=%s。"
                "次の操作: 共有状態ファイルの使用状況と書込権限を確認し、使用中なら保持する。"
                "解消できない場合はこのログを添えて管理者へ報告する。",
                path,
                exc,
            )


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
            _LOG.warning(
                "期限切れコンパクション記録を回収できませんでした: path=%s error=%s。"
                "次の操作: コンパクション記録と対応するlockの使用状況と書込権限を確認し、使用中なら保持する。"
                "解消できない場合はこのログを添えて管理者へ報告する。",
                record,
                exc,
            )


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")
