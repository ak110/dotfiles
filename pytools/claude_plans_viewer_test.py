"""pytools.claude_plans_viewer のテスト。"""

import io
import json
import logging
import os
import re
import sys
from pathlib import Path

import pytest

from pytools.claude_plans_viewer import _assets, _cli, _config, _console_title, _local

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
        # `FileEntry`はサイズを保持しない。
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

    def test_highlights_fenced_code_with_language(self):
        """言語指定ありフェンスはPygmentsの`<span class>`が出力される。"""
        src = '```python\nprint("hi")\n```\n'

        html = _local.markdown_to_html(src)

        assert 'class="codehilite language-python"' in html
        # Pygmentsのトークンクラスが含まれる（具体クラスはPygmentsバージョン依存だが`<span class`が出ること自体は安定）。
        assert "<span class=" in html

    def test_falls_back_to_plain_pre_without_language(self):
        """言語指定なしフェンスはmarkdown-it既定の素通し描画にフォールバックする。"""
        src = "```\nplain\n```\n"

        html = _local.markdown_to_html(src)

        assert "<pre><code>plain\n</code></pre>" in html
        assert "codehilite" not in html

    def test_falls_back_to_plain_pre_for_unknown_language(self):
        """未知言語フェンスもフォールバックして既定描画になる。"""
        src = "```nosuchlang\nplain\n```\n"

        html = _local.markdown_to_html(src)

        assert "<pre><code" in html
        assert "codehilite" not in html


class TestReadPygmentsCss:
    """`read_pygments_css`のテスト。"""

    def test_returns_codehilite_style_defs(self):
        """`.codehilite`スコープのスタイル定義を含む文字列を返す。"""
        css = _local.read_pygments_css()
        assert ".codehilite" in css

    def test_excludes_base_rule_line(self):
        """`.codehilite { ... }`の単独セレクタ行（背景・文字色）は含まれない。"""
        css = _local.read_pygments_css()
        for line in css.splitlines():
            stripped = line.strip()
            assert not (stripped.startswith(".codehilite {") or stripped.startswith(".codehilite{")), (
                f"基本ルール行が除外されていない: {line!r}"
            )

    def test_includes_token_specific_rules(self):
        """トークン別カラールール（`.codehilite .k`等）は含まれる。"""
        css = _local.read_pygments_css()
        # `.codehilite .<token>`形式（スペース区切りで子孫セレクタを持つ行）が存在すること。
        token_rules = [line for line in css.splitlines() if line.strip().startswith(".codehilite .")]
        assert token_rules, "トークン別ルール（`.codehilite .<token>`形式）が見つからない"


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

        Chrome 93以降はno-opのfetchハンドラーをDevToolsで警告対象とするため、
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


@pytest.mark.usefixtures("_parse_args_isolate_env")
class TestMainLoggingConfig:
    """`main()`が構成する`logging`ハンドラーのフォーマットを検証する。"""

    @pytest.fixture(autouse=True)
    def _restore_root_logger(self):
        """root loggerのハンドラー・レベルを退避し、テスト後に復元する。

        `main()`は`logging.basicConfig(force=True)`でroot loggerを再初期化するため、
        検証後に復元しないと他テストのログ捕捉（`caplog`等）へ副作用が及ぶ。
        """
        root_logger = logging.getLogger()
        saved_handlers = root_logger.handlers[:]
        saved_level = root_logger.level
        yield
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)

    def test_main_configures_logging_format_with_datetime_and_level(self, tmp_path: Path):
        """`logging.basicConfig`の`format`引数に日時・ロガー名・レベルが含まれることを確認する。

        `force=True`により、pytest実行環境で既存ハンドラーが付与済みでも再初期化される。
        `main()`本体のhypercorn起動・observer起動はディレクトリ不在エラーで
        早期returnさせることで回避する（`root`検証は`app`生成より前に実行されるため）。
        """
        result = _cli.main(["--root", str(tmp_path / "missing")])

        assert result == 1
        handler = logging.getLogger().handlers[0]
        record = logging.LogRecord(
            name="test-logger", level=logging.INFO, pathname=__file__, lineno=1, msg="hello", args=None, exc_info=None
        )
        formatted = handler.format(record)
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} test-logger INFO hello$", formatted), formatted


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

        # `onopen`・`onmessage`の両ハンドラーが設定されていること。
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
        # clickハンドラーが`/api/raw`からfetchして`navigator.clipboard.writeText`へ渡す。
        # 多ホスト統合のため`host`と`path`の両クエリを組み立てる`fileQuery`を経由する。
        assert "/api/raw?" in html_src
        assert "navigator.clipboard.writeText" in html_src
        assert "function fileQuery" in html_src
        # 成否のフィードバックはボタン文言の一時的な書き換えで示す。
        assert "コピーしました" in html_src
        assert "コピーに失敗しました" in html_src

    def test_index_html_has_copy_path_button_contract(self):
        """右ペインのtoolbarに計画ファイルパスコピーボタンが存在する契約。"""
        html_src = _assets.INDEX_HTML

        assert 'id="copy-path-btn"' in html_src
        assert "async function copySelectedPath" in html_src
        assert "if (!selectedPath || !selectedHost) return" in html_src
        assert "const LOCAL_HOST_NAME = __LOCAL_HOST_NAME_JS__" in html_src
        assert "const ROOT_DIR = __ROOT_DIR_JS__" in html_src
        assert 'document.getElementById("copy-path-btn").disabled = host !== LOCAL_HOST_NAME' in html_src
        assert 'document.getElementById("copy-path-btn").addEventListener("click", copySelectedPath)' in html_src

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
        # disabledを制御する関数があり、prev/nextの両方を更新する。
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
