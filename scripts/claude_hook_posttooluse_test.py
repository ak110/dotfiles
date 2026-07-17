"""scripts/claude_hook_posttooluse.py のテスト。

dotfiles 個人環境専用の PostToolUse フックのテスト。
独立スクリプトなのでfork-server経由（フォールバック時はsubprocess）で起動し、状態ファイルの中身を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook_posttooluse.py"
_AGENT_TOOLKIT_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"

sys.path.insert(0, str(_AGENT_TOOLKIT_SCRIPTS))
import _fork_runner  # noqa: E402  # pylint: disable=wrong-import-position
from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def _state_env(tmp_path: pathlib.Path) -> dict[str, str]:
    """`tempfile.gettempdir()`を tmp_path へ振り向ける env を返す。"""
    return {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}


def _run(payload: object, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return _fork_runner.run_script(_SCRIPT, input=text, env=env)


def _state_path(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
    return tmp_path / f"claude-agent-toolkit-{session_id}.json"


def _read_state(tmp_path: pathlib.Path, session_id: str) -> dict:
    path = _state_path(tmp_path, session_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class TestAgentToolkitEditSkillRecording:
    """`agent-toolkit-edit` スキル呼び出しを `agent_toolkit_edit_skill_invoked` へ記録する。"""

    def test_records_invocation(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        sid = "rec-1"
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit-edit"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("agent_toolkit_edit_skill_invoked") is True

    def test_ignores_other_skill(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        sid = "rec-other-skill"
        # `agent-toolkit:plan-mode`は`session_review_extension_pending`の対象でもあるため、
        # 当該フラグへ影響しない別系統のスキル名を使う。
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "some-other-plugin:some-skill"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        assert not _state_path(tmp_path, sid).exists()

    def test_ignores_non_skill_tool(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        sid = "rec-non-skill"
        result = _run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        assert not _state_path(tmp_path, sid).exists()

    def test_empty_session_id_no_write(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit-edit"},
                "session_id": "",
            },
            env=env,
        )
        assert result.returncode == 0
        # 状態ファイルが存在しないこと。
        assert not list(tmp_path.glob("claude-agent-toolkit-*.json"))

    def test_no_rewrite_when_flag_already_true(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        sid = "rec-already"
        path = _state_path(tmp_path, sid)
        path.write_text(
            json.dumps({"agent_toolkit_edit_skill_invoked": True, "other": "keep"}),
            encoding="utf-8",
        )
        mtime_before = path.stat().st_mtime_ns
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit-edit"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        # 既存内容が保持されていること。
        state = _read_state(tmp_path, sid)
        assert state == {"agent_toolkit_edit_skill_invoked": True, "other": "keep"}
        # 書き込みがスキップされていること（mtime 不変）。
        assert path.stat().st_mtime_ns == mtime_before

    def test_concurrent_write_preserves_both_keys(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """同一 session_id へ並行発行された別キー書き込みが消失しないこと。

        agent-toolkit/skills/agent-standards/references/claude-hooks.md の
        並行書き込み回帰テスト規定に整合。
        """
        # update_state 経由の書き込みは tempfile.gettempdir() を参照する。
        # gettempdir() はモジュールレベルにキャッシュするため、setenv では上書きできない。
        # tempfile.tempdir 属性を直接差し替えてキャッシュ込みで上書きする。
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        env = _state_env(tmp_path)
        sid = "concurrent"

        def _hook_call() -> None:
            _run(
                {
                    "tool_name": "Skill",
                    "tool_input": {"skill": "agent-toolkit-edit"},
                    "session_id": sid,
                },
                env=env,
            )

        def _other_writer() -> None:
            def _set_other(state: dict) -> dict:
                state["other_flag"] = True
                return state

            update_state(sid, _set_other)

        threads = [threading.Thread(target=_hook_call), threading.Thread(target=_other_writer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        state = _read_state(tmp_path, sid)
        assert state.get("agent_toolkit_edit_skill_invoked") is True
        assert state.get("other_flag") is True


class TestSessionReviewDotfilesRecording:
    """`session-review-dotfiles` スキル呼び出しを `session_review_invoked` 辞書へ記録する。"""

    _SKILL = "session-review-dotfiles"

    def test_records_invocation(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        sid = "review-rec-1"
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": self._SKILL},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        invoked = _read_state(tmp_path, sid).get("session_review_invoked")
        assert isinstance(invoked, dict)
        assert invoked.get(self._SKILL) is True

    def test_no_rewrite_when_key_already_true(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        sid = "review-rec-already"
        path = _state_path(tmp_path, sid)
        # `session_review_extension_pending`も予め真にしておくことで、本フックの両分岐とも
        # mutatorが`None`を返し書き込みが発生しない状態にする。
        path.write_text(
            json.dumps(
                {
                    "session_review_invoked": {self._SKILL: True},
                    "session_review_extension_pending": True,
                    "other": "keep",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        mtime_before = path.stat().st_mtime_ns
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": self._SKILL},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state == {
            "session_review_invoked": {self._SKILL: True},
            "session_review_extension_pending": True,
            "other": "keep",
        }
        assert path.stat().st_mtime_ns == mtime_before

    def test_merges_with_existing_key(self, tmp_path: pathlib.Path):
        """既存の他キー（例: 配布物側スキル）と共存する。"""
        env = _state_env(tmp_path)
        sid = "review-rec-merge"
        _state_path(tmp_path, sid).write_text(
            json.dumps(
                {"session_review_invoked": {"agent-toolkit:session-review": True}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": self._SKILL},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        invoked = _read_state(tmp_path, sid).get("session_review_invoked")
        assert isinstance(invoked, dict)
        assert invoked.get(self._SKILL) is True
        assert invoked.get("agent-toolkit:session-review") is True


class TestExtensionPendingRecording:
    """`session_review_extension_pending`フラグの記録検証。"""

    def test_agent_toolkit_prefix_skill_sets_flag(self, tmp_path: pathlib.Path):
        """`agent-toolkit:`で始まるスキルを観測するとフラグが真になる。"""
        env = _state_env(tmp_path)
        sid = "ext-pending-prefix"
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:plan-mode"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("session_review_extension_pending") is True

    def test_session_review_dotfiles_sets_flag(self, tmp_path: pathlib.Path):
        """`session-review-dotfiles`スキルを観測するとフラグが真になる。"""
        env = _state_env(tmp_path)
        sid = "ext-pending-dotfiles"
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "session-review-dotfiles"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("session_review_extension_pending") is True

    def test_non_target_skill_does_not_set_flag(self, tmp_path: pathlib.Path):
        """対象外スキルを観測してもフラグは変化しない。"""
        env = _state_env(tmp_path)
        sid = "ext-pending-nontarget"
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "other-skill"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        assert "session_review_extension_pending" not in _read_state(tmp_path, sid)


class TestAutonomousExitSkillRecording:
    """`agent-toolkit:exit-session`スキル呼び出しを`autonomous_exit_invoked`へ記録する。"""

    _SKILL = "agent-toolkit:exit-session"

    def test_records_invocation_falls_through_to_extension_pending(self, tmp_path: pathlib.Path):
        """`agent-toolkit:exit-session`は`_AGENT_TOOLKIT_PREFIX`分岐へフォールスルーする。

        `autonomous_exit_invoked`に加え`session_review_extension_pending`も真になる。
        """
        env = _state_env(tmp_path)
        sid = "exit-rec-fallthrough"
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": self._SKILL},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state.get("autonomous_exit_invoked") is True
        assert state.get("session_review_extension_pending") is True

    def test_other_skill_does_not_set_flag(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        sid = "exit-other-skill"
        result = _run(
            {
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:coding-standards"},
                "session_id": sid,
            },
            env=env,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("autonomous_exit_invoked") is not True


class TestGeneralBehavior:
    """共通の振る舞い。"""

    def test_invalid_json_silent(self, tmp_path: pathlib.Path):
        env = _state_env(tmp_path)
        result = _run("not json", env=env)
        assert result.returncode == 0
