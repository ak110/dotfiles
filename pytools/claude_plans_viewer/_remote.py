"""SSH経由のリモートホスト統合（リモートwatch・リモートファイル取得）。

クラス・関数名は同一パッケージ内の兄弟モジュールから参照される前提のため、
underscore接頭辞を付けない（package-internalとして扱う）。
パッケージ外への公開可否は`__init__.py`の再export一覧で制御する。
"""

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

from pytools.claude_plans_viewer import _assets, _state

logger = logging.getLogger(__name__)

# SSH接続時に共通付与するオプション。
# `BatchMode=yes`で鍵認証失敗時にパスワードプロンプトでハングしないようにする。
SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")

# `read`サブコマンド（同期SSH呼び出し）のタイムアウト秒。
# ネットワーク不通時の検知遅延と通常応答の余裕を兼ねた値。
# `watch`サブコマンドの長時間ストリームには適用しない。
SSH_TIMEOUT_SEC = 30.0

# `watch`用のSSH追加オプション。
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


# SSHランナーの抽象シグネチャ。テストではfake実装を差し込み、本番は`default_ssh_runner`を使う。
# (host, op, args) -> stdout (UTF-8文字列)。失敗時は例外を送出する。
SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]


# 行ジェネレーターのプロトコル。テストではメモリー上のリストから供給するため、
# `asyncio.subprocess.Process.stdout`に依存しないインターフェースを使う。
LineSource = typing.AsyncIterator[str]


async def default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でリモートヘルパーを実行し、stdoutをUTF-8文字列で返す。

    ヘルパースクリプト本体はstdinに、操作種別と引数はSSHコマンドのargvに渡す。
    `python`／`python3`のPATH不整合を吸収するため、リモート実行は常に`uv run --script -`で行う。
    `subprocess.run`はブロッキングのため`asyncio.to_thread`でラップする。
    """
    cmd = [
        "ssh",
        *SSH_BASE_OPTIONS,
        host,
        "uv",
        "run",
        "--no-project",
        "--script",
        "-",
        op,
        *args,
    ]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        input=_assets.REMOTE_HELPER_SCRIPT.encode("utf-8"),
        capture_output=True,
        timeout=SSH_TIMEOUT_SEC,
        check=True,
    )
    # capture_output=Trueかつtext未指定のため`stdout`は実行時bytes固定。型注釈はAnyのため明示する。
    assert isinstance(proc.stdout, bytes)
    return proc.stdout.decode("utf-8")


async def fetch_remote_file(host: str, rel: str, ssh_runner: SshRunner) -> str:
    """リモートホストの指定ファイル本文を取得する（UTF-8文字列）。

    `read`サブコマンドは1回限りの同期SSH呼び出しのため、watch用の常駐ストリームとは別経路で
    既存`SshRunner`抽象を使う。テストでは`_FakeSshRunner`に差し替える。
    """
    rel_b64 = base64.b64encode(rel.encode("utf-8")).decode("ascii")
    raw = await ssh_runner(host, "read", [rel_b64])
    return base64.b64decode(raw).decode("utf-8", errors="replace")


class RemoteWatcher:
    """1ホスト分のwatch接続ライフサイクルを担うクラス。

    `run()`の流れ:
      1. host_statusを"connecting"へ更新しSSE配信
      2. SSH+stdinでリモートwatchを起動
      3. stdoutの行を読みつつ`_handle_event`でキャッシュとSSEを更新
      4. snapshotを受信したら"connected"へ遷移
      5. EOF・例外で"disconnected"へ遷移し、指数バックオフ後に再接続
    """

    def __init__(
        self,
        host: str,
        state: _state.BroadcastState,
        helper_script: str = _assets.REMOTE_HELPER_SCRIPT,
    ) -> None:
        self.host = host
        self.state = state
        self._helper_script = helper_script
        # 長時間維持された接続が切れた後の再接続時にバックオフが最大値から始まらないよう、
        # snapshot受信（接続成功）時にリセットする。
        self._backoff = REMOTE_BACKOFF_INITIAL_SEC

    async def run(self) -> None:
        """無限ループで接続→ストリーム処理→バックオフ→再接続を行う。

        `asyncio.CancelledError`は再送出してタスク終了させる。
        それ以外の例外は warning ログに残し、`disconnected`遷移後にバックオフ再試行する。
        """
        while True:
            await self._set_status("connecting")
            # pylintのE1101 no-memberが`asyncio.subprocess.Process`に対して誤検出されるため、
            # `import asyncio.subprocess as _async_subprocess`で別名importを介して参照する。
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                assert proc.stdout is not None
                await self._process_stream(iter_stream_lines(proc.stdout))
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                if proc is not None:
                    await terminate_process(proc)
                raise
            except Exception as e:  # noqa: BLE001
                # 接続失敗・JSON解析失敗・stat不能などをまとめて拾い、ホスト単位で再接続継続する。
                logger.warning("リモートwatch失敗 host=%s: %s", self.host, e)
                await self._set_status("disconnected")
            finally:
                if proc is not None:
                    await terminate_process(proc)
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
            "uv",
            "run",
            "--no-project",
            "--script",
            "-",
            "watch",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        proc.stdin.write(self._helper_script.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await proc.stdin.wait_closed()
        return proc

    async def _process_stream(self, lines: LineSource) -> None:
        """行ストリームを受け取り、type別にハンドラへ振り分ける。

        テスト容易性のため`LineSource`を引数化し、本番は`iter_stream_lines`を渡す。
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
        logger.warning("リモートwatch 未知のイベント host=%s type=%r", self.host, kind)

    async def _set_status(self, status: str) -> None:
        async with self.state.lock:
            previous = self.state.host_status.get(self.host)
            self.state.host_status[self.host] = status
        if previous != status:
            await _state.deliver_host_status(self.state, self.host, status)


async def iter_stream_lines(stream: asyncio.StreamReader) -> typing.AsyncIterator[str]:
    """`StreamReader`から1行ずつ取り出す非同期イテレータ。

    `readline()`はEOFで空bytesを返すため、その時点で打ち切る。
    """
    while True:
        chunk = await stream.readline()
        if not chunk:
            return
        yield chunk.decode("utf-8", errors="replace")


async def terminate_process(proc: _async_subprocess.Process) -> None:
    """watch用subprocessを後始末する。

    既に終了していれば何もしない。
    `terminate`後に短時間waitし、ゾンビ化を避ける。
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), timeout=2.0)


def is_safe_remote_relpath(rel: str) -> bool:
    """SSHヘルパーへ渡す前に相対パスのトラバーサルを事前検証する。

    リモート側でも検証するが、サーバー側で先に弾くことで不要なSSH呼び出しを避け、
    ログにも危険な相対パスが残らないようにする。
    """
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = pathlib.PurePosixPath(rel).parts
    if any(p in ("", "..") for p in parts):
        return False
    return rel.endswith(".md")
