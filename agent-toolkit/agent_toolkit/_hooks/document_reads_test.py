"""同名の品質スキルと運用スキルの資料識別を分離する。"""

import json
import subprocess

import pytest

from agent_toolkit._common import plugin_roots, read_triggers
from agent_toolkit._hooks import document_reads
from agent_toolkit._hooks.pretooluse import read_delivery


def test_receipt_candidates_share_document_scan_and_refresh_changed_body(tmp_path, monkeypatch):
    """必要な節の受領を保ち、同じ本文の解析を共有して同サイズ更新も識別する。"""
    path = tmp_path / "document.md"
    text = f"# {tmp_path.name}\n" + "".join(f"## Section {index}\nold\n" for index in range(20))
    scans = []
    document_lines = read_triggers.document_lines

    def observe_lines(body, **_kwargs):
        scans.append(body)
        yield from document_lines(body)

    monkeypatch.setattr(read_triggers, "document_lines", observe_lines)
    versions = []
    for body in (text, text.replace("old", "new")):
        path.write_text(body, encoding="utf-8")
        scans.clear()
        candidates = document_reads.receipt_candidates([read_triggers.ReadTarget(path)])
        received = document_reads.received_candidates(candidates, [body])
        assert len(received) == 22
        assert next(item for item in received if item["heading"] == "Section 19")["ranges"] == [[40, 41]]
        assert len(scans) <= 2
        versions.append(received[0]["version"])
    assert versions[0] != versions[1]


@pytest.mark.parametrize("tool_name", ["WebFetch", "Read"])
def test_receipt_preparation_excludes_unrelated_documents(tmp_path, monkeypatch, tool_name):
    """公開配送操作は明示読取だけを候補化し、非読取では無関係な本文を解析しない。"""
    path = tmp_path / "document.md"
    path.write_text("# Document\ncontent\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(path, 1, "操作", ("path:*.py",), (path,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    calls = []
    receipt_candidates = document_reads.receipt_candidates

    def observe_candidates(*args, **kwargs):
        calls.append(args)
        return receipt_candidates(*args, **kwargs)

    monkeypatch.setattr(document_reads, "receipt_candidates", observe_candidates)
    read_path = tmp_path / "read.md"
    read_path.write_text("# Requested\ncontent\n", encoding="utf-8")
    inputs = {"file_path": str(read_path)} if tool_name == "Read" else {"url": "https://example.com", "prompt": "全文を返す"}
    decision = read_delivery.deliver({"cwd": str(tmp_path)}, tool_name, inputs, "receipt-preparation")
    assert decision.block is None
    assert not decision.notices
    assert {target.path for args in calls for target in args[0]} == ({read_path} if tool_name == "Read" else set())


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
