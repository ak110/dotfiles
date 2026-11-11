# pylint: disable=protected-access
"""posttooluse_skillsの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _load_posttooluse_module, _observe, _run

_POSTTOOLUSE_MODULE = _load_posttooluse_module()


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
    """UWI回答差分をPostToolUseの追加contextへ接続する。"""

    @staticmethod
    def _clear_delegation_marks(monkeypatch: pytest.MonkeyPatch) -> None:
        """メインの実行主体として判定されるよう、委譲先セッションの印を除く。"""
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
        monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)

    def test_dispatch_appends_answered_filename_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回答差分通知をLLM向けnoticeとして蓄積する。"""
        self._clear_delegation_marks(monkeypatch)
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda _session_id, _cwd, _transcript_path: "newly answered: answered.md",
        )
        payload = {
            "session_id": "uwi-answer",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
        }

        notices = _observe(payload)

        assert len(notices) == 1
        assert "newly answered: answered.md" in notices[0]

    @pytest.mark.parametrize("hook_event_name", ["PostToolUseFailure", "PermissionDenied"])
    def test_failure_events_skip_uwi_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        hook_event_name: str,
    ) -> None:
        """失敗イベントでは回答差分を問い合わせない。"""
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda *_args: pytest.fail("失敗イベントでUWI通知が呼ばれた"),
        )
        payload = {
            "session_id": "uwi-failure",
            "hook_event_name": hook_event_name,
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
        }

        notices = _observe(payload)

        assert not notices

    def test_in_process_subagent_skips_uwi_notice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`agent_id`を持つin-processのサブエージェントでは通知を組み立てない。"""
        self._clear_delegation_marks(monkeypatch)
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda *_args: pytest.fail("サブエージェントでUWI通知が呼ばれた"),
        )
        payload = {
            "session_id": "uwi-subagent",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
            "agent_id": "agent-1",
        }

        notices = _observe(payload)

        assert not notices

    @pytest.mark.parametrize(
        ("name", "value"),
        [("AGENT_TOOLKIT_DELEGATED_SESSION", "1"), ("AGENT_TOOLKIT_OWNER_SESSION", "owner-1")],
    )
    def test_delegated_session_skips_uwi_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
    ) -> None:
        """`agents_server`が起動した委譲先セッションでは通知を組み立てない。"""
        self._clear_delegation_marks(monkeypatch)
        monkeypatch.setenv(name, value)
        monkeypatch.setattr(
            _POSTTOOLUSE_MODULE._uwi_completion,  # pylint: disable=protected-access  # noqa: SLF001
            "build_notice",
            lambda *_args: pytest.fail("委譲先セッションでUWI通知が呼ばれた"),
        )
        payload = {
            "session_id": "uwi-delegate",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "README.md"},
            "cwd": "/repo",
        }

        notices = _observe(payload)

        assert not notices
