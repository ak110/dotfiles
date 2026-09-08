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
    from _atk.serve.plans.rendering import (
        MarkdownCache,
        MarkdownCacheKey,
        _highlight_code,
        _render_fence,
        make_md_renderer,
        markdown_to_html,
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


_STATIC_DIR = pathlib.Path(__file__).with_name("static")

# リモート側で実行する短いPython bootstrap。組み立ての制約は`_atk_serve_remote`を正本とする。
REMOTE_BOOTSTRAP = _atk_serve_remote.remote_bootstrap("atk_serve_plans_remote_helper.py")


# --------------------------------------------------------------------------------------
# ローカル走査
# --------------------------------------------------------------------------------------


def is_target_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が対象接尾辞・`root`配下・非dotdirの全条件を満たすか判定する。

    読取・検索・変更監視の3経路が同一の対象集合を返すよう、当該判定を1箇所へ集約する。
    メイン`<stem>.md`と付属ファイル`<stem>.detail.md`・`<stem>.bugs.md`・レビュー指摘管理表を真とする
    （付属ファイルは一覧だけから除外し、読取・検索・監視の対象には含める）。
    リモート側`atk_serve_plans_remote_helper.py`の`_is_target_path`と同一基準を保つ
    （同ファイルはSSH越しに単独実行されるためモジュールを共有できず、意図的に重複させている）。
    `root`自身がドット配下（`~/.claude/plans`など）でも通るよう、判定は`root`からの相対パスに対して行う。
    シンボリックリンクを解決してから相対化するため、`root`外を指すリンクは対象外となる。
    """
    if path.suffix != ".md" and not path.name.endswith(_TARGET_TSV_SUFFIXES):
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


def is_listed_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が計画一覧で独立項目として表示する対象かを判定する。

    メイン計画は常に一覧へ載せ、付属の詳細・バグ計画は除外する。レビュー指摘管理表は対応する
    メイン計画が存在する場合だけ付属ファイルとして除外し、存在しない場合は自身を一覧へ載せる。
    """
    if not is_target_path(path, root):
        return False
    review_suffix = next((suffix for suffix in _TARGET_TSV_SUFFIXES if path.name.endswith(suffix)), None)
    if review_suffix is not None:
        main = path.with_name(f"{path.name[: -len(review_suffix)]}.md")
        return not main.is_file()
    return not path.name.endswith((_DETAIL_SUFFIX, _BUGS_SUFFIX))


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
            if not (is_target_path(src, self.root) or is_target_path(dest, self.root)):
                return
        elif not is_target_path(src, self.root):
            return
        loop = self.state.loop
        if loop is None:
            # 起動直後にループ参照が未設定のイベントは取りこぼしてよい（直後のイベントで再通知される）。
            return
        asyncio.run_coroutine_threadsafe(schedule_broadcast(self.state), loop)


def _ctime_epoch(st: os.stat_result) -> float:
    """観測時点の作成日時候補をepoch秒で返す。

    `st_birthtime`（macOS・Windowsで実在し「作成時刻」を表す）を優先し、
    存在しないプラットフォームでは更新日時を用いる。
    初回観測時の値を保持する処理は`update_creation_time_index`が担う。
    編集で変動する`st_ctime`は用いない。
    """
    birthtime = getattr(st, "st_birthtime", None)
    return float(birthtime) if birthtime is not None else float(st.st_mtime)


def local_host_info(root: pathlib.Path) -> dict[str, str]:
    """ローカルホストの`host_info`エントリ（`root`・`home`・`os_type`・`os_name`）を組み立てる。

    `root`・`home`はクライアント側のパス結合と表記を統一するため常に`/`区切りへ正規化する。
    `home`はクライアント側のチルダ表記変換の基準パスとして使う。
    """
    home = str(pathlib.Path.home()).replace("\\", "/")
    return {
        "root": str(root).replace("\\", "/"),
        "home": home,
        "os_type": os.name,
        "os_name": os.name,
    }


def root_info(spec: RootSpec) -> dict[str, typing.Any]:
    """複数root APIへ返す保存元情報を組み立てる。"""
    info: dict[str, typing.Any] = {
        "source_id": spec.source_id,
        "portable_root": spec.portable_path,
    }
    if spec.warning is not None:
        info["warning"] = spec.warning
    return info


def root_warning(root: pathlib.Path) -> str | None:
    """rootを利用できない理由を返す。

    rootの不在は計画をまだ保存していない通常の状態であるため警告しない。
    非ディレクトリ以外の障害は走査時に判定する。
    """
    if root.exists() and not root.is_dir():
        return "rootがディレクトリではありません"
    return None


def root_status(warning: str | None) -> dict[str, str]:
    """rootの利用状態をAPI・SSE向けの小さな辞書へ変換する。"""
    if warning is None:
        return {"status": "ok", "message": ""}
    return {"status": "warning", "message": warning}


def scan_files(
    root: pathlib.Path,
    host: str,
    source_id: str = "",
    *,
    migrate_legacy_ctime: bool | None = None,
) -> tuple[list[FileEntry], str | None]:
    """`root`を走査し、一覧とroot単位の警告を返す。

    rootの非ディレクトリ・権限不足は呼び出し元が他rootの処理を継続できるよう、
    例外ではなく警告本文として返す。rootの不在は通常の状態として空の一覧だけを返す。
    rootは自動作成しない。
    """
    warning = root_warning(root)
    if warning is not None:
        return [], warning
    # 不在のrootに対する`rglob`は空を返して成功するため、走査へ進むと空の観測結果で
    # インデックスを更新し、同じ`(host, root)`に記録済みの作成日時を回収してしまう。
    if not root.is_dir():
        return [], None

    scanned: list[dict[str, typing.Any]] = []
    observed: dict[str, float] = {}
    warning = None
    try:
        for path in root.rglob("*"):
            try:
                if not path.is_file() or not is_listed_path(path, root):
                    continue
                st = path.stat()
            except OSError as error:
                warning = f"rootの走査に失敗しました: {error}"
                continue
            rel = path.relative_to(root).as_posix()
            observed[rel] = _ctime_epoch(st)
            scanned.append({"path": rel, "name": path.name, "mtime_epoch": st.st_mtime})
    except OSError as error:
        warning = f"rootの走査に失敗しました: {error}"

    # 走査後に一度だけインデックスを更新し、同じ`(host, root)`の不在エントリを回収する。
    resolved = update_creation_time_index(
        host,
        root,
        observed,
        migrate_legacy=(source_id in ("", LEGACY_SOURCE_ID) if migrate_legacy_ctime is None else migrate_legacy_ctime),
    )
    collected = [
        make_file_entry(host, {**item, "ctime_epoch": resolved[item["path"]], "source_id": source_id}) for item in scanned
    ]
    collected.sort(key=lambda entry: (entry.ctime_epoch, entry.path), reverse=True)
    return collected, warning


def list_files(root: pathlib.Path, host: str, source_id: str = "") -> list[FileEntry]:
    """`root`から一覧対象の計画ファイルを再帰的に探し、作成日時の降順で返す。"""
    entries, _ = scan_files(root, host, source_id)
    return entries


def search_files(root: pathlib.Path, query: str) -> set[str]:
    """本文へ検索語が部分一致する計画ファイルの相対パス集合を返す。"""
    needle = query.casefold()
    if not root.is_dir():
        return set()
    if not needle:
        try:
            return {
                path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and is_target_path(path, root)
            }
        except OSError:
            return set()
    matched: set[str] = set()
    try:
        for path in root.rglob("*"):
            if not path.is_file() or not is_target_path(path, root):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text.casefold():
                matched.add(path.relative_to(root).as_posix())
    except OSError:
        return matched
    return matched


def resolve_under_root(root: pathlib.Path, rel: str) -> pathlib.Path | None:
    """`rel`が`root`配下の対象ファイルを指す場合のみ絶対パスを返す。存在しない場合はNone。"""
    # シンボリックリンクを辿ってroot外へ出ないよう、resolve後のパスで範囲検査する。
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if not target.is_file() or not is_target_path(target, root):
        return None
    return target


def read_markdown_css() -> str:
    """Markdown表示用のスタイルシートを配布物から読み込む。"""
    return (_STATIC_DIR / "markdown.css").read_text(encoding="utf-8")


def read_mermaid_bundle() -> str:
    """同梱したMermaidの単一ファイルbundleを読み込む。"""
    return (_STATIC_DIR / "vendor" / "mermaid.min.js").read_text(encoding="utf-8")


def read_pygments_css() -> str:
    """Pygmentsのスタイルシートを返す。

    pygmentsの基本ルール（`.codehilite { background: ...; color: ... }`）は除外し、
    トークン別カラールール（`.codehilite .k`等）のみを返す。
    背景と既定文字色はmarkdown.css側の`pre code`ルールへ委ね、
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
