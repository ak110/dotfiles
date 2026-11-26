import asyncio
import base64
import contextlib
import json
import logging
import pathlib
import socket
import time
import typing

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import session_costs as _session_costs
from agent_toolkit._atk.serve import session_parents, session_watch
from agent_toolkit._atk.serve.remote import read_failure_status
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
    record_matches,
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
    return await run_sync_operation(create_context, **kwargs)


def _retrieve_exception(task: asyncio.Future[typing.Any]) -> None:
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

    limited = session_parents.limit_with_ancestors(
        entries,
        MAX_LIST_ENTRIES,
        path_of=lambda entry: json.dumps([entry.host, entry.path]),
        parent_of=lambda entry: json.dumps([entry.host, entry.parent_path]) if entry.parent_path else None,
    )
    return limited, warnings


def _search_local(context: SessionsContext, entries: list[SessionSummary], query: str) -> tuple[set[str], bool, list[str]]:
    found: set[str] = set()
    unavailable = False
    retry: list[str] = []
    deadline = time.monotonic() + OPERATION_TIMEOUT_SEC["search"]
    for entry in entries:
        if not is_local_record_path(context, entry.path):
            unavailable = True
            continue
        try:
            if record_matches(pathlib.Path(entry.path), entry.engine, query, deadline=deadline):
                found.add(entry.path)
        except (OSError, ValueError) as error:
            status = read_failure_status(error)
            if status == 503:
                retry.append(entry.path)
            elif status == 403:
                unavailable = True
    return found, unavailable, retry


type SearchTargets = dict[str, list[str] | None]


def _search_path_batches(paths: list[str]) -> typing.Iterator[list[str]]:
    batch: list[str] = []
    for path in paths:
        if batch and len(json.dumps([*batch, path], ensure_ascii=False).encode()) > 1800:
            yield batch
            batch = []
        batch.append(path)
    if batch:
        yield batch


async def search_sessions(
    context: SessionsContext, query: str, targets: SearchTargets | None = None
) -> tuple[list[SessionSummary], list[str], SearchTargets]:
    entries, warnings = await list_sessions(context)
    by_host: dict[str, list[SessionSummary]] = {}
    for entry in entries:
        if targets is not None:
            if entry.host not in targets:
                continue
            requested = targets[entry.host]
            if requested is not None and entry.path not in requested:
                continue
        by_host.setdefault(entry.host, []).append(entry)
    encoded_query = base64.b64encode(query.encode("utf-8")).decode("ascii")
    denied: set[str] = set()
    retry: SearchTargets = {}
    for warning in warnings:
        host = warning["host"]
        if targets is not None and host not in targets:
            continue
        if warning.get("retryable") == "true":
            retry[host] = targets.get(host) if targets is not None else None
        else:
            denied.add(host)

    async def search_host(host: str, candidates: list[SessionSummary]) -> list[SessionSummary]:
        if host == context.hostname:
            paths, unavailable, retry_paths = await asyncio.to_thread(_search_local, context, candidates, query)
            if unavailable:
                denied.add(host)
            if retry_paths:
                retry[host] = retry_paths
        else:
            paths = set()
            requested = None if targets is None else targets[host]
            batches = [None] if requested is None else list(_search_path_batches(requested))
            retry_paths = []
            for batch in batches:
                args = {"query": encoded_query}
                if batch is not None:
                    args["paths"] = base64.b64encode(json.dumps(batch, ensure_ascii=False).encode()).decode("ascii")
                try:
                    response = await _remote_call(context, host, "search", args)
                except Exception as error:  # noqa: BLE001
                    if read_failure_status(error) == 503:
                        if batch is None:
                            retry[host] = None
                        else:
                            retry_paths.extend(batch)
                    else:
                        denied.add(host)
                    continue
                supplied = response.get("paths")
                if not isinstance(supplied, list) or not all(isinstance(path, str) for path in supplied):
                    denied.add(host)
                    continue
                paths.update(supplied)
                failed = response.get("retry_paths")
                if isinstance(failed, list):
                    candidates_by_path = {entry.path for entry in candidates}
                    retry_paths.extend(path for path in failed if isinstance(path, str) and path in candidates_by_path)
                if response.get("unavailable") or (response.get("incomplete") and failed is None):
                    denied.add(host)
            if retry_paths:
                retry[host] = list(dict.fromkeys(retry_paths))
        return [entry for entry in candidates if entry.path in paths]

    results = await asyncio.gather(*(search_host(host, candidates) for host, candidates in by_host.items()))
    return [entry for matches in results for entry in matches], sorted(denied), retry


async def session_detail(context: SessionsContext, engine: str, host: str, path: str) -> dict[str, typing.Any]:
    if engine not in {"claude", "codex"}:
        raise SessionNotFoundError(f"未知の実行系です: {engine}")
    if host == context.hostname:
        return await asyncio.to_thread(read_local_detail, context, engine, path)
    if host not in context.remote_hosts:
        raise SessionNotFoundError(f"未知のホストです: {host}")
    if not is_safe_remote_record_path(path):
        raise SessionNotFoundError(path)
    path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
    payload = await _remote_call(context, host, "read", {"path": path_b64})
    text = base64.b64decode(str(payload["data"])).decode("utf-8", errors="replace")
    records, broken = session_record_format.parse_records(text, include_positions=True)
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

    detail["cost"] = payload.get("cost")
    return detail


async def session_cost(context: SessionsContext, engine: str, host: str, path: str) -> dict[str, typing.Any]:
    if host != context.hostname:
        raise SessionNotFoundError(
            f"推定費用を別に取得できるのはローカルの記録だけです: {host}。"
            "リモートの記録の費用は/api/sessions/detailの応答のcostから取得してください"
        )
    return await asyncio.to_thread(read_local_cost, context, engine, path)


async def host_status(context: SessionsContext) -> dict[str, str]:
    async with context.state.lock:
        return dict(context.state.host_status)


def start_remote_clients(context: SessionsContext) -> None:
    for host in context.remote_hosts:
        client = RemoteSessionClient(host, context.state)
        context.state.clients[host] = client
        context.state.tasks.append(asyncio.create_task(client.run()))


async def stop_remote_clients(context: SessionsContext) -> None:
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
