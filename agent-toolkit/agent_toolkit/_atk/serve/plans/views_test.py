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

from agent_toolkit._atk.serve import plans
from agent_toolkit._atk.serve.plans.test_support_test import *  # noqa: F403


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


def test_mermaid_fence_is_rendered_as_escaped_diagram() -> None:
    """Mermaidのフェンスは原文をエスケープした描画枠と原文表示へ変換する。"""
    html = plans.markdown_to_html("```mermaid\ngraph TD;\n  A[<b>x</b>] --> B;\n```\n")

    assert 'class="diagram diagram-mermaid"' in html
    assert 'class="diagram-output mermaid-output"' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_pygments_css_keeps_token_rules_without_base_rule() -> None:
    """Pygmentsのスタイルは基底ルールを除き、トークン別ルールだけを返す。"""
    css = plans.read_pygments_css()

    assert ".codehilite .k" in css
    assert not any(line.strip().startswith(".codehilite {") for line in css.splitlines())


@pytest.mark.parametrize(
    ("rel", "expected"),
    [("a.plan-review.tsv", True), ("a.exec-review.tsv", True), ("a.md", False), ("a.tsv", False)],
)
def test_review_table_path_is_identified_by_suffix(rel: str, expected: bool) -> None:
    """レビュー指摘管理表は接尾辞で判定する。"""
    assert plans.is_review_table_path(rel) is expected


def test_absent_root_is_listed_without_a_warning(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """計画を1件も保存していないrootは通常の状態として扱い、警告を返さず一覧を空とする。"""
    del index_path

    entries, warning = plans.scan_files(tmp_path / "missing", "local-host")

    assert not entries
    assert warning is None


@pytest.mark.parametrize("rel", ["../outside.md", "a/../../outside.md", "/etc/passwd.md"])
def test_resolve_under_root_rejects_traversal(tmp_path: pathlib.Path, rel: str) -> None:
    """root外を指す相対パスは解決しない。"""
    root = tmp_path / "plans"
    root.mkdir()
    _plan(tmp_path, "outside.md")

    assert plans.resolve_under_root(root, rel) is None


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
