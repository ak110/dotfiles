"""原本を解決できないときに圧縮指示を変更しない。"""

import json
from types import SimpleNamespace

import pytest

from agent_toolkit._hooks import precompact


@pytest.mark.parametrize("resolved", [True, False])
def test_compaction_guidance_requires_record(monkeypatch, tmp_path, capsys, resolved):
    """確認できた原本に限って短い要約を依頼する。"""
    record = tmp_path / "conversation.jsonl"
    record.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        precompact,
        "find_session_record",
        lambda _session: SimpleNamespace(engine="claude", paths=[record]) if resolved else None,
    )
    assert precompact.main(json.dumps({"session_id": "conversation"})) == 0
    text = capsys.readouterr().out
    assert bool(text) is resolved
