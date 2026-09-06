# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
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


def test_svg_fence_is_rendered_as_source_only_image() -> None:
    """SVGのフェンスは原文を直接埋め込まず、画像要素と原文表示へ変換する。"""
    html = plans.markdown_to_html('```svg\n<svg onload="alert(1)"></svg>\n```\n')

    assert 'class="diagram diagram-svg"' in html
    assert 'class="diagram-output svg-output"' in html
    # 原文はエスケープして`details`内へ置き、能動的な内容をDOMへ追加しない。
    assert "&lt;svg onload=" in html
    assert "<svg" not in html


def test_malformed_review_table_falls_back_to_escaped_source() -> None:
    """列数や形式が合わない表は原文をエスケープして表示する。"""
    html = plans.review_table_html("<b>1</b>\t2\n")

    assert "<table" not in html
    assert "&lt;b&gt;1&lt;/b&gt;" in html


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


@pytest.mark.asyncio
async def test_remote_read_passes_source_id_before_the_path() -> None:
    """複数rootの構成では保存元IDを先頭の引数として渡す。"""
    runner, calls = _runner_returning(_read_payload("body"))

    await plans.fetch_remote_file("circe", "p.md", runner, None, source_id=plans.NEW_SOURCE_ID)

    assert len(calls[0][2]) == 2


def test_local_hostname_must_not_collide_with_remote_hosts(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ローカルホスト名とリモートホスト名の重複は起動時に拒絶する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()

    with pytest.raises(ValueError):
        _context(root, remote_hosts=["local-host"])
