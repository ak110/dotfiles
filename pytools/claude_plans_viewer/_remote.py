"""SSH経由のリモートホスト統合（リモートwatch・リモートファイル取得）。"""

import asyncio
import asyncio.subprocess as _async_subprocess
import base64
import contextlib
import json
import logging
import pathlib
import random
import subprocess
import typing

from pytools.claude_plans_viewer import _state

logger = logging.getLogger(__name__)

# SSH接続時に共通付与するオプション。
# `BatchMode=yes`で鍵認証失敗時にパスワードプロンプトでハングしないようにする。
SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")

# 単発SSH呼び出し（fallback用`read`）のタイムアウト秒。
# ネットワーク不通時の検知遅延と通常応答の余裕を兼ねた値。
# `serve`の長時間ストリームには適用しない。
SSH_TIMEOUT_SEC = 30.0

# RPCリクエスト1件あたりのタイムアウト秒。
# 常駐SSH接続の応答が一時的に遅延した場合にfallback経路へ切り替えるための値。
RPC_REQUEST_TIMEOUT_SEC = 30.0

# `serve`用のSSH追加オプション。
# 接続確立を5秒、ServerAliveの組合せでネットワーク途絶を最大30秒程度で検知する。
SSH_WATCH_OPTIONS = (
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=3",
)

# `RemoteWatcher`の再接続バックオフ。指数増加・上限・ジッタ係数を一箇所にまとめる。
REMOTE_BACKOFF_INITIAL_SEC = 1.0
REMOTE_BACKOFF_MAX_SEC = 30.0
REMOTE_BACKOFF_JITTER_RANGE = (0.8, 1.2)

# リモートwatch subprocessのstdout用StreamReader上限（バイト）。
# `asyncio.streams._DEFAULT_LIMIT`は64KiBで、helperが1行JSONとして全エントリーを出力するsnapshot行が
# エントリー数増加でこれを超えると行末を発見できず例外送出する。
REMOTE_STREAM_LIMIT_BYTES = 8 * 1024 * 1024

# SSHランナーの抽象シグネチャ。テストではfake実装を注入し、本番は`default_ssh_runner`を使う。
# (host, op, args) -> stdout (UTF-8文字列)。失敗時は例外を送出する。
SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]


# 行ジェネレーターのプロトコル。テストではメモリー上のリストから供給するため、
# `asyncio.subprocess.Process.stdout`に依存しないインターフェースを使う。
LineSource = typing.AsyncIterator[str]

# リモート側で実行する短いPython bootstrap。
# `os.path.expanduser('~')`でホームを展開し、リモートdotfiles配下の`_remote_helper.py`を
# `read_text(encoding='utf-8')`で読み込んで`exec`する。
# `$`・`%`・`<`・`>`・`|`・`&`・`^`はPOSIXシェル/cmd.exe双方で意味を持つため
# このコード本体には含めない（`>=`を含むパッケージ指定子はargv側のダブルクォート内に置く）。
REMOTE_BOOTSTRAP = (
    "import os, pathlib; "
    "p = pathlib.Path(os.path.expanduser('~')) / "
    "'dotfiles/pytools/claude_plans_viewer/_remote_helper.py'; "
    "exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'))"
)


def _build_remote_command_argv(op: str, args: list[str]) -> list[str]:
    """SSH経由でリモートヘルパーを起動するargv要素列を返す。

    SSHは末尾の各要素を空白で連結してリモートシェルへ渡すため、
    シェルにより1単位として解釈すべき要素はあらかじめダブルクォートで囲んで返す。

    リモート起動コマンドはPOSIXシェル非依存とする。
    Windows OpenSSHの既定シェル`cmd.exe`では`bash -c`やheredoc展開・`head -c`等が
    利用できないため、シェル組み込みコマンドへ依存しないこと。
    リモート側に`$HOME/dotfiles`が存在することを前提とし、
    ヘルパースクリプトはリモート側 dotfiles から直接読み込む。
    `~`はcmd.exeでは展開されないため、Pythonの`os.path.expanduser('~')`で展開する。
    クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使い、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はコマンド本体に含めない。
    Windowsの既定ロケールはUTF-8とは限らないため、ヘルパー本体の読み込みは
    `read_text(encoding="utf-8")`でエンコーディングを明示する。
    """
    return [
        "uv",
        "run",
        "--no-project",
        "--with",
        '"watchdog>=6.0.0"',
        "python",
        "-c",
        f'"{REMOTE_BOOTSTRAP}"',
        op,
        *args,
    ]


async def default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でリモートヘルパーを単発実行し、stdoutをUTF-8文字列で返す。

    fallback経路（常駐watch経由RPCが利用できない場合）でのみ使う。
    起動経路は`RemoteWatcher`と統一し、リモート側dotfilesの`_remote_helper.py`を
    短いPython bootstrap経由で`exec`する。
    `subprocess.run`はブロッキングのため`asyncio.to_thread`でラップする。
    """
    cmd = ["ssh", *SSH_BASE_OPTIONS, host, *_build_remote_command_argv(op, args)]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        timeout=SSH_TIMEOUT_SEC,
        check=True,
    )
    # capture_output=Trueかつtext未指定のため`stdout`は実行時bytes固定。型注釈はAnyのため明示する。
    assert isinstance(proc.stdout, bytes)
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
    watcher: "RemoteWatcher | None" = None,
) -> tuple[str, float | None]:
    """リモートホストの指定ファイル本文と取得時点の`mtime_epoch`を返す。

    `watcher`が渡され、対応する常駐SSH接続が`connected`状態にあればRPC経由で読み取る。
    未接続・タイムアウト・例外などRPC不可状態では`ssh_runner`経由のfallbackへ切り替える。
    本文と`mtime_epoch`は同一読み取り処理から取り出すため、watch通知の遅延に左右されず整合する。
    `mtime_epoch`が応答に欠落している場合は`None`を返し、呼び出し側はキャッシュをバイパスする。
    """
    rel_b64 = base64.b64encode(rel.encode("utf-8")).decode("ascii")
    if watcher is not None and watcher.is_connected():
        try:
            response = await watcher.request("read", {"path": rel_b64})
        except Exception as e:  # noqa: BLE001
            # RPC失敗（タイムアウト・接続切断・応答エラー等）は警告のうえfallbackする。
            logger.warning("リモートRPC失敗 host=%s path=%s: %s（fallbackへ）", host, rel, e)
        else:
            if response.get("ok"):
                return _decode_read_payload(response)
            error_msg = response.get("error", "(no error message)")
            # `ok=False`は権限不足・パス不正など恒久的な失敗を含むため、fallbackで救済する。
            logger.warning("リモートRPCエラー host=%s path=%s: %s（fallbackへ）", host, rel, error_msg)
    raw = await ssh_runner(host, "read", [rel_b64])
    return _decode_read_payload(json.loads(raw))


class RemoteWatcher:
    """1ホスト分のwatch+RPC接続ライフサイクルを担うクラス。

    リモート監視はwatchdogによるpush方式を採用する。
    ポーリング方式は対象ファイル数が増えた場合や低リソースホストでのCPU/SSH接続コストが
    懸念されるため、SSH越しに長時間watchプロセスを常駐させて差分イベントだけを配信する。

    `run()`の流れ:
      1. host_statusを"connecting"へ更新しSSE配信
      2. SSH経由でPython bootstrapを実行し、リモート側`_remote_helper.py`の`serve`を起動
      3. stdoutの行を読みつつ`_handle_event`でキャッシュ・SSE・RPC応答を処理
      4. snapshotを受信したら"connected"へ遷移し、以降は`request()`によるRPCも可能になる
      5. EOF・例外で"disconnected"へ遷移し、pending RPCを打ち切ってから指数バックオフで再接続
    """

    def __init__(
        self,
        host: str,
        state: _state.BroadcastState,
    ) -> None:
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

        接続未確立・切断中ではRuntimeErrorを送出する。
        timeout時は対応するpendingエントリを除去してTimeoutErrorを送出する。
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
        """無限ループで接続→ストリーム処理→バックオフ→再接続を行う。

        `asyncio.CancelledError`は再送出してタスクを終了させる。
        それ以外の例外はwarningログに残し、`disconnected`遷移後にバックオフ再試行する。
        """
        while True:
            await self._set_status("connecting")
            # pylintのE1101 no-memberが`asyncio.subprocess.Process`に対して誤検出されるため、
            # `import asyncio.subprocess as _async_subprocess`で別名importを介して参照する。
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                self._proc = proc
                assert proc.stdout is not None
                await self._process_stream(_iter_stream_lines(proc.stdout))
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                self._fail_pending(asyncio.CancelledError("watcher cancelled"))
                if proc is not None:
                    await _terminate_process(proc)
                self._proc = None
                self._connected = False
                raise
            except Exception as e:  # noqa: BLE001
                # 接続失敗・JSON解析失敗・stat不能などをまとめて拾い、ホスト単位で再接続継続する。
                logger.warning("リモートwatch失敗 host=%s: %s", self.host, e)
                await self._set_status("disconnected")
            finally:
                self._fail_pending(ConnectionError(f"watch disconnected: host={self.host}"))
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
            # asyncio既定の64KiB上限を超えると`readline()`が
            # `ValueError("Separator is found, but chunk is longer than limit")`を送出する。
            # 上限を8MiB相当へ引き上げて十分な余裕を確保する。
            limit=REMOTE_STREAM_LIMIT_BYTES,
        )
        # stdinはRPC通信路としてそのまま保持する（ヘルパー本体はリモート側dotfilesから読み込まれる）。
        return proc

    async def _process_stream(self, lines: LineSource) -> None:
        """行ストリームを受け取り、type別にハンドラへ振り分ける。

        テスト容易性のため`LineSource`を引数化し、本番は`_iter_stream_lines`を渡す。
        """
        async for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("リモートwatch JSON解析失敗 host=%s: %s line=%r", self.host, e, line)
                continue
            await self._handle_event(event)

    async def _handle_event(self, event: typing.Mapping[str, typing.Any]) -> None:
        kind = event.get("type")
        if kind == "snapshot":
            entries = [_state.make_file_entry(self.host, item) for item in event.get("entries", [])]
            async with self.state.lock:
                self.state.remote_files[self.host] = entries
            await self._set_status("connected")
            self._connected = True
            # 接続成功時にバックオフをリセットし、次回切断後の再接続を初期値から始める。
            self._backoff = REMOTE_BACKOFF_INITIAL_SEC
            await _state.deliver_refresh(self.state)
            return
        if kind == "upsert":
            entry = _state.make_file_entry(self.host, event)
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                cached = [e for e in cached if e.path != entry.path]
                cached.append(entry)
                self.state.remote_files[self.host] = cached
            await _state.deliver_refresh(self.state)
            return
        if kind == "deleted":
            path = str(event.get("path", ""))
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                self.state.remote_files[self.host] = [e for e in cached if e.path != path]
            await _state.deliver_refresh(self.state)
            return
        if kind == "ping":
            return
        if kind == "response":
            self._resolve_response(event)
            return
        logger.warning("リモートwatch 未知のイベント host=%s type=%r", self.host, kind)

    def _resolve_response(self, event: typing.Mapping[str, typing.Any]) -> None:
        """`type=response`イベントに対し、対応するpending Futureを解決する。

        対応Futureが既に取り消されている・タイムアウト後の遅延応答である場合は黙って破棄する。
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
        if previous != status:
            await _state.deliver_host_status(self.state, self.host, status)


async def _iter_stream_lines(stream: asyncio.StreamReader) -> typing.AsyncIterator[str]:
    """`StreamReader`から1行ずつ取り出す非同期イテレータ。

    `readline()`はEOFで空bytesを返すため、その時点で打ち切る。
    """
    while True:
        chunk = await stream.readline()
        if not chunk:
            return
        yield chunk.decode("utf-8", errors="replace")


# 各停止段階で`proc.wait()`に与える既定タイムアウト（秒）。
# helperは数秒以内にEOF/SIGTERMへ反応するため、合計でも数秒〜十数秒に収まる。
TERMINATE_GRACE_TIMEOUT_SEC = 2.0


async def _terminate_process(
    proc: _async_subprocess.Process,
    grace_timeout: float = TERMINATE_GRACE_TIMEOUT_SEC,
) -> None:
    """watch用subprocessを段階的に終了させる。

    serveヘルパーは`for raw in sys.stdin:`のEOFで停止経路に入るため、
    まずstdinをcloseして穏当な終了を試み、応答がなければ`terminate`、
    それでも応答がなければ`kill`へ降下する。
    各段階で`grace_timeout`秒ずつ待機し、ゾンビ化や残留serveプロセスを避ける。
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
    if any(p in ("", "..") for p in parts):
        return False
    return rel.endswith(".md")
