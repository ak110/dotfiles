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


def test_review_table_is_rendered_as_table() -> None:
    """レビュー指摘管理表は7列のHTML表へ変換する。"""
    row = "\t".join(json.dumps(value, ensure_ascii=False) for value in ["1", "実装", "a.py:1", "指摘", "要", "対応", ""])

    html = plans.review_table_html(row + "\n")

    assert "<table" in html
    assert "<th>ラウンド</th>" in html
    assert "<td>指摘</td>" in html


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


def test_review_table_match_is_connected_to_the_main_plan() -> None:
    """付属ファイルの一致は、一覧で選択できるメイン計画へ接続する。"""
    assert plans.listed_plan_path("p.exec-review.tsv") == "p.md"
    assert plans.listed_plan_path("p.detail.md") == "p.md"
    assert plans.listed_plan_path("p.md") == "p.md"


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
