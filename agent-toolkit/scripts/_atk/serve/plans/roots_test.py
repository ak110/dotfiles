# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
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

import pytest

from _atk.serve import plans


from _atk.serve.plans.test_support_test import *  # noqa: F403


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


def test_dotdir_entries_are_excluded(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ドットディレクトリ配下は一覧にも検索にも含めない。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md", "本文")
    _plan(root, ".hidden/x.md", "本文")

    assert [entry.path for entry in plans.list_files(root, "local-host")] == ["p.md"]
    assert plans.search_files(root, "本文") == {"p.md"}


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
    watcher = _FakeWatcher(connected=True, response=_read_payload("rpc", 2_000.0))

    text, mtime = await plans.fetch_remote_file("circe", "p.md", runner, typing.cast(typing.Any, watcher))

    assert (text, mtime) == ("rpc", 2_000.0)
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
        await plans.resolve_text_and_mtime(context, "local-host", "", "missing.md")

    assert error.value.status == 404
