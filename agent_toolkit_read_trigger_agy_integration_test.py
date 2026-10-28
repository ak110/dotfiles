"""正式なagy生成rootの名前空間・全文転送・節指定を同じ入力で結合検証する。"""

import json
import subprocess

import pytest
from agent_toolkit._common import plugin_roots, read_triggers
from agent_toolkit._hooks import document_reads
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

from pytools._internal import antigravity_plugin


@pytest.fixture(name="generated")
def _generated(tmp_path):
    source = tmp_path / "agent-basics"
    manifest = {"name": "agent-basics", "version": "1.0.0", "description": "品質基準"}
    path = source / ".claude-plugin/plugin.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    skill = source / "skills/long/SKILL.md"
    skill.parent.mkdir(parents=True)
    body = (
        "# 長文スキル\n\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 起動時 | `references/quality.md` | 起動時 | `skill:long` |\n"
        "| 対象を選ぶ前 | `../peer/SKILL.md` | 判断 | `read:*/skills/peer/SKILL.md` |\n"
        "\n<!-- read-table-source: reading-references.md -->\n\n## 必要節\n選択した基準本文\n\n## 別用途\n"
        + "別用途の大きな本文\n"
        * 4000
    )
    raw = "---\nname: long\ndescription: 長文の品質資料\n---\n\n" + body
    skill.write_text(raw, encoding="utf-8")
    quality = skill.parent / "references/quality.md"
    quality.parent.mkdir()
    quality.write_text("# 品質\n最初の選択基準\n", encoding="utf-8")
    (skill.parent / "reading-references.md").write_text(
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 根拠を取得する時 | `references/quality.md#品質` | 判断 | `read:*/references/quality.md` |\n",
        encoding="utf-8",
    )
    peer = source / "skills/peer/SKILL.md"
    peer.parent.mkdir()
    peer.write_text("---\nname: peer\ndescription: 他資料\n---\n\n# 他資料\n", encoding="utf-8")
    toolkit = tmp_path / "agent-toolkit-agy"
    toolkit.mkdir()
    toolkit_manifest = {**manifest, "name": "agent-toolkit"}
    (toolkit / "plugin.json").write_text(json.dumps(toolkit_manifest), encoding="utf-8")
    (toolkit / ".claude-plugin").mkdir()
    (toolkit / ".claude-plugin/plugin.json").write_text(json.dumps(toolkit_manifest), encoding="utf-8")
    (toolkit / "skills").mkdir()
    assert antigravity_plugin.sync(tmp_path, "agent-basics")
    native = tmp_path / "agent-basics-agy"
    full = native / "skills/long/references/antigravity-full-instructions.md"
    assert full.read_text(encoding="utf-8") == "\n" + body
    assert full.stat().st_size > antigravity_plugin.RULE_FILE_BYTES
    return toolkit, native, full


def test_generated_root_keeps_logical_namespace_and_relative_base(generated, monkeypatch):
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    toolkit, native, full = generated
    assert plugin_roots.resolve_plugin_root("agent-basics", toolkit) == native
    entry = native / "skills/long/SKILL.md"
    assert read_triggers.resolve_read_source(entry, toolkit) == full
    # 実体を明示した同じproviderの入口は、別版のCLI rootからもその実体へ転送する。
    assert read_triggers.resolve_read_source(entry, toolkit.parent / "agent-toolkit") == full
    rows = read_triggers.parse_tables(full, toolkit, strict=True)
    assert rows[0].documents == (native / "skills/long/references/quality.md",)
    assert rows[1].documents == (native / "skills/peer/SKILL.md",)
    assert rows[2].documents == (native / "skills/long/references/quality.md",)
    assert rows[2].headings == ("品質",)
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", toolkit)
    monkeypatch.setattr(read_triggers, "document_sources", lambda *_args: [])
    assert all(row.source.is_relative_to(native) for row in read_delivery.declarations(str(toolkit.parent)))
    assert document_reads.skill_document(str(full)) == ("long", "SKILL.md")
    assert document_reads.body_returned(
        "long", "SKILL.md", full.read_text(encoding="utf-8"), plugin="agent-basics", path=str(full)
    )
    assert read_delivery.matches("skill:agent-basics:long", "Read", {"file_path": str(full)}, "")


def test_generated_section_requires_full_native_read(generated, monkeypatch):
    toolkit, _native, full = generated
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", toolkit)
    target = read_triggers.ReadTarget(full, "必要節")
    row = read_triggers.ReadTrigger(full, 1, "判断", ("tool:Do",), (full,), ("必要節",))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    memory: dict = {}
    monkeypatch.setattr(read_delivery, "read_state", lambda _session: memory)
    monkeypatch.setattr(read_delivery, "update_state", lambda _session, update: update(memory))
    lines = full.read_text(encoding="utf-8").splitlines()
    start = lines.index("## 必要節") + 1
    end = start + 1
    partial = ["sed", "-n", f"{start},{end}p", "--", str(full)]
    output = subprocess.run(partial, check=True, capture_output=True, encoding="utf-8").stdout
    command = f"sed -n \x27{start},{end}p\x27 -- {full}"
    assert read_delivery.matches(
        "section:agent-basics:*/skills/long/SKILL.md#必要節", "Bash", {"command": command}, str(toolkit.parent)
    )
    event = {
        "session_id": "agy-full",
        "turn_id": "turn",
        "cwd": str(toolkit.parent),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": output, "exit_code": 0},
    }
    assert _run_hook_in_process("posttooluse", event).returncode == 0
    records = memory.get(read_delivery.rules_context.OPERATION_SKILL_READY_KEY, {})
    assert not read_delivery.target_ready(records, target, "main")
    whole = subprocess.run(["cat", "--", str(full)], check=True, capture_output=True, encoding="utf-8").stdout
    event["tool_input"] = {"command": f"cat -- {full}"}
    event["tool_response"] = {"stdout": whole, "exit_code": 0}
    assert _run_hook_in_process("posttooluse", event).returncode == 0
    records = memory.get(read_delivery.rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_delivery.target_ready(records, target, "main")
