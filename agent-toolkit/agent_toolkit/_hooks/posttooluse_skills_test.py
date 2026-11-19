# pylint: disable=protected-access
"""posttooluse_skillsの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state, uwi_watch
from agent_toolkit._hooks import posttooluse
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _run


class TestRetiredSkillInvocation:
    """廃止したスキル名の起動を、計画の工程の開始と帰属行の配送の契機にしない。

    計画の工程の開始は`atk run-script plan-create`の成功（`posttooluse_plan_create_test.py`）、
    帰属行は`atk-mode`の`references/commit-message.md`の読取（`dispatch_commit_identity_test.py`）が契機である。
    """

    @pytest.mark.parametrize(
        "tool_input",
        [
            {"skill": "agent-toolkit:write-plan"},
            {"skill": "write-plan"},
            {"skill": "agent-toolkit:atk-mode", "args": "write-plan"},
            {"skill": "agent-toolkit:commit"},
        ],
    )
    def test_skill_invocation_neither_starts_plan_nor_delivers_trailer(
        self, tmp_path: pathlib.Path, tool_input: dict[str, str]
    ) -> None:
        sid = "retired-skill"
        transcript = tmp_path / "project-records" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True)
        line = {
            "type": "assistant",
            "version": "2.1.291",
            "effort": "medium",
            "message": {"model": "claude-opus-5-5", "role": "assistant", "content": [{"type": "text", "text": "応答"}]},
        }
        transcript.write_text(json.dumps(line) + "\n", encoding="utf-8")
        result = _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": tool_input,
                "cwd": str(tmp_path),
                "effort": {"level": "low"},
                "transcript_path": str(transcript),
            },
            state_dir=tmp_path,
            home_dir=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "Co-Authored-By:" not in result.stdout
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is not True


class TestUwiCompletionNotice:
    """投入時に記録したUWIへの回答の配送と、対象外イベントによる未消費を検証する。"""

    @pytest.mark.parametrize(
        "excluded",
        [
            None,
            "PostToolUseFailure",
            "PermissionDenied",
            "in-process",
            "AGENT_TOOLKIT_DELEGATED_SESSION",
            "AGENT_TOOLKIT_OWNER_SESSION",
        ],
    )
    @pytest.mark.cpu_limit(
        2.5,
        reason=(
            "UWI3件の回答差分をPost3回と主体別1回で確認し、通知と永続状態の両方を検証する（Linux限定再確認のCPU合計"
            "最大1.110秒）。"
        ),
    )
    def test_answer_transition_is_delivered_only_to_main(self, tmp_path, monkeypatch, capsys, excluded):
        for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION"):
            monkeypatch.delenv(name, raising=False)
        repo = "example.com/owner/repo"
        root = tmp_path / "private-notes"
        directory = root / "2026" / "11"
        directory.mkdir(parents=True)
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(root))
        entry = "---\nstate: inbox\ntarget_repo: " + repo + "\ntype: uwi\n---\n\n## 質問\n\n本文\n\n## 回答\n\n"
        new_answer, unanswered, unrecorded = (f"20261101-000000-00{index}.md" for index in (1, 2, 3))
        answered = directory / new_answer
        answered.write_text(entry, encoding="utf-8")
        (directory / unanswered).write_text(entry, encoding="utf-8")
        (directory / unrecorded).write_text(entry + "既存回答\n", encoding="utf-8")
        sid = "uwi-transition"
        assert uwi_watch.add(sid, [new_answer, unanswered])
        payload = {
            "session_id": sid,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": str(tmp_path),
        }

        def run(event):
            capsys.readouterr()
            assert posttooluse.main(json.dumps(event)) == 0
            output = capsys.readouterr()
            assert not output.err
            if not output.out:
                return ""
            return json.loads(output.out)["hookSpecificOutput"]["additionalContext"]

        assert not run(payload)
        assert uwi_watch.awaiting(session_state.read_state(sid)) == [new_answer, unanswered]
        answered.write_text(entry + "回答する。\n", encoding="utf-8")
        if excluded is not None:
            with monkeypatch.context() as marks:
                event = dict(payload)
                if excluded == "in-process":
                    event["agent_id"] = "agent-1"
                elif excluded.startswith("AGENT_TOOLKIT_"):
                    marks.setenv(excluded, "1" if excluded == "AGENT_TOOLKIT_DELEGATED_SESSION" else "owner")
                else:
                    event["hook_event_name"] = excluded
                assert not run(event)
            assert uwi_watch.awaiting(session_state.read_state(sid)) == [new_answer, unanswered]

        context = run(payload)
        assert "新たに回答されたUWI" in context
        assert new_answer in context
        assert unrecorded not in context
        assert unanswered not in context
        assert uwi_watch.awaiting(session_state.read_state(sid)) == [unanswered]
        assert not run(payload)
