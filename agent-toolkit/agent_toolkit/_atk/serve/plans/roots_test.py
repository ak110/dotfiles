"""`atk serve`の計画ファイル画面の処理のテスト。"""

# pylint: disable=protected-access

import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import plans
from agent_toolkit._atk.serve.plans import local_scan as plans_local_scan
from agent_toolkit._atk.serve.plans import remote as plans_remote
from agent_toolkit._atk.serve.plans import roots as plans_roots
from agent_toolkit._atk.serve.plans import views as plans_views
from agent_toolkit._testing.serve_plans_support import (
    _context,
    _FakeWatcher,
    _legacy_cache_path,
    _plan,
    _read_payload,
    _runner_returning,
    _write_legacy_cache,
)


def test_same_root_specified_twice_is_listed_once(tmp_path: pathlib.Path) -> None:
    """同一のcanonical pathを指すroot定義は1件へまとめる。"""
    root = tmp_path / "plans"
    root.mkdir()

    normalized = plans_roots.normalize_root_specs(
        (
            plans.RootSpec(source_id=plans.NEW_SOURCE_ID, path=root, portable_path="a"),
            plans.RootSpec(source_id=plans.LEGACY_SOURCE_ID, path=tmp_path / "." / "plans", portable_path="b"),
        )
    )

    assert [spec.source_id for spec in normalized] == [plans.NEW_SOURCE_ID]
    # 旧rootとして重複したため、旧形式の作成日時を取り込む資格を論理和で引き継ぐ。
    assert normalized[0].migrate_legacy_ctime is True


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

    new_entry = plans_local_scan.list_files(new_root, "local-host", plans.NEW_SOURCE_ID)[0]

    assert new_entry.ctime_epoch == 2_000.0
    assert legacy.exists()

    legacy_entry = plans_local_scan.list_files(legacy_root, "local-host", plans.LEGACY_SOURCE_ID)[0]

    assert legacy_entry.ctime_epoch == 500.0
    assert not legacy.exists()


@pytest.mark.asyncio
async def test_attached_plan_path_is_escaped(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """リンクへ埋め込む相対パスをエスケープする。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, 'a"b.md')
    _plan(root, 'a"b.detail.md')
    context = _context(root)

    html = await plans_views.plan_links_html(context, "local-host", "", 'a"b.md')

    assert 'data-plan-path="a&quot;b.detail.md"' in html


def test_dotdir_entries_are_excluded(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ドットディレクトリ配下は一覧にも検索にも含めない。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md", "本文")
    _plan(root, ".hidden/x.md", "本文")

    assert [entry.path for entry in plans_local_scan.list_files(root, "local-host")] == ["p.md"]
    assert plans_local_scan.search_files(root, "本文") == {"p.md"}


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


@pytest.mark.asyncio
async def test_remote_file_is_read_through_rpc_when_connected() -> None:
    """常駐RPCを利用できる場合は単発SSHを起動しない。"""
    runner, calls = _runner_returning(_read_payload("fallback"))
    watcher = _FakeWatcher(connected=True, response=_read_payload("rpc"))

    text = await plans_remote.fetch_remote_file("remote-host", "p.md", runner, typing.cast(typing.Any, watcher))

    assert text == "rpc"
    assert not calls
    assert watcher.calls[0][0] == "read"


@pytest.mark.asyncio
async def test_missing_local_file_is_reported_as_not_found(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ローカルの不在ファイルは未検出として扱う。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)

    with pytest.raises(plans.PlanFileError) as error:
        await plans.resolve_text(context, "local-host", "", "missing.md")

    assert error.value.status == 404


def test_default_legacy_root_follows_claude_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """設定でrootを明示しない場合の旧rootは、Claude Codeの設定ディレクトリ配下の`plans`を指す。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))

    specs = plans_roots.default_root_specs()

    legacy = [spec for spec in specs if spec.source_id == plans.LEGACY_SOURCE_ID]
    assert [spec.path for spec in legacy] == [(tmp_path / "claude-config" / "plans").resolve()]
