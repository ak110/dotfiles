"""pytools.claude_plans_viewer のテスト。"""

import datetime
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import sys
import typing
from pathlib import Path

import pytest

from pytools._internal import claude_common
from pytools.claude_plans_viewer import _app, _assets, _cli, _config, _console_title, _local

# _state._BROADCAST_DEBOUNCE_SEC と同値（0.3秒）。debounce窓の秒数。
_BROADCAST_DEBOUNCE_SEC = 0.3
# SSE refreshメッセージの仕様。_state._SSE_REFRESH_PAYLOAD と同一値（SSE refreshメッセージ仕様）。
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)

# `schedule_broadcast`経由のrefresh待ちは`_BROADCAST_DEBOUNCE_SEC`後に配信されるため、
# debounce窓にマージン0.7秒を加えた値をタイムアウトとする。
_QUEUE_GET_TIMEOUT_SEC = _BROADCAST_DEBOUNCE_SEC + 0.7


@pytest.fixture(autouse=True)
def _isolate_creation_time_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """作成日時インデックスを一時領域へ隔離し、開発者環境の利用者キャッシュへ書き込まないようにする。"""
    monkeypatch.setattr(_local, "_CREATION_TIME_INDEX_PATH", tmp_path / "creation-times" / "index.json")


class TestListFiles:
    """list_files のテスト。"""

    def test_sorts_by_ctime_desc(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """作成日時（ctime）降順で返ること。

        `st_ctime`（Linux上のフォールバック値）は`os.utime`で直接制御できず、
        `st_birthtime`は環境依存のため、`_local._ctime_epoch`を
        `st_mtime`の符号反転値（mtime降順とは逆順になる値）へ差し替えて、
        ソート基準が`mtime_epoch`ではなく`ctime_epoch`であることを決定論的に検証する。
        """
        old_path = tmp_path / "old.md"
        old_path.write_text("old", encoding="utf-8")
        os.utime(old_path, (1_000.0, 1_000.0))

        new_path = tmp_path / "new.md"
        new_path.write_text("new", encoding="utf-8")
        os.utime(new_path, (2_000.0, 2_000.0))

        monkeypatch.setattr(_local, "_ctime_epoch", lambda st: -st.st_mtime)

        entries = _local.list_files(tmp_path, "local-host")

        # mtime降順なら["new.md", "old.md"]になるはずだが、ctime_epoch降順（mtimeと逆順）を
        # 使うため["old.md", "new.md"]となる。
        assert [e.path for e in entries] == ["old.md", "new.md"]
        # mtimeは`yyyy/MM/dd HH:mm`書式で整形される。
        pattern = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
        for entry in entries:
            assert pattern.match(entry.mtime), entry.mtime
        # `FileEntry`はサイズを保持しない。
        assert not hasattr(entries[0], "size")
        # `mtime_epoch`・`ctime_epoch`・`host`を保持すること
        # （クライアント側mtime変化検知・ソート・多ホスト識別に使用）。
        assert hasattr(entries[0], "mtime_epoch")
        assert hasattr(entries[0], "ctime_epoch")
        assert all(e.host == "local-host" for e in entries)

    @pytest.mark.asyncio
    async def test_api_order_and_display_time_use_ctime(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """APIの一覧順と作成日時表示が同じ`ctime_epoch`に基づくこと。"""
        old_path = tmp_path / "old.md"
        old_path.write_text("old", encoding="utf-8")
        os.utime(old_path, (1_000.0, 1_000.0))
        new_path = tmp_path / "new.md"
        new_path.write_text("new", encoding="utf-8")
        os.utime(new_path, (2_000.0, 2_000.0))
        # mtimeとは逆順になる正の作成日時を与え、ソートと表示値の対応を同時に検証する。
        monkeypatch.setattr(_local, "_ctime_epoch", lambda st: 3_000.0 - st.st_mtime)
        app = _app.create_app(tmp_path, hostname="local-host")

        response = await app.test_client().get("/api/files")

        assert response.status_code == 200
        entries = json.loads(await response.get_data())
        assert [entry["path"] for entry in entries] == ["old.md", "new.md"]
        tzinfo = datetime.datetime.now().astimezone().tzinfo
        expected_ctimes = [
            datetime.datetime.fromtimestamp(entry["ctime_epoch"], tz=tzinfo).strftime("%Y/%m/%d %H:%M") for entry in entries
        ]
        assert [entry["ctime"] for entry in entries] == expected_ctimes
        assert [entry["ctime"] for entry in entries] != [entry["mtime"] for entry in entries]

    def test_includes_only_md(self, tmp_path: Path):
        """.md以外は含まず、サブディレクトリは再帰的に拾うこと。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.md").write_text("x", encoding="utf-8")

        entries = _local.list_files(tmp_path, "local-host")

        assert sorted(e.path for e in entries) == ["a.md", "sub/c.md"]

    def test_excludes_dotdir_entries(self, tmp_path: Path):
        """隠しディレクトリ配下・隠しファイルの`.md`は一覧へ含めないこと。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        (tmp_path / ".cache").mkdir()
        (tmp_path / ".cache" / "b.md").write_text("x", encoding="utf-8")
        (tmp_path / ".hidden.md").write_text("x", encoding="utf-8")

        entries = _local.list_files(tmp_path, "local-host")

        assert sorted(e.path for e in entries) == ["a.md"]

    def test_cached_creation_time_survives_file_updates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """生成時刻が無い環境では、編集後も初回観測時の作成日時を維持する。"""
        root = tmp_path / "plans"
        root.mkdir()
        index_path = tmp_path / "cache" / "index.json"
        monkeypatch.setattr(_local, "_CREATION_TIME_INDEX_PATH", index_path)
        # `st_birthtime`の有無で観測値が変わらないよう、更新日時を観測値として固定する。
        monkeypatch.setattr(_local, "_ctime_epoch", lambda st: float(st.st_mtime))
        path = root / "plan.md"
        path.write_text("初回", encoding="utf-8")
        os.utime(path, (1_000.0, 1_000.0))

        first = _local.list_files(root, "local-host")[0]
        path.write_text("更新後", encoding="utf-8")
        os.utime(path, (2_000.0, 2_000.0))
        second = _local.list_files(root, "local-host")[0]

        assert first.ctime_epoch == 1_000.0
        assert second.ctime_epoch == first.ctime_epoch
        # 単一インデックスへ集約され、値は`(host, root, 相対パス)`と作成日時を保持する。
        stored = json.loads(index_path.read_text(encoding="utf-8"))
        assert [entry["path"] for entry in stored.values()] == ["plan.md"]
        assert [entry["host"] for entry in stored.values()] == ["local-host"]
        assert [entry["ctime_epoch"] for entry in stored.values()] == [1_000.0]


class TestSearchFiles:
    """ローカル計画ファイルの本文検索を検証する。"""

    def test_matches_full_text_case_insensitively(self, tmp_path: Path):
        """ファイル名に無い検索語も本文の大文字小文字を区別せず検出する。"""
        (tmp_path / "first.md").write_text("Alpha NEEDLE omega", encoding="utf-8")
        (tmp_path / "second.md").write_text("対象外", encoding="utf-8")

        assert _local.search_files(tmp_path, "needle") == {"first.md"}

    def test_read_failure_is_not_a_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """一部ファイルの読み取りに失敗しても他の一致結果を返す。"""
        good = tmp_path / "good.md"
        bad = tmp_path / "bad.md"
        good.write_text("検索語", encoding="utf-8")
        bad.write_text("検索語", encoding="utf-8")
        original = Path.read_text

        def read_text(
            path: Path,
            encoding: str | None = None,
            errors: str | None = None,
        ) -> str:
            if path == bad:
                raise OSError("読み取り失敗")
            return original(path, encoding=encoding, errors=errors)

        monkeypatch.setattr(Path, "read_text", read_text)

        assert _local.search_files(tmp_path, "検索語") == {"good.md"}

    def test_excludes_dotdir_entries_for_empty_and_nonempty_query(self, tmp_path: Path):
        """空クエリ・非空クエリの双方で隠しディレクトリ配下の`.md`を返さないこと。"""
        (tmp_path / "visible.md").write_text("検索語", encoding="utf-8")
        (tmp_path / ".cache").mkdir()
        (tmp_path / ".cache" / "hidden.md").write_text("検索語", encoding="utf-8")

        assert _local.search_files(tmp_path, "") == {"visible.md"}
        assert _local.search_files(tmp_path, "検索語") == {"visible.md"}


class TestTargetPathConsistency:
    """一覧・検索・監視の3経路の対象集合を検証する。

    読取・検索・変更監視は`is_target_path`を共有し、付属計画`.detail.md`・`.bugs.md`も対象へ含める。
    一覧だけは`is_listed_path`を使い、付属計画を除外する。
    """

    def test_list_search_and_watch_agree_on_target_set(self, tmp_path: Path):
        """`root`直下・サブディレクトリ・隠しディレクトリ・隠しファイルの各形態で3経路が一致すること。"""
        candidates = [
            tmp_path / "top.md",
            tmp_path / "sub" / "nested.md",
            tmp_path / ".cache" / "hidden.md",
            tmp_path / ".hidden.md",
            tmp_path / "sub" / "note.txt",
        ]
        for path in candidates:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("本文", encoding="utf-8")

        listed = {entry.path for entry in _local.list_files(tmp_path, "local-host")}
        searched = _local.search_files(tmp_path, "本文")
        # 監視経路の判定は`is_target_path`へ集約済みのため、同関数の結果を3経路目として突き合わせる。
        watched = {path.relative_to(tmp_path).as_posix() for path in candidates if _local.is_target_path(path, tmp_path)}

        assert listed == {"top.md", "sub/nested.md"}
        assert searched == listed
        assert watched == listed

    def test_dotdir_root_includes_own_descendants(self, tmp_path: Path):
        """`root`自身が隠しディレクトリでも、その直下および配下の非隠しパスが対象へ含まれること。"""
        root = tmp_path / ".claude_like"
        (root / "sub").mkdir(parents=True)
        (root / "top.md").write_text("本文", encoding="utf-8")
        (root / "sub" / "nested.md").write_text("本文", encoding="utf-8")
        (root / ".cache").mkdir()
        (root / ".cache" / "hidden.md").write_text("本文", encoding="utf-8")

        listed = {entry.path for entry in _local.list_files(root, "local-host")}

        assert listed == {"top.md", "sub/nested.md"}
        assert _local.search_files(root, "本文") == listed
        assert _local.is_target_path(root / "top.md", root)
        assert not _local.is_target_path(root / ".cache" / "hidden.md", root)

    def test_detail_file_excluded_from_list_but_included_in_search_and_watch(self, tmp_path: Path) -> None:
        """付属計画は一覧から除外し、検索・変更監視の対象には含める。"""
        main = tmp_path / "plan.md"
        detail = tmp_path / "plan.detail.md"
        bugs = tmp_path / "plan.bugs.md"
        main.write_text("本文", encoding="utf-8")
        detail.write_text("本文", encoding="utf-8")
        bugs.write_text("本文", encoding="utf-8")

        listed = {entry.path for entry in _local.list_files(tmp_path, "local-host")}
        searched = _local.search_files(tmp_path, "本文")

        assert listed == {"plan.md"}
        assert searched == {"plan.md", "plan.detail.md", "plan.bugs.md"}
        assert _local.is_target_path(detail, tmp_path)
        assert not _local.is_listed_path(detail, tmp_path)
        assert _local.is_target_path(bugs, tmp_path)
        assert not _local.is_listed_path(bugs, tmp_path)
        assert _local.is_listed_path(main, tmp_path)


class TestCreationTimeIndex:
    """作成日時の単一インデックスの回収・移行・一時ファイル除去を検証する。"""

    @staticmethod
    def _legacy_path(index_path: Path, host: str, rel: str) -> Path:
        """旧形式（1エントリ1ファイル）のキャッシュパスを組み立てる。"""
        digest = hashlib.sha256(f"{host}\0{rel}".encode()).hexdigest()
        return index_path.parent / f"{digest}.json"

    @staticmethod
    def _write_legacy(path: Path, host: str, rel: str, ctime_epoch: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"host": host, "path": rel, "ctime_epoch": ctime_epoch}), encoding="utf-8")

    @pytest.fixture(name="index_path")
    def _index_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        path = tmp_path / "cache" / "index.json"
        monkeypatch.setattr(_local, "_CREATION_TIME_INDEX_PATH", path)
        monkeypatch.setattr(_local, "_ctime_epoch", lambda st: float(st.st_mtime))
        return path

    def test_absent_entries_are_pruned_and_other_roots_are_kept(self, tmp_path: Path, index_path: Path):
        """走査結果に現れないキーを回収し、別の`root`に属するキーは維持する。"""
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()
        (first_root / "keep.md").write_text("x", encoding="utf-8")
        (first_root / "gone.md").write_text("x", encoding="utf-8")
        (second_root / "other.md").write_text("x", encoding="utf-8")

        _local.list_files(first_root, "local-host")
        _local.list_files(second_root, "local-host")
        (first_root / "gone.md").unlink()
        _local.list_files(first_root, "local-host")

        stored = json.loads(index_path.read_text(encoding="utf-8"))
        assert sorted((entry["root"].rsplit("/", 1)[-1], entry["path"]) for entry in stored.values()) == [
            ("first", "keep.md"),
            ("second", "other.md"),
        ]

    def test_migrates_matching_legacy_entry(self, tmp_path: Path, index_path: Path):
        """`host`と相対パスが走査対象と一致する旧形式の作成日時を取り込み、取り込んだ旧形式を削除する。"""
        root = tmp_path / "plans"
        root.mkdir()
        path = root / "plan.md"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (2_000.0, 2_000.0))
        legacy = self._legacy_path(index_path, "local-host", "plan.md")
        self._write_legacy(legacy, "local-host", "plan.md", 500.0)

        entry = _local.list_files(root, "local-host")[0]

        assert entry.ctime_epoch == 500.0
        assert not legacy.exists()

    def test_keeps_legacy_entry_not_matching_current_scan(self, tmp_path: Path, index_path: Path):
        """走査対象と対応しない旧形式は別の`root`の記録であり得るため、移行も削除もしない。"""
        root = tmp_path / "plans"
        root.mkdir()
        (root / "plan.md").write_text("x", encoding="utf-8")
        legacy = self._legacy_path(index_path, "local-host", "elsewhere.md")
        self._write_legacy(legacy, "local-host", "elsewhere.md", 500.0)

        _local.list_files(root, "local-host")

        assert legacy.exists()
        stored = json.loads(index_path.read_text(encoding="utf-8"))
        assert [entry["path"] for entry in stored.values()] == ["plan.md"]

    def test_keeps_legacy_entry_when_index_write_fails(
        self,
        tmp_path: Path,
        index_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """インデックスの書き込みに失敗した場合は、再試行のため旧形式を残す。"""
        root = tmp_path / "plans"
        root.mkdir()
        path = root / "plan.md"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (2_000.0, 2_000.0))
        legacy = self._legacy_path(index_path, "local-host", "plan.md")
        self._write_legacy(legacy, "local-host", "plan.md", 500.0)

        def failing_write(*args: typing.Any, **kwargs: typing.Any) -> bool:
            del args, kwargs
            return False

        monkeypatch.setattr(claude_common, "atomic_write_json", failing_write)

        entry = _local.list_files(root, "local-host")[0]

        assert entry.ctime_epoch == 500.0
        assert legacy.exists()
        assert not index_path.exists()

    def test_cleanup_removes_only_temporaries(self, index_path: Path):
        """一時ファイルだけを除去し、インデックスとロックファイルは残す。"""
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("{}", encoding="utf-8")
        lock_path = index_path.with_name(index_path.name + ".lock")
        lock_path.write_text("", encoding="utf-8")
        new_temporary = index_path.with_name(f"{index_path.name}.abc123.tmp")
        new_temporary.write_text("", encoding="utf-8")
        legacy_digest = "a" * 64
        legacy_temporary = index_path.parent / f".{legacy_digest}.json.100.200.tmp"
        legacy_temporary.write_text("", encoding="utf-8")

        _local.cleanup_creation_time_temporaries()

        assert index_path.is_file()
        assert lock_path.is_file()
        assert not new_temporary.exists()
        assert not legacy_temporary.exists()

    def test_lock_failure_keeps_listing(self, tmp_path: Path, index_path: Path):
        """ロックを取得できない場合も一覧は成立し、観測値がそのまま作成日時になること。"""
        # ロックファイル名のディレクトリを配置し、ロックファイルを開けない状態を再現する。
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.with_name(index_path.name + ".lock").mkdir()
        root = tmp_path / "plans"
        root.mkdir()
        target = root / "note.md"
        target.write_text("本文", encoding="utf-8")

        entries = _local.list_files(root, "local-host")

        assert [entry.path for entry in entries] == ["note.md"]
        assert entries[0].ctime_epoch == target.stat().st_mtime
        assert not index_path.exists()

    def test_lock_failure_skips_cleanup(self, index_path: Path):
        """ロックを取得できない場合の一時ファイル除去は無動作で返ること。"""
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.with_name(index_path.name + ".lock").mkdir()
        temporary = index_path.with_name(f"{index_path.name}.abc123.tmp")
        temporary.write_text("", encoding="utf-8")

        _local.cleanup_creation_time_temporaries()

        assert temporary.is_file()


class TestLocalHostInfo:
    """local_host_info のテスト。"""

    def test_returns_home_key(self, tmp_path: Path):
        """`home`キーにホームディレクトリの絶対パスを含む契約。"""
        info = _local.local_host_info(tmp_path)

        assert info["home"] == str(Path.home()).replace("\\", "/")
        assert info["root"] == str(tmp_path).replace("\\", "/")


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

    def test_renders_mermaid_fence_as_escaped_diagram(self):
        """Mermaidフェンスはエスケープ済み原文を持つ専用コンテナーになる。"""
        src = "```mermaid\ngraph TD\n  A[<script>alert(1)</script>] --> B\n```\n"

        html = _local.markdown_to_html(src)

        assert '<figure class="diagram diagram-mermaid">' in html
        assert '<div class="diagram-output mermaid-output">' in html
        assert "<summary>Mermaid原文</summary>" in html
        assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 2
        assert "<script" not in html.lower()

    def test_renders_svg_fence_as_source_only_image(self):
        """SVGフェンスは`src`なしの画像とエスケープ済み原文になる。"""
        src = '```svg\n<svg onload="alert(1)"><script>alert(2)</script></svg>\n```\n'

        html = _local.markdown_to_html(src)

        assert '<figure class="diagram diagram-svg">' in html
        assert '<img class="diagram-output svg-output" alt="SVG図">' in html
        assert "<summary>SVG原文</summary>" in html
        assert "&lt;svg onload=&quot;alert(1)&quot;&gt;" in html
        assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
        assert "<script" not in html.lower()

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


@pytest.fixture(name="_restore_root_logger")
def _restore_root_logger_fixture():
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


@pytest.mark.usefixtures("_parse_args_isolate_env", "_restore_root_logger")
class TestMainLoggingConfig:
    """`main()`が構成する`logging`ハンドラーのフォーマットを検証する。"""

    def test_main_configures_logging_format_with_datetime_and_level(self, tmp_path: Path):
        """`logging.basicConfig`の`format`引数に日時・ロガー名・レベルが含まれることを確認する。

        `force=True`により、pytest実行環境で既存ハンドラーが付与済みでも再初期化される。
        `main()`本体のhypercorn起動・observer起動は、設定ファイルの構文エラーによる
        早期returnで回避する（`parse_args`は`logging`構成より後に実行されるため）。
        """
        (tmp_path / "config.toml").write_text("root =\n", encoding="utf-8")

        result = _cli.main([])

        assert result == 1
        handler = logging.getLogger().handlers[0]
        record = logging.LogRecord(
            name="test-logger", level=logging.INFO, pathname=__file__, lineno=1, msg="hello", args=None, exc_info=None
        )
        formatted = handler.format(record)
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} test-logger INFO hello$", formatted), formatted


@pytest.mark.usefixtures("_parse_args_isolate_env", "_restore_root_logger")
class TestMainRootDirectory:
    """`main()`が扱うローカル計画ディレクトリの契約を検証する。"""

    def test_missing_root_is_created_and_served(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """ディレクトリ不在から起動してもディレクトリが作成され配信へ進む。"""
        root = tmp_path / "missing" / "plans"
        served: list[tuple[str, int]] = []

        async def fake_serve(app: typing.Any, host: str, port: int, **kwargs: typing.Any) -> None:
            del app, kwargs
            served.append((host, port))

        monkeypatch.setattr(_cli, "serve", fake_serve)

        result = _cli.main(["--root", str(root), "--host", "127.0.0.1", "--port", "28999"])

        assert result == 0
        assert root.is_dir()
        assert served == [("127.0.0.1", 28999)]


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

    def test_index_html_does_not_restrict_same_origin_images(self):
        """同一オリジンのfaviconとMarkdown画像を遮断するCSPを埋め込まない。"""
        assert "Content-Security-Policy" not in _assets.INDEX_HTML

    def test_index_html_sizes_panes_with_small_viewport_height_unit(self):
        """高さを決めるビューポート単位を無印`vh`ではなく`svh`で書く。

        無印`vh`は大ビューポート基準のため、モバイルのブラウザーUI展開時に
        ペインの高さが可視領域を超え、下端の内容が隠れる。
        `dvh`ではなく`svh`を採用するのは、スクロールに伴うブラウザーUIの伸縮で
        高さが再レイアウトされることを避け、常に可視な下限側へ収めるためである。
        """
        assert not re.findall(r"\dvh\b", _assets.INDEX_HTML)
        assert "100svh" in _assets.INDEX_HTML

    def test_index_html_explicitly_configures_mermaid_strict_security(self):
        """Mermaidの既定値に依存せず`strict`を明示する。"""
        assert 'securityLevel: "strict"' in _assets.INDEX_HTML

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
        host_info経路も同様に取りこぼし得るため`refreshHostInfo`で救済する。
        """
        html_src = _assets.INDEX_HTML

        # `onopen`・`onmessage`の両ハンドラーが設定されていること。
        assert "es.onopen" in html_src
        assert "es.onmessage" in html_src
        # 再同期の実体は`refreshFiles`を呼ぶ`resyncFromServer`に集約されていること。
        assert "function resyncFromServer" in html_src or "async function resyncFromServer" in html_src
        # onopenではホスト状態とファイル一覧の両方を再同期する。
        assert "refreshHostStatus" in html_src
        assert "refreshHostInfo" in html_src
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
        """右ペインのtoolbarに計画ファイルパスコピーボタンが存在する契約。

        `ROOT_DIRS`は複数ホスト分（ローカル起動時注入＋リモート分SSEマージ＋`/api/host-info`再取得）の
        ホスト情報辞書契約であり、disabled判定は選択ホストが`ROOT_DIRS`に登録済みかどうかで行う。
        """
        html_src = _assets.INDEX_HTML

        assert 'id="copy-path-btn"' in html_src
        assert "async function copySelectedPath" in html_src
        assert "if (!selectedPath || !selectedHost) return" in html_src
        assert "const ROOT_DIRS = __ROOT_DIRS_JS__" in html_src
        assert 'document.getElementById("copy-path-btn").disabled = !(host in ROOT_DIRS)' in html_src
        assert 'document.getElementById("copy-path-btn").addEventListener("click", copySelectedPath)' in html_src
        # ホスト種別に応じたパス表記変換（POSIXはチルダ、WindowsはUSERPROFILE環境変数）。
        assert 'info.os_type === "nt"' in html_src
        assert "%USERPROFILE%" in html_src
        # パス置換基準はinfo.home（info.rootはplans直下パス等でホームディレクトリと一致しない場合がある）。
        assert '.replace(info.home, "~")' in html_src
        assert '.replace(winHome, "%USERPROFILE%")' in html_src
        # 取りこぼし対策の再取得経路。
        assert "/api/host-info" in html_src
        assert "async function refreshHostInfo" in html_src

    def test_index_html_refresh_host_info_avoids_stale_overwrite(self):
        """`refreshHostInfo`がfetch中に発生したSSE更新による巻き戻りを回避する契約。

        fetch開始前後で`hostInfoEventCounter`を比較し、変化していればカウンタが安定するまで
        再取得を繰り返すことで、遅れて返った古いスナップショットが新しい更新を上書きしない
        実装の存在を検証する。単発の見送りだけだと別ホスト由来の更新でもスナップショット全体を
        破棄してしまうため、リトライで収束させる設計であることを確認する。
        """
        html_src = _assets.INDEX_HTML

        assert "let hostInfoEventCounter = 0;" in html_src
        assert "hostInfoEventCounter++;" in html_src
        assert "HOST_INFO_REFRESH_MAX_ATTEMPTS" in html_src
        assert "const counterBefore = hostInfoEventCounter;" in html_src
        assert "if (hostInfoEventCounter !== counterBefore) continue;" in html_src

    def test_index_html_renders_host_and_ctime_in_meta(self):
        """左ペインのmetaが左にホスト名、右に作成日時を並べる。

        多ホスト統合表示で、行内のホスト識別とソート基準である作成日時の視認性を担保する契約。
        """
        html_src = _assets.INDEX_HTML

        # `.meta`は`display: flex; justify-content: space-between`で左右分割される。
        assert ".meta {" in html_src
        meta_block = html_src.split(".meta {", 1)[1].split("}", 1)[0]
        assert "display: flex" in meta_block
        assert "justify-content: space-between" in meta_block
        # デスクトップ行内に`host`と`ctime`の2つのspanが描画される。
        assert 'className = "meta"' in html_src or 'class="meta"' in html_src
        assert 'hostSpan.className = "host"' in html_src
        assert 'ctimeSpan.className = "ctime"' in html_src
        assert "ctimeSpan.textContent = file.ctime" in html_src
        # モバイル専用メタも同じ作成日時フィールドを表示する。
        assert 'ctimeSpan.className = "meta-ctime"' in html_src
        assert 'ctimeSpan.textContent = selected ? selected.ctime : ""' in html_src

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

    def test_full_text_search_ignores_stale_and_cleared_query_responses(self):
        """応答順が逆転しても最新検索だけを反映し、検索語消去後は全件表示を維持する。"""
        scenario = """
const snapshots = [];
renderFiles = () => snapshots.push(serverSearchKeys === null ? null : [...serverSearchKeys]);
(async () => {
  const first = searchFullText('first', ++searchGeneration);
  const second = searchFullText('second', ++searchGeneration);
  pending[1].resolve({ok: true, json: async () => [{host: 'h', path: 'new.md'}]});
  await second;
  pending[0].resolve({ok: true, json: async () => [{host: 'h', path: 'old.md'}]});
  await first;
  const afterReversed = [...serverSearchKeys];
  const third = searchFullText('third', ++searchGeneration);
  elements.filter.value = '';
  scheduleFullTextSearch();
  pending[2].resolve({ok: true, json: async () => [{host: 'h', path: 'late.md'}]});
  await third;
  process.stdout.write(JSON.stringify({afterReversed, afterCleared: serverSearchKeys, snapshots}));
})();
"""
        source = _assets.INDEX_HTML.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        source = (
            source.replace("__BASE_PATH_JS__", '""')
            .replace("__LOCAL_HOST_NAME_JS__", '"local"')
            .replace("__ROOT_DIRS_JS__", "{}")
            .replace("main();", "")
            + scenario
        )
        script = f"""
const elements = {{
  filter: {{value: '', addEventListener() {{}}}},
  'search-status': {{textContent: ''}},
  'copy-btn': {{addEventListener() {{}}}},
  'copy-path-btn': {{addEventListener() {{}}}},
  'prev-btn': {{addEventListener() {{}}}},
  'next-btn': {{addEventListener() {{}}}},
  'menu-btn': {{addEventListener() {{}}}},
  'drawer-backdrop': {{addEventListener() {{}}}},
  preview: {{addEventListener() {{}}}}
}};
globalThis.document = {{
  visibilityState: 'visible',
  getElementById(id) {{ return elements[id]; }},
  addEventListener() {{}},
  querySelector() {{ return null; }}
}};
globalThis.window = {{addEventListener() {{}}}};
globalThis.navigator = {{}};
globalThis.IntersectionObserver = class {{ observe() {{}} }};
const pending = [];
globalThis.fetch = url => new Promise(resolve => pending.push({{url, resolve}}));
eval({json.dumps(source)});
"""
        completed = subprocess.run(
            ["node", "--input-type=commonjs"],
            input=script,
            text=True,
            capture_output=True,
            check=True,
        )

        result = json.loads(completed.stdout)
        assert result["afterReversed"] == ["h\u0000new.md"]
        assert result["afterCleared"] is None
        assert ["h\u0000old.md"] not in result["snapshots"]
        assert ["h\u0000late.md"] not in result["snapshots"]
