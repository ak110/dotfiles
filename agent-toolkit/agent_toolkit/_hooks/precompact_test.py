"""原本を解決できないときに圧縮指示を変更しない。"""

import json

import pytest

from agent_toolkit._common import session_record_paths
from agent_toolkit._hooks import precompact


@pytest.mark.parametrize("resolved", [True, False])
def test_compaction_guidance_requires_record(monkeypatch, tmp_path, capsys, resolved):
    """確認できた原本に限って短い要約を依頼する。"""
    record = tmp_path / "conversation.jsonl"
    record.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(precompact, "find_claude_records", lambda _session: (record,) if resolved else ())
    assert precompact.main(json.dumps({"session_id": "conversation"})) == 0
    text = capsys.readouterr().out
    assert bool(text) is resolved


def test_compaction_guidance_does_not_list_other_engine_records(monkeypatch, tmp_path, capsys):
    """Claude Codeに記録の無い会話は、Codexなど他の実行系の保存先を一覧せずに指示を省く。"""

    def list_other_engine(*_args, **_kwargs):
        pytest.fail("Claude Code以外の保存先を一覧した")

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    (tmp_path / "claude" / "projects" / "project").mkdir(parents=True)
    monkeypatch.setattr(session_record_paths, "find_codex_records", list_other_engine)
    monkeypatch.setattr(session_record_paths, "_find_agy_or_opencode_record", list_other_engine)
    assert precompact.main(json.dumps({"session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})) == 0
    assert not capsys.readouterr().out
