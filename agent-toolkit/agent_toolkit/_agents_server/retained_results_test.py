"""`_agents_server/retained_results.py`の振る舞いを検証する。"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from agent_toolkit._agents_server import (
    retained_results,
    shared_layout,
    shared_roots,
)
from agent_toolkit._agents_server import status_file as subject


def test_unreadable_legacy_slot_does_not_hide_a_later_end(tmp_path: pathlib.Path) -> None:
    """古い原本の破損を保持し、別の終了結果は通常どおり公開・照会・回収する。"""
    path = shared_layout.results_directory("root", tmp_path) / "child.json"
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    finalized = "2026-11-21T00:02:00Z"
    body = {"status": "completed", "agent_message": "後の結果", "turn_seq": 2, "finalized_at": finalized}

    retained_results.publish_result(path, body)
    observed = retained_results.read_retained_result(
        "root", "child", tmp_path, expected_turn_seq=2, expected_finalized_at=finalized
    )
    assert observed == body
    assert retained_results.take_result(
        "root", "child", "root.json", collector="test", state_root=tmp_path, expected_turn_seq=2
    ) == ({"status": "completed", "agent_message": "後の結果"}, None)
    assert path.read_text(encoding="utf-8") == "{"


def test_take_reports_path_of_unreadable_separate_result(tmp_path: pathlib.Path) -> None:
    """旧名がない場合でも、読めなかった別名の原本を診断し保持する。"""
    directory = shared_layout.results_directory("root", tmp_path)
    directory.mkdir(parents=True)
    bad = directory / "child.2-bad.json"
    bad.write_text("{", encoding="utf-8")

    result, error = retained_results.take_result("root", "child", "root.json", collector="test", state_root=tmp_path)
    assert result is None and error is not None and str(bad) in error
    assert bad.read_text(encoding="utf-8") == "{"


def test_take_result_checks_owner_and_consumes_once(tmp_path: pathlib.Path) -> None:
    """異なる書込主体は結果を取得できず、正しい主体への配送は1回だけ成立する。"""
    directory = shared_layout.results_directory("root-session", tmp_path)
    directory.mkdir(parents=True)
    result_path = directory / "child-session.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "agent_message": "完了",
                "owner_status_file": "delegate.json",
                "turn_seq": 7,
                "finalized_at": "2026-10-03T00:00:00Z",
                "future_internal": "公開しない",
            }
        ),
        encoding="utf-8",
    )

    assert retained_results.take_result(
        "root-session",
        "child-session",
        "root.json",
        collector="test",
        state_root=tmp_path,
    ) == (None, None)
    assert result_path.exists()
    assert retained_results.take_result(
        "root-session",
        "child-session",
        "delegate.json",
        collector="test",
        state_root=tmp_path,
    ) == ({"status": "completed", "agent_message": "完了"}, None)
    assert retained_results.take_result(
        "root-session",
        "child-session",
        "delegate.json",
        collector="test",
        state_root=tmp_path,
    ) == (None, None)


def test_take_result_keeps_other_owner_result(tmp_path: pathlib.Path) -> None:
    """CLI用退避先を指定しても別の書込主体の結果は移動しない。"""
    result_path = shared_layout.results_directory("root-session", tmp_path) / "child-session.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({"status": "completed", "owner_status_file": "delegate.json"}), encoding="utf-8")
    stash_path = tmp_path / "wait-run" / "results" / "child-session.json"
    writer = subject.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root-session", "root.json", None), state_root=tmp_path
    )

    assert retained_results.take_result(
        "root-session", "child-session", "root.json", collector="cli", state_root=tmp_path, stash_path=stash_path
    ) == (None, None)
    assert not stash_path.exists()
    assert result_path.exists()
    assert writer.take_result("child-session", collector="mcp-wait") == (None, None)
    assert result_path.exists()

    owner = subject.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root-session", "delegate.json", "delegate"), state_root=tmp_path
    )
    assert owner.take_result("child-session", collector="mcp-wait") == ({"status": "completed"}, None)
    assert not result_path.exists()


@pytest.mark.asyncio
async def test_cancellation_at_claim_worker_completion_preserves_original(tmp_path, monkeypatch):
    writer = subject.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    path = shared_layout.results_directory("root", tmp_path) / "child.json"
    finalized_at = "2026-11-20T12:00:00Z"
    payload = {"status": "completed", "agent_message": "完了本文", "turn_seq": 1, "finalized_at": finalized_at}
    retained_results.publish_result(path, payload)
    loop = asyncio.get_running_loop()
    boundary = asyncio.timeout(None)
    mark = retained_results.mark_published

    def expire_on_return(path, end):
        mark(path, end)
        loop.call_soon_threadsafe(boundary.reschedule, loop.time())

    with monkeypatch.context() as patch:
        patch.setattr(retained_results, "mark_published", expire_on_return)
        with pytest.raises(TimeoutError):
            async with boundary:
                await writer.take_result_async(
                    "child", collector="mcp-wait", expected_turn_seq=1, expected_finalized_at=finalized_at
                )
    result, error = writer.take_result("child", collector="cli")
    assert error is None and result is not None and result["agent_message"] == "完了本文"
    assert writer.take_result("child", collector="cli") == (None, None)
