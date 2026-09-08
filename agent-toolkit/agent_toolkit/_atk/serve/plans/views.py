# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
"""`atk serve`の計画ファイル画面の処理本体。

ローカルと設定済みリモートホストの計画ファイルを集約し、全文検索、Markdownと
レビュー指摘管理表のHTML変換、付属計画間の移動リンク生成、更新通知の配信を担う。
ルート登録は`_atk_serve_app.py`の`_register_plan_routes`が行い、本モジュールは処理の実装だけを持つ。

記録の保存先とrootの規約は`agent-toolkit/skills/plan-mode`が定める計画ファイルの配置に従う。
リモートホスト側で実行するヘルパーは`atk_serve_plans_remote_helper.py`とする。
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as _async_subprocess
import base64
import collections
import collections.abc
import contextlib
import dataclasses
import datetime
import hashlib
import html as html_lib
import importlib
import json
import logging
import os
import pathlib
import random
import re
import socket
import subprocess
import typing
from typing import TYPE_CHECKING

import markdown_it
import markdown_it.renderer
import markdown_it.token
import markdown_it.utils
import platformdirs
import pygments
import watchdog.events
import watchdog.observers
import watchdog.observers.api
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._common import file_lock as _file_lock

if TYPE_CHECKING:
    from agent_toolkit._atk.serve.plans.ctime_index import (
        _enter_index_lock,
        _entry_ctime,
        _exclusive_file_lock,
        _index_key,
        _index_lock_path,
        _load_index,
        _load_legacy_entries,
        _root_key,
        _write_index,
        cleanup_creation_time_temporaries,
        update_creation_time_index,
    )
    from agent_toolkit._atk.serve.plans.local_scan import (
        _STATIC_DIR,
        REMOTE_BOOTSTRAP,
        PlansEventHandler,
        _ctime_epoch,
        is_listed_path,
        is_target_path,
        list_files,
        local_host_info,
        read_mermaid_bundle,
        read_pygments_css,
        resolve_under_root,
        root_info,
        root_status,
        root_warning,
        scan_files,
        search_files,
    )
    from agent_toolkit._atk.serve.plans.remote import (
        RemoteHelperError,
        RemoteSearchCoordinator,
        RemoteSearchResult,
        RemoteSearchRunner,
        RemoteSearchSuperseded,
        RemoteWatcher,
        _build_remote_command_argv,
        _decode_read_payload,
        _decode_root_info,
        _decode_root_status,
        _drain_stderr,
        _is_listed_remote_path,
        _iter_stream_lines,
        _PendingSearch,
        _stderr_excerpt,
        _terminate_process,
        _wait_with_timeout,
        default_ssh_runner,
        fetch_remote_file,
        is_safe_remote_relpath,
        search_remote_files,
    )
    from agent_toolkit._atk.serve.plans.rendering import (
        MarkdownCache,
        MarkdownCacheKey,
        _highlight_code,
        _render_fence,
        make_md_renderer,
        markdown_to_html,
    )
    from agent_toolkit._atk.serve.plans.roots import (
        _BROADCAST_DEBOUNCE_SEC,
        _BUGS_SUFFIX,
        _CREATION_TIME_INDEX_PATH,
        _DETAIL_SUFFIX,
        _LEGACY_CACHE_NAME_RE,
        _LEGACY_TEMPORARY_NAME_RE,
        _LISTED_EXCLUDED_SUFFIXES,
        _PLAN_SUFFIX_LABELS,
        _PYGMENTS_CSS_CLASS,
        _PYGMENTS_FORMATTER,
        _REVIEW_TABLE_HEADERS,
        _SSE_REFRESH_PAYLOAD,
        _TARGET_TSV_SUFFIXES,
        _UNRESOLVED_PRIVATE_NOTES_ROOT,
        _WATCHED_EVENT_TYPES,
        DEFAULT_REMOTE_SEARCH_LIMIT,
        LEGACY_PORTABLE_ROOT,
        LEGACY_SOURCE_ID,
        MARKDOWN_CACHE_MAX_BYTES,
        MARKDOWN_CACHE_MAX_ENTRIES,
        NEW_PORTABLE_ROOT,
        NEW_SOURCE_ID,
        REMOTE_BACKOFF_INITIAL_SEC,
        REMOTE_BACKOFF_JITTER_RANGE,
        REMOTE_BACKOFF_MAX_SEC,
        REMOTE_STREAM_LIMIT_BYTES,
        RPC_REQUEST_TIMEOUT_SEC,
        SSH_BASE_OPTIONS,
        SSH_TIMEOUT_SEC,
        SSH_WATCH_OPTIONS,
        STDERR_EXCERPT_MAX_CHARS,
        TERMINATE_GRACE_TIMEOUT_SEC,
        BroadcastState,
        FileEntry,
        LineSource,
        RootSpec,
        SshRunner,
        _broadcast,
        _canonical,
        _debounced_deliver,
        _private_notes_result,
        default_root_specs,
        deliver_host_info,
        deliver_host_status,
        deliver_refresh,
        deliver_root_info,
        deliver_root_status,
        explicit_root_spec,
        logger,
        make_file_entry,
        normalize_root_specs,
        schedule_broadcast,
        subscribe,
        unsubscribe,
    )


# --------------------------------------------------------------------------------------
# 画面が消費する処理
# --------------------------------------------------------------------------------------


from agent_toolkit._atk.serve.plans.roots import DEFAULT_REMOTE_SEARCH_LIMIT


@dataclasses.dataclass(frozen=True)
class PlansContext:
    """計画ファイル画面のルートが共有するアプリ単位の依存。"""

    root: pathlib.Path
    roots: tuple[RootSpec, ...]
    hostname: str
    remote_hosts: list[str]
    allowed_remote_hosts: set[str]
    runner: SshRunner
    search_coordinator: RemoteSearchCoordinator
    renderer: markdown_it.MarkdownIt
    markdown_cache: MarkdownCache
    state: BroadcastState


def create_context(
    *,
    root: pathlib.Path | None = None,
    roots: typing.Iterable[RootSpec] | None = None,
    hostname: str | None = None,
    remote_hosts: typing.Iterable[str] | None = None,
    ssh_runner: SshRunner | None = None,
    remote_search_limit: int = DEFAULT_REMOTE_SEARCH_LIMIT,
) -> PlansContext:
    """計画ファイル画面の依存と初期接続状態を生成する。

    `root`を渡した場合は当該rootだけを対象とし、`roots`を渡した場合はそのroot群を使う。
    いずれも渡さない場合はprivate-notesと`~/.claude/plans`の2rootを解決する。
    `hostname`はローカル分のファイルエントリに付与する`host`ラベルとリモートホストとの一意性検査に使う。
    """
    # 前回の異常終了で残った作成日時インデックスの一時ファイルを起動時に除去する。
    cleanup_creation_time_temporaries()
    renderer = make_md_renderer()
    markdown_cache = MarkdownCache()
    state = BroadcastState()
    if roots is not None:
        root_specs = normalize_root_specs(roots)
    elif root is not None:
        root_specs = normalize_root_specs((explicit_root_spec(root),))
    else:
        root_specs = default_root_specs()
    if not root_specs:
        raise ValueError("計画ファイルのrootが1つも解決できません")
    resolved_root = root_specs[0].path
    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    remote_host_list = list(remote_hosts) if remote_hosts else []
    if resolved_hostname in remote_host_list:
        # `remote_files`のキーが衝突しローカル/リモートが上書きし合うため、起動時に拒絶する。
        raise ValueError("ローカルホスト名がリモートホストの指定と重複しています")
    runner: SshRunner = ssh_runner if ssh_runner is not None else default_ssh_runner

    # 初期接続状態を設定する。ローカルは常にconnected、リモートはconnecting開始。
    state.host_status[resolved_hostname] = "connected"
    for host in remote_host_list:
        state.host_status[host] = "connecting"
    # ローカルホスト分の`host_info`は起動時に即座にセットする。
    # リモート分は接続確立時（初回snapshot受信）に`RemoteWatcher`側で追加する。
    state.host_info[resolved_hostname] = local_host_info(resolved_root)
    state.root_info[resolved_hostname] = {spec.source_id: root_info(spec) for spec in root_specs}
    state.root_status[resolved_hostname] = {
        spec.source_id: root_status(spec.warning or root_warning(spec.path)) for spec in root_specs
    }

    return PlansContext(
        root=resolved_root,
        roots=root_specs,
        hostname=resolved_hostname,
        remote_hosts=remote_host_list,
        allowed_remote_hosts=set(remote_host_list),
        runner=runner,
        search_coordinator=RemoteSearchCoordinator(remote_search_limit),
        renderer=renderer,
        markdown_cache=markdown_cache,
        state=state,
    )


def _scan_local_root(spec: RootSpec, host: str) -> tuple[list[FileEntry], str | None]:
    """root解決時の警告を保ったまま、利用可能なローカルrootだけを走査する。"""
    if spec.warning is not None:
        return [], spec.warning
    return scan_files(spec.path, host, spec.source_id, migrate_legacy_ctime=spec.migrate_legacy_ctime)


def _oldest_host_per_file(entries: list[FileEntry]) -> list[FileEntry]:
    """ホスト間で同期されるrootのエントリを、同じ相対パスごとに作成日時が最も古いホストの1件へ絞る。

    集約の対象は`NEW_SOURCE_ID`のroot（private-notes配下のplans）に限る。
    同rootは全環境で同期される場合があり、同一のファイルが各ホストのエントリとして重複する。
    更新は最初に作成したホストで行われるため、作成日時が最も古いエントリだけを残す。
    `ctime_epoch`が同値の場合は`host`の昇順で先頭を選び、入力順に依存しない決定的な選択にする。

    旧root（`~/.claude/plans`）と設定で明示したrootはホスト間で同期されない。
    これらは`source_id`と相対パスが同じでもホストごとに別のファイルであるため、集約せず全件を保持する。
    """
    kept: list[FileEntry] = []
    oldest: dict[str, FileEntry] = {}
    for entry in entries:
        if entry.source_id != NEW_SOURCE_ID:
            kept.append(entry)
            continue
        current = oldest.get(entry.path)
        if current is None or (entry.ctime_epoch, entry.host) < (current.ctime_epoch, current.host):
            oldest[entry.path] = entry
    return kept + list(oldest.values())


async def all_entries(context: PlansContext) -> list[FileEntry]:
    """ローカルとリモートの一覧を、同期で重複した分を1件へ絞ってから作成日時の降順で返す。"""
    # ローカル一覧はリモート集約と並列実行できるよう`asyncio.to_thread`経由で取得する。
    local_results = await asyncio.gather(
        *(asyncio.to_thread(_scan_local_root, spec, context.hostname) for spec in context.roots)
    )
    local_entries = [entry for entries, _ in local_results for entry in entries]
    local_status = {
        spec.source_id: root_status(warning) for spec, (_, warning) in zip(context.roots, local_results, strict=True)
    }
    async with context.state.lock:
        previous_status = context.state.root_status.get(context.hostname)
        context.state.root_status[context.hostname] = local_status
    if previous_status != local_status:
        await deliver_root_status(context.state, context.hostname, local_status)
    async with context.state.lock:
        remote_entries: list[FileEntry] = []
        for cached in context.state.remote_files.values():
            remote_entries.extend(cached)
    merged = _oldest_host_per_file(local_entries + remote_entries)
    merged.sort(key=lambda entry: (-entry.ctime_epoch, entry.host, entry.source_id, entry.path))
    return merged


def listed_plan_path(rel: str) -> str:
    """付属ファイルの検索一致を一覧で選択できる計画ファイル（メイン）へ接続する。"""
    suffix = next((suffix for suffix, _ in _PLAN_SUFFIX_LABELS if rel.endswith(suffix)), None)
    return rel if suffix is None else f"{rel[: -len(suffix)]}.md"


async def search_entries(context: PlansContext, query: str) -> list[FileEntry] | None:
    """本文検索に一致する一覧を返す。後続要求に置き換えられた場合は`None`を返す。"""
    entries = await all_entries(context)
    if not query:
        return entries
    local_results = await asyncio.gather(*(asyncio.to_thread(search_files, spec.path, query) for spec in context.roots))
    local_matches = {
        candidate
        for spec, paths in zip(context.roots, local_results, strict=True)
        for path in paths
        for candidate in ((spec.source_id, path), (spec.source_id, listed_plan_path(path)))
    }
    # 1ホストの打ち切りで他ホストの結果が未回収の例外にならないよう、全件を回収してから判定する。
    results = await asyncio.gather(
        *(_search_remote(context, host, query) for host in context.remote_hosts),
        return_exceptions=True,
    )
    remote_matches: dict[str, set[tuple[str, str]]] = {}
    for result in results:
        if isinstance(result, RemoteSearchSuperseded):
            return None
        if isinstance(result, BaseException):
            # `_search_remote`は`Exception`のみを捕捉するため、この分岐はキャンセル等に限られる。
            raise result
        matched_host, matched_paths = result
        remote_matches[matched_host] = {
            candidate
            for source_id, path in matched_paths
            for candidate in ((source_id, path), (source_id, listed_plan_path(path)))
        }
    return [
        entry
        for entry in entries
        if (entry.source_id, entry.path)
        in (local_matches if entry.host == context.hostname else remote_matches.get(entry.host, set()))
    ]


async def _search_remote(context: PlansContext, host: str, query: str) -> tuple[str, set[tuple[str, str]]]:
    """1台のリモートホストを本文検索する。"""
    try:
        raw_paths = await search_remote_files(
            host,
            query,
            context.runner,
            context.state.remote_watchers.get(host),
            coordinator=context.search_coordinator,
        )
    except RemoteSearchSuperseded:
        # 打ち切りは失敗として扱わず、別の検索語の結果で埋めないため呼び出し元へ伝える。
        raise
    except Exception as error:  # noqa: BLE001
        logger.warning("リモート本文検索失敗 host=%s: %s", host, error)
        raw_paths = set()
    source_ids = set(context.state.root_info.get(host, {}))
    fallback_source = next(iter(source_ids)) if len(source_ids) == 1 else ""
    paths: set[tuple[str, str]] = set()
    value: str | tuple[str, str]
    for value in raw_paths:
        if isinstance(value, tuple) and len(value) == 2:
            paths.add((str(value[0]), str(value[1])))
        else:
            paths.add((fallback_source, str(value)))
    return host, paths


def resolve_source_id(context: PlansContext, host: str, source_id: str, rel: str) -> str | None:
    """要求のsource IDを検証・補完する。確定できない場合は`None`を返す。"""
    if host == context.hostname:
        known = {spec.source_id for spec in context.roots}
        if source_id:
            return source_id if source_id in known else None
        candidates = [spec.source_id for spec in context.roots if resolve_under_root(spec.path, rel) is not None]
    else:
        known = set(context.state.root_info.get(host, {}))
        if source_id:
            return source_id if not known or source_id in known else None
        candidates = [entry.source_id for entry in context.state.remote_files.get(host, []) if entry.path == rel]
        if not candidates:
            candidates = list(known)
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return None
    # 単一rootを明示した構成ではsource IDが存在しない。
    return ""


def review_table_html(text: str) -> str:
    """JSON文字列8列又は旧7列のレビュー指摘管理表をHTML表へ変換する。"""
    rows: list[list[str]] = []
    try:
        for line in text.splitlines():
            encoded_cells = line.split("\t")
            if len(encoded_cells) == len(_REVIEW_TABLE_HEADERS) - 1:
                encoded_cells.insert(4, json.dumps(""))
            elif len(encoded_cells) != len(_REVIEW_TABLE_HEADERS):
                raise ValueError("レビュー指摘管理表の列数が不正です")
            cells = [json.loads(cell) for cell in encoded_cells]
            if not all(isinstance(cell, str) for cell in cells):
                raise ValueError("レビュー指摘管理表のセルがJSON文字列ではありません")
            rows.append(cells)
    except (json.JSONDecodeError, ValueError):
        return f"<pre>{html_lib.escape(text)}</pre>\n"

    head = "".join(f"<th>{html_lib.escape(header)}</th>" for header in _REVIEW_TABLE_HEADERS)
    body = "".join("<tr>" + "".join(f"<td>{html_lib.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<table class="review-table">\n<thead><tr>{head}</tr></thead>\n<tbody>{body}</tbody>\n</table>\n'


def is_review_table_path(rel: str) -> bool:
    """相対パスがレビュー指摘管理表かを判定する。"""
    return rel.endswith(_TARGET_TSV_SUFFIXES)


def _plan_paths(rel: str) -> tuple[tuple[str, str], ...]:
    """同じstemに属する計画ファイルの相対パスと表示名を返す。"""
    suffix = next((suffix for suffix, _ in _PLAN_SUFFIX_LABELS if rel.endswith(suffix)), None)
    if suffix is None:
        if not rel.endswith(".md"):
            return ()
        stem = rel[: -len(".md")]
    else:
        stem = rel[: -len(suffix)]
    return (
        (f"{stem}.md", "メイン"),
        *((f"{stem}{attached_suffix}", label) for attached_suffix, label in _PLAN_SUFFIX_LABELS),
    )


async def plan_links_html(context: PlansContext, host: str, source_id: str, rel: str) -> str:
    """同じstemで実在する他の計画だけを、表示応答の先頭へリンクとして付ける。"""
    plan_paths = _plan_paths(rel)
    if not plan_paths:
        return ""
    existing: list[tuple[str, str]] = []
    for plan_rel, label in plan_paths:
        if plan_rel == rel or await _plan_exists(context, host, source_id, plan_rel):
            existing.append((plan_rel, label))
    if len(existing) < 2:
        return ""
    parts: list[str] = []
    for plan_rel, label in existing:
        if plan_rel == rel:
            parts.append(html_lib.escape(label))
            continue
        escaped = html_lib.escape(plan_rel, quote=True)
        parts.append(f'<a href="#" data-plan-path="{escaped}">{html_lib.escape(label)}</a>')
    return f'<nav class="detail-link">{" | ".join(parts)}</nav>\n'


async def _plan_exists(context: PlansContext, host: str, source_id: str, plan_rel: str) -> bool:
    """付属計画の実在を判定する。

    ローカルはファイルシステムの存在確認、リモートはヘルパーのファイル取得の成否で判定する
    （付属計画は一覧から除外されるため、リモートでは一覧応答から実在を判定できない）。
    """
    if host == context.hostname:
        spec = next((item for item in context.roots if item.source_id == source_id), None)
        return spec is not None and resolve_under_root(spec.path, plan_rel) is not None
    if not is_safe_remote_relpath(plan_rel):
        return False
    watcher = context.state.remote_watchers.get(host)
    try:
        await fetch_remote_file(host, plan_rel, context.runner, watcher, source_id=source_id)
    except Exception as error:  # noqa: BLE001
        # 付属計画未作成は通常状態であり障害ではないため、記録レベルはdebugとする。
        logger.debug("付属計画の取得不可 host=%s path=%s: %s", host, plan_rel, error)
        return False
    return True


class PlanFileError(Exception):
    """計画ファイルの取得に失敗したことを、HTTPステータスとともに示す。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def resolve_text_and_mtime(
    context: PlansContext,
    host: str,
    source_id: str,
    rel: str,
) -> tuple[str, float | None]:
    """ファイル本文と`mtime_epoch`を取得する。取得できない場合は`PlanFileError`を送出する。"""
    if host == context.hostname:
        spec = next((item for item in context.roots if item.source_id == source_id), None)
        target = resolve_under_root(spec.path, rel) if spec is not None else None
        if target is None:
            raise PlanFileError(404, "not found")
        return await asyncio.to_thread(_read_with_mtime, target)
    if not is_safe_remote_relpath(rel):
        raise PlanFileError(400, "invalid path")
    watcher = context.state.remote_watchers.get(host)
    try:
        return await fetch_remote_file(host, rel, context.runner, watcher, source_id=source_id)
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, error)
        raise PlanFileError(404, "not found") from error


def _read_with_mtime(path: pathlib.Path) -> tuple[str, float]:
    """ファイル本文と更新日時を連続して取得する。"""
    data = path.read_text(encoding="utf-8", errors="replace")
    return data, path.stat().st_mtime


async def render_file_html(context: PlansContext, host: str, source_id: str, rel: str) -> str:
    """計画ファイルの表示用HTMLを、付属計画リンクを先頭へ付けて返す。"""
    text, mtime = await resolve_text_and_mtime(context, host, source_id, rel)
    # `mtime`が取れた場合のみキャッシュを参照する。リモート応答にmtimeが欠落した場合は
    # 古い結果を返さないよう安全側に倒してバイパスする。
    cache_key: MarkdownCacheKey | None = None
    if mtime is not None:
        cache_key = (host, source_id, rel, mtime) if source_id else (host, rel, mtime)
    rendered: str | None = None
    if cache_key is not None:
        rendered = context.markdown_cache.get(cache_key)
    if rendered is None:
        rendered = review_table_html(text) if is_review_table_path(rel) else markdown_to_html(text, context.renderer)
        if cache_key is not None:
            context.markdown_cache.put(cache_key, rendered)
    # 付属計画リンクは応答組み立て層で付与する。本文変換とそのキャッシュへ混ぜると、
    # 付属計画の出現・消失のたびに本文HTMLキャッシュを無効化する必要が生じるため。
    return await plan_links_html(context, host, source_id, rel) + rendered


def start_local_watchers(context: PlansContext) -> None:
    """ローカルrootのファイルシステム監視を開始する。

    計画ファイル画面の更新通知は、ローカルrootを本関数が、リモートホストを`start_remote_watchers`が担う。
    実在するrootだけをwatchdogへ登録する。private-notesを解決できなかった場合のrootのように、
    実体を持たないrootを`schedule`へ渡すと監視の起動自体が失敗するためである。
    観測したイベントは`PlansEventHandler`が`BroadcastState`のSSE購読者へ通知する。
    """
    context.state.loop = asyncio.get_running_loop()
    observer = watchdog.observers.Observer()
    scheduled = 0
    for spec in context.roots:
        if not spec.path.is_dir():
            continue
        observer.schedule(PlansEventHandler(spec.path, context.state, spec.source_id), str(spec.path), recursive=True)
        scheduled += 1
    if scheduled == 0:
        return
    observer.start()
    context.state.local_observer = observer


def stop_local_watchers(context: PlansContext) -> None:
    """ローカルrootの監視スレッドを終了させ、observerの保持欄を空へ戻す。

    監視スレッドが残ると`after_serving`が完了しないため、`join`まで待って終了を確認する。
    """
    observer = context.state.local_observer
    if observer is None:
        return
    observer.stop()
    observer.join()
    context.state.local_observer = None


def start_remote_watchers(context: PlansContext) -> None:
    """設定済みリモートホストのwatchタスクを起動する。"""
    context.state.loop = asyncio.get_running_loop()
    for host in context.remote_hosts:
        watcher = RemoteWatcher(host, context.state)
        # 本文取得がwatch経路のRPCを利用できるよう参照を共有する。
        context.state.remote_watchers[host] = watcher
        context.state.remote_tasks.append(asyncio.create_task(watcher.run()))


async def stop_remote_watchers(context: PlansContext) -> None:
    """起動済みのwatchタスクをまとめて終了させる。"""
    for task in context.state.remote_tasks:
        task.cancel()
    for task in context.state.remote_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    context.state.remote_tasks.clear()
