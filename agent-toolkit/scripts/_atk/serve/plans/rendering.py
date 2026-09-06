# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
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

import markdown_it
import markdown_it.renderer
import markdown_it.token
import markdown_it.utils
import platformdirs
import pygments
import watchdog.events
import watchdog.observers
import watchdog.observers.api
from _common import file_lock as _file_lock
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from _atk.serve import remote as _atk_serve_remote


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _atk.serve.plans.roots import (
        BroadcastState,
        DEFAULT_REMOTE_SEARCH_LIMIT,
        FileEntry,
        LEGACY_PORTABLE_ROOT,
        LEGACY_SOURCE_ID,
        LineSource,
        MARKDOWN_CACHE_MAX_BYTES,
        MARKDOWN_CACHE_MAX_ENTRIES,
        NEW_PORTABLE_ROOT,
        NEW_SOURCE_ID,
        REMOTE_BACKOFF_INITIAL_SEC,
        REMOTE_BACKOFF_JITTER_RANGE,
        REMOTE_BACKOFF_MAX_SEC,
        REMOTE_STREAM_LIMIT_BYTES,
        RPC_REQUEST_TIMEOUT_SEC,
        RootSpec,
        SSH_BASE_OPTIONS,
        SSH_TIMEOUT_SEC,
        SSH_WATCH_OPTIONS,
        STDERR_EXCERPT_MAX_CHARS,
        SshRunner,
        TERMINATE_GRACE_TIMEOUT_SEC,
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
    from _atk.serve.plans.ctime_index import (
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
    from _atk.serve.plans.local_scan import (
        PlansEventHandler,
        REMOTE_BOOTSTRAP,
        _STATIC_DIR,
        _ctime_epoch,
        is_listed_path,
        is_target_path,
        list_files,
        local_host_info,
        read_markdown_css,
        read_mermaid_bundle,
        read_pygments_css,
        resolve_under_root,
        root_info,
        root_status,
        root_warning,
        scan_files,
        search_files,
    )
    from _atk.serve.plans.remote import (
        RemoteHelperError,
        RemoteSearchCoordinator,
        RemoteSearchResult,
        RemoteSearchRunner,
        RemoteSearchSuperseded,
        RemoteWatcher,
        _PendingSearch,
        _build_remote_command_argv,
        _decode_read_payload,
        _decode_root_info,
        _decode_root_status,
        _drain_stderr,
        _is_listed_remote_path,
        _iter_stream_lines,
        _stderr_excerpt,
        _terminate_process,
        _wait_with_timeout,
        default_ssh_runner,
        fetch_remote_file,
        is_safe_remote_relpath,
        search_remote_files,
    )
    from _atk.serve.plans.views import (
        PlanFileError,
        PlansContext,
        _oldest_host_per_file,
        _plan_exists,
        _plan_paths,
        _read_with_mtime,
        _scan_local_root,
        _search_remote,
        all_entries,
        create_context,
        is_review_table_path,
        listed_plan_path,
        plan_links_html,
        render_file_html,
        resolve_source_id,
        resolve_text_and_mtime,
        review_table_html,
        search_entries,
        start_local_watchers,
        start_remote_watchers,
        stop_local_watchers,
        stop_remote_watchers,
    )


# --------------------------------------------------------------------------------------
# Markdownレンダリングとキャッシュ
# --------------------------------------------------------------------------------------


from _atk.serve.plans.roots import MARKDOWN_CACHE_MAX_BYTES, MARKDOWN_CACHE_MAX_ENTRIES


def _highlight_code(code: str, name: str, _attrs: str) -> str:
    """markdown-itのフェンスコードブロックをPygmentsでハイライトする。

    言語指定なし・未知言語フェンスは空文字を返し、markdown-it既定の素通し描画にフォールバックする。
    """
    if not name:
        return ""
    try:
        lexer = get_lexer_by_name(name, stripall=False)
    except ClassNotFound:
        return ""
    escaped_lang = html_lib.escape(name, quote=True)
    body = pygments.highlight(code, lexer, _PYGMENTS_FORMATTER).rstrip("\n")
    return f'<pre><code class="{_PYGMENTS_CSS_CLASS} language-{escaped_lang}">{body}\n</code></pre>\n'


def _render_fence(
    renderer: markdown_it.renderer.RendererHTML,
    tokens: typing.Sequence[markdown_it.token.Token],
    idx: int,
    options: markdown_it.utils.OptionsDict,
    env: collections.abc.MutableMapping[str, typing.Any],
) -> str:
    """MermaidとSVGのフェンスを専用HTML構造へ変換する。"""
    token = tokens[idx]
    info = token.info.strip() if token.info else ""
    name = info.split(maxsplit=1)[0].lower() if info else ""
    if name not in {"mermaid", "svg"}:
        return renderer.fence(tokens, idx, options, env)

    source = html_lib.escape(token.content)
    if name == "mermaid":
        return (
            '<figure class="diagram diagram-mermaid">\n'
            f'  <div class="diagram-output mermaid-output">{source}</div>\n'
            '  <details class="diagram-source"><summary>Mermaid原文</summary>'
            f"<pre>{source}</pre></details>\n"
            "</figure>\n"
        )
    return (
        '<figure class="diagram diagram-svg">\n'
        '  <img class="diagram-output svg-output" alt="SVG図">\n'
        '  <details class="diagram-source"><summary>SVG原文</summary>'
        f"<pre>{source}</pre></details>\n"
        "</figure>\n"
    )


def make_md_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化しPygmentsハイライトを注入したGFM相当のMarkdownレンダラを返す。

    GFM相当プリセットの表・取り消し線は維持し、誤リンクを防ぐため裸URLの自動リンクだけを無効化する。
    `html`も明示的に`False`へ上書きしてXSS経路を塞ぐ。
    `highlight`コールバックの戻り値はそのままHTMLとして埋め込まれるため、
    Pygmentsのエスケープ済み出力のみを返す。
    """
    renderer = markdown_it.MarkdownIt(
        "gfm-like",
        {"html": False, "highlight": _highlight_code, "linkify": False},
    )
    renderer.add_render_rule("fence", _render_fence)
    return renderer


def markdown_to_html(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    """Markdown文字列をHTMLへ変換する。"""
    md = renderer if renderer is not None else make_md_renderer()
    return md.render(text)


# 単一root互換時のキーは(host, path, mtime_epoch)、複数root時は(host, source_id, path, mtime_epoch)。
# `mtime_epoch`がキーに含まれるため、ファイル更新時は自動的に新しいエントリとなり明示的な無効化は不要。
type MarkdownCacheKey = tuple[str, str, float] | tuple[str, str, str, float]


class MarkdownCache:
    """Markdownレンダリング結果のLRUキャッシュ。

    リモート分は`fetch_remote_file`が本文と同時取得した`mtime_epoch`をそのまま使うことで、
    watch通知の遅延に左右されず整合する。
    `mtime_epoch`が`None`の場合、呼び出し側はキャッシュをバイパスする（本クラスは`None`を扱わない）。
    """

    def __init__(
        self,
        max_entries: int = MARKDOWN_CACHE_MAX_ENTRIES,
        max_bytes: int = MARKDOWN_CACHE_MAX_BYTES,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        # OrderedDictで挿入順を保ち、`move_to_end`でLRU順に保つ。
        self._entries: collections.OrderedDict[MarkdownCacheKey, str] = collections.OrderedDict()
        self._total_bytes = 0

    def get(self, key: MarkdownCacheKey) -> str | None:
        """キャッシュ済みHTMLを返す。未保持ならNone。"""
        html = self._entries.get(key)
        if html is None:
            return None
        # アクセスのたびに末尾へ移して最近使用扱いにする。
        self._entries.move_to_end(key)
        return html

    def put(self, key: MarkdownCacheKey, html: str) -> None:
        """HTMLを保持し、上限超過分を古い順に破棄する。"""
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._total_bytes -= len(existing.encode("utf-8"))
        size = len(html.encode("utf-8"))
        # 単一エントリが上限を超える場合は保持せずに諦める（次回はミスのまま再レンダリング）。
        if size > self._max_bytes:
            return
        self._entries[key] = html
        self._total_bytes += size
        self._evict_excess()

    def _evict_excess(self) -> None:
        while self._entries and (len(self._entries) > self._max_entries or self._total_bytes > self._max_bytes):
            _, evicted = self._entries.popitem(last=False)
            self._total_bytes -= len(evicted.encode("utf-8"))

    def __len__(self) -> int:
        return len(self._entries)

    def total_bytes(self) -> int:
        """テスト・観測用に現在の総バイト数を返す。"""
        return self._total_bytes
