"""`_agents_server/retained_results.py`の振る舞いを検証する。"""

from __future__ import annotations

import json
import pathlib

from agent_toolkit._agents_server import (
    retained_results,
    shared_layout,
    shared_roots,
)
from agent_toolkit._agents_server import status_file as subject


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
