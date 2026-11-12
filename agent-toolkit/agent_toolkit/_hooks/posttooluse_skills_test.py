# pylint: disable=protected-access
"""posttooluse_skillsの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse, uwi_completion
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
    """実回答差分の配送と、対象外イベントによる未消費を検証する。"""

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
    def test_answer_transition_is_delivered_only_to_main(self, tmp_path, monkeypatch, capsys, excluded):
        for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION"):
            monkeypatch.delenv(name, raising=False)
        repo = "example.com/owner/repo"
        root = tmp_path / "private-notes"
        inbox = root / "inbox"
        inbox.mkdir(parents=True)
        (root / "processing").mkdir()
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(root))
        monkeypatch.setattr(uwi_completion, "resolve_target_repo", lambda _cwd: repo)
        entry = "---\ntarget_repo: " + repo + "\ntype: uwi\n---\n\n## 質問\n\n本文\n\n## 回答\n\n"
        answered = inbox / "new-answer.md"
        answered.write_text(entry, encoding="utf-8")
        (inbox / "unanswered.md").write_text(entry, encoding="utf-8")
        (inbox / "already-answered.md").write_text(entry + "既存回答\n", encoding="utf-8")
        sid = "uwi-transition"
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
        baseline = {"main": {repo: ["already-answered.md"]}}
        assert session_state.read_state(sid)[uwi_completion.STATE_KEY_ANSWERED] == baseline
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
            assert session_state.read_state(sid)[uwi_completion.STATE_KEY_ANSWERED] == baseline

        context = run(payload)
        assert "新たに回答されたUWI" in context
        assert "new-answer.md" in context
        assert "already-answered.md" not in context
        assert "unanswered.md" not in context
        assert session_state.read_state(sid)[uwi_completion.STATE_KEY_ANSWERED] == {
            "main": {repo: ["already-answered.md", "new-answer.md"]}
        }
        assert not run(payload)
