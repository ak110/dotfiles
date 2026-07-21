"""pytools.claude_plans_viewer のリモートウォッチャー関連テスト。"""

import asyncio
import json
import typing

import pytest

from pytools.claude_plans_viewer import _remote, _state
from pytools.claude_plans_viewer_remote_test_helpers import aiter_lines as _aiter_lines
from pytools.claude_plans_viewer_remote_test_helpers import attach_fake_connection as _attach_fake_connection

_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)


class TestRemoteWatcher:
    """`RemoteWatcher._process_stream`の行処理ユニットテスト。

    純粋な行ジェネレーターを引数化することで、subprocess・SSHを介さず分岐網羅する。
    `_process_stream`・`_set_status`・`_backoff`等の`RemoteWatcher`内部状態を直接参照する。
    公開経路（`run()`）経由ではSSH/subprocess起動と再接続ループを伴うため、
    各イベント分岐とstate遷移を網羅検証できない。
    例外的に最小限の直接テストへ限定する。
    """

    @pytest.mark.asyncio
    async def test_snapshot_updates_cache_and_marks_connected(self) -> None:
        state = _state.BroadcastState()
        # 本番購読者の`maxsize=1`は容量超過時に新規通知を破棄する設計のため、
        # snapshotで連続発火する host-status と refresh の両方を観測するには十分な容量を要する。
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        async with state.lock:
            state.subscribers.add(q)
        try:
            watcher = _remote.RemoteWatcher("host1", state)
            lines = [
                json.dumps(
                    {
                        "type": "snapshot",
                        "entries": [
                            {"path": "a.md", "name": "a.md", "mtime_epoch": 100.0, "ctime_epoch": 50.0},
                            {"path": "sub/b.md", "name": "b.md", "mtime_epoch": 200.0, "ctime_epoch": 150.0},
                        ],
                    }
                )
                + "\n",
            ]
            await watcher._process_stream(_aiter_lines(lines))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）

            assert state.host_status["host1"] == "connected"
            cached = state.remote_files["host1"]
            assert sorted(e.path for e in cached) == ["a.md", "sub/b.md"]
            # `ctime_epoch`がFileEntryへ保持されること。
            assert {e.path: e.ctime_epoch for e in cached} == {"a.md": 50.0, "sub/b.md": 150.0}
            # snapshot受信時は host-status と refresh の両方が配信される。
            received: list[str] = []
            while not q.empty():
                received.append(q.get_nowait())
            assert _SSE_REFRESH_PAYLOAD in received
            host_status_payload = json.dumps(
                {"type": "host-status", "host": "host1", "status": "connected"}, ensure_ascii=False
            )
            assert host_status_payload in received
        finally:
            async with state.lock:
                state.subscribers.discard(q)

    @pytest.mark.asyncio
    async def test_snapshot_with_host_info_registers_and_broadcasts(self) -> None:
        """snapshotの`host_info`が`BroadcastState.host_info`へ登録され、SSEへ配信される。"""
        state = _state.BroadcastState()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        async with state.lock:
            state.subscribers.add(q)
        try:
            watcher = _remote.RemoteWatcher("host1", state)
            host_info = {"root": "/home/remote/.claude/plans", "os_type": "posix", "os_name": "posix"}
            lines = [
                json.dumps({"type": "snapshot", "entries": [], "host_info": host_info}) + "\n",
            ]
            await watcher._process_stream(_aiter_lines(lines))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）

            assert state.host_info["host1"] == host_info
            received: list[str] = []
            while not q.empty():
                received.append(q.get_nowait())
            host_info_payload = json.dumps({"type": "host_info_update", "host": "host1", "info": host_info}, ensure_ascii=False)
            assert host_info_payload in received
        finally:
            async with state.lock:
                state.subscribers.discard(q)

    @pytest.mark.asyncio
    async def test_upsert_adds_new_path(self) -> None:
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # 既存snapshotを与えてから、新規pathのupsertが追加されることを確認する。
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
            _aiter_lines(
                [
                    json.dumps(
                        {
                            "type": "snapshot",
                            "entries": [{"path": "a.md", "name": "a.md", "mtime_epoch": 100.0, "ctime_epoch": 50.0}],
                        }
                    )
                    + "\n",
                    json.dumps({"type": "upsert", "path": "b.md", "name": "b.md", "mtime_epoch": 200.0, "ctime_epoch": 150.0})
                    + "\n",
                ]
            )
        )

        cached = state.remote_files["host1"]
        assert sorted(e.path for e in cached) == ["a.md", "b.md"]
        # upsert経路でも`ctime_epoch`が保持されること。
        assert {e.path: e.ctime_epoch for e in cached} == {"a.md": 50.0, "b.md": 150.0}

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing_path(self) -> None:
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
            _aiter_lines(
                [
                    json.dumps(
                        {
                            "type": "snapshot",
                            "entries": [{"path": "a.md", "name": "a.md", "mtime_epoch": 100.0, "ctime_epoch": 50.0}],
                        }
                    )
                    + "\n",
                    json.dumps({"type": "upsert", "path": "a.md", "name": "a.md", "mtime_epoch": 999.0, "ctime_epoch": 500.0})
                    + "\n",
                ]
            )
        )

        cached = state.remote_files["host1"]
        assert len(cached) == 1
        assert cached[0].mtime_epoch == 999.0
        assert cached[0].ctime_epoch == 500.0

    @pytest.mark.asyncio
    async def test_deleted_removes_path(self) -> None:
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
            _aiter_lines(
                [
                    json.dumps(
                        {
                            "type": "snapshot",
                            "entries": [
                                {"path": "a.md", "name": "a.md", "mtime_epoch": 100.0, "ctime_epoch": 50.0},
                                {"path": "b.md", "name": "b.md", "mtime_epoch": 200.0, "ctime_epoch": 150.0},
                            ],
                        }
                    )
                    + "\n",
                    json.dumps({"type": "deleted", "path": "a.md"}) + "\n",
                ]
            )
        )

        cached = state.remote_files["host1"]
        assert [e.path for e in cached] == ["b.md"]

    @pytest.mark.asyncio
    async def test_ping_does_not_emit_anything(self) -> None:
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            watcher = _remote.RemoteWatcher("host1", state)
            await watcher._process_stream(_aiter_lines([json.dumps({"type": "ping"}) + "\n"]))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
            # キャッシュにもhost_statusにも一切影響しない（接続確立前なので空のまま）。
            assert "host1" not in state.remote_files
            assert not state.host_status
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_invalid_json_logged_and_processing_continues(self, caplog: pytest.LogCaptureFixture) -> None:
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        secret = "token=secret-value"
        with caplog.at_level("WARNING", logger="pytools.claude_plans_viewer"):
            await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
                _aiter_lines(
                    [
                        f'{{"{secret}"\n',
                        json.dumps(
                            {
                                "type": "snapshot",
                                "entries": [{"path": "a.md", "name": "a.md", "mtime_epoch": 100.0, "ctime_epoch": 50.0}],
                            }
                        )
                        + "\n",
                    ]
                )
            )
        # 後続行は処理が継続される。
        assert "host1" in state.remote_files
        assert any("JSON解析失敗" in r.message for r in caplog.records if r.levelname == "WARNING")
        assert all(secret not in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_set_status_disconnected_emits_sse_after_snapshot(self) -> None:
        """`_set_status`経由のdisconnected遷移とSSE配信を検証する。

        `run`本体の再接続ループはSSH/subprocess起動とバックオフ待機を含み、
        単体テストの所要時間と決定性の制約内で網羅検証できないため、
        `_set_status`を直接呼び出してhost_statusの遷移と
        host-statusのSSE配信を確認する形に限定する。
        """
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            watcher = _remote.RemoteWatcher("host1", state)
            # snapshot を受信し、いったん connected へ遷移させる。
            await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
                _aiter_lines(
                    [
                        json.dumps({"type": "snapshot", "entries": []}) + "\n",
                    ]
                )
            )
            # キューの中身を消費してから切断遷移を観測する。
            while not q.empty():
                q.get_nowait()
            await watcher._set_status("disconnected")  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（再接続ループを伴う公開経路run()の切断遷移を単体で発火不能）
            assert state.host_status["host1"] == "disconnected"
            payload = json.dumps({"type": "host-status", "host": "host1", "status": "disconnected"}, ensure_ascii=False)
            assert q.get_nowait() == payload
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_set_status_disconnected_removes_host_info(self) -> None:
        """disconnected遷移時に`host_info`のキーが削除され、SSEへ`info: null`が配信される。

        `_state.subscribe`既定の`maxsize=1`キューでは`host-status`と`host_info_update`の
        同時発火時に一方が破棄され得るため、両方を観測できる容量のキューを使う。
        """
        state = _state.BroadcastState()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        async with state.lock:
            state.subscribers.add(q)
        try:
            watcher = _remote.RemoteWatcher("host1", state)
            state.host_info["host1"] = {"root": "/home/remote/.claude/plans", "os_type": "posix", "os_name": "posix"}
            await watcher._set_status("connecting")  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（再接続ループを伴う公開経路run()の状態遷移を単体で発火不能）
            while not q.empty():
                q.get_nowait()

            await watcher._set_status("disconnected")  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（再接続ループを伴う公開経路run()の切断遷移を単体で発火不能）

            assert "host1" not in state.host_info
            received: list[str] = []
            while not q.empty():
                received.append(q.get_nowait())
            host_info_payload = json.dumps({"type": "host_info_update", "host": "host1", "info": None}, ensure_ascii=False)
            assert host_info_payload in received
        finally:
            async with state.lock:
                state.subscribers.discard(q)

    @pytest.mark.asyncio
    async def test_snapshot_resets_backoff(self) -> None:
        """snapshot受信でバックオフが`_REMOTE_BACKOFF_INITIAL_SEC`にリセットされること。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # 最大値まで増加していると仮定してから snapshot を送信する。
        watcher._backoff = _remote.REMOTE_BACKOFF_MAX_SEC  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（再接続ループ内のバックオフ増加を単体で到達させる経路がない）
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
            _aiter_lines(
                [
                    json.dumps({"type": "snapshot", "entries": []}) + "\n",
                ]
            )
        )
        assert watcher._backoff == _remote.REMOTE_BACKOFF_INITIAL_SEC  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（再接続ループ内のバックオフ増加を単体で到達させる経路がない）


class TestIterStreamLinesLimitOverrun:
    """`_iter_stream_lines`が`LimitOverrunError`を見過ごさず打ち切ることを確認する。

    実運用では`RemoteWatcher._connect`が`limit=REMOTE_STREAM_LIMIT_BYTES`(8MiB)を渡すが、
    テストでは`limit=64`の`StreamReader`へ64バイト超のchunkを投入して再現性を高める。
    見過ごしたまま再接続ループへ抜けると原因不明のリコネクトが続くため、
    明示的なwarningとイテレータ打ち切りを検証する。
    """

    @pytest.mark.asyncio
    async def test_limit_overrun_logs_warning_and_terminates(self, caplog: pytest.LogCaptureFixture) -> None:
        stream = asyncio.StreamReader(limit=64)
        # 改行を含まない長いバイト列を投入し、`readline`が`LimitOverrunError`を送出する条件を構成する。
        stream.feed_data(b"x" * 200)
        stream.feed_eof()

        collected: list[str] = []
        with caplog.at_level("WARNING", logger="pytools.claude_plans_viewer"):
            async for line in _remote._iter_stream_lines(stream):  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（`_iter_stream_lines`はモジュール内部の非同期イテレータで公開経路がSSH/subprocess経由に限定される）
                collected.append(line)

        assert not collected
        assert any("snapshot行がlimit超過" in r.message for r in caplog.records if r.levelname == "WARNING")


class TestDrainStderr:
    """`_drain_stderr`がhelper起動失敗の標準エラー出力をwarningへ転写することを確認する。

    実運用ではリモート側`_remote_helper.py`不在・`uv`未導入・依存解決失敗などがstderr経由でのみ観測できる。
    捕捉に失敗すると空EOFで見過ごされ原因不明のリコネクトが続くため、明示的なログ転写を検証する。
    """

    @pytest.mark.asyncio
    async def test_stderr_lines_forwarded_to_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        stream = asyncio.StreamReader()
        stream.feed_data(b"ModuleNotFoundError: No module named 'watchdog'\n")
        stream.feed_data(b"\n")  # 空行はwarning対象外
        stream.feed_data(b"Traceback (most recent call last):\n")
        stream.feed_eof()

        with caplog.at_level("WARNING", logger="pytools.claude_plans_viewer"):
            await _remote._drain_stderr("host1", stream)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（`_drain_stderr`はモジュール内部のヘルパーでSSH/subprocess由来のstderrのみを扱う）

        messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("host1" in m and "ModuleNotFoundError" in m for m in messages)
        assert any("host1" in m and "Traceback" in m for m in messages)
        # 空行はログ転写しない。
        assert not any(m.endswith("host=host1: ") for m in messages)

    @pytest.mark.asyncio
    async def test_stderr_read_exception_logged_and_returns(self, caplog: pytest.LogCaptureFixture) -> None:
        """`readline`が例外を送出しても、`_drain_stderr`はwarningを記録して処理を終える。"""

        class _BrokenStream:
            async def readline(self) -> bytes:
                raise RuntimeError("broken pipe")

        with caplog.at_level("WARNING", logger="pytools.claude_plans_viewer"):
            await _remote._drain_stderr("host1", typing.cast(typing.Any, _BrokenStream()))  # pylint: disable=protected-access  # noqa: SLF001

        assert any("stderr読取失敗" in r.message for r in caplog.records if r.levelname == "WARNING")


class TestCancelStderrTask:
    """`RemoteWatcher._cancel_stderr_task`のライフサイクル検証。

    `_connect`は`_stderr_task`を生成し、`run`のfinally経路で`_cancel_stderr_task`を呼ぶ。
    stderr読取タスク単体では次の2点を検証する。
    - `_stderr_task=None`（`_connect`前）でも例外なく完了する
    - 生存タスクが設定されていればcancelして待機する
    """

    @pytest.mark.asyncio
    async def test_cancel_stderr_task_no_task_is_noop(self) -> None:
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        assert watcher._stderr_task is None  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（`_connect`前の初期状態確認）
        # noopで例外を送出しないこと。
        await watcher._cancel_stderr_task()  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（`_connect`前の直接呼び出し検証）
        assert watcher._stderr_task is None  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（呼び出し後の状態確認）

    @pytest.mark.asyncio
    async def test_cancel_stderr_task_cancels_running_task(self) -> None:
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        stream = asyncio.StreamReader()
        # EOFを与えず永続的に待機するタスクを設定する。
        task = asyncio.create_task(_remote._drain_stderr("host1", stream))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（`_connect`が生成するタスクを再現するため）
        watcher._stderr_task = task  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（`_connect`経路と等価な直接注入）

        await watcher._cancel_stderr_task()  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（キャンセル経路の単体検証）

        assert watcher._stderr_task is None  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（キャンセル後の状態確認）
        assert task.cancelled() or task.done()


class TestRemoteWatcherRpc:
    """`RemoteWatcher`の双方向RPCの単体検証。"""

    @pytest.mark.asyncio
    async def test_request_resolves_with_response_event(self) -> None:
        """`request`は対応する`response`イベント受信で結果を返す。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        proc = _attach_fake_connection(watcher)

        async def _drive() -> None:
            # `request`が送信を完了してpendingに登録されるまで少し待つ。
            await asyncio.sleep(0.05)
            # サーバー側のJSON行から取り出した応答を`_handle_event`へ渡す。
            await watcher._handle_event({"type": "response", "id": 1, "ok": True, "data": "QUJD", "mtime_epoch": 12.5})  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess stdoutから配信されるイベントを単体で注入するため）

        request_task = asyncio.create_task(watcher.request("read", {"path": "Zg=="}))
        drive_task = asyncio.create_task(_drive())
        result = await asyncio.wait_for(request_task, timeout=1.0)
        await drive_task

        assert result["ok"] is True
        assert result["data"] == "QUJD"
        assert result["mtime_epoch"] == 12.5
        # stdinへ送信されたリクエストJSONには`id`/`op`/`path`が乗る。
        sent = b"".join(proc.stdin.buffer).decode("utf-8")
        assert '"op": "read"' in sent or '"op":"read"' in sent
        assert '"path": "Zg=="' in sent or '"path":"Zg=="' in sent

    @pytest.mark.asyncio
    async def test_request_when_disconnected_raises(self) -> None:
        """切断状態（`_connected=False`）の`request`はRuntimeErrorを送出する。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # `_proc`は設定するが`_connected`はFalseのままにする。
        _attach_fake_connection(watcher)
        watcher._connected = False  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（接続状態の直接制御が必要）
        with pytest.raises(RuntimeError, match="not connected"):
            await watcher.request("read", {"path": "Zg=="})

    @pytest.mark.asyncio
    async def test_fail_pending_breaks_inflight_requests(self) -> None:
        """`_fail_pending`は実行中のすべての`request`を例外で解決する。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        _attach_fake_connection(watcher)

        async def _drive() -> None:
            await asyncio.sleep(0.05)
            watcher._fail_pending(ConnectionError("disconnected"))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（run()のキャンセル/切断経路を単体で発火するにはSSH/subprocess起動が必要）

        request_task = asyncio.create_task(watcher.request("read", {"path": "Zg=="}))
        drive_task = asyncio.create_task(_drive())
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(request_task, timeout=1.0)
        await drive_task

    @pytest.mark.asyncio
    async def test_request_timeout_removes_pending(self) -> None:
        """応答が届かないとtimeoutでTimeoutErrorとなり、pendingエントリが残らない。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        _attach_fake_connection(watcher)

        with pytest.raises(asyncio.TimeoutError):
            await watcher.request("read", {"path": "Zg=="}, timeout=0.05)
        # 失敗側でpendingが除去されていること（後続応答の遅延配達でメモリリークしない）。
        assert not watcher._pending  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（pending辞書の直接観測が必要）
