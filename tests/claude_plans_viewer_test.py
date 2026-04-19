"""pytools.claude_plans_viewer のテスト。"""

# 本モジュールはプライベート関数（`_list_files`・`_resolve_under_root`・`_markdown_to_html`・
# `_resolve_css_path`・`_read_css`）や同モジュール内の定数を単体でテストするため、protected-accessを一括で許可する。
# pylint: disable=protected-access

import http.client
import http.server
import json
import os
import queue
import re
import socket
import threading
from pathlib import Path

import pytest
import watchdog.events

from pytools import claude_plans_viewer


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

    def test_read_css_nonempty(self):
        """_read_cssがCSS本文を返す（空でない）。"""
        css = claude_plans_viewer._read_css()

        assert css.strip()


class TestPwaAssets:
    """favicon・manifest・service workerのインライン定数の内容検査。

    HTTPハンドラは標準ライブラリの`BaseHTTPRequestHandler`に委ねているため、
    統合テストは持たずに定数側で契約を固定する。
    """

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


class TestHostnameEmbedding:
    """`/` 応答HTMLへのホスト名埋め込みを検証する。

    実際のHTTPサーバーをポート0で起動し、`/` を取得して本文を確認する。
    別スレッドで1回だけ serve するため、テスト終了時には `shutdown()` と `join()` で
    確実に停止させる。
    """

    def test_index_html_contains_escaped_hostname(self, tmp_path: Path):
        """`/` 応答に socket.gethostname() 相当のホスト名がエスケープ済みで含まれる。"""
        # HTML特殊文字を含む値で埋め込みのエスケープ処理も同時に検査する。
        hostname = 'host<&"test'
        claude_plans_viewer._PlansHandler.root = tmp_path
        claude_plans_viewer._PlansHandler.renderer = claude_plans_viewer._make_md_renderer()
        claude_plans_viewer._PlansHandler.hostname = hostname

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), claude_plans_viewer._PlansHandler)
        try:
            _port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", _port, timeout=5)
                conn.request("GET", "/")
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                conn.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
        finally:
            server.server_close()

        assert response.status == 200
        # 生のホスト名文字列は含まれない（エスケープされている）こと。
        assert hostname not in body
        # エスケープ済みの形で含まれること。
        assert "host&lt;&amp;&quot;test" in body
        # 実ホスト名取得関数が問題なく呼べることを動作として担保する。
        assert isinstance(socket.gethostname(), str)


class TestSubscribers:
    """購読者管理（_subscribe・_unsubscribe・_broadcast）のテスト。"""

    def test_subscribe_unsubscribe_roundtrip(self):
        """_subscribe で登録し _unsubscribe で解除できること。重複解除もエラーにならないこと。"""
        q = claude_plans_viewer._subscribe()
        try:
            assert q in claude_plans_viewer._subscribers
        finally:
            claude_plans_viewer._unsubscribe(q)
        assert q not in claude_plans_viewer._subscribers
        # 重複解除してもエラーにならない
        claude_plans_viewer._unsubscribe(q)

    def test_broadcast_delivers_refresh(self):
        """_broadcast 後にキューから "refresh" が取得できること。"""
        q = claude_plans_viewer._subscribe()
        try:
            claude_plans_viewer._broadcast()
            msg = q.get(timeout=1)
            assert msg == "refresh"
        finally:
            claude_plans_viewer._unsubscribe(q)

    def test_broadcast_deduplicates_via_maxsize(self):
        """_broadcast を2回連続で呼んでも、キュー長が1のまま（2件目は握りつぶされる）こと。"""
        q = claude_plans_viewer._subscribe()
        try:
            claude_plans_viewer._broadcast()
            claude_plans_viewer._broadcast()
            assert q.qsize() == 1
        finally:
            claude_plans_viewer._unsubscribe(q)


class TestWatchdogHandler:
    """_PlansEventHandler のイベントフィルタリングテスト。"""

    def test_md_event_broadcasts(self, tmp_path: Path):
        """.md ファイルの変更イベントで購読者へ refresh が届くこと。"""
        q = claude_plans_viewer._subscribe()
        try:
            md_file = tmp_path / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(tmp_path).on_any_event(event)
            msg = q.get(timeout=1)
            assert msg == "refresh"
        finally:
            claude_plans_viewer._unsubscribe(q)

    def test_non_md_event_ignored(self, tmp_path: Path):
        """.md 以外のファイルイベントでは購読者へ通知しないこと。"""
        q = claude_plans_viewer._subscribe()
        try:
            txt_file = tmp_path / "note.txt"
            txt_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(txt_file))
            claude_plans_viewer._PlansEventHandler(tmp_path).on_any_event(event)
            with pytest.raises(queue.Empty):
                q.get_nowait()
        finally:
            claude_plans_viewer._unsubscribe(q)

    def test_dotdir_event_ignored(self, tmp_path: Path):
        """root配下のdotdir配下のイベントでは購読者へ通知しないこと。"""
        q = claude_plans_viewer._subscribe()
        try:
            cache_dir = tmp_path / ".cache"
            cache_dir.mkdir()
            md_file = cache_dir / "plan.md"
            md_file.write_text("x", encoding="utf-8")
            event = watchdog.events.FileModifiedEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(tmp_path).on_any_event(event)
            with pytest.raises(queue.Empty):
                q.get_nowait()
        finally:
            claude_plans_viewer._unsubscribe(q)

    def test_directory_event_ignored(self, tmp_path: Path):
        """is_directory=True のイベントでは購読者へ通知しないこと。"""
        q = claude_plans_viewer._subscribe()
        try:
            event = watchdog.events.DirModifiedEvent(str(tmp_path / "subdir"))
            claude_plans_viewer._PlansEventHandler(tmp_path).on_any_event(event)
            with pytest.raises(queue.Empty):
                q.get_nowait()
        finally:
            claude_plans_viewer._unsubscribe(q)

    def test_dotdir_root_events_pass(self, tmp_path: Path):
        """rootそのものがdotdir配下にあっても、root配下の通常.mdは通知されること。

        ~/.claude/plansのようにrootのパス成分にdotdirが含まれるケースの回帰テスト。
        旧実装ではsrc_path全体のpartsを判定していたためrootのパス成分にも誤マッチしていた。
        """
        # rootをdotdir名のディレクトリとし、その配下の通常ファイルが通知される側に入ることを確認する
        dot_root = tmp_path / ".claude_like"
        dot_root.mkdir()
        md_file = dot_root / "plan.md"
        md_file.write_text("x", encoding="utf-8")

        q = claude_plans_viewer._subscribe()
        try:
            event = watchdog.events.FileModifiedEvent(str(md_file))
            claude_plans_viewer._PlansEventHandler(dot_root).on_any_event(event)
            msg = q.get(timeout=1)
            assert msg == "refresh"
        finally:
            claude_plans_viewer._unsubscribe(q)


class TestEventsEndpoint:
    """/api/events エンドポイントの統合テスト。"""

    def test_sse_headers_and_refresh_delivered(self, tmp_path: Path):
        """SSEヘッダが正しく返り、_broadcast 後に data: refresh 行が届くこと。"""
        claude_plans_viewer._PlansHandler.root = tmp_path
        claude_plans_viewer._PlansHandler.renderer = claude_plans_viewer._make_md_renderer()
        claude_plans_viewer._PlansHandler.hostname = "testhost"

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), claude_plans_viewer._PlansHandler)
        try:
            _port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", _port, timeout=5)
                conn.request("GET", "/api/events")
                response = conn.getresponse()

                # ヘッダを確認
                assert response.status == 200
                assert response.getheader("Content-Type") == "text/event-stream"
                assert response.getheader("Cache-Control") == "no-store"

                # 少し待ってから _broadcast を呼ぶ
                threading.Timer(0.3, claude_plans_viewer._broadcast).start()

                # data: refresh 行が届くまで読み取る
                received_refresh = False
                for _ in range(50):
                    line = response.fp.readline().decode("utf-8")
                    if line.strip() == "data: refresh":
                        received_refresh = True
                        break

                response.close()
                conn.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
        finally:
            server.server_close()
            # テスト終了時に購読者集合を後始末する
            with claude_plans_viewer._subscribers_lock:
                claude_plans_viewer._subscribers.clear()

        assert received_refresh
