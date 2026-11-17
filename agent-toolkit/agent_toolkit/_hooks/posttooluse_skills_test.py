# pylint: disable=protected-access
"""posttooluse_skillsの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state, uwi_watch
from agent_toolkit._hooks import posttooluse
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _run


class TestPlanModeSkillInvocation:
    """write-plan スキル呼び出し検出 (Skill ツール)。"""

    @pytest.mark.parametrize("skill_name", ["agent-toolkit:write-plan", "write-plan"])
    def test_skill_invocation_sets_flag(self, tmp_path: pathlib.Path, skill_name: str):
        sid = "skill-flag"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": skill_name},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_mode_skill_invoked") is True

    def test_other_skill_does_not_set_flag(self, tmp_path: pathlib.Path):
        sid = "skill-other"
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-basics:writing-standards"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_mode_skill_invoked") is not True


class TestCommitSkillAttributionNotice:
    """Claude Codeで`agent-toolkit:commit`の起動が完了した時点に、観測した帰属行を示す。"""

    @staticmethod
    def _payload(tmp_path: pathlib.Path, skill: str, *, with_transcript: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "session_id": "commit-skill",
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": skill},
            "cwd": str(tmp_path),
            "effort": {"level": "low"},
        }
        if with_transcript:
            transcript = tmp_path / "project-records" / "commit-skill.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            line = {
                "type": "assistant",
                "version": "2.1.291",
                "effort": "medium",
                "message": {"model": "claude-opus-5-5", "role": "assistant", "content": [{"type": "text", "text": "応答"}]},
            }
            transcript.write_text(json.dumps(line) + "\n", encoding="utf-8")
            payload["transcript_path"] = str(transcript)
        return payload

    def test_commit_skill_delivers_observed_trailer(self, tmp_path: pathlib.Path) -> None:
        # test-copy-contract: transcriptの観測モデルとpayloadの推論量から帰属行を生成し、Skill完了時に配送する契約。
        result = _run(self._payload(tmp_path, "agent-toolkit:commit"), state_dir=tmp_path, home_dir=tmp_path)

        assert result.returncode == 0, result.stderr
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "Co-Authored-By: Claude Opus 5.5 / Low <noreply@anthropic.com>" in context

    @pytest.mark.parametrize(
        ("skill", "with_transcript"),
        [("agent-toolkit:commit", False), ("agent-toolkit:write-plan", True)],
    )
    def test_trailer_is_not_delivered_without_observation_or_for_other_skills(
        self, tmp_path: pathlib.Path, skill: str, with_transcript: bool
    ) -> None:
        payload = self._payload(tmp_path, skill, with_transcript=with_transcript)

        result = _run(payload, state_dir=tmp_path, home_dir=tmp_path)

        assert result.returncode == 0, result.stderr
        assert "Co-Authored-By:" not in result.stdout


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
