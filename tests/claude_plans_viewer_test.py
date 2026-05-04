"""pytools.claude_plans_viewer のテスト。"""

# 本モジュールはモジュール内部の関数・定数を単体テスト対象とするため、protected-accessを一括で許可する。
# 単一テスト対象モジュールに対する全テストを集約するため行数制限も緩和する。
# pylint: disable=protected-access,too-many-lines

import asyncio
import base64
import dataclasses
import json
import os
import re
import typing
from pathlib import Path

import pytest
import watchdog.events
from quart.testing.connections import TestHTTPConnection as _TestHTTPConnection

from pytools.claude_plans_viewer import _app, _assets, _cli, _local, _remote, _state

# `schedule_broadcast`経由のrefresh待ちは`_BROADCAST_DEBOUNCE_SEC`後に配信されるため、
# debounce窓にマージン0.7秒を加えた値をタイムアウトとする。
_QUEUE_GET_TIMEOUT_SEC = _state._BROADCAST_DEBOUNCE_SEC + 0.7


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

        entries = _local.list_files(tmp_path, "local-host")

        assert [e.path for e in entries] == ["new.md", "old.md"]
        # mtimeは`yyyy/MM/dd HH:mm`書式で整形される。
        pattern = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
        for entry in entries:
            assert pattern.match(entry.mtime), entry.mtime
        # `_FileEntry`はサイズを保持しない。
        assert not hasattr(entries[0], "size")
        # `mtime_epoch`・`host`を保持すること（クライアント側mtime変化検知・多ホスト識別に使用）。
        assert hasattr(entries[0], "mtime_epoch")
        assert all(e.host == "local-host" for e in entries)

    def test_includes_only_md(self, tmp_path: Path):
        """.md以外は含まず、サブディレクトリは再帰的に拾うこと。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.md").write_text("x", encoding="utf-8")

        entries = _local.list_files(tmp_path, "local-host")

        assert sorted(e.path for e in entries) == ["a.md", "sub/c.md"]


class TestResolveUnderRoot:
    """_resolve_under_root のテスト。"""

    def test_valid_md_path(self, tmp_path: Path):
        """root配下の.mdを正常に解決する。"""
        target_path = tmp_path / "a.md"
        target_path.write_text("x", encoding="utf-8")

        result = _local.resolve_under_root(tmp_path, "a.md")

        assert result == target_path.resolve()

    @pytest.mark.parametrize("rel", ["../outside.md", "sub/../../outside.md"])
    def test_rejects_traversal(self, tmp_path: Path, rel: str):
        """root外へ出るパスはNoneを返す。"""
        # root外の実体を作っても相対参照で抜けられないことを確認する。
        outside = tmp_path.parent / "outside.md"
        outside.write_text("x", encoding="utf-8")
        try:
            assert _local.resolve_under_root(tmp_path, rel) is None
        finally:
            outside.unlink()

    def test_rejects_non_md(self, tmp_path: Path):
        """拡張子が.md以外のファイルはNoneを返す。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")

        assert _local.resolve_under_root(tmp_path, "a.txt") is None

    def test_rejects_missing(self, tmp_path: Path):
        """存在しないファイルはNoneを返す。"""
        assert _local.resolve_under_root(tmp_path, "missing.md") is None


class TestMarkdownToHtml:
    """_markdown_to_html のテスト。"""

    def test_renders_basic_markdown(self):
        """見出し・コードブロック・表が反映される。"""
        src = "# title\n\n```\ncode\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"

        html = _local.markdown_to_html(src)

        assert "<h1>title</h1>" in html
        assert "<pre><code>code\n</code></pre>" in html
        assert "<table>" in html
        assert "<th>a</th>" in html

    def test_escapes_raw_html(self):
        """raw HTMLタグは出力にそのまま現れず、エスケープされる。"""
        src = "# t\n\n<script>alert(1)</script>\n\n<img src=x onerror=y>\n"

        html = _local.markdown_to_html(src)

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
        path = _local.resolve_css_path()

        assert path is not None
        assert path.name == "markdown.css"
        assert path.is_file()
        assert path.parent.name == "vscode"
        assert path.parent.parent.name == "share"

    @pytest.mark.asyncio
    async def test_read_css_nonempty(self):
        """_read_cssがCSS本文を返す（空でない）。"""
        css = await _local.read_css()

        assert css.strip()


class TestPwaAssets:
    """favicon・manifest・service workerのインライン定数の内容検査。"""

    def test_favicon_svg_root(self):
        """favicon定数がSVGルート要素で始まる。"""
        svg = _assets.FAVICON_SVG

        assert svg.lstrip().startswith("<svg")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg

    def test_manifest_build_has_required_keys(self):
        """build_manifestがPWAの必須キーを持つ辞書を返し、JSONとして直列化可能。"""
        manifest = _assets.build_manifest("")
        # 直列化可能であることを別途確認しておく（型違反ではjson.dumpsが落ちるため）。
        # 型推論に依存せず、JSON文字列に戻して再パースした側で構造比較する。
        round_tripped = json.loads(json.dumps(manifest))

        assert round_tripped["name"] == "Claude plans"
        assert round_tripped["display"] == "standalone"
        assert round_tripped["start_url"] == "/"
        # iconsはSVG1件で、192x192と512x512を同時に宣言してChromiumのインストール要件を満たす。
        icons = round_tripped["icons"]
        assert len(icons) == 1
        icon = icons[0]
        assert icon["src"] == "/favicon.svg"
        assert icon["type"] == "image/svg+xml"
        assert "192x192" in icon["sizes"]
        assert "512x512" in icon["sizes"]

    def test_manifest_build_with_base_path_prefixes_urls(self):
        """base_pathが与えられたmanifestはstart_url・icons.srcの双方に反映される。"""
        round_tripped = json.loads(json.dumps(_assets.build_manifest("/plans")))
        assert round_tripped["start_url"] == "/plans/"
        assert round_tripped["icons"][0]["src"] == "/plans/favicon.svg"

    def test_service_worker_contract(self):
        """service worker定数がinstall・activateを登録し、fetchリスナーは持たないこと。

        Chrome 93以降はno-opのfetchハンドラをDevToolsで警告対象とするため、
        意図的にfetchリスナーを登録せず、install／activateのみでPWAインストール可能性を満たす。
        """
        sw_js = _assets.SERVICE_WORKER_JS

        assert 'addEventListener("install"' in sw_js
        assert 'addEventListener("activate"' in sw_js
        assert 'addEventListener("fetch"' not in sw_js


class TestParseArgs:
    """_parse_args の環境変数フォールバック検証。

    「CLI引数 > 環境変数 > 組み込み既定値」の優先順位を固定するため、
    monkeypatch で環境変数を明示的に設定/解除した上で解決結果を検査する。
    """

    def test_defaults_when_env_unset(self, monkeypatch: pytest.MonkeyPatch):
        """環境変数未設定時は組み込み既定値を採用する。"""
        monkeypatch.delenv(_cli.ENV_ROOT, raising=False)
        monkeypatch.delenv(_cli.ENV_HOST, raising=False)
        monkeypatch.delenv(_cli.ENV_PORT, raising=False)
        monkeypatch.delenv(_cli.ENV_REMOTE_HOSTS, raising=False)

        args = _cli.parse_args([])

        assert args.root == _cli.DEFAULT_ROOT
        assert args.host == _cli.DEFAULT_HOST
        assert args.port == _cli.DEFAULT_PORT
        assert args.remote_host == []

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        """環境変数が設定されていればそれを既定値として使う。"""
        monkeypatch.setenv(_cli.ENV_ROOT, "/tmp/plans-env")
        monkeypatch.setenv(_cli.ENV_HOST, "0.0.0.0")  # noqa: S104
        monkeypatch.setenv(_cli.ENV_PORT, "12345")
        monkeypatch.setenv(_cli.ENV_REMOTE_HOSTS, "host1:user@host2")

        args = _cli.parse_args([])

        assert args.root == "/tmp/plans-env"
        assert args.host == "0.0.0.0"  # noqa: S104
        assert args.port == 12345
        assert args.remote_host == ["host1", "user@host2"]

    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch):
        """CLI引数は環境変数より優先する。"""
        monkeypatch.setenv(_cli.ENV_ROOT, "/tmp/plans-env")
        monkeypatch.setenv(_cli.ENV_HOST, "0.0.0.0")  # noqa: S104
        monkeypatch.setenv(_cli.ENV_PORT, "12345")
        monkeypatch.setenv(_cli.ENV_REMOTE_HOSTS, "envhost")

        args = _cli.parse_args(
            [
                "--root",
                "/tmp/plans-cli",
                "--host",
                "127.0.0.1",
                "--port",
                "54321",
                "--remote-host",
                "cli1",
                "--remote-host",
                "cli2",
            ]
        )

        assert args.root == "/tmp/plans-cli"
        assert args.host == "127.0.0.1"
        assert args.port == 54321
        assert args.remote_host == ["cli1", "cli2"]


class TestIndexHtml:
    """`/`応答HTMLへのホスト名埋め込みを検証する。

    実ホスト名ではなくcreate_appへ明示指定した値を埋め込むことで、
    環境依存を排除しつつエスケープ挙動も同時に検査する。
    """

    @pytest.mark.asyncio
    async def test_index_html_contains_escaped_hostname(self, tmp_path: Path):
        """`/`応答にホスト名がエスケープ済みで含まれる。"""
        hostname = 'host<&"test'
        app = _app.create_app(tmp_path, hostname=hostname)
        client = app.test_client()
        response = await client.get("/")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        # 生のホスト名文字列は含まれない（エスケープされている）こと。
        assert hostname not in body
        # エスケープ済みの形で含まれること。
        assert "host&lt;&amp;&quot;test" in body

    def test_index_html_handles_pagehide_and_pageshow(self):
        """クライアントJSがpagehideでEventSource.close()を呼び、pageshowのbfcache復帰時に再接続する。

        SSE切断時のERR_INCOMPLETE_CHUNKED_ENCODING抑制と
        bfcache復帰後の自動反映継続を両立するための契約。
        """
        html_src = _assets.INDEX_HTML

        # pagehideでEventSourceをcloseする
        assert 'addEventListener("pagehide"' in html_src
        assert ".close()" in html_src
        # pageshowのevent.persisted=true（bfcache復帰）で再接続する
        assert 'addEventListener("pageshow"' in html_src
        assert "event.persisted" in html_src

    def test_index_html_resyncs_on_eventsource_open(self):
        """EventSourceの`onopen`で初回／再接続のいずれもファイル一覧と接続状態を強制再取得する。

        ブラウザの自動再接続中に発生したSSEイベントが取り逃される構造的な問題を解消する契約。
        host-status経路は取りこぼし可能性があるためonopen時の`refreshHostStatus`で救済する。
        """
        html_src = _assets.INDEX_HTML

        # `onopen`・`onmessage`の両ハンドラが設定されていること。
        assert "es.onopen" in html_src
        assert "es.onmessage" in html_src
        # 再同期の実体は`refreshFiles`を呼ぶ`resyncFromServer`に集約されていること。
        assert "function resyncFromServer" in html_src or "async function resyncFromServer" in html_src
        # onopenではホスト状態とファイル一覧の両方を再同期する。
        assert "refreshHostStatus" in html_src
        # onmessageはJSONパース結果のtypeで分岐する`handleSseMessage`に集約されていること。
        assert "handleSseMessage" in html_src

    def test_index_html_handles_host_status_badge(self):
        """サイドペインのホスト名横に控えめな接続状態バッジを描画する契約。

        Connecting → 「再接続中」、Disconnected → 「切断中」、Connected → 非表示。
        SSE取りこぼし対策として`/api/host-status`を初回／再接続時に再取得する。
        """
        html_src = _assets.INDEX_HTML

        # CSS: `.host-badge`の既定は非表示、状態クラス付与で表示。
        assert ".host-badge {" in html_src
        assert ".host-badge.connecting" in html_src
        assert ".host-badge.disconnected" in html_src
        # JSラベルが定数として用意されている。
        assert "再接続中" in html_src
        assert "切断中" in html_src
        # `/api/host-status`を呼んでhostStatusを取得する関数がある。
        assert "/api/host-status" in html_src
        assert "hostStatus" in html_src
        # SSEのtype=host-statusを受けた際の分岐がある。
        assert "host-status" in html_src

    def test_index_html_has_copy_button_contract(self):
        """右ペインのsticky toolbarにコピーボタンが存在し、`/api/raw`をクリップボードへ書き込む。

        生Markdownをエディタへ貼り付けるためのスモーク。
        secure context（HTTPSまたはhttp://localhost）での動作前提。
        """
        html_src = _assets.INDEX_HTML

        # toolbarがmain側にも置かれること（既存のaside側のtoolbarに加えて）。
        assert html_src.count('class="toolbar"') >= 2
        # ボタン要素のid指定。
        assert 'id="copy-btn"' in html_src
        # clickハンドラが`/api/raw`からfetchして`navigator.clipboard.writeText`へ渡す。
        # 多ホスト統合のため`host`と`path`の両クエリを組み立てる`fileQuery`を経由する。
        assert "/api/raw?" in html_src
        assert "navigator.clipboard.writeText" in html_src
        assert "function fileQuery" in html_src
        # 成否のフィードバックはボタン文言の一時的な書き換えで示す。
        assert "コピーしました" in html_src
        assert "コピーに失敗しました" in html_src

    def test_index_html_renders_host_and_mtime_in_meta(self):
        """左ペインのmetaが左にホスト名、右にmtimeを並べる。

        多ホスト統合表示で、行内のホスト識別と更新日時の視認性を担保する契約。
        """
        html_src = _assets.INDEX_HTML

        # `.meta`は`display: flex; justify-content: space-between`で左右分割される。
        assert ".meta {" in html_src
        meta_block = html_src.split(".meta {", 1)[1].split("}", 1)[0]
        assert "display: flex" in meta_block
        assert "justify-content: space-between" in meta_block
        # 行内に`host`と`mtime`の2つのspanが描画される。
        assert 'className = "meta"' in html_src or 'class="meta"' in html_src
        assert 'hostSpan.className = "host"' in html_src
        assert 'mtimeSpan.className = "mtime"' in html_src

    def test_index_html_has_mobile_drawer_contract(self):
        """モバイル幅（768px以下）で左ペインをドロワー化する契約。

        ハンバーガーボタン・ドロワーbackdrop・モバイル専用メタブロックが要素として存在し、
        メディアクエリで切替されること。
        """
        html_src = _assets.INDEX_HTML

        # 768pxメディアクエリでドロワー化する。
        assert "@media (max-width: 768px)" in html_src
        # ハンバーガーボタン・backdrop・モバイル専用メタブロックの存在。
        assert 'id="menu-btn"' in html_src
        assert 'id="drawer-backdrop"' in html_src
        assert 'id="meta-mobile"' in html_src
        # ドロワー開閉はasideに`open`クラスを付与して制御する。
        assert 'classList.toggle("open"' in html_src

    def test_index_html_has_nav_buttons_contract(self):
        """↑↓ナビゲーションボタンが存在し、活性/非活性をJSで制御する契約。

        フィルタや選択変更に追従して活性状態を再評価し、リスト先頭/末尾で非活性にする。
        """
        html_src = _assets.INDEX_HTML

        # ボタン要素のid指定。
        assert 'id="prev-btn"' in html_src
        assert 'id="next-btn"' in html_src
        # disabled制御を行う関数があり、prev/nextの両方を更新する。
        assert "function updateNavButtons" in html_src
        assert "prevBtn.disabled" in html_src
        assert "nextBtn.disabled" in html_src
        # 活性状態の再評価はrenderFiles末尾でも行う（filter変更に追従するため）。
        # 現在描画リストはvisibleFilesに保持される。
        assert "visibleFiles" in html_src

    def test_index_html_toolbar_does_not_stick(self):
        """右ペインのコピーボタンバーがstickyで上部に固定されない契約。

        モバイル/デスクトップともに本文と一緒にスクロールする。
        """
        html_src = _assets.INDEX_HTML

        # `main .toolbar`定義ブロックを抽出し、`position: sticky`が含まれないこと。
        assert "main .toolbar {" in html_src
        toolbar_block = html_src.split("main .toolbar {", 1)[1].split("}", 1)[0]
        assert "position: sticky" not in toolbar_block


class TestSubscribers:
    """購読者管理(`_subscribe`・`_unsubscribe`・`_schedule_broadcast`)のテスト。"""

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_roundtrip(self):
        """_subscribeで登録し_unsubscribeで解除できること。重複解除もエラーにならないこと。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        assert q in state.subscribers
        await _state.unsubscribe(state, q)
        assert q not in state.subscribers
        # 重複解除してもエラーにならない
        await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_delivers_refresh(self):
        """`_schedule_broadcast`後にキューから"refresh"が取得できること（debounce経由で届く）。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            await _state.schedule_broadcast(state)
            msg = await asyncio.wait_for(q.get(), timeout=_QUEUE_GET_TIMEOUT_SEC)
            assert msg == _state._SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_coalesces_via_debounce(self):
        """`_schedule_broadcast`を連続で呼んでもdebounce窓内は1件にまとめられること。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            await _state.schedule_broadcast(state)
            await _state.schedule_broadcast(state)
            await asyncio.sleep(_state._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.qsize() == 1
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_schedule_broadcast_many_calls(self):
        """`_schedule_broadcast`を短時間に10回呼んでも、debounce窓満了後にキューは1件であること。"""
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            for _ in range(10):
                await _state.schedule_broadcast(state)
            await asyncio.sleep(_state._BROADCAST_DEBOUNCE_SEC + 0.2)
            assert q.qsize() == 1
        finally:
            await _state.unsubscribe(state, q)


class TestWatchdogHandler:
    """_PlansEventHandler のイベントフィルタリングテスト。"""

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
            assert msg == _state._SSE_REFRESH_PAYLOAD
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
            await asyncio.sleep(_state._BROADCAST_DEBOUNCE_SEC + 0.2)
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
            await asyncio.sleep(_state._BROADCAST_DEBOUNCE_SEC + 0.2)
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
            assert msg == _state._SSE_REFRESH_PAYLOAD
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
            assert msg == _state._SSE_REFRESH_PAYLOAD
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
            await asyncio.sleep(_state._BROADCAST_DEBOUNCE_SEC + 0.2)
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
            await asyncio.sleep(_state._BROADCAST_DEBOUNCE_SEC + 0.2)
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
            await asyncio.sleep(_state._BROADCAST_DEBOUNCE_SEC + 0.2)
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
            assert msg == _state._SSE_REFRESH_PAYLOAD
        finally:
            await _state.unsubscribe(state, q)


class TestApiEndpoints:
    """Quartアプリの各種APIエンドポイントのスモーク。"""

    @pytest.mark.asyncio
    async def test_api_files_returns_list(self, tmp_path: Path):
        """/api/filesが.mdの一覧をJSONで返す。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        app = _app.create_app(tmp_path, hostname="test")
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


class TestEventsEndpoint:
    """`/api/events`エンドポイントの統合テスト。"""

    @pytest.mark.asyncio
    async def test_sse_stream_contract(self, tmp_path: Path):
        """接続時のContent-Type、配信フォーマット、debounce挙動を一連で検証する。

        ストリーミング応答のため`TestHTTPConnection.receive()`でチャンクを逐次読み取る。
        `_schedule_broadcast`を2回連続で呼んでもdebounceで1件に畳まれることを確認する。
        """
        app = _app.create_app(tmp_path, hostname="test")
        # test_client経由の呼び出しでは`before_serving`が発火しないため、loop参照を手動注入する。
        state: _state.BroadcastState = app.config["PLANS_STATE"]
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
            await _state.schedule_broadcast(state)
            # 直後にもう1回呼んで畳まれること（debounce）を同時に確認する。
            await _state.schedule_broadcast(state)

            # ストリーミングチャンクを逐次受信し、refreshのJSONペイロードを含むまで読み進める。
            expected_data_line = "data: " + _state._SSE_REFRESH_PAYLOAD
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
    """`_BroadcastState`のフィールド既定値の契約を固定する。"""

    def test_defaults(self):
        """新規状態の購読者は空、ループは未設定、debounceタスクは未起動、ホスト状態は空。"""
        state = _state.BroadcastState()
        assert not state.subscribers
        assert state.debounce_task is None
        assert state.loop is None
        assert not state.remote_files
        assert not state.remote_tasks
        assert not state.host_status
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
        }


class _FakeSshRunner:
    """テスト用SshRunner。

    現在は`/api/file`/`/api/raw`のリモート参照経路（read）専用。
    `read_responses`は`(host, rel)`→Markdown原文の辞書。
    `failing_hosts`に含めたホストへの呼び出しは`RuntimeError`を送出する。
    呼び出し履歴は`calls`に`(host, op, args)`タプルで蓄積される。
    """

    def __init__(
        self,
        *,
        read_responses: dict[tuple[str, str], str] | None = None,
        failing_hosts: set[str] | None = None,
    ) -> None:
        self._read_responses = read_responses or {}
        self._failing_hosts = failing_hosts or set()
        self.calls: list[tuple[str, str, list[str]]] = []

    async def __call__(self, host: str, op: str, args: list[str]) -> str:
        self.calls.append((host, op, list(args)))
        if host in self._failing_hosts:
            raise RuntimeError(f"ssh failed for {host}")
        if op == "read":
            rel = base64.b64decode(args[0]).decode("utf-8")
            body = self._read_responses[(host, rel)]
            return base64.b64encode(body.encode("utf-8")).decode("ascii")
        raise ValueError(f"unknown op: {op}")


async def _aiter_lines(lines: list[str]) -> typing.AsyncIterator[str]:
    """インメモリーの行リストを`_RemoteWatcher._process_stream`に流し込むためのヘルパー。"""
    for line in lines:
        yield line


def _seed_remote_cache(state: _state.BroadcastState, host: str, items: list[dict[str, typing.Any]]) -> None:
    """テスト用に`state.remote_files`へ直接エントリを書き込む。

    `_RemoteWatcher._process_stream`を経由せずに`/api/files`merge挙動を検証するための土台。
    """
    state.remote_files[host] = [_state.make_file_entry(host, item) for item in items]


class TestRemoteWatcher:
    """`_RemoteWatcher._process_stream`の行処理ユニットテスト。

    純粋な行ジェネレーターを引数化することで、subprocess・SSHを介さず分岐網羅する。
    """

    @pytest.mark.asyncio
    async def test_snapshot_updates_cache_and_marks_connected(self):
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
                            {"path": "a.md", "name": "a.md", "mtime_epoch": 100.0},
                            {"path": "sub/b.md", "name": "b.md", "mtime_epoch": 200.0},
                        ],
                    }
                )
                + "\n",
            ]
            await watcher._process_stream(_aiter_lines(lines))

            assert state.host_status["host1"] == "connected"
            cached = state.remote_files["host1"]
            assert sorted(e.path for e in cached) == ["a.md", "sub/b.md"]
            # snapshot受信時は host-status と refresh の両方が配信される。
            received: list[str] = []
            while not q.empty():
                received.append(q.get_nowait())
            assert _state._SSE_REFRESH_PAYLOAD in received
            host_status_payload = json.dumps(
                {"type": "host-status", "host": "host1", "status": "connected"}, ensure_ascii=False
            )
            assert host_status_payload in received
        finally:
            async with state.lock:
                state.subscribers.discard(q)

    @pytest.mark.asyncio
    async def test_upsert_adds_new_path(self):
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # 既存snapshotを与えてから、新規pathのupsertが追加されることを確認する。
        await watcher._process_stream(
            _aiter_lines(
                [
                    json.dumps(
                        {
                            "type": "snapshot",
                            "entries": [{"path": "a.md", "name": "a.md", "mtime_epoch": 100.0}],
                        }
                    )
                    + "\n",
                    json.dumps({"type": "upsert", "path": "b.md", "name": "b.md", "mtime_epoch": 200.0}) + "\n",
                ]
            )
        )

        cached = state.remote_files["host1"]
        assert sorted(e.path for e in cached) == ["a.md", "b.md"]

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing_path(self):
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        await watcher._process_stream(
            _aiter_lines(
                [
                    json.dumps(
                        {
                            "type": "snapshot",
                            "entries": [{"path": "a.md", "name": "a.md", "mtime_epoch": 100.0}],
                        }
                    )
                    + "\n",
                    json.dumps({"type": "upsert", "path": "a.md", "name": "a.md", "mtime_epoch": 999.0}) + "\n",
                ]
            )
        )

        cached = state.remote_files["host1"]
        assert len(cached) == 1
        assert cached[0].mtime_epoch == 999.0

    @pytest.mark.asyncio
    async def test_deleted_removes_path(self):
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        await watcher._process_stream(
            _aiter_lines(
                [
                    json.dumps(
                        {
                            "type": "snapshot",
                            "entries": [
                                {"path": "a.md", "name": "a.md", "mtime_epoch": 100.0},
                                {"path": "b.md", "name": "b.md", "mtime_epoch": 200.0},
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
    async def test_ping_does_not_emit_anything(self):
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            watcher = _remote.RemoteWatcher("host1", state)
            await watcher._process_stream(_aiter_lines([json.dumps({"type": "ping"}) + "\n"]))
            # キャッシュにもhost_statusにも一切影響しない（接続確立前なので空のまま）。
            assert "host1" not in state.remote_files
            assert not state.host_status
            assert q.empty()
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_invalid_json_logged_and_processing_continues(self, caplog: pytest.LogCaptureFixture):
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        with caplog.at_level("WARNING", logger="pytools.claude_plans_viewer"):
            await watcher._process_stream(
                _aiter_lines(
                    [
                        "not-a-json-line\n",
                        json.dumps(
                            {
                                "type": "snapshot",
                                "entries": [{"path": "a.md", "name": "a.md", "mtime_epoch": 100.0}],
                            }
                        )
                        + "\n",
                    ]
                )
            )
        # 後続行は処理が継続される。
        assert "host1" in state.remote_files
        assert any("JSON解析失敗" in r.message for r in caplog.records if r.levelname == "WARNING")

    @pytest.mark.asyncio
    async def test_set_status_disconnected_emits_sse_after_snapshot(self):
        """`_set_status`経由のdisconnected遷移とSSE配信を検証する。

        `run`本体の再接続ループは同期テストが困難なため手動確認に委ねる。
        本テストでは`_set_status`を直接呼び出してhost_statusの遷移と
        host-statusのSSE配信が想定通り動くことを確認する。
        """
        state = _state.BroadcastState()
        q = await _state.subscribe(state)
        try:
            watcher = _remote.RemoteWatcher("host1", state)
            # snapshot を受け、いったん connected へ遷移させる。
            await watcher._process_stream(
                _aiter_lines(
                    [
                        json.dumps({"type": "snapshot", "entries": []}) + "\n",
                    ]
                )
            )
            # キューを掃き出してから切断遷移を観測する。
            while not q.empty():
                q.get_nowait()
            await watcher._set_status("disconnected")
            assert state.host_status["host1"] == "disconnected"
            payload = json.dumps({"type": "host-status", "host": "host1", "status": "disconnected"}, ensure_ascii=False)
            assert q.get_nowait() == payload
        finally:
            await _state.unsubscribe(state, q)

    @pytest.mark.asyncio
    async def test_snapshot_resets_backoff(self):
        """snapshot受信でバックオフが`_REMOTE_BACKOFF_INITIAL_SEC`にリセットされること。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # 最大値まで増加していると仮定してから snapshot を流す。
        watcher._backoff = _remote.REMOTE_BACKOFF_MAX_SEC
        await watcher._process_stream(
            _aiter_lines(
                [
                    json.dumps({"type": "snapshot", "entries": []}) + "\n",
                ]
            )
        )
        assert watcher._backoff == _remote.REMOTE_BACKOFF_INITIAL_SEC


class TestRemoteHostIntegration:
    """リモートホスト統合（API・許可リスト・host-status）の挙動を検証する。"""

    @pytest.mark.asyncio
    async def test_api_files_merges_local_and_remote_sorted(self, tmp_path: Path):
        """`/api/files`がローカル＋全リモートホストのエントリをmtime降順で統合する。"""
        local = tmp_path / "local.md"
        local.write_text("local", encoding="utf-8")
        os.utime(local, (3_000.0, 3_000.0))

        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1", "host2"],
        )
        state: _state.BroadcastState = app.config["PLANS_STATE"]
        _seed_remote_cache(state, "host1", [{"path": "h1.md", "name": "h1.md", "mtime_epoch": 5_000.0}])
        _seed_remote_cache(state, "host2", [{"path": "h2.md", "name": "h2.md", "mtime_epoch": 1_000.0}])

        client = app.test_client()
        response = await client.get("/api/files")

        assert response.status_code == 200
        data = json.loads(await response.get_data())
        assert [(e["host"], e["path"]) for e in data] == [
            ("host1", "h1.md"),
            ("local-host", "local.md"),
            ("host2", "h2.md"),
        ]
        # 全エントリに`host`フィールドが乗ること。
        assert {e["host"] for e in data} == {"host1", "host2", "local-host"}

    @pytest.mark.asyncio
    async def test_api_file_for_remote_host_renders(self, tmp_path: Path):
        """`/api/file?host=host1&path=foo.md`がfake runnerの`read`応答をHTMLレンダリングして返す。"""
        runner = _FakeSshRunner(
            read_responses={("host1", "foo.md"): "# remote title\n"},
        )
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get("/api/file?host=host1&path=foo.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "<h1>remote title</h1>" in body
        # `read`オペレーションがhost1宛に1回発行され、引数はbase64エンコードされた相対パス。
        read_calls = [c for c in runner.calls if c[1] == "read"]
        assert len(read_calls) == 1
        assert read_calls[0][0] == "host1"
        assert base64.b64decode(read_calls[0][2][0]).decode("utf-8") == "foo.md"

    @pytest.mark.asyncio
    async def test_api_raw_for_remote_host_returns_markdown(self, tmp_path: Path):
        """`/api/raw?host=host1&path=foo.md`がfake runnerから取得した生Markdownを返す。"""
        body_src = "# title\n\n本文\n"
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): body_src})
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get("/api/raw?host=host1&path=foo.md")

        assert response.status_code == 200
        assert response.content_type == "text/markdown; charset=utf-8"
        assert await response.get_data(as_text=True) == body_src

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["/api/file", "/api/raw"])
    async def test_unknown_host_rejected_without_ssh_call(self, tmp_path: Path, endpoint: str):
        """許可リスト外のhost指定は400で拒否され、`ssh_runner`は呼ばれない。

        サーバーが`0.0.0.0`等で公開された場合に、クライアントが任意のSSH接続先へ
        接続試行を誘発できないようにするための境界検証。
        """
        runner = _FakeSshRunner()
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get(f"{endpoint}?host=evil&path=foo.md")

        assert response.status_code == 400
        # ssh_runnerは一度も呼ばれていない。
        assert not runner.calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["/api/file", "/api/raw"])
    async def test_remote_traversal_rejected_without_ssh_call(self, tmp_path: Path, endpoint: str):
        """`..`を含む相対パスはSSH呼び出し前に400で拒否される。"""
        runner = _FakeSshRunner()
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get(f"{endpoint}?host=host1&path=../escape.md")

        assert response.status_code == 400
        assert not runner.calls

    @pytest.mark.asyncio
    async def test_local_host_query_uses_local_path(self, tmp_path: Path):
        """`host`にローカル名を明示してもローカル経路で解決され、SSHは呼ばれない。"""
        (tmp_path / "a.md").write_text("# local\n", encoding="utf-8")
        runner = _FakeSshRunner()
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        response = await client.get("/api/file?host=local-host&path=a.md")

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert "<h1>local</h1>" in body
        assert not runner.calls

    def test_local_hostname_conflict_rejected(self, tmp_path: Path):
        """ローカルhostnameと同じ`--remote-host`を渡すと`create_app`が拒絶する。"""
        with pytest.raises(ValueError, match="local hostname"):
            _app.create_app(
                tmp_path,
                hostname="local-host",
                remote_hosts=["local-host"],
            )

    @pytest.mark.asyncio
    async def test_api_host_status_initial_state(self, tmp_path: Path):
        """`/api/host-status`の初期応答はローカル=connected・リモート=connecting。"""
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
        )
        client = app.test_client()
        response = await client.get("/api/host-status")

        assert response.status_code == 200
        assert response.content_type == "application/json; charset=utf-8"
        data = json.loads(await response.get_data())
        assert data == {"local-host": "connected", "host1": "connecting"}

    @pytest.mark.asyncio
    async def test_api_host_status_updates_after_snapshot(self, tmp_path: Path):
        """snapshot受信後は`/api/host-status`がそのホストを`connected`として返す。"""
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
        )
        state: _state.BroadcastState = app.config["PLANS_STATE"]
        watcher = _remote.RemoteWatcher("host1", state)
        await watcher._process_stream(_aiter_lines([json.dumps({"type": "snapshot", "entries": []}) + "\n"]))

        client = app.test_client()
        response = await client.get("/api/host-status")
        data = json.loads(await response.get_data())
        assert data == {"local-host": "connected", "host1": "connected"}


class TestRemoteStreamLimit:
    """`asyncio.create_subprocess_exec`既定StreamReader上限超過時の挙動。"""

    @pytest.mark.asyncio
    async def test_iter_stream_lines_handles_oversized_line(self):
        """64KiB既定上限を超える1行をlimit引き上げ後のStreamReaderで読み取れる。

        modules内に専用モジュールを足さず、`asyncio.StreamReader`に対し
        `iter_stream_lines`の前提（`readline()`が分離記号を見つけるまで読み続ける）が
        `limit`引数で制御可能であることを直接確認する。
        既定limit=64KiBではvalueErrorが上がる挙動を再現したうえで、
        REMOTE_STREAM_LIMIT_BYTES適用時に同じ行が完了することを示す。
        """
        big_payload = ("a" * (200 * 1024)) + "\n"
        # 既定limit (64KiB) では行末を見つけられず例外を送出する。
        small_reader = asyncio.StreamReader()
        small_reader.feed_data(big_payload.encode("utf-8"))
        small_reader.feed_eof()
        with pytest.raises(ValueError, match="chunk is longer than limit"):
            await small_reader.readline()

        # REMOTE_STREAM_LIMIT_BYTES適用後はそのまま読み切れる。
        big_reader = asyncio.StreamReader(limit=_remote.REMOTE_STREAM_LIMIT_BYTES)
        big_reader.feed_data(big_payload.encode("utf-8"))
        big_reader.feed_eof()

        async def _consume() -> list[str]:
            collected: list[str] = []
            async for line in _remote.iter_stream_lines(big_reader):
                collected.append(line)
            return collected

        lines = await _consume()
        assert len(lines) == 1
        assert lines[0].rstrip("\n") == "a" * (200 * 1024)


class TestSafeBasePath:
    """`_app.safe_base_path`の入力検証。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", ""),
            ("/", ""),
            ("/plans", "/plans"),
            ("/plans/", "/plans"),
            ("/api/v1", "/api/v1"),
            ("/foo-bar_baz.qux", "/foo-bar_baz.qux"),
        ],
    )
    def test_accepts_safe_values(self, raw: str, expected: str):
        assert _app.safe_base_path(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "//evil.example",
            "/foo//bar",
            '/"><script>',
            '/"; alert(1); //',
            "no-leading-slash",
            "/has space",
            "/has\nnewline",
            "/has<tag>",
        ],
    )
    def test_rejects_unsafe_values(self, raw: str):
        assert _app.safe_base_path(raw) == ""


class TestProxyFixIntegration:
    """ProxyFixミドルウェアがX-Forwarded-Prefix/Protoを反映する経路の統合検証。

    ASGI scopeでは`root_path`に対しQuartが`path`の冒頭から同値を切り落とすため、
    リバースプロキシは「prefixを保持したままバックエンドへ転送する」構成（nginxで
    `proxy_pass http://backend;`をtrailing slash無しで指定する形）を想定する。
    テストもクライアントがプレフィクス付きの絶対URLを叩く前提で組み立てる。
    """

    @pytest.mark.asyncio
    async def test_index_includes_prefix_in_links_and_js(self, tmp_path: Path):
        """`X-Forwarded-Prefix`付与時、href・JS const `BASE_PATH`の双方に反映される。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get(
            "/plans/",
            headers={"X-Forwarded-Prefix": "/plans", "X-Forwarded-Proto": "https"},
        )

        assert response.status_code == 200
        body = await response.get_data(as_text=True)
        assert 'href="/plans/favicon.svg"' in body
        assert 'href="/plans/manifest.webmanifest"' in body
        assert 'href="/plans/static/markdown.css"' in body
        # JSリテラルはjson.dumpsで生成されるためダブルクォート付き。
        assert 'const BASE_PATH = "/plans";' in body

    @pytest.mark.asyncio
    async def test_index_without_prefix_uses_empty_base(self, tmp_path: Path):
        """ヘッダー無しでは空文字列扱いとなりプレフィクスが付かない。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("/")

        body = await response.get_data(as_text=True)
        assert 'href="/favicon.svg"' in body
        assert 'const BASE_PATH = "";' in body

    @pytest.mark.asyncio
    async def test_manifest_includes_prefix(self, tmp_path: Path):
        """manifest.webmanifestの`start_url`・`icons.src`がプレフィクス付きになる。"""
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get(
            "/plans/manifest.webmanifest",
            headers={"X-Forwarded-Prefix": "/plans"},
        )

        data = json.loads(await response.get_data())
        assert data["start_url"] == "/plans/"
        assert data["icons"][0]["src"] == "/plans/favicon.svg"

    @pytest.mark.parametrize(
        "malicious_path",
        [
            "//evil.example/",
            "/foo//bar/",
        ],
    )
    @pytest.mark.asyncio
    async def test_routable_malicious_prefix_neutralized_in_output(
        self,
        tmp_path: Path,
        malicious_path: str,
    ):
        """ルート到達可能な悪意プレフィクスでも出力に生バイトが漏れない。

        ProxyFixがroot_pathに設定し、Quartが路追剥がしを行ってルートに到達するパスを
        投げる。`safe_base_path`が空扱いに正規化するため、HTML属性・JS定数・manifestの
        いずれにもプレフィクス文字列が漏れず、外部オリジンへのスキーム相対URLも生まれない。
        """
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        # ProxyFixはheader値をrstrip("/")してから格納するため、headerは末尾スラッシュを含めない。
        prefix_header = malicious_path.rstrip("/")
        response_index = await client.get(malicious_path, headers={"X-Forwarded-Prefix": prefix_header})
        body_index = await response_index.get_data(as_text=True)
        assert response_index.status_code == 200
        assert prefix_header not in body_index
        assert "//evil.example" not in body_index
        assert 'href="/favicon.svg"' in body_index
        assert 'const BASE_PATH = "";' in body_index

        response_manifest = await client.get(
            f"{malicious_path}manifest.webmanifest",
            headers={"X-Forwarded-Prefix": prefix_header},
        )
        manifest = json.loads(await response_manifest.get_data())
        assert manifest["start_url"] == "/"
        assert manifest["icons"][0]["src"] == "/favicon.svg"
