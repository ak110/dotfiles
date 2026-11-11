"""セッション画面の公開操作とホスト間の調整。"""

import asyncio
import base64
import contextlib
import json
import logging
import pathlib
import socket
import typing

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import session_costs as _session_costs
from agent_toolkit._atk.serve import session_parents, session_watch
from agent_toolkit._atk.serve.sessions_events import (
    deliver_record,
    deliver_refresh,
    start_local_watch,
    start_local_watch_async,
    stop_local_watch,
    stop_local_watch_async,
    subscribe,
    unsubscribe,
)
from agent_toolkit._atk.serve.sessions_local import (
    _load_local_list,
    estimate_local_family,
    is_local_record_path,
    list_local_sessions,
    read_local_cost,
    read_local_detail,
)
from agent_toolkit._atk.serve.sessions_models import (
    MAX_LIST_ENTRIES,
    OPERATION_TIMEOUT_SEC,
    REMOTE_BOOTSTRAP,
    STDERR_EXCERPT_MAX_CHARS,
    SessionNotFoundError,
    SessionsContext,
    SessionsState,
    SessionSummary,
    SshRunner,
    _isoformat,
    _started_at,
    build_detail,
)
from agent_toolkit._atk.serve.sessions_remote import (
    RemoteSessionClient,
    _remote_call,
    _remote_sessions,
    _remote_subagents,
    default_ssh_runner,
    is_safe_remote_record_path,
)
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import session_cost_records, session_cost_slim
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.async_operations import run_sync_operation

logger = logging.getLogger(__name__)


def create_context(
    *,
    hostname: str | None = None,
    claude_home: pathlib.Path | None = None,
    codex_home: pathlib.Path | None = None,
    remote_hosts: typing.Iterable[str] | None = None,
    ssh_runner: SshRunner | None = None,
    state_dir: pathlib.Path | None = None,
) -> SessionsContext:
    """セッション画面の依存と初期接続状態を生成する。"""
    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    hosts = tuple(remote_hosts or ())
    if resolved_hostname in hosts:
        raise ValueError("ローカルホスト名がリモートホストの指定と重複しています")
    resolved_state_dir = state_dir if state_dir is not None else _state_paths.state_dir()
    state = SessionsState(
        record_index=session_watch.RecordSummaryIndex(cache_path=resolved_state_dir / "serve" / "session-summaries.json"),
        family_costs=_session_costs.FamilyCostCache(resolved_state_dir / "serve" / "session-costs.json"),
        slim_records=session_cost_slim.SlimRecordStore(resolved_state_dir / "serve" / "cost-records"),
        local_list_path=resolved_state_dir / "serve" / "session-list.json",
    )
    state.saved_local_list = _load_local_list(state.local_list_path)
    state.slim_records.prune()
    state.host_status[resolved_hostname] = "connected"
    for host in hosts:
        state.host_status[host] = "connecting"
    return SessionsContext(
        hostname=resolved_hostname,
        claude_home=claude_home if claude_home is not None else _host_homes.claude_config_dir(),
        codex_home=codex_home if codex_home is not None else _host_homes.codex_home(),
        remote_hosts=hosts,
        runner=ssh_runner if ssh_runner is not None else default_ssh_runner,
        state=state,
        state_dir=resolved_state_dir,
    )


async def create_context_async(**kwargs: typing.Any) -> SessionsContext:
    """保存先の設定解決をworkerで完了して初期状態を返す。"""
    return await run_sync_operation(create_context, **kwargs)


def _retrieve_exception(task: asyncio.Future[typing.Any]) -> None:
    """待つ要求が残らずに終わった取得の例外を回収し、未回収の例外としてログへ出力しない。"""
    if not task.cancelled():
        task.exception()


async def list_sessions(context: SessionsContext) -> tuple[list[SessionSummary], list[dict[str, str]]]:
    """ローカルと設定済みリモートホストの一覧を、到達できないホストの警告とともに返す。

    進行中の取得があればその結果を共有し、無ければ新しく取得する。同時に届いた要求ごとに取得すると、
    ローカルの重い走査がスレッドで重なってGILを奪い合い、他の画面の要求も待たされる。
    リモートホストへの要求も常駐接続の上で順に処理されるため、後の要求ほど待たされる。
    共有する取得は要求のキャンセルで止めない（`asyncio.shield`）。1つの要求の打ち切りが、
    同じ結果を待つ他の要求を失敗させないためである。ローカルの走査はサーバーの停止要求
    （`stop_requested`）で打ち切る。
    """
    state = context.state
    task = state.list_task
    if task is None or task.done():
        task = asyncio.ensure_future(_collect_sessions(context))
        task.add_done_callback(_retrieve_exception)
        state.list_task = task
    entries, warnings = await asyncio.shield(task)
    return list(entries), list(warnings)


async def _collect_sessions(context: SessionsContext) -> tuple[list[SessionSummary], list[dict[str, str]]]:
    """ローカルと設定済みリモートホストの一覧を取得する。

    到達できないホストがある場合も、他のホストとローカルの一覧は返す。
    """
    local_entries, remote_results = await asyncio.gather(
        asyncio.to_thread(list_local_sessions, context),
        asyncio.gather(*(_remote_sessions(context, host) for host in context.remote_hosts)),
    )
    entries: list[SessionSummary] = list(local_entries)
    warnings: list[dict[str, str]] = []
    for remote_entries, warning in remote_results:
        entries.extend(remote_entries)
        if warning is not None:
            warnings.append(warning)
    entries.sort(key=lambda entry: entry.started_at or "", reverse=True)
    # 親の参照は同じホストの記録を指すため、ホストとパスの組で祖先を戻す。
    limited = session_parents.limit_with_ancestors(
        entries,
        MAX_LIST_ENTRIES,
        path_of=lambda entry: json.dumps([entry.host, entry.path]),
        parent_of=lambda entry: json.dumps([entry.host, entry.parent_path]) if entry.parent_path else None,
    )
    return limited, warnings


async def session_detail(context: SessionsContext, engine: str, host: str, path: str) -> dict[str, typing.Any]:
    """指定ホストの記録1件を共通の表示モデルへ正規化して返す。"""
    if engine not in {"claude", "codex"}:
        raise SessionNotFoundError(f"未知の実行系です: {engine}")
    if host == context.hostname:
        return await asyncio.to_thread(read_local_detail, context, engine, path)
    if host not in context.remote_hosts:
        raise SessionNotFoundError(f"未知のホストです: {host}")
    if not is_safe_remote_record_path(path):
        raise SessionNotFoundError(path)
    path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
    try:
        payload = await _remote_call(context, host, "read", {"path": path_b64})
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートのセッション記録を取得できません host=%s path=%s: %s", host, path, error)
        raise SessionNotFoundError(path) from error
    text = base64.b64decode(str(payload["data"])).decode("utf-8", errors="replace")
    records, broken = session_record_format.parse_records(text)
    subagents, subagents_unavailable = _remote_subagents(engine, payload)
    detail = build_detail(
        engine, records, broken_lines=broken, subagents=subagents, subagents_unavailable=subagents_unavailable
    )
    pure_path = pathlib.PureWindowsPath(path) if "\\" in path else pathlib.PurePosixPath(path)
    detail["session_id"] = pure_path.stem
    detail["host"] = host
    detail["path"] = path
    detail["started_at"] = _started_at(engine, records)
    # Codexのthread名はロールアウトの記録に無く、リモートホストの`session_index.jsonl`をヘルパーが読んで返す。
    # 欄を返さない旧版のヘルパーでは名前なしとして扱う。
    thread_name = payload.get("thread_name")
    detail["session_name"] = (
        session_record_format.claude_session_name(records)
        if engine == "claude"
        else (thread_name if isinstance(thread_name, str) and thread_name.strip() else None)
    )
    # 算出は記録があるホストで行う。子孫の本文を画面サーバーへ転送しない。
    detail["cost"] = payload.get("cost")
    return detail


async def session_cost(context: SessionsContext, engine: str, host: str, path: str) -> dict[str, typing.Any]:
    """ローカルの記録の推定費用を返す。リモートの記録の費用は詳細の応答に含まれるため対象外とする。"""
    if host != context.hostname:
        raise SessionNotFoundError(
            f"推定費用を別に取得できるのはローカルの記録だけです: {host}。"
            "リモートの記録の費用は/api/sessions/detailの応答のcostから取得してください"
        )
    return await asyncio.to_thread(read_local_cost, context, engine, path)


async def host_status(context: SessionsContext) -> dict[str, str]:
    """ホストごとの接続状態を返す。"""
    async with context.state.lock:
        return dict(context.state.host_status)


def start_remote_clients(context: SessionsContext) -> None:
    """設定済みリモートホストの常駐接続を開始する。"""
    for host in context.remote_hosts:
        client = RemoteSessionClient(host, context.state)
        context.state.clients[host] = client
        context.state.tasks.append(asyncio.create_task(client.run()))


async def stop_remote_clients(context: SessionsContext) -> None:
    """常駐接続をまとめて終了させる。"""
    for task in context.state.tasks:
        task.cancel()
    for task in context.state.tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    context.state.tasks.clear()


__all__ = [
    "MAX_LIST_ENTRIES",
    "OPERATION_TIMEOUT_SEC",
    "REMOTE_BOOTSTRAP",
    "RemoteSessionClient",
    "STDERR_EXCERPT_MAX_CHARS",
    "SessionNotFoundError",
    "SessionSummary",
    "SessionsContext",
    "SessionsState",
    "SshRunner",
    "_isoformat",
    "_remote_call",
    "deliver_record",
    "deliver_refresh",
    "estimate_local_family",
    "is_local_record_path",
    "is_safe_remote_record_path",
    "list_local_sessions",
    "read_local_cost",
    "read_local_detail",
    "session_cost_records",
    "session_parents",
    "session_record_format",
    "session_watch",
    "start_local_watch",
    "start_local_watch_async",
    "stop_local_watch",
    "stop_local_watch_async",
    "subscribe",
    "unsubscribe",
]
