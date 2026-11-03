"""公開hookから終了証拠まで、資料の所有pluginと実体の識別を保持する。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process, codex_delivered_payload


@pytest.mark.parametrize("skill", ["report-completion", "process-wi", "add-awi-by-user"])
@pytest.mark.parametrize("plugin", ["agent-toolkit", "foreign-plugin"])
@pytest.mark.parametrize("result", ["body", "different", "failure", "truncated", "persisted"])
@pytest.mark.parametrize("extra_eof_lf", [False, True])
def test_public_hook_keeps_document_owner(tmp_path, monkeypatch, skill, plugin, result, extra_eof_lf):
    """同名foreign資料・別本文・失敗・省略をToolkitの起動証拠へ転用しない。"""
    root = tmp_path / plugin
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": plugin}), encoding="utf-8")
    document = root / "skills" / skill / "SKILL.md"
    document.parent.mkdir(parents=True)
    body = f"---\nname: {skill}\n---\n# 起動資料\n固有本文\n"
    document.write_text(body + ("\n" if extra_eof_lf else ""), encoding="utf-8")
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
    assert not termination_evidence.session_works(payload)
    delivered = codex_delivered_payload(
        {
            **payload,
            "tool_response": body
            if result == "body"
            else "Warning: truncated output (original token count: 100)\n…100 tokens truncated…",
        },
        tmp_path,
    )
    follow = _run_hook_in_process(
        "pretooluse", {**delivered, "hook_event_name": "PreToolUse", "tool_name": "ObserveDelivery", "tool_input": {}}
    )
    assert follow.returncode == 0 and not follow.stderr
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


@pytest.mark.parametrize("skill", ["report-completion", "process-wi", "add-awi-by-user"])
@pytest.mark.parametrize("delivery", ["full", "omitted", "split", "recovery"])
def test_codex_skill_start_follows_produced_outer_delivery(tmp_path, monkeypatch, host_environ, skill, delivery):
    """実cat・sedから外側配送と公開Preを経由し、3種類の開始証拠まで確認する。"""
    root = tmp_path / "plugin"
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    path = root / "skills" / skill / "SKILL.md"
    path.parent.mkdir(parents=True)
    body = f"---\nname: {skill}\n---\n# 開始資料\n必要な固有本文\n"
    path.write_text(body, encoding="utf-8")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [])
    commands = ["cat -- " + shlex.quote(str(path))]
    if delivery == "split":
        commands = [f"sed -n '{span}p' -- {shlex.quote(str(path))}" for span in ("1,3", "4,$")]
    if delivery == "recovery":
        saved = tmp_path / "saved.txt"
        saved.write_text(body, encoding="utf-8")
        commands.append("cat -- " + shlex.quote(str(saved)))
    for index, command in enumerate(commands):
        payload = {
            "session_id": "produced-start",
            "turn_id": "turn",
            "tool_use_id": f"read-{index}",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(tmp_path),
        }
        before = _run_hook_in_process("pretooluse", {**payload, "hook_event_name": "PreToolUse"})
        assert before.returncode == 0 and not before.stderr
        result = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert result.returncode == 0 and not result.stderr
        after = _run_hook_in_process(
            "posttooluse", {**payload, "hook_event_name": "PostToolUse", "tool_response": result.stdout}
        )
        assert after.returncode == 0 and not after.stderr
        if index == 0:
            assert not termination_evidence.session_works(payload)
        visible = result.stdout
        if delivery == "omitted" or delivery == "recovery" and index == 0:
            visible = "Warning: truncated output (original token count: 100)\n…100 tokens truncated…"
        delivered = codex_delivered_payload({**payload, "tool_response": visible}, tmp_path)
        observed = _run_hook_in_process(
            "pretooluse", {**delivered, "hook_event_name": "PreToolUse", "tool_name": "ObserveDelivery", "tool_input": {}}
        )
        assert observed.returncode == 0 and not observed.stderr
        if delivery == "split" and index == 0:
            assert not termination_evidence.session_works(payload)
    works = termination_evidence.session_works({"session_id": "produced-start"})
    assert bool(works) is (delivery != "omitted")
    if works:
        assert bool(works[0][1].get("completion_report")) is (skill == "report-completion")
