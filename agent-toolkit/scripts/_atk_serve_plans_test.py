"""`atk serve`の計画ファイル画面の処理のテスト。"""

# pylint: disable=protected-access

import asyncio
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import typing

import _atk_serve_plans as plans
import pytest


@pytest.fixture(name="index_path")
def _index_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """作成日時インデックスを一時ディレクトリへ隔離する。"""
    path = tmp_path / "cache" / "index.json"
    monkeypatch.setattr(plans, "_CREATION_TIME_INDEX_PATH", path)
    monkeypatch.setattr(plans, "_ctime_epoch", lambda st: float(st.st_mtime))
    return path


def _legacy_cache_path(index_path: pathlib.Path, host: str, rel: str) -> pathlib.Path:
    """旧形式（1エントリ1ファイル）のキャッシュパスを組み立てる。"""
    digest = hashlib.sha256(f"{host}\0{rel}".encode()).hexdigest()
    return index_path.parent / f"{digest}.json"


def _write_legacy_cache(path: pathlib.Path, host: str, rel: str, ctime_epoch: float) -> None:
    """旧形式のキャッシュを出力する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"host": host, "path": rel, "ctime_epoch": ctime_epoch}), encoding="utf-8")


def _plan(root: pathlib.Path, rel: str, body: str = "x", *, mtime: float = 2_000.0) -> pathlib.Path:
    """計画ファイルを1件作成する。"""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


# --------------------------------------------------------------------------------------
# rootの正規化と重複排除
# --------------------------------------------------------------------------------------


def test_same_root_specified_twice_is_listed_once(tmp_path: pathlib.Path) -> None:
    """同一のcanonical pathを指すroot定義は1件へまとめる。"""
    root = tmp_path / "plans"
    root.mkdir()

    normalized = plans.normalize_root_specs(
        (
            plans.RootSpec(source_id=plans.NEW_SOURCE_ID, path=root, portable_path="a"),
            plans.RootSpec(source_id=plans.LEGACY_SOURCE_ID, path=tmp_path / "." / "plans", portable_path="b"),
        )
    )

    assert [spec.source_id for spec in normalized] == [plans.NEW_SOURCE_ID]
    # 旧rootとして重複したため、旧形式の作成日時を取り込む資格を論理和で引き継ぐ。
    assert normalized[0].migrate_legacy_ctime is True


def test_symlinked_root_is_deduplicated_by_identity(tmp_path: pathlib.Path) -> None:
    """別名でも同一実体を指すrootは1件へまとめる。"""
    root = tmp_path / "plans"
    root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)

    normalized = plans.normalize_root_specs(
        (
            plans.RootSpec(source_id=plans.NEW_SOURCE_ID, path=root, portable_path="a"),
            plans.RootSpec(source_id="other", path=link, portable_path="b"),
        )
    )

    assert [spec.source_id for spec in normalized] == [plans.NEW_SOURCE_ID]


def test_distinct_roots_are_kept_separately(tmp_path: pathlib.Path) -> None:
    """別実体のrootは同名ファイルを持っていても別々に保持する。"""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    normalized = plans.normalize_root_specs(
        (
            plans.RootSpec(source_id=plans.NEW_SOURCE_ID, path=first, portable_path="a"),
            plans.RootSpec(source_id=plans.LEGACY_SOURCE_ID, path=second, portable_path="b"),
        )
    )

    assert [spec.source_id for spec in normalized] == [plans.NEW_SOURCE_ID, plans.LEGACY_SOURCE_ID]


# --------------------------------------------------------------------------------------
# 作成日時インデックス
# --------------------------------------------------------------------------------------


def test_first_observed_time_is_kept_across_updates(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """初回観測時刻を作成日時として保持し、以後の更新で変動させない。"""
    root = tmp_path / "plans"
    root.mkdir()
    path = _plan(root, "plan.md", mtime=1_000.0)

    first = plans.list_files(root, "local-host")[0]
    os.utime(path, (9_000.0, 9_000.0))
    second = plans.list_files(root, "local-host")[0]

    assert first.ctime_epoch == 1_000.0
    assert second.ctime_epoch == 1_000.0
    assert second.mtime_epoch == 9_000.0
    assert index_path.is_file()


def test_absent_entries_are_pruned_and_other_roots_are_kept(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """走査結果に現れないキーを回収し、別のrootに属するキーは維持する。"""
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    _plan(first_root, "keep.md")
    _plan(first_root, "gone.md")
    _plan(second_root, "other.md")

    plans.list_files(first_root, "local-host")
    plans.list_files(second_root, "local-host")
    (first_root / "gone.md").unlink()
    plans.list_files(first_root, "local-host")

    stored = json.loads(index_path.read_text(encoding="utf-8"))
    assert sorted((entry["root"].rsplit("/", 1)[-1], entry["path"]) for entry in stored.values()) == [
        ("first", "keep.md"),
        ("second", "other.md"),
    ]


def test_absent_root_keeps_the_recorded_creation_times(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """rootへ一時的に到達できない間は、記録済みの作成日時を回収しない。"""
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "plan.md", mtime=1_000.0)
    plans.list_files(root, "local-host")
    stored = json.loads(index_path.read_text(encoding="utf-8"))
    (root / "plan.md").unlink()
    root.rmdir()

    entries, warning = plans.scan_files(root, "local-host")

    assert not entries
    assert warning is None
    assert json.loads(index_path.read_text(encoding="utf-8")) == stored


def test_migrates_matching_legacy_entry(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """旧形式の作成日時を取り込み、取り込んだ旧形式のファイルを削除する。"""
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "plan.md")
    legacy = _legacy_cache_path(index_path, "local-host", "plan.md")
    _write_legacy_cache(legacy, "local-host", "plan.md", 500.0)

    entry = plans.list_files(root, "local-host")[0]

    assert entry.ctime_epoch == 500.0
    assert not legacy.exists()


def test_only_legacy_root_migrates_matching_legacy_entry(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """旧形式はrootを持たないため、旧rootだけが取り込む。"""
    new_root = tmp_path / "new"
    legacy_root = tmp_path / "legacy"
    new_root.mkdir()
    legacy_root.mkdir()
    for root in (new_root, legacy_root):
        _plan(root, "same.md")
    legacy = _legacy_cache_path(index_path, "local-host", "same.md")
    _write_legacy_cache(legacy, "local-host", "same.md", 500.0)

    new_entry = plans.list_files(new_root, "local-host", plans.NEW_SOURCE_ID)[0]

    assert new_entry.ctime_epoch == 2_000.0
    assert legacy.exists()

    legacy_entry = plans.list_files(legacy_root, "local-host", plans.LEGACY_SOURCE_ID)[0]

    assert legacy_entry.ctime_epoch == 500.0
    assert not legacy.exists()


def test_cleanup_removes_only_temporaries(index_path: pathlib.Path) -> None:
    """残存した一時ファイルだけを除去し、インデックスと旧形式は残す。"""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("{}", encoding="utf-8")
    digest = "0" * 64
    keep = index_path.parent / f"{digest}.json"
    keep.write_text("{}", encoding="utf-8")
    current_temporary = index_path.with_name(f"{index_path.name}.123.tmp")
    current_temporary.write_text("{}", encoding="utf-8")
    legacy_temporary = index_path.parent / f".{digest}.json.123.456.tmp"
    legacy_temporary.write_text("{}", encoding="utf-8")

    plans.cleanup_creation_time_temporaries()

    assert index_path.exists()
    assert keep.exists()
    assert not current_temporary.exists()
    assert not legacy_temporary.exists()


# --------------------------------------------------------------------------------------
# Markdownとレビュー表の描画
# --------------------------------------------------------------------------------------


def test_raw_html_is_escaped(tmp_path: pathlib.Path) -> None:
    """本文中の生HTMLを実行させず、文字として表示する。"""
    del tmp_path
    html = plans.markdown_to_html('<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n')

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "onerror" not in html or "&lt;img" in html


def test_mermaid_fence_is_rendered_as_escaped_diagram() -> None:
    """Mermaidのフェンスは原文をエスケープした描画枠と原文表示へ変換する。"""
    html = plans.markdown_to_html("```mermaid\ngraph TD;\n  A[<b>x</b>] --> B;\n```\n")

    assert 'class="diagram diagram-mermaid"' in html
    assert 'class="diagram-output mermaid-output"' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_svg_fence_is_rendered_as_source_only_image() -> None:
    """SVGのフェンスは原文を直接埋め込まず、画像要素と原文表示へ変換する。"""
    html = plans.markdown_to_html('```svg\n<svg onload="alert(1)"></svg>\n```\n')

    assert 'class="diagram diagram-svg"' in html
    assert 'class="diagram-output svg-output"' in html
    # 原文はエスケープして`details`内へ置き、能動的な内容をDOMへ追加しない。
    assert "&lt;svg onload=" in html
    assert "<svg" not in html


def test_fenced_code_is_highlighted_by_language() -> None:
    """言語指定のあるフェンスはハイライトし、未知言語は素通しで描画する。"""
    highlighted = plans.markdown_to_html("```python\nx = 1\n```\n")
    plain = plans.markdown_to_html("```nosuchlanguage\nx = 1\n```\n")

    assert 'class="codehilite language-python"' in highlighted
    assert "codehilite" not in plain
    assert "<pre>" in plain


def test_pygments_css_keeps_token_rules_without_base_rule() -> None:
    """Pygmentsのスタイルは基底ルールを除き、トークン別ルールだけを返す。"""
    css = plans.read_pygments_css()

    assert ".codehilite .k" in css
    assert not any(line.strip().startswith(".codehilite {") for line in css.splitlines())


def test_review_table_is_rendered_as_table() -> None:
    """レビュー指摘管理表は7列のHTML表へ変換する。"""
    row = "\t".join(json.dumps(value, ensure_ascii=False) for value in ["1", "実装", "a.py:1", "指摘", "要", "対応", ""])

    html = plans.review_table_html(row + "\n")

    assert "<table" in html
    assert "<th>ラウンド</th>" in html
    assert "<td>指摘</td>" in html


def test_malformed_review_table_falls_back_to_escaped_source() -> None:
    """列数や形式が合わない表は原文をエスケープして表示する。"""
    html = plans.review_table_html("<b>1</b>\t2\n")

    assert "<table" not in html
    assert "&lt;b&gt;1&lt;/b&gt;" in html


@pytest.mark.parametrize(
    ("rel", "expected"),
    [("a.plan-review.tsv", True), ("a.exec-review.tsv", True), ("a.md", False), ("a.tsv", False)],
)
def test_review_table_path_is_identified_by_suffix(rel: str, expected: bool) -> None:
    """レビュー指摘管理表は接尾辞で判定する。"""
    assert plans.is_review_table_path(rel) is expected


# --------------------------------------------------------------------------------------
# 付属計画間の移動
# --------------------------------------------------------------------------------------


def _context(root: pathlib.Path, **kwargs: typing.Any) -> plans.PlansContext:
    """単一rootの計画ファイル画面のコンテキストを生成する。"""
    return plans.create_context(root=root, hostname="local-host", **kwargs)


@pytest.mark.asyncio
async def test_attached_plan_navigation_is_symmetric(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """メインと付属計画のどちらを表示中でも、実在する全ページへのリンクを返す。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    for rel in ("p.md", "p.detail.md", "p.bugs.md"):
        _plan(root, rel)
    context = _context(root)

    for current in ("p.md", "p.detail.md", "p.bugs.md"):
        html = await plans.plan_links_html(context, "local-host", "", current)
        assert 'class="detail-link"' in html
        others = {rel for rel in ("p.md", "p.detail.md", "p.bugs.md") if rel != current}
        for other in others:
            assert f'data-plan-path="{other}"' in html
        # 現在表示中のページはリンクにせず、ラベルだけを置く。
        assert f'data-plan-path="{current}"' not in html


@pytest.mark.asyncio
async def test_attached_links_are_omitted_when_other_pages_are_absent(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    """付属計画が無い計画ではリンク行を付けない。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md")
    context = _context(root)

    assert await plans.plan_links_html(context, "local-host", "", "p.md") == ""


@pytest.mark.asyncio
async def test_attached_plan_path_is_escaped(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """リンクへ埋め込む相対パスをエスケープする。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, 'a"b.md')
    _plan(root, 'a"b.detail.md')
    context = _context(root)

    html = await plans.plan_links_html(context, "local-host", "", 'a"b.md')

    assert 'data-plan-path="a&quot;b.detail.md"' in html


@pytest.mark.asyncio
async def test_review_table_is_reachable_from_the_main_plan(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """レビュー指摘管理表もメイン計画からの移動先へ含める。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md")
    _plan(root, "p.exec-review.tsv")
    context = _context(root)

    html = await plans.plan_links_html(context, "local-host", "", "p.md")

    assert 'data-plan-path="p.exec-review.tsv"' in html
    assert "実行レビュー指摘管理表" in html


# --------------------------------------------------------------------------------------
# 一覧・検索・パス解決
# --------------------------------------------------------------------------------------


def test_attached_files_are_excluded_from_the_listing(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """付属計画は一覧へ出力せず、検索とパス解決の対象には残す。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md", "本文")
    _plan(root, "p.detail.md", "詳細の本文")
    _plan(root, "p.exec-review.tsv", "表の本文")
    _plan(root, "note.txt", "対象外")

    assert [entry.path for entry in plans.list_files(root, "local-host")] == ["p.md"]
    assert plans.search_files(root, "詳細の本文") == {"p.detail.md"}
    assert plans.resolve_under_root(root, "p.detail.md") is not None
    assert plans.resolve_under_root(root, "note.txt") is None


def test_absent_root_is_listed_without_a_warning(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """計画を1件も保存していないrootは通常の状態として扱い、警告を返さず一覧を空とする。"""
    del index_path

    entries, warning = plans.scan_files(tmp_path / "missing", "local-host")

    assert not entries
    assert warning is None


def test_non_directory_root_is_reported_as_a_warning(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """rootが通常ファイルの場合は、利用できない理由を警告として返す。"""
    del index_path
    root = tmp_path / "plans"
    root.write_text("x", encoding="utf-8")

    entries, warning = plans.scan_files(root, "local-host")

    assert not entries
    assert warning == "rootがディレクトリではありません"


@pytest.mark.parametrize("rel", ["../outside.md", "a/../../outside.md", "/etc/passwd.md"])
def test_resolve_under_root_rejects_traversal(tmp_path: pathlib.Path, rel: str) -> None:
    """root外を指す相対パスは解決しない。"""
    root = tmp_path / "plans"
    root.mkdir()
    _plan(tmp_path, "outside.md")

    assert plans.resolve_under_root(root, rel) is None


def test_dotdir_entries_are_excluded(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ドットディレクトリ配下は一覧にも検索にも含めない。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md", "本文")
    _plan(root, ".hidden/x.md", "本文")

    assert [entry.path for entry in plans.list_files(root, "local-host")] == ["p.md"]
    assert plans.search_files(root, "本文") == {"p.md"}


def test_search_matches_full_text_case_insensitively(tmp_path: pathlib.Path) -> None:
    """本文検索は大文字小文字を区別しない部分一致とする。"""
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md", "Hello World")

    assert plans.search_files(root, "hello") == {"p.md"}
    assert plans.search_files(root, "missing") == set()


def test_review_table_match_is_connected_to_the_main_plan() -> None:
    """付属ファイルの一致は、一覧で選択できるメイン計画へ接続する。"""
    assert plans.listed_plan_path("p.exec-review.tsv") == "p.md"
    assert plans.listed_plan_path("p.detail.md") == "p.md"
    assert plans.listed_plan_path("p.md") == "p.md"


@pytest.mark.asyncio
async def test_same_relative_path_in_two_roots_stays_separate(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    """別rootの同名ファイルは、保存元IDで区別して両方返す。"""
    del index_path
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _plan(first, "same.md")
    _plan(second, "same.md")
    context = plans.create_context(
        roots=(
            plans.RootSpec(source_id=plans.NEW_SOURCE_ID, path=first, portable_path="a"),
            plans.RootSpec(source_id=plans.LEGACY_SOURCE_ID, path=second, portable_path="b"),
        ),
        hostname="local-host",
    )

    entries = await plans.all_entries(context)

    assert sorted((entry.source_id, entry.path) for entry in entries) == [
        (plans.LEGACY_SOURCE_ID, "same.md"),
        (plans.NEW_SOURCE_ID, "same.md"),
    ]


def test_synchronized_root_keeps_only_the_oldest_host(tmp_path: pathlib.Path) -> None:
    """ホスト間で同期されるrootの同名ファイルは、作成日時が最も古いホストの1件へ絞る。"""
    del tmp_path

    def entry(host: str, source_id: str, path: str, ctime: float) -> plans.FileEntry:
        return plans.FileEntry(
            host=host,
            path=path,
            name=path,
            mtime="",
            ctime="",
            mtime_epoch=ctime,
            ctime_epoch=ctime,
            source_id=source_id,
        )

    merged = plans._oldest_host_per_file(
        [
            entry("a", plans.NEW_SOURCE_ID, "p.md", 200.0),
            entry("b", plans.NEW_SOURCE_ID, "p.md", 100.0),
            entry("a", plans.LEGACY_SOURCE_ID, "q.md", 200.0),
            entry("b", plans.LEGACY_SOURCE_ID, "q.md", 100.0),
        ]
    )

    assert sorted((item.host, item.source_id) for item in merged) == [
        ("a", plans.LEGACY_SOURCE_ID),
        ("b", plans.LEGACY_SOURCE_ID),
        ("b", plans.NEW_SOURCE_ID),
    ]


# --------------------------------------------------------------------------------------
# ローカルrootの変更監視
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_local_watchers_schedules_existing_roots(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    """実在するrootだけを監視へ登録する。"""
    del index_path
    present = tmp_path / "present"
    present.mkdir()
    absent = tmp_path / "absent"
    context = plans.create_context(
        roots=[
            plans.RootSpec(source_id="present", path=present, portable_path=str(present)),
            plans.RootSpec(source_id="absent", path=absent, portable_path=str(absent)),
        ],
        hostname="local-host",
    )

    plans.start_local_watchers(context)
    try:
        observer = context.state.local_observer
        assert observer is not None
        assert {emitter.watch.path for emitter in observer.emitters} == {str(present.resolve())}
    finally:
        plans.stop_local_watchers(context)


@pytest.mark.asyncio
async def test_local_watcher_broadcasts_on_local_change(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    """ローカルrootへ計画ファイルを追加すると、購読中のSSEへ更新を通知する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)
    # debounce窓の実時間待ちを避けるため短縮する。
    context.state.debounce_sec = 0.01
    queue = await plans.subscribe(context.state)

    plans.start_local_watchers(context)
    try:
        _plan(root, "p.md")
        payload = await asyncio.wait_for(queue.get(), timeout=10.0)
    finally:
        plans.stop_local_watchers(context)

    assert json.loads(payload)["type"] == "refresh"


@pytest.mark.asyncio
async def test_stop_local_watchers_releases_observer(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """監視を停止するとobserverの保持欄が空へ戻り、監視スレッドが終了する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)
    plans.start_local_watchers(context)
    observer = context.state.local_observer
    assert observer is not None

    plans.stop_local_watchers(context)

    assert context.state.local_observer is None
    assert not observer.is_alive()


# --------------------------------------------------------------------------------------
# リモートホスト
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("plan.md", True),
        ("dir/plan.md", True),
        ("plan.exec-review.tsv", True),
        ("../plan.md", False),
        ("dir/../../plan.md", False),
        ("/abs/plan.md", False),
        ("dir\\plan.md", False),
        ("plan.txt", False),
        ("", False),
    ],
)
def test_remote_relative_path_is_validated_before_ssh(rel: str, expected: bool) -> None:
    """上位ディレクトリ参照と対象外の接尾辞は、SSH呼び出しの前に拒否する。"""
    assert plans.is_safe_remote_relpath(rel) is expected


class _FakeWatcher:
    """常駐SSH接続のRPCを差し替える検体。"""

    def __init__(self, *, connected: bool, response: typing.Any) -> None:
        self._connected = connected
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def is_connected(self) -> bool:
        """接続状態を返す。"""
        return self._connected

    async def request(self, op: str, args: dict[str, str]) -> dict[str, typing.Any]:
        """RPC応答を返すか、設定された例外を送出する。"""
        self.calls.append((op, args))
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


def _read_payload(text: str, mtime: float | None = 1_000.0) -> dict[str, typing.Any]:
    """リモートヘルパーの`read`応答を組み立てる。"""
    payload: dict[str, typing.Any] = {"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    if mtime is not None:
        payload["mtime_epoch"] = mtime
    return payload


def _runner_returning(payload: dict[str, typing.Any]) -> tuple[plans.SshRunner, list[tuple[str, str, list[str]]]]:
    """単発SSHの呼び出しを記録するrunnerと、その記録先を返す。"""
    calls: list[tuple[str, str, list[str]]] = []

    async def runner(host: str, op: str, args: list[str]) -> str:
        calls.append((host, op, args))
        return json.dumps(payload)

    return runner, calls


@pytest.mark.asyncio
async def test_remote_file_is_read_through_rpc_when_connected() -> None:
    """常駐RPCを利用できる場合は単発SSHを起動しない。"""
    runner, calls = _runner_returning(_read_payload("fallback"))
    watcher = _FakeWatcher(connected=True, response=_read_payload("rpc", 2_000.0))

    text, mtime = await plans.fetch_remote_file("circe", "p.md", runner, typing.cast(typing.Any, watcher))

    assert (text, mtime) == ("rpc", 2_000.0)
    assert not calls
    assert watcher.calls[0][0] == "read"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [None, RuntimeError("切断"), {"ok": False, "error": "permission denied"}],
    ids=["disconnected", "rpc-raises", "rpc-error"],
)
async def test_remote_file_falls_back_to_single_ssh(response: typing.Any) -> None:
    """RPCが未接続・失敗・エラー応答の場合は単発SSHへ切り替える。"""
    runner, calls = _runner_returning(_read_payload("fallback"))
    watcher = None if response is None else _FakeWatcher(connected=True, response=response)

    text, _ = await plans.fetch_remote_file("circe", "p.md", runner, typing.cast(typing.Any, watcher))

    assert text == "fallback"
    assert [call[0:2] for call in calls] == [("circe", "read")]


@pytest.mark.asyncio
async def test_remote_read_passes_source_id_before_the_path() -> None:
    """複数rootの構成では保存元IDを先頭の引数として渡す。"""
    runner, calls = _runner_returning(_read_payload("body"))

    await plans.fetch_remote_file("circe", "p.md", runner, None, source_id=plans.NEW_SOURCE_ID)

    assert len(calls[0][2]) == 2


def _failed_ssh(returncode: int, stderr: bytes) -> typing.Callable[..., subprocess.CompletedProcess[bytes]]:
    """指定した終了コードと標準エラー出力を返す`subprocess.run`の代用を組み立てる。"""

    def run(*args: typing.Any, **kwargs: typing.Any) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=b"", stderr=stderr)

    return run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"helper not found\n", "helper not found"),
        (b"  \n ", "標準エラー出力はありません"),
        (b"\xff\xfe helper failed", "helper failed"),
    ],
    ids=["message", "empty", "undecodable"],
)
async def test_remote_read_failure_reports_stderr(monkeypatch: pytest.MonkeyPatch, stderr: bytes, expected: str) -> None:
    """リモート実行が非0で終了した場合、終了コードと失敗元の標準エラー出力を例外本文へ引き継ぐ。"""
    monkeypatch.setattr(plans.subprocess, "run", _failed_ssh(3, stderr))

    with pytest.raises(plans.RemoteHelperError) as error:
        await plans.fetch_remote_file("circe", "p.md", plans.default_ssh_runner, None)

    assert "終了コード3" in str(error.value)
    assert expected in str(error.value)


@pytest.mark.asyncio
async def test_long_stderr_keeps_the_tail_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """標準エラー出力が上限を超える場合、失敗の直接原因が現れる末尾側を残して切り詰める。"""
    head = "先頭の行" * plans.STDERR_EXCERPT_MAX_CHARS
    monkeypatch.setattr(plans.subprocess, "run", _failed_ssh(3, f"{head}\n末尾の理由\n".encode()))

    with pytest.raises(plans.RemoteHelperError) as error:
        await plans.fetch_remote_file("circe", "p.md", plans.default_ssh_runner, None)

    message = str(error.value)
    assert "末尾の理由" in message
    assert head not in message
    assert len(message) < len(head)


@pytest.mark.asyncio
async def test_remote_path_outside_the_root_is_rejected_before_ssh(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    """危険な相対パスはリモートへ送らず、入力不正として拒否する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    runner, calls = _runner_returning(_read_payload("body"))
    context = _context(root, remote_hosts=["circe"], ssh_runner=runner)

    with pytest.raises(plans.PlanFileError) as error:
        await plans.resolve_text_and_mtime(context, "circe", "", "../secret.md")

    assert error.value.status == 400
    assert not calls


@pytest.mark.asyncio
async def test_missing_local_file_is_reported_as_not_found(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ローカルの不在ファイルは未検出として扱う。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)

    with pytest.raises(plans.PlanFileError) as error:
        await plans.resolve_text_and_mtime(context, "local-host", "", "missing.md")

    assert error.value.status == 404


def test_local_hostname_must_not_collide_with_remote_hosts(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ローカルホスト名とリモートホスト名の重複は起動時に拒絶する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()

    with pytest.raises(ValueError):
        _context(root, remote_hosts=["local-host"])
