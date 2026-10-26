"""両ホストの公開hookから、配送・取得・再発行までを通す。"""

import json
import shlex

import pytest

from agent_toolkit._hooks import plugin_resources, posttooluse
from agent_toolkit._hooks.pretooluse import dispatch, read_delivery


def _context(captured):
    assert not captured.err
    if not captured.out.strip():
        return ""
    return json.loads(captured.out)["hookSpecificOutput"]["additionalContext"]


def _payload(tmp_path, codex):
    target = tmp_path / "plan.md"
    payload: dict = {"session_id": "delivery-acceptance", "cwd": str(tmp_path)}
    if codex:
        payload.update(
            {
                "turn_id": "turn",
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": f"*** Begin Patch\n*** Add File: {target}\n+## 原因分析\n+観測した根拠\n*** End Patch\n"
                },
            }
        )
    else:
        payload.update(
            {"tool_name": "Write", "tool_input": {"file_path": str(target), "content": "## 原因分析\n観測した根拠\n"}}
        )
    return payload


def test_claude_guidance_does_not_send_document_body_or_record_acquisition(tmp_path, capsys):
    payload = _payload(tmp_path, False)
    assert dispatch.main(json.dumps(payload)) == 0
    context = _context(capsys.readouterr())
    assert "root-cause-analysis.md" in context
    assert "記録の粒度は" not in context
    assert dispatch.main(json.dumps(payload)) == 0
    _context(capsys.readouterr())
    records = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
    root_cause = plugin_resources.skill_document_path("bugfix", "references/root-cause-analysis.md")
    assert not read_delivery.target_ready(records, read_delivery.read_triggers.ReadTarget(root_cause), "main")


def test_claude_reissues_after_all_declared_bodies_arrive(tmp_path, capsys):
    payload = _payload(tmp_path, False)
    assert dispatch.main(json.dumps(payload)) == 0
    context = _context(capsys.readouterr())
    root_cause = plugin_resources.skill_document_path("bugfix", "references/root-cause-analysis.md")
    assert str(root_cause) in context
    assert root_cause.read_text(encoding="utf-8").strip() not in context
    assert dispatch.main(json.dumps(payload)) == 0
    _context(capsys.readouterr())
    records = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
    assert not read_delivery.target_ready(records, read_delivery.read_triggers.ReadTarget(root_cause), "main")
    assert not (tmp_path / "plan.md").exists()


def test_codex_read_acquisition_is_allowed_then_original_operation_passes(tmp_path, capsys):
    payload = _payload(tmp_path, True)
    assert dispatch.main(json.dumps(payload)) == 0
    assert "root-cause-analysis.md" in _context(capsys.readouterr())
    assert dispatch.main(json.dumps(payload)) == 0
    assert not _context(capsys.readouterr())
    seen = set()
    for _ in range(12):
        records = read_delivery.read_state(payload["session_id"])
        selected = records[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
        targets = [
            target
            for target in read_delivery.selected_targets(selected, "main")
            if not read_delivery.target_ready(selected, target, "main")
        ]
        if not targets:
            break
        paths = list(dict.fromkeys(target.path for target in targets if target.path not in seen))
        if paths:
            read = {**payload, "tool_name": "Bash", "tool_input": {"command": "cat -- " + shlex.join(map(str, paths))}}
            assert dispatch.main(json.dumps(read)) == 0
            _context(capsys.readouterr())
            result = {
                **read,
                "hook_event_name": "PostToolUse",
                "tool_response": {"stdout": "".join(path.read_text(encoding="utf-8") for path in paths), "exit_code": 0},
            }
            assert posttooluse.main(json.dumps(result)) == 0
            capsys.readouterr()
            seen.update(paths)
        assert dispatch.main(json.dumps(payload)) == 0
        _context(capsys.readouterr())
    else:
        pytest.fail("明示した全資料の取得で元の操作が通らなかった")
    assert seen
    assert dispatch.main(json.dumps(payload)) == 0
    assert not _context(capsys.readouterr())


@pytest.mark.parametrize("codex", [False, True])
def test_confirmation_entry_warns_without_blocking(tmp_path, capsys, codex):
    payload = {
        "session_id": "confirmation-acceptance",
        "cwd": str(tmp_path),
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": []},
    }
    if codex:
        payload["turn_id"] = "turn"
    for index in range(2):
        assert dispatch.main(json.dumps(payload)) == 0
        context = _context(capsys.readouterr())
        if index:
            assert not context
        else:
            assert "approval-scope.md" in context
            assert "choice-construction.md" in context


@pytest.mark.parametrize("codex", [False, True])
def test_pattern_process_termination_stays_blocked_before_document_delivery(tmp_path, capsys, codex):
    payload = _payload(tmp_path, codex)
    payload.update({"tool_name": "Bash", "tool_input": {"command": "pkill -f worker"}})
    assert dispatch.main(json.dumps(payload)) == 2
    captured = capsys.readouterr()
    assert "pkill" in captured.err
    assert "read-delivery" not in captured.err
