"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import shutil
import subprocess

import pytest

from agent_toolkit import hook as hook_entry
from agent_toolkit._common import plugin_roots
from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _guidance,
    _local_plugin_root,
    _memory,
    _notice_body,
)

__all__ = ["_guidance", "_local_plugin_root", "_memory", "_notice_body"]


@pytest.mark.parametrize(
    "payload,expected", [({}, "Readで全文読"), ({"agent_id": "child"}, "Readで全文読"), ({"turn_id": "turn"}, "cat --")]
)
@pytest.mark.usefixtures("memory")
def test_missing_heading_guides_full_read_without_marking(tmp_path, monkeypatch, payload, expected):
    source = tmp_path / "source.md"
    source.write_text("# 実見出し\n固有本文\n", encoding="utf-8")
    row = ReadTrigger(source, 1, "操作", ("tool:Do",), (source,), ("不存在",))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    notice = _notice_body(read_delivery.deliver(payload, "Do", {}, "test"))
    assert "不存在" in notice and "見出し" in notice and expected in notice
    assert "固有本文" not in notice
    assert _guidance(read_delivery.deliver(payload, "Do", {}, "test"))


@pytest.mark.parametrize("agent", [False, True])
def test_range_failure_skill_fallback_and_missing_target(tmp_path, agent):
    skill = tmp_path / ".claude" / "skills" / "local" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 固有本文\n", encoding="utf-8")
    target = ReadTarget(skill, "不存在")
    notice = rules_context.acquisition_failure(target, ValueError("範囲不明"), shell=False, agent=agent, cwd=str(tmp_path))
    assert "`Skill`で`local`" in notice and "固有本文" not in notice
    assert ("Readで全文読" in notice) is agent
    decoding = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
    notice = rules_context.acquisition_failure(target, decoding, shell=False, agent=agent, cwd=str(tmp_path))
    assert "invalid" in notice and "宣言の見出しと資料を整合" not in notice
    skill.unlink()
    notice = rules_context.acquisition_failure(target, FileNotFoundError(), shell=False, agent=agent)
    assert str(skill) in notice and "存在しない" in notice and "復旧" in notice
    assert "委譲元" in notice and "ユーザー" in notice and "`Skill`" not in notice


@pytest.mark.parametrize("command", ["pretooluse", "posttooluse"])
def test_public_hook_scope_reaches_dynamic_targets_and_document_read_consumers(tmp_path, monkeypatch, command):
    """動的解決と直接の本文識別・読取記録も、payloadのsessionの一覧を使う。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    toolkit = tmp_path / "cache/mine/agent-toolkit/9"
    basics = tmp_path / "cache/mine/agent-basics/1"
    (basics / ".claude-plugin").mkdir(parents=True)
    (basics / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics","version":"1"}', encoding="utf-8")
    author = basics / "skills/writing-standards/SKILL.md"
    reference = author.parent / "references/writing.md"
    reference.parent.mkdir(parents=True)
    author.write_text("# 作成基準\n", encoding="utf-8")
    reference.write_text("# 共通基準\n内容\n", encoding="utf-8")
    skill = tmp_path / ".claude/skills/project/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 起動時 | `agent-basics:writing-standards/references/writing.md` | 起動時 | |\n",
        encoding="utf-8",
    )
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: str(executable))
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", toolkit)
    monkeypatch.setattr(hook_entry, "_record_activity", lambda *_args, **_kwargs: None)
    declaration = ReadTrigger(author, 1, "操作", ("path:*.py",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [declaration])
    seen = []

    def inventory(args, **_kwargs):
        data = [
            {"id": f"{name}@mine", "scope": "user", "enabled": True, "installPath": str(root)}
            for name, root in (("agent-toolkit", toolkit), ("agent-basics", basics))
        ]
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    def consume(_module, _text, payload, _args, **_kwargs):
        session = payload["session_id"]
        row = ReadTrigger(skill, 1, "操作", (), (), selector="project-skill")
        project = read_delivery.dynamic_targets(row, payload, "Read", {"file_path": str(skill)}, {}, "main", session)
        row = ReadTrigger(basics / "skills/review-standards/SKILL.md", 1, "判断", (), (), selector="artifact-standards")
        artifact = read_delivery.dynamic_targets(
            row, payload, "Write", {"file_path": str(tmp_path / "code.py")}, {}, "main", session
        )
        returned = document_reads.body_returned(
            "writing-standards", "references/writing.md", "# 共通基準\n内容\n", plugin="agent-basics", project=tmp_path
        )
        observe_received(payload, "Read", payload["tool_input"], session)
        seen.append((ReadTarget(reference) in project, ReadTarget(author) in artifact, returned))
        return 0

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", inventory)
    monkeypatch.setattr(hook_entry, "_invoke_module", consume)
    for _ in range(2):
        result = _run_hook_in_process(
            command,
            {
                "session_id": "dynamic-session",
                "cwd": str(tmp_path),
                "hook_event_name": "PostToolUse",
                "tool_input": {"file_path": str(reference)},
                "tool_response": "# 共通基準\n内容\n",
            },
        )
        assert result.returncode == 0
    assert seen == [(True, True, True), (True, True, True)]


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_installed_declarations_detect_inventory_revision(tmp_path, monkeypatch, host):
    """host別の導入資料を解決し、次の要求では更新された版の資料を選ぶ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    toolkit = tmp_path / "cache/mine/agent-toolkit/9.9.9"
    basics = tmp_path / "cache/mine/agent-basics/1.0"
    (basics / "skills/writing-standards").mkdir(parents=True)
    (basics / "skills/writing-standards/SKILL.md").write_text("# 品質\n", encoding="utf-8")
    sources = [toolkit / "share" / f"{index}.md" for index in range(2)]
    for source in sources:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
            "| 起動時 | `agent-basics:writing-standards/SKILL.md` | 起動時 | |\n",
            encoding="utf-8",
        )
    claude = [
        {"id": f"{name}@mine", "scope": "user", "enabled": True, "installPath": str(root)}
        for name, root in (("agent-toolkit", toolkit), ("agent-basics", basics))
    ]
    codex = {
        "installed": [
            {"name": name, "marketplaceName": "mine", "enabled": True, "installed": True, "version": version}
            for name, version in (("agent-toolkit", "9.9.9"), ("agent-basics", "1.0"))
        ]
    }

    def claude_inventory(args, **_kwargs):
        return subprocess.CompletedProcess(args, 0, json.dumps(claude), "")

    codex_cli = codex_plugin_cli(codex["installed"], other=claude_inventory)

    def inventory(args, **kwargs):
        return codex_cli(args, **kwargs)

    # Codexの公開cache配置をそのまま使う。
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    codex_basics = tmp_path / "codex/plugins/cache/mine/agent-basics/1.0"
    codex_basics.mkdir(parents=True)
    shutil.copytree(basics / "skills", codex_basics / "skills")
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", inventory)
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", toolkit)
    monkeypatch.setattr(read_delivery.read_triggers, "document_sources", lambda *_args: sources)
    assert plugin_roots.sibling_plugin_root("agent-basics", toolkit) is None
    for version in ("1.0", "2.0"):
        if version == "2.0":
            basics = basics.with_name(version)
            (basics / "skills/writing-standards").mkdir(parents=True)
            (basics / "skills/writing-standards/SKILL.md").write_text("# 改訂品質\n", encoding="utf-8")
            claude[1]["installPath"] = str(basics)
            codex["installed"][1]["version"] = version
            codex_cli = codex_plugin_cli(codex["installed"], other=claude_inventory)
            codex_basics = codex_basics.with_name(version)
            shutil.copytree(basics / "skills", codex_basics / "skills")
        with read_delivery.read_triggers.resolution_scope():
            rows = read_delivery.declarations(str(tmp_path), host=host)
            assert len(rows) == 2
            root = basics if host == "claude" else codex_basics
            assert {target.path for row in rows for target in row.targets} == {root / "skills/writing-standards/SKILL.md"}


def test_body_tables_keep_document_order(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)

    def table(name):
        return (
            "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n"
            "| --- | --- | --- | --- |\n"
            f"| 取得時 | `{name}.md` | 判断 | `read:*/{name}.md` |\n"
        )

    for name in ("before", "after"):
        (tmp_path / f"{name}.md").write_text(f"# {name}\n基準\n", encoding="utf-8")
    owner = tmp_path / "SKILL.md"
    owner.write_text(table("before") + "\n本文\n\n" + table("after"), encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", tmp_path)
    monkeypatch.setattr(read_delivery.read_triggers, "document_sources", lambda *_args: [owner])
    rows = read_delivery.declarations(str(tmp_path))
    assert [row.targets[0].path.name for row in rows] == ["before.md", "after.md"]
