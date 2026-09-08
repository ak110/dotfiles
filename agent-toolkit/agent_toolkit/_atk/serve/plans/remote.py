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
    from agent_toolkit._atk.serve.plans.views import (
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
# リモートホスト統合
# --------------------------------------------------------------------------------------


from agent_toolkit._atk.serve.plans.roots import (
    DEFAULT_REMOTE_SEARCH_LIMIT,
    RPC_REQUEST_TIMEOUT_SEC,
    TERMINATE_GRACE_TIMEOUT_SEC,
)


def _build_remote_command_argv(op: str, args: list[str]) -> list[str]:
    """SSH経由でリモートヘルパーを起動するargv要素列を返す。

    SSHは末尾の各要素を空白で連結してリモートシェルへ渡すため、
    シェルにより1単位として解釈すべき要素はあらかじめダブルクォートで囲んで返す。

    リモート起動コマンドはPOSIXシェル非依存とする。
    Windows OpenSSHの既定シェル`cmd.exe`では`bash -c`やheredoc展開が利用できないため、
    シェル組み込みコマンドへ依存しないこと。
    リモート側に`$HOME/dotfiles`が存在することを前提とし、ヘルパースクリプトは当該配下から読み込む。
    クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使い、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はコマンド本体に含めない。
    bootstrapコード本体が満たす制約は`_atk_serve_remote.remote_bootstrap`を正本とする。
    """
    return [
        "uv",
        "run",
        "--no-project",
        "--with",
        '"watchdog>=6.0.0"',
        "--with",
        '"platformdirs>=4.0"',
        "python",
        "-c",
        f'"{REMOTE_BOOTSTRAP}"',
        op,
        *args,
    ]


class RemoteHelperError(Exception):
    """リモートヘルパーの実行が非0で終了したことを、失敗元の標準エラー出力とともに示す。

    本例外の文字列表現は利用者へ渡る警告本文と記録へそのまま引き継がれるため、失敗元の標準エラー出力を含める。
    SSHの接続が成立したうえでリモート側の実行が失敗する場合も本例外となるため、
    到達可否を判別していない語で原因を断定しない。
    """

    def __init__(self, returncode: int, stderr: bytes) -> None:
        super().__init__(f"リモートヘルパーの実行が終了コード{returncode}で失敗しました: {_stderr_excerpt(stderr)}")


def _stderr_excerpt(stderr: bytes) -> str:
    """失敗元の標準エラー出力を、警告本文へ埋め込む1行の文字列へ整える。

    復号できない列は置換し、末尾側を残して切り詰める（失敗の直接原因は出力の末尾に現れるため）。
    """
    text = " ".join(stderr.decode("utf-8", errors="replace").split())
    if not text:
        return "標準エラー出力はありません"
    if len(text) > STDERR_EXCERPT_MAX_CHARS:
        return f"...{text[-STDERR_EXCERPT_MAX_CHARS:]}"
    return text


async def default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でリモートヘルパーを単発実行し、stdoutをUTF-8文字列で返す。

    fallback経路（常駐watch経由RPCが利用できない場合）でのみ使う。
    `subprocess.run`はブロッキングのため`asyncio.to_thread`でラップする。
    非0終了は`RemoteHelperError`として送出し、失敗元の標準エラー出力を呼び出し元へ渡す。
    """
    cmd = ["ssh", *SSH_BASE_OPTIONS, host, *_build_remote_command_argv(op, args)]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        timeout=SSH_TIMEOUT_SEC,
        check=False,
    )
    # capture_output=Trueかつtext未指定のため`stdout`・`stderr`は実行時bytes固定。型注釈はAnyのため明示する。
    assert isinstance(proc.stdout, bytes)
    assert isinstance(proc.stderr, bytes)
    if proc.returncode != 0:
        raise RemoteHelperError(proc.returncode, proc.stderr)
    return proc.stdout.decode("utf-8")


def _decode_read_payload(payload: typing.Mapping[str, typing.Any]) -> tuple[str, float | None]:
    """`read`応答辞書（RPC・fallback共通）から`(本文, mtime_epoch)`を取り出す。

    `mtime_epoch`は応答に含まれない場合や数値でない場合に`None`を返す。
    その場合、呼び出し側はMarkdownキャッシュを安全側に倒してバイパスする。
    """
    data_b64 = str(payload["data"])
    text = base64.b64decode(data_b64).decode("utf-8", errors="replace")
    raw_mtime = payload.get("mtime_epoch")
    mtime = float(raw_mtime) if isinstance(raw_mtime, (int, float)) else None
    return text, mtime


async def fetch_remote_file(
    host: str,
    rel: str,
    ssh_runner: SshRunner,
    watcher: RemoteWatcher | None = None,
    *,
    source_id: str = "",
) -> tuple[str, float | None]:
    """リモートホストの指定ファイル本文と取得時点の`mtime_epoch`を返す。

    `watcher`が渡され、対応する常駐SSH接続が`connected`状態にあればRPC経由で読み取る。
    未接続・タイムアウト・例外などRPC不可状態では`ssh_runner`経由のfallbackへ切り替える。
    本文と`mtime_epoch`は同一読み取り処理から取り出すため、watch通知の遅延に左右されず整合する。
    """
    rel_b64 = base64.b64encode(rel.encode("utf-8")).decode("ascii")
    request_args: dict[str, str] = {"path": rel_b64}
    if source_id:
        request_args["source_id"] = source_id
    if watcher is not None and watcher.is_connected():
        try:
            response = await watcher.request("read", request_args)
        except Exception as error:  # noqa: BLE001
            # RPC失敗（タイムアウト・接続切断・応答エラー等）は警告のうえfallbackする。
            logger.warning("リモートRPC失敗 host=%s path=%s: %s（fallbackへ）", host, rel, error)
        else:
            if response.get("ok"):
                return _decode_read_payload(response)
            error_msg = response.get("error", "(no error message)")
            # `ok=False`は権限不足・パス不正など恒久的な失敗を含むため、fallbackで救済する。
            logger.warning("リモートRPCエラー host=%s path=%s: %s（fallbackへ）", host, rel, error_msg)
    args = [rel_b64]
    if source_id:
        source_b64 = base64.b64encode(source_id.encode("utf-8")).decode("ascii")
        args = [source_b64, rel_b64]
    raw = await ssh_runner(host, "read", args)
    return _decode_read_payload(json.loads(raw))


class RemoteSearchSuperseded(Exception):
    """後続の検索要求へ置き換えられ、実行されないまま打ち切られたことを示す。

    置き換えられた要求へ別の検索語の結果や空集合を返すと、
    呼び出し元は誤った一覧を検索結果として扱う。打ち切りを明示的な失敗として伝える。
    """


# コーディネーター経由で実行する検索本体。結果は一致した相対パス集合とする。
type RemoteSearchResult = set[str] | set[tuple[str, str]]
type RemoteSearchRunner = typing.Callable[[], typing.Awaitable[RemoteSearchResult]]


@dataclasses.dataclass
class _PendingSearch:
    """待機中の検索要求（実行本体と、結果を受け取るFuture）。"""

    run: RemoteSearchRunner
    future: asyncio.Future[RemoteSearchResult]


class RemoteSearchCoordinator:
    """SSHフォールバック経路の検索をホスト単位で直列化し、全ホスト合計でも有界化する。

    検索は利用者の入力ごとに発行されるため、素朴に実行するとSSHとリモートPythonの組が
    入力継続中に積み上がる。ホストごとに実行中1件・待機中1件へ制限し、
    3件目以降の到着時は待機中の要求を最新の1件へ置き換える。
    置き換えられた要求へは`RemoteSearchSuperseded`を返し、別の検索語の結果を共有しない。
    """

    def __init__(self, limit: int = DEFAULT_REMOTE_SEARCH_LIMIT) -> None:
        self._semaphore = asyncio.Semaphore(limit)
        # host -> 待機中の要求。実行中の要求はワーカーが保持するためここには現れない。
        self._pending: dict[str, _PendingSearch] = {}
        # host -> 稼働中のワーカータスク。キーの有無をホスト単位の実行中判定に使う。
        self._workers: dict[str, asyncio.Task[None]] = {}

    async def submit(self, host: str, run: RemoteSearchRunner) -> RemoteSearchResult:
        """検索本体をホストの実行枠へ載せ、自身の要求に対応する結果を返す。"""
        loop = asyncio.get_running_loop()
        request = _PendingSearch(run=run, future=loop.create_future())
        if host in self._workers:
            superseded = self._pending.get(host)
            self._pending[host] = request
            if superseded is not None and not superseded.future.done():
                superseded.future.set_exception(RemoteSearchSuperseded(f"リモート検索が後続要求へ置き換えられた: host={host}"))
        else:
            # 最初の要求は待機列を経由せず実行枠へ渡す。待機列へ置くと、
            # ワーカーの起動前に到着した後続要求が最初の要求を置き換える。
            self._workers[host] = asyncio.create_task(self._run_host(host, request))
        # 呼び出し元（HTTP要求）のキャンセルがワーカーへ波及しないよう遮蔽する。
        return await asyncio.shield(request.future)

    async def _run_host(self, host: str, request: _PendingSearch) -> None:
        """1ホスト分の要求を順に実行する。待機列が尽きた時点でホスト状態を削除して終了する。"""
        while True:
            async with self._semaphore:
                try:
                    result = await request.run()
                except asyncio.CancelledError:
                    if not request.future.done():
                        request.future.cancel()
                    raise
                except Exception as error:  # noqa: BLE001
                    # 例外は握り潰さず、要求元のFutureへそのまま転送する。
                    if not request.future.done():
                        request.future.set_exception(error)
                else:
                    if not request.future.done():
                        request.future.set_result(result)
            next_request = self._pending.pop(host, None)
            if next_request is None:
                # 削除と終了の間にawaitが無いため、この直後の`submit`は新しいワーカーを起動できる。
                self._workers.pop(host, None)
                return
            request = next_request


async def search_remote_files(
    host: str,
    query: str,
    ssh_runner: SshRunner,
    watcher: RemoteWatcher | None = None,
    coordinator: RemoteSearchCoordinator | None = None,
    *,
    source_id: str | None = None,
) -> RemoteSearchResult:
    """リモートホストで本文検索を実行し、一致した相対パス集合を返す。

    `coordinator`を渡した場合、SSHフォールバック経路だけが直列化と全体上限の対象になる。
    常駐SSH接続経由のRPCはプロセスを起動しないため対象外とする。
    """
    query_b64 = base64.b64encode(query.encode("utf-8")).decode("ascii")
    rpc_args: dict[str, str] = {"query": query_b64}
    if source_id:
        rpc_args["source_id"] = source_id
    if watcher is not None and watcher.is_connected():
        try:
            response = await watcher.request("search", rpc_args)
        except Exception as error:  # noqa: BLE001
            logger.warning("リモート検索RPC失敗 host=%s: %s（fallbackへ）", host, error)
        else:
            if response.get("ok"):
                matches = response.get("matches")
                if isinstance(matches, list):
                    return {
                        (str(item["source_id"]), str(item["path"]))
                        for item in matches
                        if isinstance(item, dict) and "source_id" in item and "path" in item
                    }
                if isinstance(response.get("paths"), list):
                    paths = {str(path) for path in response["paths"]}
                    if source_id:
                        return {(source_id, path) for path in paths}
                    return paths
            logger.warning("リモート検索RPCエラー host=%s: %s（fallbackへ）", host, response.get("error"))

    async def _run_ssh_search() -> RemoteSearchResult:
        args = [query_b64]
        if source_id:
            source_b64 = base64.b64encode(source_id.encode("utf-8")).decode("ascii")
            args = [source_b64, query_b64]
        raw = await ssh_runner(host, "search", args)
        payload = json.loads(raw)
        matches = payload.get("matches")
        if isinstance(matches, list):
            return {
                (str(item["source_id"]), str(item["path"]))
                for item in matches
                if isinstance(item, dict) and "source_id" in item and "path" in item
            }
        paths = payload.get("paths", [])
        if not isinstance(paths, list):
            return set()
        result = {str(path) for path in paths}
        if source_id:
            return {(source_id, path) for path in result}
        return result

    if coordinator is None:
        return await _run_ssh_search()
    return await coordinator.submit(host, _run_ssh_search)


def _decode_root_info(raw: typing.Any) -> dict[str, dict[str, typing.Any]]:
    """snapshotの新旧root情報をsource ID keyed形式へ正規化する。"""
    if isinstance(raw, dict):
        if "root" in raw or "portable_root" in raw or "source_id" in raw:
            return {str(raw.get("source_id", "")): dict(raw)}
        return {str(source_id): dict(info) for source_id, info in raw.items() if isinstance(info, dict)}
    if isinstance(raw, list):
        return {str(info.get("source_id", "")): dict(info) for info in raw if isinstance(info, dict) and "source_id" in info}
    return {}


def _decode_root_status(raw: typing.Any) -> dict[str, dict[str, str]]:
    """snapshotのroot状態をsource ID keyed形式へ正規化する。"""
    if not isinstance(raw, dict):
        return {}
    if "status" in raw:
        return {str(raw.get("source_id", "")): dict(raw)}
    return {str(source_id): dict(status) for source_id, status in raw.items() if isinstance(status, dict)}


def _is_listed_remote_path(path: str) -> bool:
    """リモートwatchイベントのパスが一覧対象かを判定する。"""
    return not pathlib.PurePosixPath(path).name.endswith(_LISTED_EXCLUDED_SUFFIXES)


class RemoteWatcher:
    """1ホスト分のwatch+RPC接続ライフサイクルを担うクラス。

    リモート監視はwatchdogによるpush方式を採用する。
    ポーリング方式は対象ファイル数が増えた場合や低リソースホストでのコストが懸念されるため、
    SSH越しに長時間watchプロセスを常駐させて差分イベントだけを配信する。

    `run()`の流れ:
      1. host_statusを"connecting"へ更新しSSE配信
      2. SSH経由でPython bootstrapを実行し、リモート側ヘルパーの`serve`を起動
      3. stdoutの行を読みつつ`_handle_event`でキャッシュ・SSE・RPC応答を処理
      4. snapshotを受信したら"connected"へ遷移し、以降は`request()`によるRPCも可能になる
      5. EOF・例外で"disconnected"へ遷移し、pending RPCを打ち切ってから指数バックオフで再接続
    """

    def __init__(self, host: str, state: BroadcastState) -> None:
        self.host = host
        self.state = state
        # 長時間維持された接続が途絶した後の再接続時にバックオフが最大値から始まらないよう、
        # snapshot受信（接続成功）時にリセットする。
        self._backoff = REMOTE_BACKOFF_INITIAL_SEC
        # RPC状態。接続未確立または接続切断中はNone。
        self._proc: _async_subprocess.Process | None = None
        # request id -> 応答待ちFuture。応答到着・タイムアウト・切断のいずれかで解決する。
        self._pending: dict[int, asyncio.Future[dict[str, typing.Any]]] = {}
        self._next_request_id = 1
        # stdinへの書き込みは複数タスクから発生し得るため`asyncio.Lock`で排他する。
        self._send_lock = asyncio.Lock()
        # snapshot受信後にTrueになり、接続切断時にFalseに戻る。
        self._connected = False
        self._stderr_task: asyncio.Task[None] | None = None

    def is_connected(self) -> bool:
        """RPCを送信可能な状態か（snapshot受信済みかつstdinが生存）を返す。"""
        if not self._connected:
            return False
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        return not proc.stdin.is_closing()

    async def request(
        self,
        op: str,
        args: dict[str, typing.Any],
        timeout: float = RPC_REQUEST_TIMEOUT_SEC,
    ) -> dict[str, typing.Any]:
        """常駐SSH接続経由でRPCリクエストを送信し、応答辞書を返す。

        接続未確立・切断中では`RuntimeError`を送出する。
        timeout時は対応するpendingエントリを除去して`TimeoutError`を送出する。
        """
        if not self.is_connected():
            raise RuntimeError(f"watch not connected: host={self.host}")
        proc = self._proc
        # 同時実行下では`is_connected`通過後に切断される可能性があるため、ここで再確認する。
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise RuntimeError(f"watch not connected: host={self.host}")
        loop = asyncio.get_running_loop()
        req_id = self._next_request_id
        self._next_request_id += 1
        fut: asyncio.Future[dict[str, typing.Any]] = loop.create_future()
        self._pending[req_id] = fut
        payload: dict[str, typing.Any] = {"id": req_id, "op": op, **args}
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            async with self._send_lock:
                proc.stdin.write(line.encode("utf-8"))
                await proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def run(self) -> None:
        """無限ループで接続→ストリーム処理→バックオフ→再接続を繰り返す。

        `asyncio.CancelledError`は再送出してタスクを終了させる。
        それ以外の例外はwarningログに残し、`disconnected`遷移後にバックオフ再試行する。
        """
        while True:
            await self._set_status("connecting")
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                self._proc = proc
                assert proc.stdout is not None
                await self._process_stream(_iter_stream_lines(proc.stdout))
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                self._fail_pending(asyncio.CancelledError("watcher cancelled"))
                raise
            except Exception as error:  # noqa: BLE001
                # 接続失敗・JSON解析失敗・stat不能などをまとめて拾い、ホスト単位で再接続継続する。
                logger.warning("リモートwatch失敗 host=%s: %s", self.host, error)
                await self._set_status("disconnected")
            finally:
                self._fail_pending(ConnectionError(f"watch disconnected: host={self.host}"))
                await self._cancel_stderr_task()
                if proc is not None:
                    await _terminate_process(proc)
                self._proc = None
                self._connected = False
            # 指数バックオフ（上限・±20%ジッタ）。リトライ上限なし。
            jittered = self._backoff * random.uniform(*REMOTE_BACKOFF_JITTER_RANGE)
            await asyncio.sleep(jittered)
            self._backoff = min(self._backoff * 2, REMOTE_BACKOFF_MAX_SEC)

    async def _connect(self) -> _async_subprocess.Process:
        cmd = [
            "ssh",
            *SSH_BASE_OPTIONS,
            *SSH_WATCH_OPTIONS,
            self.host,
            *_build_remote_command_argv("serve", []),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # ヘルパーは初回snapshotで全エントリーを1行JSONとして出力するため、
            # asyncio既定の64KiB上限を超えると`readline()`が例外を送出する。
            limit=REMOTE_STREAM_LIMIT_BYTES,
        )
        # helper起動失敗（依存解決失敗など）はstdoutが空EOFとなり原因ログが残らないため、
        # stderrを常時読み取ってwarningへ転写する。
        assert proc.stderr is not None
        self._stderr_task = asyncio.create_task(_drain_stderr(self.host, proc.stderr))
        return proc

    async def _cancel_stderr_task(self) -> None:
        """stderr読取タスクを終了させる。切断・キャンセルのfinally経路から呼ぶ。"""
        task = self._stderr_task
        if task is None:
            return
        self._stderr_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _process_stream(self, lines: LineSource) -> None:
        """行ストリームを受け取り、type別にハンドラへ振り分ける。"""
        async for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                logger.warning(
                    "リモートwatch JSON解析失敗 host=%s msg=%s pos=%d length=%d",
                    self.host,
                    error.msg,
                    error.pos,
                    len(line),
                )
                continue
            await self._handle_event(event)

    async def _handle_event(self, event: typing.Mapping[str, typing.Any]) -> None:
        kind = event.get("type")
        if kind == "snapshot":
            await self._handle_snapshot(event)
            return
        if kind == "upsert":
            entry = make_file_entry(self.host, event)
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                cached = [item for item in cached if (item.source_id, item.path) != (entry.source_id, entry.path)]
                if _is_listed_remote_path(entry.path):
                    cached.append(entry)
                self.state.remote_files[self.host] = cached
            await deliver_refresh(self.state)
            return
        if kind == "deleted":
            path = str(event.get("path", ""))
            source_id = str(event.get("source_id", event.get("source", "")))
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                self.state.remote_files[self.host] = [
                    item for item in cached if (item.source_id, item.path) != (source_id, path)
                ]
            await deliver_refresh(self.state)
            return
        if kind == "ping":
            return
        if kind == "response":
            self._resolve_response(event)
            return
        logger.warning("リモートwatch 未知のイベント host=%s type=%r", self.host, kind)

    async def _handle_snapshot(self, event: typing.Mapping[str, typing.Any]) -> None:
        """初回・再接続時のsnapshotをキャッシュへ反映し、接続確立を配信する。"""
        entries = [
            make_file_entry(self.host, item) for item in event.get("entries", []) if _is_listed_remote_path(str(item["path"]))
        ]
        host_info = event.get("host_info")
        has_root_info = "root_info" in event
        decoded_root_info = _decode_root_info(event.get("root_info"))
        has_root_status = "root_status" in event
        decoded_root_status = _decode_root_status(event.get("root_status"))
        async with self.state.lock:
            self.state.remote_files[self.host] = entries
            if isinstance(host_info, dict):
                self.state.host_info[self.host] = dict(host_info)
            if has_root_info:
                self.state.root_info[self.host] = decoded_root_info
            else:
                self.state.root_info.pop(self.host, None)
            if has_root_status:
                self.state.root_status[self.host] = decoded_root_status
            else:
                self.state.root_status.pop(self.host, None)
        await self._set_status("connected")
        self._connected = True
        # 接続成功時にバックオフをリセットし、次回切断後の再接続を初期値から始める。
        self._backoff = REMOTE_BACKOFF_INITIAL_SEC
        await deliver_refresh(self.state)
        if isinstance(host_info, dict):
            await deliver_host_info(self.state, self.host, dict(host_info))
        if has_root_info:
            await deliver_root_info(self.state, self.host, decoded_root_info)
        if has_root_status:
            await deliver_root_status(self.state, self.host, decoded_root_status)

    def _resolve_response(self, event: typing.Mapping[str, typing.Any]) -> None:
        """`type=response`イベントに対し、対応するpending Futureを解決する。

        対応Futureが既に取り消されている・タイムアウト後の遅延応答である場合は破棄する。
        """
        req_id = event.get("id")
        if not isinstance(req_id, int):
            logger.warning("リモートwatch 不正な応答id host=%s id=%r", self.host, req_id)
            return
        fut = self._pending.get(req_id)
        if fut is None or fut.done():
            return
        fut.set_result(dict(event))

    def _fail_pending(self, exc: BaseException) -> None:
        """切断・キャンセル時に全pending Futureを例外で解決する。"""
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    async def _set_status(self, status: str) -> None:
        async with self.state.lock:
            previous = self.state.host_status.get(self.host)
            self.state.host_status[self.host] = status
            if status == "disconnected":
                # 接続喪失時は`host_info`のキー自体を削除する（`None`値保持ではない）。
                # 再接続成功時はsnapshot分岐で再登録される。
                self.state.host_info.pop(self.host, None)
                self.state.root_info.pop(self.host, None)
                self.state.root_status.pop(self.host, None)
        if previous != status:
            await deliver_host_status(self.state, self.host, status)
            if status == "disconnected":
                await deliver_host_info(self.state, self.host, None)
                await deliver_root_info(self.state, self.host, None)
                await deliver_root_status(self.state, self.host, {})


async def _iter_stream_lines(stream: asyncio.StreamReader) -> typing.AsyncIterator[str]:
    """`StreamReader`から1行ずつ取り出す非同期イテレータ。

    `readline()`はEOFで空bytesを返すため、その時点で打ち切る。
    snapshot行が`REMOTE_STREAM_LIMIT_BYTES`を超過した場合、`readline`は`ValueError`を送出する。
    明示捕捉してwarningを残して打ち切らないと`run`の広範な`except Exception`で見過ごされ、
    原因不明のリコネクトが続く。
    """
    while True:
        try:
            chunk = await stream.readline()
        except ValueError as error:
            logger.warning("リモートwatch snapshot行がlimit超過 limit=%d: %s", REMOTE_STREAM_LIMIT_BYTES, error)
            return
        if not chunk:
            return
        yield chunk.decode("utf-8", errors="replace")


async def _drain_stderr(host: str, stream: asyncio.StreamReader) -> None:
    """stderrを行単位で読み続けてwarningへ転写する（詳細は`_connect`のコメント参照）。"""
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.warning("リモートwatch stderr host=%s: %s", host, text)
    except Exception as error:  # noqa: BLE001
        # CancelledErrorはBaseException派生のため`Exception`で拾わず、通常経路で再送出される。
        logger.warning("リモートwatch stderr読取失敗 host=%s: %s", host, error)


async def _terminate_process(
    proc: _async_subprocess.Process,
    grace_timeout: float = TERMINATE_GRACE_TIMEOUT_SEC,
) -> None:
    """watch用subprocessを段階的に終了させる。

    serveヘルパーは`for raw in sys.stdin:`のEOFで停止経路に入るため、
    まずstdinをcloseして穏当な終了を試み、応答がなければ`terminate`、
    それでも応答がなければ`kill`へ降下する。
    """
    if proc.returncode is not None:
        return
    # 1) stdinへEOFを送ってhelperのreader_loopをbreakさせる。
    if proc.stdin is not None and not proc.stdin.is_closing():
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            proc.stdin.close()
    if await _wait_with_timeout(proc, grace_timeout):
        return
    # 2) SIGTERM相当でhelperへ停止指示する。
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    if await _wait_with_timeout(proc, grace_timeout):
        return
    # 3) 最後にSIGKILL相当で強制終了させる。
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await _wait_with_timeout(proc, grace_timeout)


async def _wait_with_timeout(proc: _async_subprocess.Process, timeout: float) -> bool:
    """`proc.wait()`を時間制限付きで実行し、終了済みならTrueを返す。

    `_terminate_process`はキャンセル経路からも呼ばれるため、
    `CancelledError`は吸収して段階的処理を継続する。
    """
    if proc.returncode is not None:
        return True
    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    return proc.returncode is not None


def is_safe_remote_relpath(rel: str) -> bool:
    """SSHヘルパーへ渡す前に相対パスのトラバーサルを検証する。

    リモート側でも検証するが、サーバー側で先に拒否することで不要なSSH呼び出しを避け、
    ログにも危険な相対パスが残らないようにする。
    """
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = pathlib.PurePosixPath(rel).parts
    if any(part in ("", "..") for part in parts):
        return False
    return rel.endswith(".md") or rel.endswith(_TARGET_TSV_SUFFIXES)
