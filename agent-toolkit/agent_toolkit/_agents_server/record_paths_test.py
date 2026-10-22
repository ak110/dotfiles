"""record_pathsモジュールのテスト。"""

import pathlib

import pytest

from agent_toolkit._agents_server import backends, record_paths


@pytest.mark.parametrize(
    ("identifier", "accepted"),
    [
        ("11111111-2222-4333-8444-555555555555", True),
        ("a3aa408fdf18a760f", True),
        ("agent-a3aa408fdf18a760f", True),
        ("a", True),
        ("agent-ABCDEF0123456789ABCDEF0123456789ABCDEF", True),
        ("/root/reader_review", True),
        ("/root/reviewer/reader_review_r2", True),
        ("/root/reviewer/group_a/reader_review_r2", True),
        ("reader-fit-review", False),
        ("読者別探索", False),
        ("", False),
        ("/root/", False),
        ("/root/reviewer//reader_review", False),
        ("/root/reviewer/reader_review/", False),
        (None, False),
        (123, False),
    ],
)
def test_launch_identifier_formats(identifier: object, accepted: bool) -> None:
    """受理形式は起動結果の種類に従い、観測例の長さに固定しない。"""
    assert record_paths.is_launch_identifier(identifier) is accepted


def test_record_engines_match_supported_engines() -> None:
    """記録を探索できる実行系は、agents_serverが起動できる実行系と一致する。

    実行系を追加して記録の探索を追随させないと、その実行系の委譲先は`atk agents logs`と
    `atk run-script session-review-evidence`の双方で記録なしとして扱われる。
    """
    assert record_paths.RECORD_ENGINES == backends.SUPPORTED_ENGINES


def test_claude_subagent_record_is_found_by_its_agent_id(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開CLIへ渡すサブエージェント識別子で親の下の記録を解決する。"""
    claude_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
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
