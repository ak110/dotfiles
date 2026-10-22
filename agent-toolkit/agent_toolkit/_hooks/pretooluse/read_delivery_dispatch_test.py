"""両ホストの公開hook入口から、配送・取得・再発行までを通す。"""

import json

import pytest

from agent_toolkit._hooks import plugin_resources, posttooluse
from agent_toolkit._hooks.pretooluse import dispatch, read_delivery


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


def test_claude_reissues_after_all_declared_bodies_arrive(tmp_path, capsys):
    payload = _payload(tmp_path, False)
    bodies = []
    for _ in range(12):
        outcome = dispatch.main(json.dumps(payload))
        captured = capsys.readouterr()
        if outcome == 0:
            break
        assert outcome == 2
        bodies.append(captured.err)
    else:
        pytest.fail("配送の再発行で取得が完了しなかった")
    root_cause = plugin_resources.skill_document_path("bugfix", "references/root-cause-analysis.md")
    assert root_cause.read_text(encoding="utf-8").strip() in "\n".join(bodies)
    assert not (tmp_path / "plan.md").exists()


def test_codex_read_acquisition_is_allowed_then_original_operation_passes(tmp_path, capsys):
    payload = _payload(tmp_path, True)
    assert dispatch.main(json.dumps(payload)) == 2
    captured = capsys.readouterr()
    assert "root-cause-analysis.md" in captured.err
    seen = set()
    for _ in range(12):
        records = read_delivery.read_state(payload["session_id"])
        selected = records[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
        targets = read_delivery.selected_targets(selected, "main")
        assert targets
        for path in dict.fromkeys(target.path for target in targets if target.path not in seen):
            read = {**payload, "tool_name": "Bash", "tool_input": {"command": f"cat -- {path}"}}
            assert dispatch.main(json.dumps(read)) == 0
            capsys.readouterr()
            result = {
                **read,
                "hook_event_name": "PostToolUse",
                "tool_response": {"stdout": path.read_text(encoding="utf-8"), "exit_code": 0},
            }
            assert posttooluse.main(json.dumps(result)) == 0
            capsys.readouterr()
            seen.add(path)
        outcome = dispatch.main(json.dumps(payload))
        captured = capsys.readouterr()
        if outcome == 0:
            assert not captured.err
            break
        assert outcome == 2
    else:
        pytest.fail("明示した全資料の取得で元の操作が通らなかった")


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
    for _ in range(2):
        assert dispatch.main(json.dumps(payload)) == 0
        captured = capsys.readouterr()
        assert not captured.err
        assert "approval-scope.md" in captured.out
        assert "choice-construction.md" in captured.out
