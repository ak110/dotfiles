"""同名の品質スキルと運用スキルの資料識別を分離する。"""

import json

from agent_toolkit._hooks import document_reads
from agent_toolkit._hooks.pretooluse import operation_skills


def test_basics_reference_uses_observed_manifest_and_path(tmp_path):
    root = tmp_path / "agent-basics"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "agent-basics"}), encoding="utf-8")
    path = root / "skills/writing-standards/references/writing.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 品質基準\n必要な本文\n", encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    response = {"type": "text", "file": {"content": text, "startLine": 1, "numLines": 2, "totalLines": 2}}
    observed = document_reads.read_tool_document({"file_path": str(path)}, response)
    assert observed is not None
    assert observed.plugin == "agent-basics"
    assert not observed.partial
    assert observed.path == str(path)
    reads = document_reads.bash_document_reads(f"cat -- {path}")
    assert len(reads) == 1 and reads[0].plugin == "agent-basics"


def test_basics_acquisition_does_not_mark_legacy_toolkit_skill(monkeypatch):
    writes = []
    monkeypatch.setattr(operation_skills, "update_state", lambda *args, **kwargs: writes.append(args))
    observed = document_reads.DocumentRead("writing-standards", "SKILL.md", partial=False, plugin="agent-basics")
    operation_skills.record_document_read("session", observed, "main")
    assert not writes
