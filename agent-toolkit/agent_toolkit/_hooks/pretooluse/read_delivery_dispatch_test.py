"""両ホストの公開hookから、配送・取得・再発行までを通す。"""

import json
import shlex

import pytest

from agent_toolkit._hooks import plugin_resources, posttooluse
from agent_toolkit._hooks.pretooluse import dispatch, read_delivery
from agent_toolkit._testing import git_repository, pretooluse_support


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
    assert dispatch.main(json.dumps(payload)) == 2
    captured = capsys.readouterr()
    assert "未取得資料" in captured.err
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
    assert dispatch.main(json.dumps(payload)) == 2
    captured = capsys.readouterr()
    assert "未取得資料" in captured.err
    records = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
    assert not read_delivery.target_ready(records, read_delivery.read_triggers.ReadTarget(root_cause), "main")
    assert not (tmp_path / "plan.md").exists()
    for _ in range(12):
        selected = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
        targets = [
            target
            for target in read_delivery.selected_targets(selected, "main")
            if not read_delivery.target_ready(selected, target, "main")
        ]
        if not targets:
            break
        for path in dict.fromkeys(target.path for target in targets):
            skill = read_delivery.skill_name(path)
            result = {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill" if skill else "Read",
                "tool_input": {"skill": skill} if skill else {"file_path": str(path)},
                "tool_response": "起動成功" if skill else path.read_text(encoding="utf-8"),
            }
            assert posttooluse.main(json.dumps(result)) == 0
            capsys.readouterr()
        status = dispatch.main(json.dumps(payload))
        captured = capsys.readouterr()
        if status == 0:
            _context(captured)
        else:
            assert status == 2 and "未取得資料" in captured.err
    else:
        pytest.fail("全資料取得後も未取得資料が残った")
    assert dispatch.main(json.dumps(payload)) == 0
    assert not _context(capsys.readouterr())


def test_codex_read_acquisition_is_allowed_then_original_operation_passes(tmp_path, capsys):
    payload = _payload(tmp_path, True)
    assert dispatch.main(json.dumps(payload)) == 0
    assert "root-cause-analysis.md" in _context(capsys.readouterr())
    assert dispatch.main(json.dumps(payload)) == 2
    assert "未取得資料" in capsys.readouterr().err
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
        status = dispatch.main(json.dumps(payload))
        captured = capsys.readouterr()
        if status == 0:
            _context(captured)
        else:
            assert status == 2 and "未取得資料" in captured.err
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


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("temporary", [False, True])
@pytest.mark.parametrize("filename", ["memo.md", "wait.sh"])
def test_new_body_keeps_writing_standards_without_existing_edit_materials(
    tmp_path, monkeypatch, capsys, codex, exists, temporary, filename
):
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    folder = tmp_path / ("managed-temp" if temporary else "project")
    folder.mkdir()
    if not temporary:
        git_repository.init_repository(folder)
    target = folder / filename
    content = "sleep 1\n" if filename.endswith(".sh") else "新本文\n"
    if exists:
        target.write_text("旧本文\n", encoding="utf-8")
    payload: dict = {"session_id": "edit-materials", "cwd": str(folder)}
    if codex:
        payload.update(
            {
                "turn_id": "turn",
                "tool_name": "apply_patch",
                "tool_input": {"command": f"*** Begin Patch\n*** Add File: {target}\n+{content}*** End Patch"},
            }
        )
    else:
        payload.update({"tool_name": "Write", "tool_input": {"file_path": str(target), "content": content}})
    assert dispatch.main(json.dumps(payload)) == 0
    context = _context(capsys.readouterr())
    assert "references/writing.md" in context
    if filename.endswith(".sh"):
        assert "references/bash.md" in context
    assert ("references/file-editing.md" in context) is exists
    assert ("references/file-editing-claude.md" in context) is (exists and not codex)


def test_request_reuses_read_parsing_and_rechecks_the_next_request(tmp_path, monkeypatch):
    target = tmp_path / "source.md"
    target.write_text("# 節\n本文\n", encoding="utf-8")
    rows = [
        read_delivery.read_triggers.ReadTrigger(target, number, "判断", ("section:*/source.md#節",), (target,))
        for number in range(40)
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    original = read_delivery.bash_read_paths
    calls = []

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(read_delivery, "bash_read_paths", counted)
    payload = {"cwd": str(tmp_path), "turn_id": "turn"}
    data = {"command": f"cat -- {target}"}
    read_delivery.deliver(payload, "Bash", data, "parsing-first")
    first = len(calls)
    assert 0 < first <= 3
    target.write_text("# 節\n変更後\n", encoding="utf-8")
    read_delivery.deliver(payload, "Bash", data, "parsing-second")
    assert len(calls) == first * 2


def test_prepare_batches_complete_reads_and_reacquires_changed_body(tmp_path, monkeypatch):
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_text("# 第一\n本文\n", encoding="utf-8")
    second.write_text("# 第二\n別本文\n", encoding="utf-8")
    row = read_delivery.read_triggers.ReadTrigger(first, 1, "操作", ("body-writing",), (first, second))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    # 取得イベントを実際のPostToolUseへ通し、準備処理の被覆判定を監視する。
    original = pretooluse_support._run_posttooluse  # pylint: disable=protected-access
    events = []

    def recorded(payload, env, **_kwargs):
        events.append(payload)
        return original(payload, env)

    monkeypatch.setattr(pretooluse_support, "_run_posttooluse", recorded)
    payload: dict = {
        "cwd": str(tmp_path),
        "turn_id": "turn",
        "session_id": "batched-reads",
        "tool_name": "apply_patch",
        "tool_input": {"command": f"*** Begin Patch\n*** Add File: {tmp_path / 'new.md'}\n+本文\n*** End Patch"},
    }
    pretooluse_support.prepare_read_delivery(payload)
    assert len(events) == 1
    assert events[0]["tool_name"] == "Bash"
    records = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
    assert all(
        read_delivery.target_ready(records, read_delivery.read_triggers.ReadTarget(path), "main") for path in (first, second)
    )
    first.write_text("# 第一\n更新された本文\n", encoding="utf-8")
    assert not read_delivery.target_ready(records, read_delivery.read_triggers.ReadTarget(first), "main")
    pretooluse_support.prepare_read_delivery(payload)
    assert len(events) == 2
    records = read_delivery.read_state(payload["session_id"])[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
    assert read_delivery.target_ready(records, read_delivery.read_triggers.ReadTarget(first), "main")
