"""多数の識別子を続けて探す`RecordLocator`が、`find_session_record`と同じ記録を返すことを検証する。"""

import pathlib

import pytest

from agent_toolkit._common import session_record_paths

_CLAUDE_ID = "11111111-2222-3333-4444-555555555555"
_CODEX_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _touch(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "session_id",
    [_CLAUDE_ID, "22222222-2222-3333-4444-555555555555", _CODEX_ID, "agent-abc123", "missing-id", "bad/id", "late-created"],
)
def test_locator_matches_find_session_record(tmp_path: pathlib.Path, session_id: str) -> None:
    """Claude Codeの複数project、Codex、サブエージェント、未作成と一覧の後に作られた記録で同じ結果を返す。"""
    claude_home, codex_home = tmp_path / "claude", tmp_path / "codex"
    _touch(claude_home / f"projects/b-project/{_CLAUDE_ID}.jsonl")
    _touch(claude_home / f"projects/a-project/{_CLAUDE_ID}.jsonl")
    _touch(claude_home / "projects/a-project/22222222-2222-3333-4444-555555555555.jsonl")
    _touch(claude_home / f"projects/a-project/{_CLAUDE_ID}/subagents/agent-abc123.jsonl")
    _touch(codex_home / f"sessions/2026/11/07/rollout-2026-11-07T00-00-00-{_CODEX_ID}.jsonl")
    _touch(codex_home / f"sessions/2026/11/06/rollout-2026-11-06T00-00-00-{_CODEX_ID}.jsonl")
    (claude_home / "projects/not-a-directory").write_text("", encoding="utf-8")
    homes = {"claude_home": claude_home, "codex_home": codex_home, "state_root": tmp_path / "state"}
    locator = session_record_paths.RecordLocator(**homes)
    assert locator.find(_CLAUDE_ID) is not None
    _touch(claude_home / "projects/a-project/late-created.jsonl")
    assert locator.find(session_id) == session_record_paths.find_session_record(session_id, **homes)


def test_locator_finds_antigravity_record_without_rescanning_claude_and_codex(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一覧に無いAntigravityの委譲先は、Claude CodeとCodexの保存先を走査し直さずに同じ結果を返す。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    agy_id = "33333333-2222-3333-4444-555555555555"
    _touch(tmp_path / f"home/.gemini/antigravity-cli/brain/{agy_id}/.system_generated/logs/transcript_full.jsonl")
    homes = {"claude_home": tmp_path / "claude", "codex_home": tmp_path / "codex", "state_root": tmp_path / "state"}
    _touch(homes["claude_home"] / f"projects/a-project/{_CLAUDE_ID}.jsonl")
    expected = session_record_paths.find_session_record(agy_id, **homes)
    assert expected is not None and expected.engine == "agy"
    locator = session_record_paths.RecordLocator(**homes)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("一覧に無い識別子でClaude CodeとCodexの保存先を走査し直した")

    monkeypatch.setattr(session_record_paths, "find_session_record", unexpected)
    assert locator.find(agy_id) == expected
