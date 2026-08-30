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

from pytools import claude_plans_viewer_remote_test_helpers as _remote_test_helpers
from pytools.claude_plans_viewer import _app, _local, _remote, _state

# テスト用debounce短縮値。本番既定値(0.3秒)を検証する対象は
# TestEventsEndpoint.test_sse_stream_contract（アプリ生成経由の統合テスト）のみで、
# 本定数は`BroadcastState(debounce_sec=...)`による個別テストの短縮専用とする。
_TEST_DEBOUNCE_SEC = 0.02
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)
_QUEUE_GET_TIMEOUT_SEC = _TEST_DEBOUNCE_SEC + 0.3


async def _wait_until(predicate: typing.Callable[[], bool], timeout: float = 5.0) -> None:
    """条件が成立するまでイベントループを回す。制限時間内に成立しなければ失敗させる。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "条件が制限時間内に成立しなかった"
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def _isolate_creation_time_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """作成日時インデックスを一時領域へ隔離し、開発者環境の利用者キャッシュへ書き込まないようにする。"""
    monkeypatch.setattr(_local, "_CREATION_TIME_INDEX_PATH", tmp_path / "creation-times" / "index.json")


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
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        q = await _state.subscribe(state)
        try:
            await _state.schedule_broadcast(state)
            await _state.schedule_broadcast(state)
            assert state.debounce_task is not None
            await state.debounce_task
            assert q.qsize() == 1
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_many_calls(self):
        """`schedule_broadcast`を短時間に10回呼んでも、debounce窓満了後にキューは1件であること。"""
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        q = await _state.subscribe(state)
        try:
            for _ in range(10):
                await _state.schedule_broadcast(state)
            assert state.debounce_task is not None
            await state.debounce_task
            assert q.qsize() == 1
        finally:
            await _state.unsubscribe(state, q)


class TestWatchdogHandler:
    """PlansEventHandler のイベントフィルタリングテスト。"""

    @pytest.mark.asyncio
    async def test_md_event_broadcasts(self, tmp_path: Path):
        """.mdファイルの変更イベントで購読者へrefreshが届くこと（debounce経由で届く）。"""
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
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
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileOpenedEvent(str(md_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            # 除外イベントはschedule_broadcastへ到達せずdebounceタスクが生成されないため、
            # 待機なしで即座に判定できる（`_local.py`のon_any_eventが早期returnすることを確認済み）。
            assert state.debounce_task is None
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_closed_nowrite_event_ignored(self, tmp_path: Path):
        """FileClosedNoWriteEventでは購読者へ通知しないこと（feedback loopの起点を遮断する回帰テスト）。"""
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileClosedNoWriteEvent(str(md_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            # 除外イベントはschedule_broadcastへ到達せずdebounceタスクが生成されないため、
            # 待機なしで即座に判定できる（`_local.py`のon_any_eventが早期returnすることを確認済み）。
            assert state.debounce_task is None
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_moved_event_to_md_broadcasts(self, tmp_path: Path):
        """FileMovedEvent(src=*.md.tmp, dest=*.md)で購読者へ通知されること（atomic-write保存の回帰テスト）。"""
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
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
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
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
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            txt_file = tmp_path / "note.txt"
            txt_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(txt_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            # 除外イベントはschedule_broadcastへ到達せずdebounceタスクが生成されないため、
            # 待機なしで即座に判定できる（`_local.py`のon_any_eventが早期returnすることを確認済み）。
            assert state.debounce_task is None
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_dotdir_event_ignored(self, tmp_path: Path):
        """root配下のdotdir配下のイベントでは購読者へ通知しないこと。"""
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            cache_dir = tmp_path / ".cache"
            cache_dir.mkdir()
            md_file = cache_dir / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(md_file))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            # 除外イベントはschedule_broadcastへ到達せずdebounceタスクが生成されないため、
            # 待機なしで即座に判定できる（`_local.py`のon_any_eventが早期returnすることを確認済み）。
            assert state.debounce_task is None
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_directory_event_ignored(self, tmp_path: Path):
        """is_directory=Trueのイベントでは購読者へ通知しないこと。"""
        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            event = watchdog.events.DirModifiedEvent(str(tmp_path / "subdir"))
            _local.PlansEventHandler(tmp_path, state).on_any_event(event)
            # 除外イベントはschedule_broadcastへ到達せずdebounceタスクが生成されないため、
            # 待機なしで即座に判定できる（`_local.py`のon_any_eventが早期returnすることを確認済み）。
            assert state.debounce_task is None
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

        state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
        state.loop = asyncio.get_running_loop()
        q = await _state.subscribe(state)
        try:
            event = watchdog.events.FileModifiedEvent(str(md_file))
            _local.PlansEventHandler(dot_root, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == _SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_handler_agrees_with_target_path_helper(self, tmp_path: Path):
        """監視ハンドラーの通知可否が`_local.is_target_path`と一致すること。

        `root`直下・サブディレクトリ配下・隠しディレクトリ配下・隠しファイル・
        `root`自身が隠しディレクトリの5形態で照合する。
        """
        plain_root = tmp_path / "plans"
        dot_root = tmp_path / ".claude_like"
        cases = [
            (plain_root, plain_root / "top.md"),
            (plain_root, plain_root / "sub" / "nested.md"),
            (plain_root, plain_root / ".cache" / "hidden.md"),
            (plain_root, plain_root / ".hidden.md"),
            (dot_root, dot_root / "top.md"),
        ]
        for _, md_file in cases:
            md_file.parent.mkdir(parents=True, exist_ok=True)
            md_file.write_text("x", encoding="utf-8")

        for root, md_file in cases:
            state = _state.BroadcastState(debounce_sec=_TEST_DEBOUNCE_SEC)
            state.loop = asyncio.get_running_loop()
            q = await _state.subscribe(state)
            try:
                event = watchdog.events.FileModifiedEvent(str(md_file))
                _local.PlansEventHandler(root, state).on_any_event(event)
                # `on_any_event`は`run_coroutine_threadsafe`でループへ委譲するため、
                # コールバック実行とdebounceタスク生成の2段階が進むまでループへ制御を返す。
                for _ in range(3):
                    await asyncio.sleep(0)
                if state.debounce_task is not None:
                    await state.debounce_task
                notified = not q.empty()
                assert notified == _local.is_target_path(md_file, root), md_file
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
    async def test_multiple_roots_keep_same_relative_path_separate(self, tmp_path: Path):
        """同一hostの同名ファイルをsource IDで分離し、一覧・検索・読取へ伝搬する。"""
        new_root = tmp_path / "new"
        legacy_root = tmp_path / "legacy"
        new_root.mkdir()
        legacy_root.mkdir()
        (new_root / "same.md").write_text("新rootのneedle", encoding="utf-8")
        (legacy_root / "same.md").write_text("旧rootのneedle", encoding="utf-8")
        os.utime(new_root / "same.md", (1_000.0, 1_000.0))
        os.utime(legacy_root / "same.md", (2_000.0, 2_000.0))
        roots = (
            _state.RootSpec("new-source", new_root, "$(atk config get private_notes)/plans"),
            _state.RootSpec("legacy-source", legacy_root, "~/.claude/plans"),
        )
        app = _app.create_app(hostname="test-host", roots=roots)
        client = app.test_client()

        response = await client.get("/api/files")
        assert response.status_code == 200
        entries = json.loads(await response.get_data())
        assert [(entry["source_id"], entry["path"]) for entry in entries] == [
            ("legacy-source", "same.md"),
            ("new-source", "same.md"),
        ]

        ambiguous = await client.get("/api/file?path=same.md")
        assert ambiguous.status_code == 400
        assert await ambiguous.get_data(as_text=True) == "source is required"

        new_response = await client.get("/api/file?path=same.md&source=new-source")
        legacy_response = await client.get("/api/file?path=same.md&source=legacy-source")
        assert new_response.status_code == 200
        assert legacy_response.status_code == 200
        assert "新rootのneedle" in await new_response.get_data(as_text=True)
        assert "旧rootのneedle" in await legacy_response.get_data(as_text=True)

        search_response = await client.get("/api/search?q=needle")
        assert search_response.status_code == 200
        search_entries = json.loads(await search_response.get_data())
        assert {(entry["source_id"], entry["path"]) for entry in search_entries} == {
            ("new-source", "same.md"),
            ("legacy-source", "same.md"),
        }

        root_info_response = await client.get("/api/root-info")
        root_info = json.loads(await root_info_response.get_data())
        assert root_info["test-host"]["new-source"] == {
            "source_id": "new-source",
            "portable_root": "$(atk config get private_notes)/plans",
        }
        assert "root" not in root_info["test-host"]["new-source"]

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
        """`/api/file`がGFM相当のMarkdownをHTMLへ変換して返す。"""
        (tmp_path / "a.md").write_text(
            "# title\n\n"
            "通常 ~~取消~~ https://example.com www.example.com user@example.com\n\n"
            "[明示リンク](https://example.net) <https://example.org>\n\n"
            "| 列 |\n| --- |\n| 値 |\n",
            encoding="utf-8",
        )
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=a.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "<h1>title</h1>" in body
        assert "<s>取消</s>" in body
        assert '<a href="https://example.com">' not in body
        assert '<a href="http://www.example.com">' not in body
        assert '<a href="mailto:user@example.com">' not in body
        assert '<a href="https://example.net">明示リンク</a>' in body
        assert '<a href="https://example.org">https://example.org</a>' in body
        assert "<table>" in body

    @pytest.mark.asyncio
    async def test_api_file_includes_detail_link_when_local_detail_exists(self, tmp_path: Path):
        """detail側が実在するローカル計画の表示応答へ、detailを開くリンク要素を含める。"""
        (tmp_path / "a.md").write_text("# title\n", encoding="utf-8")
        (tmp_path / "a.detail.md").write_text("# detail\n", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=a.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert '<a href="#" data-plan-path="a.detail.md">実装詳細を開く</a>' in body

    @pytest.mark.asyncio
    async def test_api_file_omits_detail_link_when_local_detail_absent(self, tmp_path: Path):
        """detail側が無いローカル計画の表示応答にはリンク要素を含めない。"""
        (tmp_path / "a.md").write_text("# title\n", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=a.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "data-plan-path" not in body

    @pytest.mark.asyncio
    async def test_api_file_omits_detail_link_for_detail_itself(self, tmp_path: Path):
        """detail側を表示した応答は、自身へのリンクを含めない。"""
        (tmp_path / "a.detail.md").write_text("# detail\n", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=a.detail.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "data-plan-path" not in body

    @pytest.mark.asyncio
    async def test_api_file_navigates_symmetrically_between_attached_plans(self, tmp_path: Path):
        """base・detail・bugsが実在する場合、各ページを現在ページのテキストと他ページのリンクへ分ける。"""
        contents = {
            "a.md": "# base\n",
            "a.detail.md": "# detail\n",
            "a.bugs.md": "# bugs\n",
        }
        for path, content in contents.items():
            (tmp_path / path).write_text(content, encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()

        labels = {"a.md": "計画本体", "a.detail.md": "実装詳細", "a.bugs.md": "バグ調査"}
        for current, current_label in labels.items():
            response = await client.get(f"/api/file?path={current}")

            assert response.status_code == 200
            body = await response.get_data(as_text=True)
            assert current_label in body
            for target, target_label in labels.items():
                if target == current:
                    assert f'data-plan-path="{target}"' not in body
                else:
                    assert f'<a href="#" data-plan-path="{target}">{target_label}を開く</a>' in body

    @pytest.mark.asyncio
    @pytest.mark.parametrize("current", ["a.md", "a.detail.md", "a.bugs.md"])
    async def test_api_file_omits_attached_links_when_other_pages_are_absent(self, tmp_path: Path, current: str):
        """同じstemの他ページが無い場合、base・detail・bugsのいずれからもリンクを生成しない。"""
        (tmp_path / current).write_text("# only\n", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()

        response = await client.get(f"/api/file?path={current}")

        assert response.status_code == 200
        assert "data-plan-path" not in await response.get_data(as_text=True)

    @pytest.mark.asyncio
    async def test_api_file_escapes_attached_plan_path(self, tmp_path: Path):
        """付属計画へのdata属性はHTML属性値としてエスケープする。"""
        stem = "a&<x>"
        (tmp_path / f"{stem}.md").write_text("# base\n", encoding="utf-8")
        (tmp_path / f"{stem}.detail.md").write_text("# detail\n", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()

        response = await client.get("/api/file", query_string={"path": f"{stem}.md"})

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert 'data-plan-path="a&amp;&lt;x&gt;.detail.md"' in body

    @pytest.mark.asyncio
    async def test_detail_link_follows_detail_creation_despite_body_cache(self, tmp_path: Path):
        """本文HTMLがキャッシュ済みでも、detailの作成がリンク表示へ反映される。"""
        (tmp_path / "a.md").write_text("# title\n", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        first = await client.get("/api/file?path=a.md")
        (tmp_path / "a.detail.md").write_text("# detail\n", encoding="utf-8")
        second = await client.get("/api/file?path=a.md")

        assert "data-plan-path" not in await first.get_data(as_text=True)
        assert 'data-plan-path="a.detail.md"' in await second.get_data(as_text=True)

    @pytest.mark.asyncio
    async def test_api_file_renders_diagrams_without_raw_html(self, tmp_path: Path):
        """/api/fileがMermaid・SVG専用構造を返し、Raw HTMLを無効化する。"""
        (tmp_path / "a.md").write_text(
            "```mermaid\ngraph TD\n  A --> B\n```\n\n"
            '```svg\n<svg><rect width="10" height="10"/></svg>\n```\n\n'
            "<script>alert(1)</script>\n",
            encoding="utf-8",
        )
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=a.md")

        assert response.status_code == 200
        rendered = await response.get_data(as_text=True)
        assert "diagram-mermaid" in rendered
        assert "diagram-svg" in rendered
        assert "<script" not in rendered.lower()

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
    async def test_static_mermaid_bundle_served(self, tmp_path: Path):
        """/static/mermaid.min.jsが単一bundleを安全なJavaScript応答として返す。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/static/mermaid.min.js")

        assert response.status_code == 200
        assert response.content_type == "text/javascript; charset=utf-8"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert await response.get_data()

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


class TestAppStartup:
    """`create_app`が起動時に実施する後始末の契約を検証する。"""

    def test_cleans_up_creation_time_temporaries_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """前回の異常終了で残った作成日時インデックスの一時ファイル除去を1回だけ呼ぶ。"""
        calls: list[None] = []

        def _cleanup() -> None:
            calls.append(None)

        monkeypatch.setattr(_local, "cleanup_creation_time_temporaries", _cleanup)
        _app.create_app(tmp_path, hostname="test")

        assert len(calls) == 1


class TestApiSearchSupersession:
    """置き換えられたリモート検索要求に対する`/api/search`の応答契約を検証する。"""

    @pytest.mark.asyncio
    async def test_superseded_request_returns_conflict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """置き換えられた要求へは409を返し、別の検索語の結果を返さない。"""
        (tmp_path / "q1.md").write_text("q1", encoding="utf-8")
        (tmp_path / "q3.md").write_text("q3", encoding="utf-8")
        runner = _remote_test_helpers.BlockingSearchRunner()
        coordinator = _remote.RemoteSearchCoordinator()

        def _coordinator_factory(_limit: int) -> _remote.RemoteSearchCoordinator:
            return coordinator

        monkeypatch.setattr(_remote, "RemoteSearchCoordinator", _coordinator_factory)
        app = _app.create_app(tmp_path, hostname="local-host", remote_hosts=["host1"], ssh_runner=runner)
        client = app.test_client()

        first = asyncio.create_task(client.get("/api/search", query_string={"q": "q1"}))
        # 先行要求がSSHフォールバックを開始するまで待ち、以降の要求が待機列へ入る前提を整える。
        await _wait_until(lambda: bool(runner.started))
        second = asyncio.create_task(client.get("/api/search", query_string={"q": "q2"}))
        # 経過時間ではなく、制御譲渡後に検索要求が待機列へ到達したことを状態から確認する。
        await _remote_test_helpers.settle_event_loop()
        await _wait_until(lambda: "host1" in coordinator._pending)  # pylint: disable=protected-access  # noqa: SLF001  # 待機列到達の直接観測
        assert runner.started == [("host1", "q1")]
        second_pending = coordinator._pending["host1"]  # pylint: disable=protected-access  # noqa: SLF001  # 3件目による置き換えの観測基準
        third = asyncio.create_task(client.get("/api/search", query_string={"q": "q3"}))
        # 3件目が待機要求を置き換えた状態を観測し、SSH実行が増えていないことを確認する。
        await _remote_test_helpers.settle_event_loop()
        await _wait_until(lambda: coordinator._pending.get("host1") is not second_pending)  # pylint: disable=protected-access  # noqa: SLF001  # 待機要求の置き換えを直接観測
        assert runner.started == [("host1", "q1")]
        runner.release()
        responses = await asyncio.wait_for(asyncio.gather(first, second, third), timeout=5.0)

        assert [response.status_code for response in responses] == [200, 409, 200]
        assert await responses[1].get_data(as_text=True) == "search superseded"
        # 置き換えられた検索語のSSHは起動せず、実行された2件は自身の検索語の結果を返す。
        assert runner.started == [("host1", "q1"), ("host1", "q3")]
        assert [entry["path"] for entry in json.loads(await responses[0].get_data())] == ["q1.md"]
        assert [entry["path"] for entry in json.loads(await responses[2].get_data())] == ["q3.md"]


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
        # 限界に起因する誤検出のためここでは`ty: ignore`で抑制する。
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
        """新規状態の購読者は空、ループは未設定、debounceタスクは未起動、ホスト状態・root情報は空。"""
        state = _state.BroadcastState()
        assert not state.subscribers
        assert state.debounce_task is None
        assert state.loop is None
        assert not state.remote_files
        assert not state.remote_tasks
        assert not state.host_status
        assert not state.remote_watchers
        assert not state.host_info
        assert not state.root_info
        assert not state.root_status
        # `dataclasses.fields`経由で契約を固定し、意図しないフィールド追加を検出する。
        fields = {f.name for f in dataclasses.fields(state)}
        assert fields == {
            "subscribers",
            "lock",
            "debounce_task",
            "debounce_sec",
            "loop",
            "remote_files",
            "remote_tasks",
            "host_status",
            "remote_watchers",
            "host_info",
            "root_info",
            "root_status",
        }
        assert state.debounce_sec == _state._BROADCAST_DEBOUNCE_SEC  # pylint: disable=protected-access
