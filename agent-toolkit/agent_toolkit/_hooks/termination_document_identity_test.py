"""公開hookから終了証拠まで、資料の所有pluginと実体の識別を保持する。"""

import json
import shlex

import pytest

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.mark.parametrize("skill", ["report-completion", "process-wi", "add-awi-by-user"])
@pytest.mark.parametrize("plugin", ["agent-toolkit", "foreign-plugin"])
@pytest.mark.parametrize("result", ["body", "different", "failure", "truncated", "persisted"])
def test_public_hook_keeps_document_owner(tmp_path, monkeypatch, skill, plugin, result):
    """同名foreign資料・別本文・失敗・省略をToolkitの起動証拠へ転用しない。"""
    root = tmp_path / plugin
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": plugin}), encoding="utf-8")
    document = root / "skills" / skill / "SKILL.md"
    document.parent.mkdir(parents=True)
    body = f"---\nname: {skill}\n---\n# 起動資料\n固有本文\n"
    document.write_text(body, encoding="utf-8")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [])
    response = body
    if result == "different":
        response = "# 別の資料\n本文\n"
    elif result == "truncated":
        # Codex 0.166.0のnative省略包絡。識別行が残っても起動成功を証明しない。
        response = f"Warning: truncated output (original token count: 100)\n{body}…90 tokens truncated…"
    elif result == "persisted":
        response = {"stdout": body, "persistedOutputPath": str(tmp_path / "saved.txt")}
    payload = {
        "session_id": "document-identity",
        "turn_id": "turn",
        "tool_use_id": "read-document",
        "hook_event_name": "PostToolUseFailure" if result == "failure" else "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat -- " + shlex.quote(str(document))},
        "tool_response": response,
        "cwd": str(tmp_path),
    }
    observed = _run_hook_in_process("posttooluse", payload)
    assert observed.returncode == 0 and not observed.stderr
    works = termination_evidence.session_works(payload)
    accepted = plugin == "agent-toolkit" and result == "body"
    assert bool(works) is accepted
    if accepted:
        work = works[0][1]
        assert bool(work.get("completion_report")) is (skill == "report-completion")
        if skill != "report-completion":
            assert work["deliverables"] == [f"agent-toolkit:{skill}の開始"]


@pytest.mark.parametrize("same_body", [False, True])
def test_public_hook_uses_observed_instruction_path(tmp_path, monkeypatch, same_body):
    """生成資料は観測した実体の識別行を使い、稼働rootの同名資料へ置き換えない。"""
    root = tmp_path / "generated-plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    (root / "plugin.json").write_text("{}", encoding="utf-8")
    document = root / "skills/report-completion/references/antigravity-full-instructions.md"
    document.parent.mkdir(parents=True)
    document.write_text("# 観測した生成資料\n固有本文\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [])
    payload = {
        "session_id": "instruction-path",
        "runtime": "agy",
        "tool_use_id": "read-instructions",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat -- " + shlex.quote(str(document))},
        "tool_response": document.read_text(encoding="utf-8") if same_body else "# 別資料\n",
        "cwd": str(tmp_path),
    }
    result = _run_hook_in_process("posttooluse", payload)
    assert result.returncode == 0 and not result.stderr
    works = termination_evidence.session_works(payload)
    assert bool(works) is same_body
    if same_body:
        assert works[0][1]["completion_report"] is True
