"""record_pathsモジュールのテスト。"""

import pathlib

import pytest

from agent_toolkit import agents_server_mcp
from agent_toolkit._agents_server import record_paths
from agent_toolkit._atk.serve import sessions as session_records


def test_record_engines_match_supported_engines() -> None:
    """記録を探索できる実行系は、agents_serverが起動できる実行系と一致する。

    実行系を追加して記録の探索を追随させないと、その実行系の委譲先は`atk agents logs`と
    証拠抽出器の双方で記録なしとして扱われる。
    """
    assert record_paths.RECORD_ENGINES == agents_server_mcp.SUPPORTED_ENGINES


def test_claude_subagent_record_is_found_by_its_agent_id(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開CLIへ渡すサブエージェント識別子で親の下の記録を解決する。"""
    claude_home = tmp_path / "claude"
    monkeypatch.setattr(session_records, "default_claude_home", lambda: claude_home)
    agent_id = "agent-a7157f79d55caab81"
    child = claude_home / "projects" / "project" / "parent" / "subagents" / f"{agent_id}.jsonl"
    child.parent.mkdir(parents=True)
    child.write_text("{}\n", encoding="utf-8")

    assert record_paths.find_session_record(agent_id, codex_home=tmp_path / "codex") == record_paths.SessionRecord(
        "claude", (child,)
    )


def test_claude_parent_record_precedes_same_named_subagent(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同名記録がある場合は既存の親セッションの探索順を維持する。"""
    claude_home = tmp_path / "claude"
    monkeypatch.setattr(session_records, "default_claude_home", lambda: claude_home)
    agent_id = "agent-a7157f79d55caab81"
    project = claude_home / "projects" / "project"
    project.mkdir(parents=True)
    parent = project / f"{agent_id}.jsonl"
    parent.write_text("{}\n", encoding="utf-8")
    child = project / "other-parent" / "subagents" / f"{agent_id}.jsonl"
    child.parent.mkdir(parents=True)
    child.write_text("{}\n", encoding="utf-8")

    assert record_paths.find_session_record(agent_id, codex_home=tmp_path / "codex") == record_paths.SessionRecord(
        "claude", (parent,)
    )
