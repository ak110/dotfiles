"""要求受付の編集前通知をhookの入出力で確かめる。 pyfltr: allow-comment"""

import json
import shlex

import pytest

from agent_toolkit._git import command as git_command
from agent_toolkit._hooks import termination_evidence, user_prompt_submit
from agent_toolkit._hooks.pretooluse import dispatch


@pytest.mark.parametrize("tool", ["Edit", "repl_edit", "Write", "NotebookEdit", "Bash"])
def test_intake_repo_edit_notice_once_per_human_input(tmp_path, monkeypatch, capsys, tool):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(dispatch.repository_protection, "check", lambda *_args: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    git_command.run(["init", "-q"], cwd=repo, check=True)
    trace = tmp_path / "trace.jsonl"
    target = repo / "new" / "file"
    tracked = repo / "tracked.txt"
    tracked.write_text("before", encoding="utf-8")
    git_command.run(["add", "--", tracked.name], cwd=repo, check=True)
    base = {"session_id": "intake-test", "cwd": str(repo), "transcript_path": str(trace)}

    def prompt(text, **extra):
        termination_evidence.observe_user(json.dumps({**base, "prompt": text, **extra}))

    def start(work):
        termination_evidence.observe_tool(
            json.dumps(
                {
                    **base,
                    "tool_name": "Skill",
                    "tool_use_id": work,
                    "tool_input": {"skill": "agent-toolkit:atk-mode", "args": work},
                }
            ),
            after=True,
        )

    def edit(path=target, **extra):
        tool_name = "Edit" if tool == "repl_edit" else tool
        tool_input = {
            "Edit": {"file_path": str(path), "old_string": "before", "new_string": "after"},
            "Write": {"file_path": str(path), "content": "after"},
            "NotebookEdit": {"notebook_path": str(path), "new_source": "after"},
            "Bash": {"command": f"echo after > {shlex.quote(str(path))}"},
        }[tool_name]
        assert (
            dispatch.main(
                json.dumps(
                    {
                        **base,
                        "tool_name": tool_name,
                        "tool_use_id": "repl_edit" if tool == "repl_edit" else "edit",
                        "tool_input": tool_input,
                        **extra,
                    }
                )
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out or "{}").get("hookSpecificOutput", {})
        assert result.get("permissionDecision") != "deny"
        return result.get("additionalContext", "")

    prompt("要求を登録して")
    start("add-awi-by-user")
    assert "セッション境界" not in edit(tmp_path / "draft.md")
    assert "セッション境界" not in edit(agent_id="child")
    context = edit(tracked)
    assert 'kind="notice"' in context
    assert str(tracked.name) in context
    assert "セッション境界" in context
    assert "セッション境界" not in edit()
    prompt("完了通知", source="agent")
    assert "セッション境界" not in edit()
    trace.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": context}]}}) + "\n", encoding="utf-8"
    )
    prompt("別の要求を登録して")
    assert "セッション境界" in edit()
    for work in ("implement", "process-wi"):
        prompt("別の作業を開始して")
        start(work)
        assert "セッション境界" not in edit()


@pytest.mark.parametrize(
    ("host", "entry", "context"),
    [
        ("codex", "$atk-mode", {"turn_id": "turn"}),
        ("agy", "/atk-mode", {"runtime": "agy"}),
    ],
)
def test_named_intake_entry_tracks_human_prompts(tmp_path, monkeypatch, capsys, host, entry, context):
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(dispatch.repository_protection, "check", lambda *_args: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    git_command.run(["init", "-q"], cwd=repo, check=True)
    base = {"session_id": "named-intake", "cwd": str(repo), **context}

    def prompt(message, turn, **extra):
        meta = {"turn_id": str(turn)} if host == "codex" else {}
        assert user_prompt_submit.main(json.dumps({**base, **meta, "prompt": message, **extra})) == 0
        return capsys.readouterr().out

    def patch(turn):
        payload = {
            **base,
            "turn_id": str(turn),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Add File: example.txt\n+new\n*** End Patch"},
        }
        assert dispatch.main(json.dumps(payload)) == 0
        return capsys.readouterr().out

    assert "セッション境界" not in prompt("add-awi-by-user の使い方は？", 1)
    assert "セッション境界" not in patch(1)
    started = prompt(entry + " add-awi-by-user 登録して", 2)
    assert "セッション境界" in started
    if host == "codex":
        assert "セッション境界" in patch(2)
        assert "セッション境界" not in patch(2)
    assert "セッション境界" in prompt("追加の変更をお願いします", 3)
    if host == "codex":
        assert "セッション境界" in patch(3)
    assert "セッション境界" not in prompt(entry + " implement", 4, source="agent")
    followed = prompt("もう1件お願いします", 5)
    assert "セッション境界" in (followed if host == "agy" else patch(5))
    assert "セッション境界" not in prompt(entry + " implement", 6)
    assert "セッション境界" not in patch(6)
