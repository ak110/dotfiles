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
import sys
import types
import typing

import pytest

from agent_toolkit._atk.serve import plans
from agent_toolkit._atk.serve.plans.test_support_test import *  # noqa: F403


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

    await plans.fetch_remote_file("remote-host", "p.md", runner, None, source_id=plans.NEW_SOURCE_ID)

    assert len(calls[0][2]) == 2


def test_local_hostname_must_not_collide_with_remote_hosts(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """ローカルホスト名とリモートホスト名の重複は起動時に拒絶する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()

    with pytest.raises(ValueError):
        _context(root, remote_hosts=["local-host"])


@pytest.mark.asyncio
async def test_run_does_not_reconnect_when_cancelled_during_cleanup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常駐SSHが停止処理と同時に終了しても、停止処理のキャンセルで再接続せずに終わる。

    プロセスグループ全体へSIGTERMが届くと、子のSSHが先に終了して後始末へ入ったタスクへ停止処理のキャンセルが届く。
    後始末の待機がキャンセルを吸収したまま再接続すると、新しいSSHの出力を待ち続けてatk serveの停止が完了しない。
    """
    started: list[tuple[typing.Any, ...]] = []
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd: typing.Any, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        # 標準出力を閉じて接続断を起こし、標準入力の終端を受けてから少し遅れて終了する子プロセスで代替する。
        started.append(cmd)
        script = "import os, sys, time; os.close(1); sys.stdin.read(); time.sleep(0.3)"
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    context = _context(tmp_path, remote_hosts=["remote-host"])
    plans.start_remote_watchers(context)
    for _ in range(500):
        if context.state.host_status.get("remote-host") == "disconnected":
            break
        await asyncio.sleep(0.01)
    assert context.state.host_status.get("remote-host") == "disconnected"

    await asyncio.wait_for(plans.stop_remote_watchers(context), timeout=5)

    assert len(started) == 1


@pytest.mark.asyncio
async def test_rpc_timeout_error_names_operation_and_limit(tmp_path: pathlib.Path) -> None:
    """常駐接続のRPCの上限超過の例外は、操作名と上限秒数を持つ。

    `asyncio.wait_for`の例外は文字列を持たず、本文の読み取りと検索の失敗の警告が理由を欠く。
    """

    class _Stdin:
        def write(self, data: bytes) -> None:
            del data

        async def drain(self) -> None:
            pass

        def is_closing(self) -> bool:
            return False

    context = _context(tmp_path, remote_hosts=["remote-host"])
    watcher = plans.RemoteWatcher("remote-host", context.state)
    watcher._proc = typing.cast(typing.Any, types.SimpleNamespace(stdin=_Stdin()))
    watcher._connected = True

    with pytest.raises(TimeoutError) as error:
        await watcher.request("read", {"path": "a.md"}, timeout=0.05)

    assert "op=read" in str(error.value)
    assert "上限の0.05秒" in str(error.value)


@pytest.mark.asyncio
async def test_single_shot_ssh_launches_plans_helper_and_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """計画ファイル画面の単発SSHは、計画ファイル画面のヘルパーを従来の起動形で起動し、失敗を標準エラー付きで示す。

    起動形をセッション画面と共通の関数へ移したため、計画ファイル画面のbootstrap・op・argsが渡らないか、
    `watchdog`・`platformdirs`が欠けると、リモートの計画ファイル一覧と本文を取得できなくなる。
    """
    sent: list[tuple[list[str], float]] = []

    async def fake_run_ssh(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        sent.append((cmd, timeout))
        return 3, b"", b"helper not found\n"

    monkeypatch.setattr(plans._atk_serve_remote, "run_ssh", fake_run_ssh)

    with pytest.raises(plans.RemoteHelperError) as error:
        await plans.default_ssh_runner("remote-host", "read", ["cGF0aA=="])

    assert "atk_serve_plans_remote_helper.py" in plans.REMOTE_BOOTSTRAP
    assert sent == [
        (
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "remote-host",
                "uv",
                "run",
                "--no-project",
                "--with",
                '"watchdog>=6.0.0"',
                "--with",
                '"platformdirs>=4.0"',
                "python",
                "-c",
                f'"{plans.REMOTE_BOOTSTRAP}"',
                "read",
                "cGF0aA==",
            ],
            plans.SSH_TIMEOUT_SEC,
        )
    ]
    assert "終了コード3" in str(error.value)
    assert "helper not found" in str(error.value)
