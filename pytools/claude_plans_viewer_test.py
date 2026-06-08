"""pytools.claude_plans_viewer のテスト。"""

# 単一テスト対象モジュールに対する全テストを集約するため行数制限を緩和する。
# pylint: disable=too-many-lines

import asyncio
import base64
import dataclasses
import io
import json
import os
import re
import sys
import typing
from pathlib import Path

import pytest
import watchdog.events
from quart.testing.connections import TestHTTPConnection as _TestHTTPConnection

from pytools.claude_plans_viewer import _app, _assets, _cli, _config, _console_title, _local, _remote, _state

# _state._BROADCAST_DEBOUNCE_SEC と同値（0.3秒）。debounce窓の秒数。
_BROADCAST_DEBOUNCE_SEC = 0.3
# SSE refreshメッセージの仕様。_state._SSE_REFRESH_PAYLOAD と同一値（SSE refreshメッセージ仕様）。
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)

# `schedule_broadcast`経由のrefresh待ちは`_BROADCAST_DEBOUNCE_SEC`後に配信されるため、
# debounce窓にマージン0.7秒を加えた値をタイムアウトとする。
_QUEUE_GET_TIMEOUT_SEC = _BROADCAST_DEBOUNCE_SEC + 0.7


class TestListFiles:
    """list_files のテスト。"""

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
    """resolve_under_root のテスト。"""

    def test_valid_md_path(self, tmp_path: Path):
        """root配下の.mdを正常に解決する。"""
        target_path = tmp_path / "a.md"
        target_path.write_text("x", encoding="utf-8")

        result = _local.resolve_under_root(tmp_path, "a.md")

        assert result == target_path.resolve()

    @pytest.mark.parametrize("rel", ["../outside.md", "sub/../../outside.md"])
    def test_rejects_traversal(self, tmp_path: Path, rel: str):
        """root外へ出るパスはNoneを返す。"""
        # root外の実体を用意しても相対参照で抜けられないことを確認する。
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
    """markdown_to_html のテスト。"""

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


class TestMarkdownCache:
    """`MarkdownCache`のヒット/ミス/容量上限/`mtime_epoch`変化挙動を検証する。"""

    def test_hit_returns_cached_html(self):
        """同一キーの`get`はputした値をそのまま返す。"""
        cache = _local.MarkdownCache()
        cache.put(("local", "a.md", 1.0), "<p>a</p>")
        assert cache.get(("local", "a.md", 1.0)) == "<p>a</p>"

    def test_miss_returns_none(self):
        """未登録キーの`get`はNoneを返す。"""
        cache = _local.MarkdownCache()
        assert cache.get(("local", "missing.md", 1.0)) is None

    def test_mtime_change_invalidates(self):
        """`mtime_epoch`が変わると別エントリ扱いとなる（自動無効化）。"""
        cache = _local.MarkdownCache()
        cache.put(("local", "a.md", 1.0), "<p>old</p>")
        # 新しいmtimeで参照すると未ヒットになる。
        assert cache.get(("local", "a.md", 2.0)) is None
        # 旧キーは別物として残るが、同一(host,path)で新キーをputすれば共存する。
        cache.put(("local", "a.md", 2.0), "<p>new</p>")
        assert cache.get(("local", "a.md", 2.0)) == "<p>new</p>"

    def test_evicts_oldest_on_entry_limit(self):
        """エントリ数上限を超えると最古のエントリから削除される（LRU）。"""
        cache = _local.MarkdownCache(max_entries=2, max_bytes=1024 * 1024)
        cache.put(("local", "a.md", 1.0), "<p>a</p>")
        cache.put(("local", "b.md", 1.0), "<p>b</p>")
        # `a.md`を参照して最近使用扱いに昇格させる。
        assert cache.get(("local", "a.md", 1.0)) == "<p>a</p>"
        cache.put(("local", "c.md", 1.0), "<p>c</p>")
        # `b.md`が最古（最終アクセスが最初）のため削除される。
        assert cache.get(("local", "b.md", 1.0)) is None
        assert cache.get(("local", "a.md", 1.0)) == "<p>a</p>"
        assert cache.get(("local", "c.md", 1.0)) == "<p>c</p>"

    def test_evicts_on_byte_limit(self):
        """総バイト数上限を超えると古い順に削除される。"""
        # 1エントリ約100バイト。max_bytes=200で2件程度に制限される。
        big_html = "x" * 100
        cache = _local.MarkdownCache(max_entries=100, max_bytes=200)
        cache.put(("local", "a.md", 1.0), big_html)
        cache.put(("local", "b.md", 1.0), big_html)
        cache.put(("local", "c.md", 1.0), big_html)
        # 最古の`a.md`は削除される。
        assert cache.get(("local", "a.md", 1.0)) is None
        # 上限の二重制約のうち先に到達した方で削除するため、現存数は最大2件。
        assert len(cache) <= 2
        assert cache.total_bytes() <= 200

    def test_oversized_entry_not_stored(self):
        """単一エントリが`max_bytes`を超える場合は保持しない（メモリ暴走を防ぐ）。"""
        cache = _local.MarkdownCache(max_entries=10, max_bytes=10)
        cache.put(("local", "huge.md", 1.0), "x" * 1000)
        assert cache.get(("local", "huge.md", 1.0)) is None
        assert len(cache) == 0


class TestReadCss:
    """read_css のテスト。

    editable install前提でリポジトリ配下の`share/vscode/markdown.css`を返すことを確認する。
    本テストはdotfilesリポジトリ内で実行される前提で、配布CSSの所在を固定する。
    """

    @pytest.mark.asyncio
    async def test_read_css_nonempty(self):
        """read_cssがリポジトリ内CSSの本文を返す（フォールバックではない）。

        フォールバックCSS（`_assets.FALLBACK_CSS`）が返るとeditable installの解決経路が
        破綻している兆候となるため、フォールバックと一致しないことで区別する。
        """
        css = await _local.read_css()

        assert css.strip()
        # フォールバックCSSが返った場合は実CSSの解決経路が破綻している。
        assert css != _assets.FALLBACK_CSS


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
        # 直列化可能であることを別途確認しておく（型違反ではjson.dumpsが失敗するため）。
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


@pytest.fixture(name="_parse_args_isolate_env")
def _parse_args_isolate_env_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """`parse_args`関連テスト用の環境隔離。

    `CLAUDE_PLANS_VIEWER_CONFIG`を`tmp_path / "config.toml"`（未作成）に向け、
    既存の`CLAUDE_PLANS_VIEWER_*`環境変数を解除する。これにより配布先の
    `~/.config/pytools/claude-plans-viewer.toml`や利用者環境の環境変数の
    影響を受けず、テストが期待する解決経路を通せる。
    """
    monkeypatch.setenv(_config.ENV_CONFIG, str(tmp_path / "config.toml"))
    monkeypatch.delenv(_cli.ENV_ROOT, raising=False)
    monkeypatch.delenv(_cli.ENV_HOST, raising=False)
    monkeypatch.delenv(_cli.ENV_PORT, raising=False)
    monkeypatch.delenv(_cli.ENV_REMOTE_HOSTS, raising=False)


@pytest.mark.usefixtures("_parse_args_isolate_env")
class TestParseArgs:
    """parse_args の環境変数フォールバック検証。

    「CLI引数 > 環境変数 > 組み込み既定値」の優先順位を固定するため、
    monkeypatch で環境変数を明示的に設定/解除した上で解決結果を検査する。
    設定ファイル経由の影響を排除するため、module-level fixture
    `_parse_args_isolate_env`で`CLAUDE_PLANS_VIEWER_CONFIG`を未作成パスへ向ける。
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


@pytest.mark.usefixtures("_parse_args_isolate_env")
class TestParseArgsConfigFile:
    """設定ファイル経由の解決と優先順位（CLI引数 > 環境変数 > 設定ファイル > 既定値）を検証する。

    全テストで`CLAUDE_PLANS_VIEWER_CONFIG`を`tmp_path`配下に向け、
    環境変数群も`monkeypatch`で隔離する（共通fixture`_parse_args_isolate_env`を参照）。
    設定ファイルのキーはkebab-case（`remote-hosts`）であり、
    `_config.load_config`がsnake_caseへ正規化する。
    """

    def test_missing_config_falls_back_to_defaults(self, tmp_path: Path):
        """設定ファイル不在時は組み込み既定値を採用する。"""
        # `_parse_args_isolate_env`が指す`tmp_path / "config.toml"`は未作成のため不在経路を通る。
        assert not (tmp_path / "config.toml").exists()

        args = _cli.parse_args([])

        assert args.root == _cli.DEFAULT_ROOT
        assert args.host == _cli.DEFAULT_HOST
        assert args.port == _cli.DEFAULT_PORT
        assert args.remote_host == []

    def test_config_file_values_applied(self, tmp_path: Path):
        """設定ファイルの値が反映される（kebab-caseキーがsnake_caseへ正規化される）。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'root = "/tmp/plans-config"\nhost = "127.0.0.5"\nport = 30000\nremote-hosts = ["confhost1", "user@confhost2"]\n',
            encoding="utf-8",
        )

        args = _cli.parse_args([])

        assert args.root == "/tmp/plans-config"
        assert args.host == "127.0.0.5"
        assert args.port == 30000
        assert args.remote_host == ["confhost1", "user@confhost2"]

    def test_env_overrides_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """環境変数は設定ファイルより優先する。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'root = "/tmp/plans-config"\nhost = "127.0.0.5"\nport = 30000\nremote-hosts = ["confhost"]\n',
            encoding="utf-8",
        )
        monkeypatch.setenv(_cli.ENV_ROOT, "/tmp/plans-env")
        monkeypatch.setenv(_cli.ENV_HOST, "127.0.0.7")
        monkeypatch.setenv(_cli.ENV_PORT, "31000")
        monkeypatch.setenv(_cli.ENV_REMOTE_HOSTS, "envhost1:envhost2")

        args = _cli.parse_args([])

        assert args.root == "/tmp/plans-env"
        assert args.host == "127.0.0.7"
        assert args.port == 31000
        assert args.remote_host == ["envhost1", "envhost2"]

    def test_cli_overrides_config_file(self, tmp_path: Path):
        """CLI引数は設定ファイルより優先する。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'root = "/tmp/plans-config"\nhost = "127.0.0.5"\nport = 30000\nremote-hosts = ["confhost"]\n',
            encoding="utf-8",
        )

        args = _cli.parse_args(
            [
                "--root",
                "/tmp/plans-cli",
                "--host",
                "127.0.0.9",
                "--port",
                "32000",
                "--remote-host",
                "clihost",
            ]
        )

        assert args.root == "/tmp/plans-cli"
        assert args.host == "127.0.0.9"
        assert args.port == 32000
        assert args.remote_host == ["clihost"]

    def test_unknown_key_logs_warning_and_is_ignored(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """未知キーは警告ログを記録して無視する（typo検出のため）。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'root = "/tmp/plans-config"\nunknown-key = "ignored"\n',
            encoding="utf-8",
        )

        with caplog.at_level("WARNING", logger=_config.logger.name):
            args = _cli.parse_args([])

        assert args.root == "/tmp/plans-config"
        assert any(record.levelname == "WARNING" and "unknown-key" in record.message for record in caplog.records), (
            caplog.records
        )

    def test_remote_hosts_non_list_logs_warning_and_is_ignored(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """`remote-hosts`が非リストの場合は警告ログを記録して無視する。"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'remote-hosts = "single"\n',
            encoding="utf-8",
        )

        with caplog.at_level("WARNING", logger=_config.logger.name):
            args = _cli.parse_args([])

        assert args.remote_host == []
        assert any(record.levelname == "WARNING" and "remote-hosts" in record.message for record in caplog.records), (
            caplog.records
        )

    def test_invalid_toml_raises_value_error(self, tmp_path: Path):
        """TOML構文エラーは`ValueError`を送出して早期失敗する。"""
        config_path = tmp_path / "config.toml"
        # 閉じ括弧のないリストは`tomllib`が`TOMLDecodeError`を送出する。
        config_path.write_text("host = [unterminated\n", encoding="utf-8")

        with pytest.raises(ValueError, match="設定ファイルのTOMLが不正です"):
            _cli.parse_args([])

    def test_env_config_path_redirects_load_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """`CLAUDE_PLANS_VIEWER_CONFIG`で読み込み先を切り替えられる。"""
        primary = tmp_path / "primary.toml"
        primary.write_text('root = "/tmp/plans-primary"\n', encoding="utf-8")
        alternate = tmp_path / "alternate.toml"
        alternate.write_text('root = "/tmp/plans-alternate"\n', encoding="utf-8")

        monkeypatch.setenv(_config.ENV_CONFIG, str(primary))
        args_primary = _cli.parse_args([])
        assert args_primary.root == "/tmp/plans-primary"

        monkeypatch.setenv(_config.ENV_CONFIG, str(alternate))
        args_alternate = _cli.parse_args([])
        assert args_alternate.root == "/tmp/plans-alternate"


class TestBuildConsoleTitle:
    """`build_console_title`のタイトル組み立てを検証する。"""

    @pytest.mark.parametrize(
        ("remote_hosts", "expected"),
        [
            ([], "claude-plans-viewer :28765"),
            (["myhost"], "claude-plans-viewer :28765 (myhost)"),
            (["myhost", "host2"], "claude-plans-viewer :28765 (myhost, host2)"),
        ],
    )
    def test_format(self, remote_hosts: list[str], expected: str):
        """リモートホスト件数に応じてホスト名を付加する。"""
        assert _cli.build_console_title(28765, remote_hosts) == expected


class _FakeTtyStream(io.StringIO):
    """`isatty`の結果を制御できるテキストストリーム。"""

    def __init__(self, *, isatty: bool):
        super().__init__()
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


@pytest.mark.skipif(sys.platform == "win32", reason="OSC方式の出力検証はnon-Windows限定")
class TestConsoleTitle:
    """`console_title`がOSC制御シーケンスを出力することを検証する（non-Windows）。"""

    def test_writes_set_and_restore_when_tty(self):
        """ターミナル接続時は開始でタイトル設定、終了で空タイトルへの復元を書く。"""
        stream = _FakeTtyStream(isatty=True)
        title = "claude-plans-viewer :28765"
        with _console_title.console_title(title, stream=stream):
            assert stream.getvalue() == f"\033]2;{title}\a"
        assert stream.getvalue() == f"\033]2;{title}\a\033]2;\a"

    def test_writes_nothing_when_not_tty(self):
        """ターミナル未接続時は何も書かない。"""
        stream = _FakeTtyStream(isatty=False)
        with _console_title.console_title("claude-plans-viewer :28765", stream=stream):
            pass
        assert not stream.getvalue()


class TestIndexHtml:
    """`/`応答HTMLとSPAクライアントJSの契約を検証する。"""

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
        # SSEのtype=host-statusを受信した際の分岐がある。
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

    def test_index_html_force_resyncs_on_tab_activation(self):
        """タブ復帰時にホスト状態とファイル一覧を強制再同期する契約。

        Chromium系のバックグラウンドタブはタイマー・SSEコールバックを抑制するため、
        SSE経由の自動更新だけではタブを前面へ戻した時点で蓄積イベントの処理が体感数秒ずれ込む。
        `visibilitychange`（タブ可視性変化）と`window.focus`
        （PWAウィンドウ単独でフォーカスのみ変動）の2系統で強制再同期する。
        """
        html_src = _assets.INDEX_HTML

        # 2系統のリスナー登録が両方存在する。
        assert 'addEventListener("visibilitychange"' in html_src
        assert 'addEventListener("focus"' in html_src
        # 強制再同期はホスト状態とファイル一覧の双方を呼ぶ`forceResync`に集約され、
        # 上記2リスナーから発火する（onopen等の別経路と区別するため関数名一致まで確認する）。
        assert "async function forceResync" in html_src
        # `visibilitychange`時は`visible`化のみで発火する。
        assert 'document.visibilityState === "visible"' in html_src

    def test_index_html_has_paginated_render_contract(self):
        """大量件数時の段階展開描画の契約。

        フィルタ後の全件を保持しつつ、DOM化対象は先頭`VISIBLE_FILES_INITIAL`件のみへ制限する。
        末尾の番兵要素を`IntersectionObserver`で監視し、可視化されるたびに表示上限を
        `VISIBLE_FILES_STEP`件ずつ拡張する。フィルタ入力時は上限を初期値へ戻す。
        """
        html_src = _assets.INDEX_HTML

        assert "const VISIBLE_FILES_INITIAL = 100" in html_src
        assert "const VISIBLE_FILES_STEP = 100" in html_src
        # `IntersectionObserver`を生成し、番兵要素に対して`observe`を呼ぶ。
        assert "new IntersectionObserver" in html_src
        assert "observe(sentinel)" in html_src
        assert 'id="files-sentinel"' in html_src
        # フィルタ入力時に表示上限を初期値へリセットする。
        assert "visibleLimit = VISIBLE_FILES_INITIAL" in html_src
        # `renderFiles`はフィルタ後件数と表示上限の小さい方までDOM化する。
        assert "Math.min(visibleLimit, visibleFiles.length)" in html_src


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
        """新規状態の購読者は空、ループは未設定、debounceタスクは未起動、ホスト状態は空。"""
        state = _state.BroadcastState()
        assert not state.subscribers
        assert state.debounce_task is None
        assert state.loop is None
        assert not state.remote_files
        assert not state.remote_tasks
        assert not state.host_status
        assert not state.remote_watchers
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
        }


class _FakeSshRunner:
    """テスト用SshRunner。

    `/api/file`/`/api/raw`のリモート参照経路（read）専用。
    `read_responses`は`(host, rel)`→`(本文, mtime_epoch)`の辞書、
    または`(host, rel)`→本文（mtimeは1000.0固定）の辞書。
    `mtime_epoch`を`None`にすると応答ペイロードからキー自体を除去し、
    フォールバック経路でmtime欠落時の挙動（キャッシュバイパス）を再現できる。
    `failing_hosts`に含めたホストへの呼び出しは`RuntimeError`を送出する。
    呼び出し履歴は`calls`に`(host, op, args)`タプルで蓄積される。
    """

    def __init__(
        self,
        *,
        read_responses: dict[tuple[str, str], str | tuple[str, float | None]] | None = None,
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
            entry = self._read_responses[(host, rel)]
            if isinstance(entry, tuple):
                body, mtime = entry
            else:
                body, mtime = entry, 1_000.0
            payload: dict[str, typing.Any] = {
                "data": base64.b64encode(body.encode("utf-8")).decode("ascii"),
            }
            # `mtime_epoch`が`None`のときはキー自体を含めず、ヘルパー側が`mtime_epoch`を
            # 付与せずに応答するケースを再現する（`fetch_remote_file`はキャッシュをバイパスする）。
            if mtime is not None:
                payload["mtime_epoch"] = mtime
            return json.dumps(payload, ensure_ascii=False)
        raise ValueError(f"unknown op: {op}")


async def _aiter_lines(lines: list[str]) -> typing.AsyncIterator[str]:
    """インメモリーの行リストを`RemoteWatcher._process_stream`へ供給するためのヘルパー。"""
    for line in lines:
        yield line


def _seed_remote_cache(state: _state.BroadcastState, host: str, items: list[dict[str, typing.Any]]) -> None:
    """テスト用に`state.remote_files`へ直接エントリを書き込む。

    `RemoteWatcher._process_stream`を経由せずに`/api/files`merge挙動を検証するための土台。
    """
    state.remote_files[host] = [_state.make_file_entry(host, item) for item in items]


class TestRemoteWatcher:
    """`RemoteWatcher._process_stream`の行処理ユニットテスト。

    純粋な行ジェネレーターを引数化することで、subprocess・SSHを介さず分岐網羅する。
    `_process_stream`・`_set_status`・`_backoff`等の`RemoteWatcher`内部状態を直接参照する。
    公開経路（`run()`）経由ではSSH/subprocess起動と再接続ループを伴うため、
    各イベント分岐とstate遷移を網羅検証できない。
    例外的に最小限の直接テストへ限定する。
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
            await watcher._process_stream(_aiter_lines(lines))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）

            assert state.host_status["host1"] == "connected"
            cached = state.remote_files["host1"]
            assert sorted(e.path for e in cached) == ["a.md", "sub/b.md"]
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
    async def test_upsert_adds_new_path(self):
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # 既存snapshotを与えてから、新規pathのupsertが追加されることを確認する。
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
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
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
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
        await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
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
            await watcher._process_stream(_aiter_lines([json.dumps({"type": "ping"}) + "\n"]))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
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
            await watcher._process_stream(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）
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
    async def test_snapshot_resets_backoff(self):
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


class _FakeStdin:
    """テスト用の擬似`StreamWriter`。`write`/`drain`/`is_closing`のみ実装する。

    `RemoteWatcher.request`が呼び出すstdin APIを最小限満たす。
    送出されたバイト列は`buffer`に蓄積し、テストから解析できる。
    """

    def __init__(self) -> None:
        self.buffer: list[bytes] = []
        self._closing = False

    def write(self, data: bytes) -> None:
        self.buffer.append(data)

    async def drain(self) -> None:
        return

    def is_closing(self) -> bool:
        return self._closing

    def mark_closing(self) -> None:
        self._closing = True


class _FakeProc:
    """テスト用の擬似`asyncio.subprocess.Process`。stdinのみ提供する。"""

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = None
        self.stderr = None
        self.returncode = None


def _attach_fake_connection(watcher: _remote.RemoteWatcher) -> _FakeProc:
    """`RemoteWatcher`を擬似的に接続済みにする。

    `_connect`を経由せずに`_proc`/`_connected`を直接設定し、
    RPCテストを最小限の依存で記述できるようにする。
    SSH/subprocess起動を伴う公開経路（`run()`）では単体テスト内で接続状態を注入できないため、
    引数注入では到達不能なグローバル状態として直接設定する。
    """
    proc = _FakeProc()
    watcher._proc = typing.cast(typing.Any, proc)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH接続状態の直接注入）
    watcher._connected = True  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH接続状態の直接注入）
    return proc


class TestRemoteWatcherRpc:
    """`RemoteWatcher`の双方向RPCの単体検証。"""

    @pytest.mark.asyncio
    async def test_request_resolves_with_response_event(self):
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
    async def test_request_when_disconnected_raises(self):
        """切断状態（`_connected=False`）の`request`はRuntimeErrorを送出する。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # `_proc`は設定するが`_connected`はFalseのままにする。
        _attach_fake_connection(watcher)
        watcher._connected = False  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（接続状態の直接制御が必要）
        with pytest.raises(RuntimeError, match="not connected"):
            await watcher.request("read", {"path": "Zg=="})

    @pytest.mark.asyncio
    async def test_fail_pending_breaks_inflight_requests(self):
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
    async def test_request_timeout_removes_pending(self):
        """応答が届かないとtimeoutでTimeoutErrorとなり、pendingエントリが残らない。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        _attach_fake_connection(watcher)

        with pytest.raises(asyncio.TimeoutError):
            await watcher.request("read", {"path": "Zg=="}, timeout=0.05)
        # 失敗側でpendingが除去されていること（後続応答の遅延配達でメモリリークしない）。
        assert not watcher._pending  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（pending辞書の直接観測が必要）


class TestFetchRemoteFile:
    """`fetch_remote_file`のRPC優先・フォールバック分岐とmtime同梱の挙動を検証する。"""

    @pytest.mark.asyncio
    async def test_uses_watcher_rpc_when_connected(self):
        """watcherが接続中ならRPCで取得し、フォールバック経路の`ssh_runner`は呼ばれない。"""
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        _attach_fake_connection(watcher)
        runner = _FakeSshRunner()

        async def _drive() -> None:
            await asyncio.sleep(0.05)
            await watcher._handle_event(  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess stdoutから配信されるイベントを単体で注入するため）
                {
                    "type": "response",
                    "id": 1,
                    "ok": True,
                    "data": base64.b64encode(b"# remote\n").decode("ascii"),
                    "mtime_epoch": 42.0,
                }
            )

        drive_task = asyncio.create_task(_drive())
        text, mtime = await asyncio.wait_for(
            _remote.fetch_remote_file("host1", "foo.md", runner, watcher),
            timeout=1.0,
        )
        await drive_task

        assert text == "# remote\n"
        assert mtime == 42.0
        # フォールバック経路は使われない。
        assert not runner.calls

    @pytest.mark.asyncio
    async def test_falls_back_when_watcher_disconnected(self):
        """watcher未接続時はフォールバック経路の`ssh_runner`で取得し、mtimeも返す。"""
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): ("# fallback\n", 7.0)})
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        # `_connected=False`のまま渡す。

        text, mtime = await _remote.fetch_remote_file("host1", "foo.md", runner, watcher)

        assert text == "# fallback\n"
        assert mtime == 7.0
        read_calls = [c for c in runner.calls if c[1] == "read"]
        assert len(read_calls) == 1

    @pytest.mark.asyncio
    async def test_falls_back_on_rpc_error_response(self):
        """watcherが`ok=False`を返した場合もフォールバック経由で救済する。"""
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): ("# fallback\n", 8.0)})
        state = _state.BroadcastState()
        watcher = _remote.RemoteWatcher("host1", state)
        _attach_fake_connection(watcher)

        async def _drive() -> None:
            await asyncio.sleep(0.05)
            await watcher._handle_event({"type": "response", "id": 1, "ok": False, "error": "permission denied"})  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess stdoutから配信されるイベントを単体で注入するため）

        drive_task = asyncio.create_task(_drive())
        text, mtime = await asyncio.wait_for(
            _remote.fetch_remote_file("host1", "foo.md", runner, watcher),
            timeout=1.0,
        )
        await drive_task

        assert text == "# fallback\n"
        assert mtime == 8.0
        # フォールバック経路が1回だけ呼ばれる。
        read_calls = [c for c in runner.calls if c[1] == "read"]
        assert len(read_calls) == 1

    @pytest.mark.asyncio
    async def test_returns_none_mtime_when_missing_in_payload(self):
        """応答に`mtime_epoch`が欠落していると`mtime`は`None`になる（キャッシュバイパス目的）。

        フォールバック経路（`ssh_runner`単発呼び出し）でヘルパーが`mtime_epoch`キーを返さない
        ケースを`_FakeSshRunner`で再現し、`fetch_remote_file`の戻り値`mtime`が`None`になることを
        公開インターフェース経由で確認する。
        """
        runner = _FakeSshRunner(read_responses={("host1", "foo.md"): ("hello", None)})
        # watcher=Noneでフォールバック経路（RPC不在）を強制する。
        text, mtime = await _remote.fetch_remote_file("host1", "foo.md", runner, None)

        assert text == "hello"
        assert mtime is None


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
    async def test_api_file_caches_remote_response_by_mtime(self, tmp_path: Path):
        """リモート応答のmtimeをキーにMarkdownキャッシュへ格納し、同一ファイルの再要求でssh呼び出しが増えない。"""
        runner = _FakeSshRunner(
            read_responses={("host1", "foo.md"): ("# remote\n", 1234.5)},
        )
        app = _app.create_app(
            tmp_path,
            hostname="local-host",
            remote_hosts=["host1"],
            ssh_runner=runner,
        )
        client = app.test_client()
        first = await client.get("/api/file?host=host1&path=foo.md")
        second = await client.get("/api/file?host=host1&path=foo.md")

        assert first.status_code == 200
        assert second.status_code == 200
        cache: _local.MarkdownCache = app.config["PLANS_MARKDOWN_CACHE"]
        # `(host, rel, mtime_epoch)`キーで格納されている。
        assert cache.get(("host1", "foo.md", 1234.5)) is not None

    @pytest.mark.asyncio
    async def test_api_file_caches_local_response_by_mtime(self, tmp_path: Path):
        """ローカル応答も`stat`から取得した`mtime_epoch`をキーにMarkdownキャッシュへ格納する。"""
        target = tmp_path / "a.md"
        target.write_text("# title\n", encoding="utf-8")
        os.utime(target, (4_200.0, 4_200.0))
        app = _app.create_app(tmp_path, hostname="local-host")
        client = app.test_client()
        await client.get("/api/file?path=a.md")

        cache: _local.MarkdownCache = app.config["PLANS_MARKDOWN_CACHE"]
        # ローカルの`mtime_epoch`はstatのst_mtimeに一致する。
        assert cache.get(("local-host", "a.md", 4_200.0)) is not None

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
        await watcher._process_stream(_aiter_lines([json.dumps({"type": "snapshot", "entries": []}) + "\n"]))  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（SSH/subprocess起動を伴う公開経路run()を単体で網羅不能）

        client = app.test_client()
        response = await client.get("/api/host-status")
        data = json.loads(await response.get_data())
        assert data == {"local-host": "connected", "host1": "connected"}


class TestBuildRemoteCommand:
    """`_build_remote_command_argv`はPOSIXシェル非依存・cmd.exe互換であること。

    Windows OpenSSHの既定シェル`cmd.exe`では`bash -c`・heredoc・`head -c`等の
    POSIX組み込みが解釈できない。リモート起動コマンドはこれらに依存せず、
    クォート境界はダブルクォートのみで表現する不変条件を持つ。

    本クラスのテストはprivate関数`_build_remote_command_argv`を直接検証する。
    公開経路（`default_ssh_runner`・`RemoteWatcher._connect`）経由ではSSH/subprocessの
    実起動が必要で、Windowsシェル互換性の境界条件を網羅検証できない。
    例外的に最小限の直接テストへ限定する。
    """

    @pytest.mark.parametrize("op,args", [("serve", []), ("read", ["YWJjLm1k"])])
    def test_excludes_posix_shell_idioms(self, op: str, args: list[str]):
        """argv連結文字列にPOSIXシェル組み込み・heredoc・パイプ等が含まれない。"""
        argv = _remote._build_remote_command_argv(op, args)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（Windows cmd.exe互換のシェル境界条件を個別に検証するため実SSH起動不可）
        joined = " ".join(argv)
        # POSIXシェル非依存に必須となる禁止トークン。
        for token in ("bash ", "bash\t", "head ", "mkdir ", "<<", "<<<", "<<-", "exec ", " | ", "&&", "||"):
            assert token not in joined, f"{token!r} unexpectedly present: {joined!r}"
        # 単独の文字としてリダイレクト・パイプ・cmd.exeエスケープが現れない。
        # `>=`はwatchdogバージョン指定子として`"..."`内に閉じ込められて出現するため除外する。
        for ch in ("|", "&", "^"):
            assert ch not in joined, f"{ch!r} unexpectedly present: {joined!r}"

    def test_python_bootstrap_excludes_shell_specials(self):
        """bootstrapコード本体にはPOSIX/cmd.exeで意味を持つ特殊文字を含めない。"""
        bootstrap = _remote.REMOTE_BOOTSTRAP
        # `$`はPOSIXのダブルクォート内でも展開される。`%`はcmd.exeでも展開される。
        # `<`・`>`・`|`・`&`・`^`はクォート外でリダイレクト・連結・エスケープに解釈される。
        # `\`はPOSIXダブルクォート内でエスケープ扱いになる。
        for ch in ("$", "%", "<", ">", "|", "&", "^", "\\"):
            assert ch not in bootstrap, f"bootstrap contains forbidden char {ch!r}: {bootstrap!r}"

    def test_op_and_args_appended_at_tail(self):
        """`op`と`args`はargv末尾に未加工のまま追加される（ヘルパーはsys.argvで読み取る）。"""
        argv = _remote._build_remote_command_argv("read", ["YWJjLm1k"])  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（Windows cmd.exe互換のシェル境界条件を個別に検証するため実SSH起動不可）
        assert argv[-2:] == ["read", "YWJjLm1k"]

    def test_uses_double_quote_boundaries_only(self):
        """空白を含む要素は両端ダブルクォートで囲み、シングルクォート境界は使わない。"""
        argv = _remote._build_remote_command_argv("serve", [])  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（Windows cmd.exe互換のシェル境界条件を個別に検証するため実SSH起動不可）
        for elem in argv:
            if " " not in elem:
                continue
            # cmd.exeはシングルクォートをクォート境界として認識しない。
            assert elem.startswith('"') and elem.endswith('"'), elem


class _FakeProcessForTerminate:
    """`_terminate_process`の段階的フォールバック検証用の擬似Process。

    `exits_on`で「どの段階で終了するか」を制御し、ヘルパーがstdin EOF / SIGTERM /
    SIGKILLのいずれで停止するかを再現する。`wait()`はreturncodeが入るまで待つ。
    """

    def __init__(self, exits_on: typing.Literal["stdin_close", "terminate", "kill", "never"]) -> None:
        self._exits_on = exits_on
        self.stdin = self._Stdin(self._on_stdin_close)
        self.returncode: int | None = None
        self.terminate_called = False
        self.kill_called = False
        self._wait_event = asyncio.Event()

    class _Stdin:
        def __init__(self, on_close: typing.Callable[[], None]) -> None:
            self._closing = False
            self._on_close = on_close

        def is_closing(self) -> bool:
            return self._closing

        def close(self) -> None:
            if not self._closing:
                self._closing = True
                self._on_close()

    def _on_stdin_close(self) -> None:
        if self._exits_on == "stdin_close":
            self._set_exited(0)

    def terminate(self) -> None:
        self.terminate_called = True
        if self._exits_on == "terminate":
            self._set_exited(-15)

    def kill(self) -> None:
        self.kill_called = True
        if self._exits_on == "kill":
            self._set_exited(-9)

    def _set_exited(self, rc: int) -> None:
        if self.returncode is None:
            self.returncode = rc
            self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        assert self.returncode is not None
        return self.returncode


class TestTerminateProcess:
    """`_terminate_process`の段階的フォールバック挙動を検証する。

    本クラスのテストはprivate関数`_terminate_process`を直接検証する。
    段階的停止経路（stdin close→terminate→kill）は実プロセス起動を伴わないと
    各フォールバック段を選択的に発火できず、公開経路（`RemoteWatcher.run`の
    キャンセル経路）では実SSH/subprocess起動が必要となる。
    例外的に最小限の直接テストへ限定する。
    """

    @pytest.mark.asyncio
    async def test_stdin_close_triggers_graceful_exit(self):
        """stdin closeで停止経路に乗る場合、terminate/killは呼ばれない。"""
        proc = _FakeProcessForTerminate(exits_on="stdin_close")
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.1)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert proc.returncode == 0
        assert not proc.terminate_called
        assert not proc.kill_called

    @pytest.mark.asyncio
    async def test_terminate_after_stdin_unresponsive(self):
        """stdin closeで応答が無ければterminateで停止し、killは呼ばれない。"""
        proc = _FakeProcessForTerminate(exits_on="terminate")
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.05)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert proc.returncode == -15
        assert proc.terminate_called
        assert not proc.kill_called

    @pytest.mark.asyncio
    async def test_kill_when_process_is_unresponsive(self):
        """terminateにも応答しないプロセスはkillで打ち切られる。"""
        proc = _FakeProcessForTerminate(exits_on="kill")
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.05)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert proc.returncode == -9
        assert proc.terminate_called
        assert proc.kill_called

    @pytest.mark.asyncio
    async def test_already_exited_process_is_no_op(self):
        """既に終了済みのプロセスはstdin close・terminate・killいずれも呼ばれない。"""
        proc = _FakeProcessForTerminate(exits_on="never")
        proc.returncode = 0
        await _remote._terminate_process(typing.cast(typing.Any, proc), grace_timeout=0.05)  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（実プロセス起動なしに停止フォールバック段を選択的に発火するため）
        assert not proc.terminate_called
        assert not proc.kill_called
        assert not proc.stdin.is_closing()


class TestRemoteStreamLimit:
    """`asyncio.create_subprocess_exec`既定StreamReader上限超過時の挙動。

    本クラスのテストはprivate関数`_iter_stream_lines`を`asyncio.StreamReader`へ直接渡して
    検証する。`limit`引上げ後の挙動は実subprocess起動を伴う公開経路（`RemoteWatcher._connect`）
    では再現コストが高く、`asyncio.StreamReader`を直接構成する直接テストの方が安定する。
    例外的に最小限の直接テストへ限定する。
    """

    @pytest.mark.asyncio
    async def test_iter_stream_lines_handles_oversized_line(self):
        """64KiB既定上限を超える1行をlimit引き上げ後のStreamReaderで読み取れる。

        modules内に専用モジュールを足さず、`asyncio.StreamReader`に対し
        `_iter_stream_lines`の前提（`readline()`が分離記号を見つけるまで読み続ける）が
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

        # REMOTE_STREAM_LIMIT_BYTES適用後はそのまま読み終えられる。
        big_reader = asyncio.StreamReader(limit=_remote.REMOTE_STREAM_LIMIT_BYTES)
        big_reader.feed_data(big_payload.encode("utf-8"))
        big_reader.feed_eof()

        async def _consume() -> list[str]:
            collected: list[str] = []
            async for line in _remote._iter_stream_lines(big_reader):  # pylint: disable=protected-access  # noqa: SLF001  # 引数注入では到達不能（asyncio.StreamReaderを直接構成しlimit引き上げ後の読み取りを確認するため）
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

    ASGI scopeでは`root_path`に対しQuartが`path`の冒頭から同値を除去するため、
    リバースプロキシは「prefixを保持したままバックエンドへ転送する」構成（nginxで
    `proxy_pass http://backend;`をtrailing slash無しで指定する形）を想定する。
    テストもクライアントがプレフィクス付きの絶対URLへ要求する前提で組み立てる。
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
        assert 'href="/plans/manifest.webmanifest" crossorigin="use-credentials"' in body
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

    @pytest.mark.asyncio
    async def test_protocol_relative_prefix_rejected_by_proxy_fix(self, tmp_path: Path):
        """プロトコル相対形式のプレフィクスはProxyFix層で拒否される。

        pytilpackの`validate_forwarded_prefix`が先頭`//`を不正値として拒否するため
        `root_path`は設定されず、Quartは`//evil.example/`をルート未マッチとして404を返す。
        `safe_base_path`へ到達する前段で防御される二段構えを担保する。
        """
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response = await client.get("//evil.example/", headers={"X-Forwarded-Prefix": "//evil.example"})
        assert response.status_code == 404
        body = await response.get_data(as_text=True)
        assert "//evil.example" not in body

    @pytest.mark.asyncio
    async def test_routable_malicious_prefix_neutralized_in_output(self, tmp_path: Path):
        """ルート到達可能な悪意プレフィクスでも出力に生バイトが漏れない。

        途中に`//`を含むプレフィクスはProxyFix層を通過してroot_pathに設定され、
        Quartがprefix除去してルートに到達する。`safe_base_path`が空扱いに正規化するため、
        HTML属性・JS定数・manifestのいずれにもプレフィクス文字列が漏れない。
        """
        malicious_path = "/foo//bar/"
        prefix_header = "/foo//bar"
        app = _app.create_app(tmp_path, hostname="test")
        client = app.test_client()
        response_index = await client.get(malicious_path, headers={"X-Forwarded-Prefix": prefix_header})
        body_index = await response_index.get_data(as_text=True)
        assert response_index.status_code == 200
        assert prefix_header not in body_index
        assert 'href="/favicon.svg"' in body_index
        assert 'const BASE_PATH = "";' in body_index

        response_manifest = await client.get(
            f"{malicious_path}manifest.webmanifest",
            headers={"X-Forwarded-Prefix": prefix_header},
        )
        manifest = json.loads(await response_manifest.get_data())
        assert manifest["start_url"] == "/"
        assert manifest["icons"][0]["src"] == "/favicon.svg"
