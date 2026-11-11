"""役割の起動配送と、主体別の取得手段をまたぐ読込契約を確かめる。"""

import json
import subprocess

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_acquisition_support import (
    _assert_continues_without_guidance,
    _assert_no_read_guidance,
    _state,
)

__all__ = ["_assert_continues_without_guidance", "_assert_no_read_guidance", "_state"]


@pytest.mark.parametrize("tool", ["Edit", "MultiEdit", "Write"])
def test_hook_self_edit_twice_preserves_acquired_body(tmp_path, monkeypatch, state, host_environ, tool):
    """実読取後の自己編集成功を経由し、2回目の編集前に再取得や遮断を求めない。"""
    document = tmp_path / "reference.md"
    document.write_text("# 基準\n本文0\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:" + tool,), (document,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "self-edit", "cwd": str(tmp_path)}
    command = read_delivery.acquisition_step(read_triggers.ReadTarget(document), "codex").split("`", 2)[1]
    produced = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert produced.returncode == 0 and not produced.stderr
    acquired = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": produced.stdout, "stderr": produced.stderr, "exit_code": produced.returncode},
        },
    )
    assert acquired.returncode == 0 and not acquired.stderr
    for index in range(2):
        before = document.read_text(encoding="utf-8")
        after = before.replace(f"本文{index}", f"本文{index + 1}")
        edit = {"old_string": f"本文{index}", "new_string": f"本文{index + 1}"}
        inputs = {
            "file_path": str(document),
            **({"content": after} if tool == "Write" else {"edits": [edit]} if tool == "MultiEdit" else edit),
        }
        request = {**payload, "tool_name": tool, "tool_input": inputs}
        allowed = _run_hook_in_process("pretooluse", request)
        assert allowed.returncode == 0 and not allowed.stderr
        assert str(document) not in allowed.stdout and '"permissionDecision": "deny"' not in allowed.stdout
        document.write_text(after, encoding="utf-8")
        # Edit/Writeの型はClaude Code 2.1.316の出力スキーマに基づく。
        # MultiEditは同じ前全文を渡す境界の検証であり、現行ホストの起動対応を保証しない。
        observed = _run_hook_in_process(
            "posttooluse",
            {
                **request,
                "hook_event_name": "PostToolUse",
                "tool_response": {"filePath": str(document), "originalFile": before, "userModified": False},
            },
        )
        assert observed.returncode == 0 and not observed.stderr
    assert read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(document), "main"
    )


@pytest.mark.parametrize(
    "change", ["unread", "other-agent", "bash", "external", "failed", "missing-original", "user-modified", "staged"]
)
def test_hook_unknown_edits_require_reacquisition(tmp_path, monkeypatch, state, change):
    """取得・主体・編集成功・前後本文の対応が欠ける変更は継承しない。"""
    document = tmp_path / "reference.md"
    before = "# 基準\n前本文\n"
    document.write_text(before, encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Edit",), (document,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "unknown-edit", "cwd": str(tmp_path)}
    if change != "unread":
        acquired = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": str(document)},
                "tool_response": before,
            },
        )
        assert acquired.returncode == 0 and not acquired.stderr
    after = before.replace("前本文", "後本文")
    document.write_text(after + ("外部変更\n" if change == "external" else ""), encoding="utf-8")
    response = {
        "filePath": str(document),
        "originalFile": None if change == "missing-original" else before,
        "userModified": change == "user-modified",
        "staged": change == "staged",
    }
    result = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            **({"agent_id": "other"} if change == "other-agent" else {}),
            "hook_event_name": "PostToolUseFailure" if change == "failed" else "PostToolUse",
            "tool_name": "Bash" if change == "bash" else "Edit",
            "tool_input": {"command": "sed -i 's/前本文/後本文/' reference.md"}
            if change == "bash"
            else {"file_path": str(document), "old_string": "前本文", "new_string": "後本文"},
            "tool_response": response,
        },
    )
    assert result.returncode == 0 and not result.stderr
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert document_reads.acquisition_state(records, read_triggers.ReadTarget(document), "main") == (
        "missing" if change == "unread" else "updated"
    )
    request = {
        **payload,
        "tool_name": "Edit",
        "tool_input": {"file_path": str(document), "old_string": "後本文", "new_string": "次本文"},
    }
    guided = _run_hook_in_process("pretooluse", request)
    assert guided.returncode == 0 and str(document) in guided.stdout
    following = _run_hook_in_process("pretooluse", request)
    _assert_continues_without_guidance(following)
    assert document_reads.acquisition_state(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(document), "main"
    ) == ("missing" if change == "unread" else "updated")


def test_hook_invoked_skill_self_edit_twice_keeps_invocation(tmp_path, monkeypatch, state):
    """起動済み本文の自己編集では起動証拠も継承し、参照取得とは区別する。"""
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/process-wi/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: target\n---\n# 基準\n本文0\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Edit",), (skill,))
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "skill-self-edit", "cwd": str(tmp_path)}
    acquired = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "agent-toolkit:process-wi"},
            "tool_response": "起動済み",
        },
    )
    assert acquired.returncode == 0 and not acquired.stderr
    for index in range(2):
        before = skill.read_text(encoding="utf-8")
        inputs = {"file_path": str(skill), "old_string": f"本文{index}", "new_string": f"本文{index + 1}"}
        request = {**payload, "tool_name": "Edit", "tool_input": inputs}
        allowed = _run_hook_in_process("pretooluse", request)
        assert allowed.returncode == 0 and not allowed.stderr
        assert str(skill) not in allowed.stdout and '"permissionDecision": "deny"' not in allowed.stdout
        skill.write_text(before.replace(inputs["old_string"], inputs["new_string"]), encoding="utf-8")
        observed = _run_hook_in_process(
            "posttooluse",
            {
                **request,
                "hook_event_name": "PostToolUse",
                "tool_response": {"filePath": str(skill), "originalFile": before, "userModified": False},
            },
        )
        assert observed.returncode == 0 and not observed.stderr
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "main", invocation=True)


@pytest.mark.parametrize("purpose", ["reference", "invocation", "both"])
def test_public_compact_preserves_skill_acquisition_purpose(tmp_path, monkeypatch, state, purpose):
    """Read→compact→書込の案内でも、名指し参照と起動要求の用途を保つ。"""
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/process-wi/SKILL.md"
    reference = skill.parent / "references/reference.md"
    reference.parent.mkdir(parents=True)
    skill.write_text("---\nname: process-wi\n---\n# 基準\n本文\n", encoding="utf-8")
    reference.write_text("# 参照元\n本文\n", encoding="utf-8")
    named = read_triggers.ReadTrigger(reference, 1, "操作", ("read:*/reference.md", "tool:Write"), (skill,))
    invocation = read_triggers.ReadTrigger(root / "source.md", 1, "操作", ("skill:write-plan", "tool:Write"), (skill,))
    rows = [named] if purpose == "reference" else [invocation] if purpose == "invocation" else [named, invocation]
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    payload = {"session_id": "compact-purpose", "cwd": str(tmp_path)}
    write = {"file_path": str(tmp_path / "output.md"), "content": "本文"}

    def invoke(event, tool, inputs, response=None):
        result = _run_hook_in_process(
            "posttooluse" if event == "PostToolUse" else "pretooluse",
            {**payload, "hook_event_name": event, "tool_name": tool, "tool_input": inputs, "tool_response": response},
        )
        assert result.returncode == 0 and not result.stderr
        return result.stdout

    if purpose != "invocation":
        invoke("PreToolUse", "Read", {"file_path": str(reference)})
        invoke("PostToolUse", "Read", {"file_path": str(reference)}, reference.read_text(encoding="utf-8"))
    invoke("PreToolUse", "Write", write)
    if purpose == "reference":
        invoke("PostToolUse", "Read", {"file_path": str(skill)}, skill.read_text(encoding="utf-8"))
    else:
        invoke("PostToolUse", "Skill", {"skill": "agent-toolkit:process-wi"}, "起動済み")
    compact = _run_hook_in_process("rules_context", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
    assert compact.returncode == 0 and not compact.stderr
    context = json.loads(compact.stdout)["hookSpecificOutput"]["additionalContext"]
    skill_step = "`Skill`で`agent-toolkit:process-wi`"
    assert skill_step not in context
    assert ("Readで`" + str(skill) + "`" in context) is (purpose == "reference")
    failure = rules_context.acquisition_failure(
        read_triggers.ReadTarget(skill, "欠落"), ValueError("見出し不明"), shell=False, reference=purpose == "reference"
    )
    assert (skill_step in failure) is (purpose != "reference")
    current = invoke("PreToolUse", "Write", write)
    assert (str(skill) in current) is (purpose == "reference")
    assert (skill_step in current) is (purpose != "reference")
    invoke("PostToolUse", "Read", {"file_path": str(skill)}, skill.read_text(encoding="utf-8"))
    following = invoke("PreToolUse", "Write", write)
    if purpose == "reference":
        assert str(skill) not in following and '"permissionDecision": "deny"' not in following
    else:
        _assert_no_read_guidance(following)
        assert not read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main", invocation=True
        )
        invoke("PostToolUse", "Skill", {"skill": "agent-toolkit:process-wi"}, "起動済み")
        _assert_no_read_guidance(invoke("PreToolUse", "Write", write))
        assert read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main", invocation=True
        )
