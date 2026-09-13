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


def test_non_directory_root_is_reported_as_a_warning(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """rootが通常ファイルの場合は、利用できない理由を警告として返す。"""
    del index_path
    root = tmp_path / "plans"
    root.write_text("x", encoding="utf-8")

    entries, warning = plans.scan_files(root, "local-host")

    assert not entries
    assert warning == "rootがディレクトリではありません"


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
