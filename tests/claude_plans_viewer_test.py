"""pytools.claude_plans_viewer のテスト。"""

# 本モジュールはプライベート関数（`_list_files`・`_resolve_under_root`・`_markdown_to_html`・
# `_resolve_css_path`・`_read_css`）や同モジュール内の定数を単体でテストするため、protected-accessを一括で許可する。
# pylint: disable=protected-access

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

from pytools import claude_plans_viewer

# `_schedule_broadcast`経由のrefresh待ちは`_BROADCAST_DEBOUNCE_SEC`後に配信されるため、
# debounce窓にマージン0.7秒を加えた値をタイムアウトとする。
_QUEUE_GET_TIMEOUT_SEC = claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.7


class TestListFiles:
    """_list_files のテスト。"""

    def test_sorts_by_mtime_desc(self, tmp_path: Path):
        """mtime降順で返ること。"""
        old_path = tmp_path / "old.md"
        old_path.write_text("old", encoding="utf-8")
        os.utime(old_path, (1_000.0, 1_000.0))

        new_path = tmp_path / "new.md"
        new_path.write_text("new", encoding="utf-8")
        os.utime(new_path, (2_000.0, 2_000.0))

        entries = claude_plans_viewer._list_files(tmp_path)

        assert [e.path for e in entries] == ["new.md", "old.md"]
        # mtimeは`yyyy/MM/dd HH:mm`書式で整形される。
        pattern = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
        for entry in entries:
            assert pattern.match(entry.mtime), entry.mtime
        # `_FileEntry`はサイズを保持しない。
        assert not hasattr(entries[0], "size")
        # `mtime_epoch`を保持すること（クライアント側mtime変化検知に使用）。
        assert hasattr(entries[0], "mtime_epoch")

    def test_includes_only_md(self, tmp_path: Path):
        """.md以外は含まず、サブディレクトリは再帰的に拾うこと。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.md").write_text("x", encoding="utf-8")

        entries = claude_plans_viewer._list_files(tmp_path)

        assert sorted(e.path for e in entries) == ["a.md", "sub/c.md"]


class TestResolveUnderRoot:
    """_resolve_under_root のテスト。"""

    def test_valid_md_path(self, tmp_path: Path):
        """root配下の.mdを正常に解決する。"""
        target_path = tmp_path / "a.md"
        target_path.write_text("x", encoding="utf-8")

        result = claude_plans_viewer._resolve_under_root(tmp_path, "a.md")

        assert result == target_path.resolve()

    @pytest.mark.parametrize("rel", ["../outside.md", "sub/../../outside.md"])
    def test_rejects_traversal(self, tmp_path: Path, rel: str):
        """root外へ出るパスはNoneを返す。"""
        # root外の実体を作っても相対参照で抜けられないことを確認する。
        outside = tmp_path.parent / "outside.md"
        outside.write_text("x", encoding="utf-8")
        try:
            assert claude_plans_viewer._resolve_under_root(tmp_path, rel) is None
        finally:
            outside.unlink()

    def test_rejects_non_md(self, tmp_path: Path):
        """拡張子が.md以外のファイルはNoneを返す。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")

        assert claude_plans_viewer._resolve_under_root(tmp_path, "a.txt") is None

    def test_rejects_missing(self, tmp_path: Path):
        """存在しないファイルはNoneを返す。"""
        assert claude_plans_viewer._resolve_under_root(tmp_path, "missing.md") is None


class TestMarkdownToHtml:
    """_markdown_to_html のテスト。"""

    def test_renders_basic_markdown(self):
        """見出し・コードブロック・表が反映される。"""
        src = "# title\n\n```\ncode\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"

        html = claude_plans_viewer._markdown_to_html(src)

        assert "<h1>title</h1>" in html
        assert "<pre><code>code\n</code></pre>" in html
        assert "<table>" in html
        assert "<th>a</th>" in html

    def test_escapes_raw_html(self):
        """raw HTMLタグは出力にそのまま現れず、エスケープされる。"""
        src = "# t\n\n<script>alert(1)</script>\n\n<img src=x onerror=y>\n"

        html = claude_plans_viewer._markdown_to_html(src)

        # 生タグが残らないこと（属性付きを含む広めの判定）
        assert "<script" not in html.lower()
        assert "<img" not in html.lower()
        # エスケープされた形で残ること
        assert "&lt;script&gt;" in html


class TestResolveCssPath:
    """_resolve_css_path のテスト。

    editable install前提でリポジトリ配下の`share/vscode/markdown.css`を返すことを確認する。
    本テストはdotfilesリポジトリ内で実行される前提で、配布CSSの所在を固定する。
    """

    def test_returns_repo_css(self):
        path = claude_plans_viewer._resolve_css_path()

        assert path is not None
        assert path.name == "markdown.css"
        assert path.is_file()
        assert path.parent.name == "vscode"
        assert path.parent.parent.name == "share"

    @pytest.mark.asyncio
    async def test_read_css_nonempty(self):
        """_read_cssがCSS本文を返す（空でない）。"""
        css = await claude_plans_viewer._read_css()

        assert css.strip()


class TestPwaAssets:
    """favicon・manifest・service workerのインライン定数の内容検査。"""

    def test_favicon_svg_root(self):
        """favicon定数がSVGルート要素で始まる。"""
        svg = claude_plans_viewer._FAVICON_SVG

        assert svg.lstrip().startswith("<svg")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg

    def test_manifest_json_has_required_keys(self):
        """manifest定数がJSONとしてパースでき、PWAの必須キーを持つ。"""
        manifest = json.loads(claude_plans_viewer._MANIFEST_JSON)

        assert manifest["name"] == "Claude plans"
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "/"
        # iconsはSVG1件で、192x192と512x512を同時に宣言してChromiumのインストール要件を満たす。
        assert len(manifest["icons"]) == 1
        icon = manifest["icons"][0]
        assert icon["src"] == "/favicon.svg"
        assert icon["type"] == "image/svg+xml"
        assert "192x192" in icon["sizes"]
        assert "512x512" in icon["sizes"]

    def test_service_worker_registers_fetch(self):
        """service worker定数がfetchリスナーを登録する（インストール可能性判定のための最小要件）。"""
        sw_js = claude_plans_viewer._SERVICE_WORKER_JS

        assert 'addEventListener("fetch"' in sw_js


class TestParseArgs:
    """_parse_args の環境変数フォールバック検証。

    「CLI引数 > 環境変数 > 組み込み既定値」の優先順位を固定するため、
    monkeypatch で環境変数を明示的に設定/解除した上で解決結果を検査する。
    """

    def test_defaults_when_env_unset(self, monkeypatch: pytest.MonkeyPatch):
        """環境変数未設定時は組み込み既定値を採用する。"""
        monkeypatch.delenv(claude_plans_viewer._ENV_ROOT, raising=False)
        monkeypatch.delenv(claude_plans_viewer._ENV_HOST, raising=False)
        monkeypatch.delenv(claude_plans_viewer._ENV_PORT, raising=False)

        args = claude_plans_viewer._parse_args([])

        assert args.root == claude_plans_viewer._DEFAULT_ROOT
        assert args.host == claude_plans_viewer._DEFAULT_HOST
        assert args.port == claude_plans_viewer._DEFAULT_PORT

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        """環境変数が設定されていればそれを既定値として使う。"""
        monkeypatch.setenv(claude_plans_viewer._ENV_ROOT, "/tmp/plans-env")
        monkeypatch.setenv(claude_plans_viewer._ENV_HOST, "0.0.0.0")  # noqa: S104
        monkeypatch.setenv(claude_plans_viewer._ENV_PORT, "12345")

        args = claude_plans_viewer._parse_args([])

        assert args.root == "/tmp/plans-env"
        assert args.host == "0.0.0.0"  # noqa: S104
        assert args.port == 12345

    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch):
        """CLI引数は環境変数より優先する。"""
        monkeypatch.setenv(claude_plans_viewer._ENV_ROOT, "/tmp/plans-env")
        monkeypatch.setenv(claude_plans_viewer._ENV_HOST, "0.0.0.0")  # noqa: S104
        monkeypatch.setenv(claude_plans_viewer._ENV_PORT, "12345")

        args = claude_plans_viewer._parse_args(["--root", "/tmp/plans-cli", "--host", "127.0.0.1", "--port", "54321"])

        assert args.root == "/tmp/plans-cli"
        assert args.host == "127.0.0.1"
        assert args.port == 54321


class TestIndexHtml:
    """`/`応答HTMLへのホスト名埋め込みを検証する。

    実ホスト名ではなくcreate_appへ明示指定した値を埋め込むことで、
    環境依存を排除しつつエスケープ挙動も同時に検査する。
    """

    @pytest.mark.asyncio
    async def test_index_html_contains_escaped_hostname(self, tmp_path: Path):
        """`/`応答にホスト名がエスケープ済みで含まれる。"""
        hostname = 'host<&"test'
        app = claude_plans_viewer.create_app(tmp_path, hostname=hostname)
        client = app.test_client()
        response = await client.get("/")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # 生のホスト名文字列は含まれない（エスケープされている）こと。
        assert hostname not in body
        # エスケープ済みの形で含まれること。
        assert "host&lt;&amp;&quot;test" in body


class TestSubscribers:
    """購読者管理(`_subscribe`・`_unsubscribe`・`_schedule_broadcast`)のテスト。"""

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_roundtrip(self):
        """_subscribeで登録し_unsubscribeで解除できること。重複解除もエラーにならないこと。"""
        state = claude_plans_viewer._BroadcastState()
        q = await claude_plans_viewer._subscribe(state)
        assert q in state.subscribers
        await claude_plans_viewer._unsubscribe(state, q)
        assert q not in state.subscribers
        # 重複解除してもエラーにならない
        await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_delivers_refresh(self):
        """`_schedule_broadcast`後にキューから"refresh"が取得できること（debounce経由で届く）。"""
        state = claude_plans_viewer._BroadcastState()
        q = await claude_plans_viewer._subscribe(state)
        try:
            await claude_plans_viewer._schedule_broadcast(state)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == "refresh"
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_coalesces_via_debounce(self):
        """`_schedule_broadcast`を連続で呼んでもdebounce窓内は1件にまとめられること。"""
        state = claude_plans_viewer._BroadcastState()
        q = await claude_plans_viewer._subscribe(state)
        try:
            await claude_plans_viewer._schedule_broadcast(state)
            await claude_plans_viewer._schedule_broadcast(state)
            await asyncio.sleep(claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.qsize() == 1
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_many_calls(self):
        """`_schedule_broadcast`を短時間に10回呼んでも、debounce窓満了後にキューは1件であること。"""
        state = claude_plans_viewer._BroadcastState()
        q = await claude_plans_viewer._subscribe(state)
        try:
            for _ in range(10):
                await claude_plans_viewer._schedule_broadcast(state)
            await asyncio.sleep(claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.qsize() == 1
        finally:
            await claude_plans_viewer._unsubscribe(state, q)


class TestWatchdogHandler:
    """_PlansEventHandler のイベントフィルタリングテスト。"""

    @pytest.mark.asyncio
    async def test_md_event_broadcasts(self, tmp_path: Path):
        """.mdファイルの変更イベントで購読者へrefreshが届くこと（debounce経由で届く）。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == "refresh"
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_opened_event_ignored(self, tmp_path: Path):
        """FileOpenedEventでは購読者へ通知しないこと（feedback loopの起点を遮断する回帰テスト）。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileOpenedEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            # debounce窓より長く待ってもキューに入らないこと
            await asyncio.sleep(claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_closed_nowrite_event_ignored(self, tmp_path: Path):
        """FileClosedNoWriteEventでは購読者へ通知しないこと（feedback loopの起点を遮断する回帰テスト）。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileClosedNoWriteEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_moved_event_to_md_broadcasts(self, tmp_path: Path):
        """FileMovedEvent(src=*.md.tmp, dest=*.md)で購読者へ通知されること（atomic-write保存の回帰テスト）。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            event = watchdog.events.FileMovedEvent(
                src_path=str(tmp_path / "x.md.tmp"),
                dest_path=str(tmp_path / "x.md"),
            )
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == "refresh"
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_file_moved_event_from_md_broadcasts(self, tmp_path: Path):
        """FileMovedEvent(src=*.md, dest=*.md)で購読者へ通知されること（rename・移動操作の検出）。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            event = watchdog.events.FileMovedEvent(
                src_path=str(tmp_path / "x.md"),
                dest_path=str(tmp_path / "y.md"),
            )
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == "refresh"
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_non_md_event_ignored(self, tmp_path: Path):
        """.md以外のファイルイベントでは購読者へ通知しないこと。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            txt_file = tmp_path / "note.txt"
            txt_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(txt_file))
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_dotdir_event_ignored(self, tmp_path: Path):
        """root配下のdotdir配下のイベントでは購読者へ通知しないこと。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            cache_dir = tmp_path / ".cache"
            cache_dir.mkdir()
            md_file = cache_dir / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_directory_event_ignored(self, tmp_path: Path):
        """is_directory=Trueのイベントでは購読者へ通知しないこと。"""
        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            event = watchdog.events.DirModifiedEvent(str(tmp_path / "subdir"))
            claude_plans_viewer._PlansEventHandler(tmp_path, state).on_any_event(event)
            await asyncio.sleep(claude_plans_viewer._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.empty()
        finally:
            await claude_plans_viewer._unsubscribe(state, q)

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

        state = claude_plans_viewer._BroadcastState()
        state.loop = asyncio.get_running_loop()
        q = await claude_plans_viewer._subscribe(state)
        try:
            event = watchdog.events.FileModifiedEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(dot_root, state).on_any_event(event)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == "refresh"
        finally:
            await claude_plans_viewer._unsubscribe(state, q)


class TestApiEndpoints:
    """Quartアプリの各種APIエンドポイントのスモーク。"""

    @pytest.mark.asyncio
    async def test_api_files_returns_list(self, tmp_path: Path):
        """/api/filesが.mdの一覧をJSONで返す。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/files")

        assert response.status_code == 200
        assert response.content_type == "application/json; charset=utf-8"
        data = json.loads(await response.get_data())
        assert [e["path"] for e in data] == ["a.md"]

    @pytest.mark.asyncio
    async def test_api_file_renders_markdown(self, tmp_path: Path):
        """/api/fileがMarkdownをHTMLへ変換して返す。"""
        (tmp_path / "a.md").write_text("# title\n", encoding="utf-8")
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=a.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "<h1>title</h1>" in body

    @pytest.mark.asyncio
    async def test_api_file_missing_path_returns_400(self, tmp_path: Path):
        """/api/fileでpathパラメーターがなければ400を返す。"""
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_api_file_not_found_returns_404(self, tmp_path: Path):
        """/api/fileで存在しないファイルを指すと404を返す。"""
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/api/file?path=missing.md")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_static_markdown_css_served(self, tmp_path: Path):
        """/static/markdown.cssがCSSを返す。"""
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/static/markdown.css")

        assert response.status_code == 200
        assert response.content_type.startswith("text/css")

    @pytest.mark.asyncio
    async def test_favicon_served(self, tmp_path: Path):
        """/favicon.svgがSVGを返す。"""
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/favicon.svg")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert body.lstrip().startswith("<svg")

    @pytest.mark.asyncio
    async def test_manifest_served(self, tmp_path: Path):
        """/manifest.webmanifestがJSONを返す。"""
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/manifest.webmanifest")

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert data["name"] == "Claude plans"


class TestEventsEndpoint:
    """`/api/events`エンドポイントの統合テスト。"""

    @pytest.mark.asyncio
    async def test_sse_stream_contract(self, tmp_path: Path):
        """接続時のContent-Type、配信フォーマット、debounce挙動を一連で検証する。

        ストリーミング応答のため`TestHTTPConnection.receive()`でチャンクを逐次読み取る。
        `_schedule_broadcast`を2回連続で呼んでもdebounceで1件に畳まれることを確認する。
        """
        app = claude_plans_viewer.create_app(tmp_path, hostname="test")
        # test_client経由の呼び出しでは`before_serving`が発火しないため、loop参照を手動注入する。
        state: claude_plans_viewer._BroadcastState = app.config["PLANS_STATE"]
        state.loop = asyncio.get_running_loop()
        client = app.test_client()

        # client.request()の戻り値はProtocol型のためcastで実装クラスへ寄せる。
        # QuartのTestHTTPConnectionは`__aexit__`の型注釈が`exc_type: type`固定のため、
        # 厳格な型検査(ty)では`async with`の実装として認識されない。ライブラリ側の型注釈の
        # 限界に起因するfalse positiveのためここでは`ty: ignore`で抑制する。
        raw_connection = client.request(path="/api/events", method="GET")
        conn = typing.cast(_TestHTTPConnection, raw_connection)
        async with conn:  # ty: ignore[invalid-context-manager]
            await conn.send_complete()
            # ヘッダ受信まで待機する。Quartのtest connectionはbodyが来るとheaderが確定する仕様のため、
            # サーバー側から初回チャンクが来るようbroadcastを事前に1回仕込む。
            await claude_plans_viewer._schedule_broadcast(state)
            # 直後にもう1回呼んで畳まれること（debounce）を同時に確認する。
            await claude_plans_viewer._schedule_broadcast(state)

            # ストリーミングチャンクを逐次受信し、`data: refresh`を含むまで読み進める。
            body_text = ""
            try:
                while "data: refresh" not in body_text:
                    chunk = await asyncio.wait_for(conn.receive(), timeout=_QUEUE_GET_TIMEOUT_SEC + 1.0)
                    body_text += chunk.decode("utf-8")
            finally:
                await conn.disconnect()

            assert conn.status_code == 200
            assert conn.headers is not None
            assert conn.headers.get("content-type") == "text/event-stream"

        # event名は付かない（`event: refresh`は含まれない）こと。
        assert "event: refresh" not in body_text
        # `data: refresh`が現れ、SSEイベントの終端`\n\n`で区切られていること。
        assert re.search(r"data: refresh\r?\n\r?\n", body_text) is not None
        # debounce畳み込みのため、`data: refresh`は1回だけ含まれる。
        assert body_text.count("data: refresh") == 1


class TestBroadcastStateDataclass:
    """`_BroadcastState`のフィールド既定値の契約を固定する。"""

    def test_defaults(self):
        """新規状態の購読者は空、ループは未設定、debounceタスクは未起動。"""
        state = claude_plans_viewer._BroadcastState()
        assert state.subscribers == set()
        assert state.debounce_task is None
        assert state.loop is None
        # `dataclasses.fields`経由で契約を固定し、意図しないフィールド追加を検出する。
        fields = {f.name for f in dataclasses.fields(state)}
        assert fields == {"subscribers", "lock", "debounce_task", "loop"}
