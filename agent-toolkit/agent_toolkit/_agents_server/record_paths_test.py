"""record_pathsモジュールのテスト。"""

import json
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


@pytest.mark.parametrize("engine", sorted(backends.SUPPORTED_ENGINES))
def test_every_start_engine_resolves_its_isolated_record(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    """起動可能な各engineの記録を実際に探索し、登録追加時の記録側の未対応も検出する。"""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    claude_home, codex_home, state_root = (tmp_path / name for name in ("claude", "codex", "state"))
    identifier = "11111111-2222-4333-8444-555555555555"
    inputs = {
        "claude": claude_home / "projects" / "project" / f"{identifier}.jsonl",
        "codex": codex_home / "sessions" / "2026" / "11" / "12" / f"rollout-2026-11-12T00-00-00-{identifier}.jsonl",
        "agy": state_root / "agents-server" / "root" / "logs" / f"{identifier}.jsonl",
        "opencode": state_root / "agents-server" / "root" / "logs" / "opencode" / f"{identifier}.jsonl",
    }
    assert engine in inputs, f"新しい起動engineの記録入力と解決契約を追加する必要がある: {engine}"
    record = inputs[engine]
    record.parent.mkdir(parents=True)
    body = json.dumps({"engine_input": engine, "session": identifier}, ensure_ascii=False) + "\n"
    record.write_text(body, encoding="utf-8")

    resolved = record_paths.find_session_record(
        identifier, claude_home=claude_home, codex_home=codex_home, state_root=state_root
    )

    assert resolved is not None
    assert resolved.engine == engine
    assert resolved.paths == (record,)
    assert resolved.paths[0].read_text(encoding="utf-8") == body
    assert (
        record_paths.find_session_record(
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            claude_home=claude_home,
            codex_home=codex_home,
            state_root=state_root,
        )
        is None
    )
