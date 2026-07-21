"""pytools.claude_plans_viewer のサーバー・購読者・イベント関連テスト。"""

import asyncio
import dataclasses
import json
import os
import re
import typing
from pathlib import Path

import pytest
import watchdog.events
from quart.testing.connections import TestHTTPConnection as _TestHTTPConnection

from pytools.claude_plans_viewer import _app, _local, _state

_BROADCAST_DEBOUNCE_SEC = 0.3
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)
_QUEUE_GET_TIMEOUT_SEC = _BROADCAST_DEBOUNCE_SEC + 0.7


class TestSubscribers:
    """購読者管理(`subscribe`・`unsubscribe`・`schedule_broadcast`)のテスト。"""

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_roundtrip(self):
        """subscribeで登録しunsubscribeで解除できること。重複解除もエラーにならないこと。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        assert q in state.subscribers
        await _state.unsubscribe(state, q)
        assert q not in state.subscribers
        # 重複解除してもエラーにならない
        await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_delivers_refresh(self):
        """`schedule_broadcast`後にキューから"refresh"が取得できること（debounce経由で届く）。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            await _state.schedule_broadcast(state)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == _SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_coalesces_via_debounce(self):
        """`schedule_broadcast`を連続で呼んでもdebounce窓内は1件にまとめられること。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            await _state.schedule_broadcast(state)
            await _state.schedule_broadcast(state)
            await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.qsize() == 1
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_many_calls(self):
        """`schedule_broadcast`を短時間に10回呼んでも、debounce窓満了後にキューは1件であること。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            for _ in range(10):
                await _state.schedule_broadcast(state)
            await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.qsize() == 1
        finally:
            await _state.unsubscribe(state, q)


class TestWatchdogHandler:
    """PlansEventHandler のイベントフィルタリングテスト。"""

    @pytest.mark.asyncio
    async def test_md_event_broadcasts(self, tmp_path: Path):
        """.mdファイルの変更イベントで購読者へrefreshが届くこと（debounce経由で届く）。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(md_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == _SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_opened_event_ignored(self, tmp_path: Path):
        """FileOpenedEventでは購読者へ通知しないこと（feedback loopの起点を遮断する回帰テスト）。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileOpenedEvent(str(md_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            # debounce窓より長く待ってもキューに入らないこと
            await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_closed_nowrite_event_ignored(self, tmp_path: Path):
        """FileClosedNoWriteEventでは購読者へ通知しないこと（feedback loopの起点を遮断する回帰テスト）。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileClosedNoWriteEvent(str(md_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_moved_event_to_md_broadcasts(self, tmp_path: Path):
        """FileMovedEvent(src=*.md.tmp, dest=*.md)で購読者へ通知されること（atomic-write保存の回帰テスト）。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            event = watchdog.events.FileMovedEvent(
                src_path=str(tmp_path / "x.md.tmp"),
                dest_path=str(tmp_path / "x.md"),
            )
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == _SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_moved_event_from_md_broadcasts(self, tmp_path: Path):
        """FileMovedEvent(src=*.md, dest=*.md)で購読者へ通知されること（rename・移動操作の検出）。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            event = watchdog.events.FileMovedEvent(
                src_path=str(tmp_path / "x.md"),
                dest_path=str(tmp_path / "y.md"),
            )
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == _SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_non_md_event_ignored(self, tmp_path: Path):
        """.md以外のファイルイベントでは購読者へ通知しないこと。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            txt_file = tmp_path / "note.txt"
            txt_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(txt_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_dotdir_event_ignored(self, tmp_path: Path):
        """root配下のdotdir配下のイベントでは購読者へ通知しないこと。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            cache_dir = tmp_path / ".cache"
            cache_dir.mkdir()
            md_file = cache_dir / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(md_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_directory_event_ignored(self, tmp_path: Path):
        """is_directory=Trueのイベントでは購読者へ通知しないこと。"""
        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            event = watchdog.events.DirModifiedEvent(str(tmp_path / "subdir"))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_dotdir_root_events_pass(self, tmp_path: Path):
        """rootそのものがdotdir配下にあっても、root配下の通常.mdは通知されること。

        ~/.claude/plansのようにrootのパス成分にdotdirが含まれるケースの回帰テスト。
        旧実装ではsrc_path全体のpartsを判定していたためrootのパス成分にも誤マッチしていた。
        """
        dot_root = tmp_path / ".claude_like"
        dot_root.mkdir()
        md_file = dot_root / "plan.md"
        md_file.write_text("x", encoding="utf-8")

        state = _state.BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            event = watchdog.events.FileModifiedEvent(str(md_file))
            _local.PlansEventHandler(dot_root, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == _SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)


class TestApiEndpoints:
    """Quartアプリの各種APIエンドポイントのスモーク。"""

    @pytest.mark.asyncio
    async def test_api_files_returns_list(self, tmp_path: Path):
        """/api/filesが.mdの一覧をJSONで返す（`ctime_epoch`を含む）。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/files")

        assert response.status_code == 200
        assert response.content_type == "application/json; charset=utf-8"
        data = json.loads(await response.get_data())
        assert [e["path"] for e in data] == ["a.md"]
        assert "ctime_epoch" in data[0]

    @pytest.mark.asyncio
    async def test_api_host_info_returns_snapshot(self, tmp_path: Path):
        """/api/host-infoが現在の`host_info`スナップショットをJSONで返す。

        `host_info_update`のSSE購読前配信を取りこぼした場合の再取得経路が返す値の契約を検証する。
        """
        app = _app.create_app(tmp_path, hostname="test-host")
        state: _state.BroadcastState = app.config["PLANS_STATE"]
        # SSE購読前に配信されたためクライアントが受け取れなかった通知を模した状態を用意する。
        state.host_info["remote-host"] = {
            "root": "/home/alice/.claude/plans",
            "os_type": "posix",
            "os_name": "posix",
        }
        client = app.test_client()
        response = await client.get("/api/host-info")

        assert response.status_code == 200
        assert response.content_type == "application/json; charset=utf-8"
        data = json.loads(await response.get_data())
        assert data["test-host"] == {
            "root": str(tmp_path).replace("\\", "/"),
            "home": str(Path.home()).replace("\\", "/"),
            "os_type": os.name,
            "os_name": os.name,
        }
        assert data["remote-host"] == {
            "root": "/home/alice/.claude/plans",
            "os_type": "posix",
            "os_name": "posix",
        }

    @pytest.mark.asyncio
    async def test_index_embeds_copy_path_constants(self, tmp_path: Path):
        """/応答HTMLがパスコピー用のJS定数を埋め込む。

        `ROOT_DIRS`はページロード時点ではローカルホスト分のみを含む
        （リモート分はSSE経由の`host_info_update`受信、または`/api/host-info`への
        再取得で反映される）。
        """
        app = _app.create_app(tmp_path, hostname="test-host")
        client = app.test_client()
        response = await client.get("/")

        body = await response.get_data(as_text=True)
        expected_root = str(tmp_path).replace("\\", "/")
        assert response.status_code == 200
        assert f"const LOCAL_HOST_NAME = {json.dumps('test-host')};" in body
        expected_home = str(Path.home()).replace("\\", "/")
        expected_root_dirs = {
            "test-host": {"root": expected_root, "home": expected_home, "os_type": os.name, "os_name": os.name}
        }
        assert f"const ROOT_DIRS = {json.dumps(expected_root_dirs, ensure_ascii=False)};" in body

    @pytest.mark.asyncio
    async def test_api_file_renders_markdown(self, tmp_path: Path):
        """/api/fileがMarkdownをHTMLへ変換して返す。"""
        (tmp_path / "a.md").write_text("# title\n", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=a.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "<h1>title</h1>" in body

    @pytest.mark.asyncio
    async def test_api_file_missing_path_returns_400(self, tmp_path: Path):
        """/api/fileでpathパラメーターがなければ400を返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_api_file_not_found_returns_404(self, tmp_path: Path):
        """/api/fileで存在しないファイルを指すと404を返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=missing.md")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_api_raw_returns_markdown(self, tmp_path: Path):
        """/api/rawはMarkdown原文をtext/markdownで返す。"""
        body = "# title\n\n本文\n"
        (tmp_path / "a.md").write_text(body, encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/raw?path=a.md")

        assert response.status_code == 200
        assert response.content_type == "text/markdown; charset=utf-8"
        assert await response.get_data(as_text=True) == body

    @pytest.mark.asyncio
    async def test_api_raw_missing_path_returns_400(self, tmp_path: Path):
        """/api/rawでpathパラメーターがなければ400を返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/raw")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_api_raw_not_found_returns_404(self, tmp_path: Path):
        """/api/rawで存在しないファイルを指すと404を返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/raw?path=missing.md")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_static_markdown_css_served(self, tmp_path: Path):
        """/static/markdown.cssがCSSを返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/static/markdown.css")

        assert response.status_code == 200
        assert response.content_type.startswith("text/css")

    @pytest.mark.asyncio
    async def test_favicon_served(self, tmp_path: Path):
        """/favicon.svgがSVGを返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/favicon.svg")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert body.lstrip().startswith("<svg")

    @pytest.mark.asyncio
    async def test_manifest_served(self, tmp_path: Path):
        """/manifest.webmanifestがJSONを返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/manifest.webmanifest")

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "Claude plans"


class TestHostInfo:
    """`BroadcastState.host_info`のローカル登録と`deliver_host_info`のSSE配信契約を検証する。

    リモート分の登録・削除契約（snapshot受信時の登録、切断時のキー削除）は
    `pytools/claude_plans_viewer_remote_host_test.py`側で検証する。
    `GET /api/host-info`エンドポイントの契約は`TestApiEndpoints.test_api_host_info_returns_snapshot`側で
    検証する。本クラスはSSE配信契約のみを検証対象とする。
    """

    @pytest.mark.asyncio
    async def test_create_app_registers_local_host_info(self, tmp_path: Path):
        """`create_app`起動時にローカルホスト分の`host_info`が即座に登録される。"""
        app = _app.create_app(tmp_path, hostname="local-host")
        state: _state.BroadcastState = app.config["PLANS_STATE"]

        assert state.host_info["local-host"] == {
            "root": str(tmp_path).replace("\\", "/"),
            "home": str(Path.home()).replace("\\", "/"),
            "os_type": os.name,
            "os_name": os.name,
        }

    @pytest.mark.asyncio
    async def test_deliver_host_info_broadcasts_update(self):
        """`deliver_host_info`が`{"type":"host_info_update","host":...,"info":...}`を配信する。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            info = {"root": "/home/alice/.claude/plans", "os_type": "posix", "os_name": "posix"}
            await _state.deliver_host_info(state, "host1", info)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == json.dumps({"type": "host_info_update", "host": "host1", "info": info}, ensure_ascii=False)
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_deliver_host_info_none_signals_removal(self):
        """`info=None`は該当ホストキーの削除指示を意味するペイロードとして配信される。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            await _state.deliver_host_info(state, "host1", None)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == json.dumps({"type": "host_info_update", "host": "host1", "info": None}, ensure_ascii=False)
        finally:
            await _state.unsubscribe(state, q)


class TestEventsEndpoint:
    """`/api/events`エンドポイントの統合テスト。"""

    @pytest.mark.asyncio
    async def test_sse_stream_contract(self, tmp_path: Path):
        """接続時のContent-Type、配信フォーマット、debounce挙動を一連で検証する。

        ストリーミング応答のため`TestHTTPConnection.receive()`でチャンクを逐次読み取る。
        `schedule_broadcast`を2回連続で呼んでもdebounceで1件に畳まれることを確認する。
        """
        app = _app.create_app(tmp_path, hostname="test")
        # test_client経由の呼び出しでは`before_serving`が発火しないため、loop参照を手動注入する。
        state: _state.BroadcastState = app.config["PLANS_STATE"]
        state.loop = asyncio.get_running_loop()
        client = app.test_client()

        # client.request()の戻り値はProtocol型のためcastで実装クラスへキャストする。
        # QuartのTestHTTPConnectionは`__aexit__`の型注釈が`exc_type: type`固定のため、
        # 厳格な型検査(ty)では`async with`の実装として認識されない。ライブラリ側の型注釈の
        # 限界に起因するfalse positiveのためここでは`ty: ignore`で抑制する。
        raw_connection = client.request(path="/api/events", method="GET")
        conn = typing.cast(_TestHTTPConnection, raw_connection)
        async with conn:  # ty: ignore[invalid-context-manager]
            await conn.send_complete()
            # ヘッダ受信まで待機する。Quartのtest connectionはbodyが届くとheaderが確定する仕様のため、
            # サーバー側から初回チャンクが届くようbroadcastを事前に1回発行する。
            await _state.schedule_broadcast(state)
            # 直後にもう1回呼んで畳まれること（debounce）を同時に確認する。
            await _state.schedule_broadcast(state)

            # ストリーミングチャンクを逐次受信し、refreshのJSONペイロードを含むまで読み進める。
            expected_data_line = "data: " + _SSE_REFRESH_PAYLOAD
            body_text = ""
            try:
                while expected_data_line not in body_text:
                    chunk = await asyncio.wait_for(conn.receive(), timeout=_QUEUE_GET_TIMEOUT_SEC + 1.0)
                    body_text += chunk.decode("utf-8")
            finally:
                await conn.disconnect()

            assert conn.status_code == 200
            assert conn.headers is not None
            assert conn.headers.get("content-type") == "text/event-stream"

        # event名は付かない（`event: refresh`は含まれない）こと。
        assert "event: refresh" not in body_text
        # `data: {"type":"refresh"}`が現れ、SSEイベントの終端`\n\n`で区切られていること。
        assert re.search(re.escape(expected_data_line) + r"\r?\n\r?\n", body_text) is not None
        # debounce畳み込みのため、refreshペイロードは1回だけ含まれる。
        assert body_text.count(expected_data_line) == 1


class TestBroadcastStateDataclass:
    """`BroadcastState`のフィールド既定値の契約を固定する。"""

    def test_defaults(self):
        """新規状態の購読者は空、ループは未設定、debounceタスクは未起動、ホスト状態・host_infoは空。"""
        state = _state.BroadcastState()
        assert not state.subscribers
        assert state.debounce_task is None
        assert state.loop is None
        assert not state.remote_files
        assert not state.remote_tasks
        assert not state.host_status
        assert not state.remote_watchers
        assert not state.host_info
        # `dataclasses.fields`経由で契約を固定し、意図しないフィールド追加を検出する。
        fields = {f.name for f in dataclasses.fields(state)}
        assert fields == {
            "subscribers",
            "lock",
            "debounce_task",
            "loop",
            "remote_files",
            "remote_tasks",
            "host_status",
            "remote_watchers",
            "host_info",
        }
