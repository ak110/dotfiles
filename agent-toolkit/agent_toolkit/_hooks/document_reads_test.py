"""同名の品質スキルと運用スキルの資料識別を分離する。"""

import json
import subprocess

from agent_toolkit._common import plugin_roots
from agent_toolkit._hooks import document_reads
from agent_toolkit._hooks.pretooluse import read_delivery


def test_basics_reference_uses_observed_manifest_and_path(tmp_path):
    """Bashの読取は資料の所有プラグインを実在のmanifestから識別し、観測した実体を保持する。"""
    root = tmp_path / "agent-basics"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "agent-basics"}), encoding="utf-8")
    path = root / "skills/writing-standards/references/writing.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 品質基準\n必要な本文\n", encoding="utf-8")
    reads = document_reads.bash_document_reads(f"cat -- {path}")
    assert len(reads) == 1 and reads[0].plugin == "agent-basics" and reads[0].path == str(path)


def test_hook_declarations_and_body_identity_use_the_effective_project(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    root = tmp_path / "toolkit"
    source = root / "skills/example/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        "| 起動時 | `agent-basics:writing-standards/references/writing.md` | 起動時 | |\n",
        encoding="utf-8",
    )
    projects = [tmp_path / "first", tmp_path / "second"]
    installed = [{"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(root)}]
    for index, project in enumerate(projects):
        project.mkdir()
        basics = tmp_path / f"basics-{index}"
        path = basics / "skills/writing-standards/references/writing.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"# 品質本文{index}\n", encoding="utf-8")
        installed.append(
            {
                "id": "agent-basics@mine",
                "scope": "project",
                "enabled": True,
                "projectPath": str(project),
                "installPath": str(basics),
            }
        )
    monkeypatch.setattr(
        plugin_roots.process_tree,
        "run_with_timeout",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(installed), ""),
    )
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    for index, project in enumerate(projects):
        rows = read_delivery.declarations(str(project), host="claude")
        assert [row.targets[0].body() for row in rows] == [f"# 品質本文{index}\n"]
        assert document_reads.body_returned(
            "writing-standards",
            "references/writing.md",
            f"# 品質本文{index}",
            plugin="agent-basics",
            host="claude",
            project=project,
        )
        assert not document_reads.body_returned(
            "writing-standards",
            "references/writing.md",
            f"# 品質本文{1 - index}",
            plugin="agent-basics",
            host="claude",
            project=project,
        )
