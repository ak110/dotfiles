"""agent-toolkit/scripts/stop_advisor.py のテスト。

`is_pending_async_work`と`has_session_review_skill_invoked`によるapprove条件、
未コミット変更通知とセッション振り返り誘導の組み合わせを検証する。
"""

import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "stop_advisor.py"


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _parse_decision(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _additional_context(decision: dict) -> str:
    """`hookSpecificOutput.additionalContext`本文を取り出す。"""
    hook_output = decision.get("hookSpecificOutput")
    assert isinstance(hook_output, dict)
    assert hook_output.get("hookEventName") == "Stop"
    body = hook_output.get("additionalContext")
    assert isinstance(body, str)
    return body


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _user_entry(text: str = "hello") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_text_only(text: str = "作業を継続します。") -> dict:
    """end_turnで停止したテキストのみのアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    }


def _assistant_with_async_tool(tool_name: str) -> dict:
    """非同期待機系tool_useで終わるアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "作業を継続します。"},
                {"type": "tool_use", "id": "x", "name": tool_name, "input": {}},
            ],
            "stop_reason": "end_turn",
        },
    }


def _assistant_with_skill(skill: str) -> dict:
    """Skill tool_useを含むアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "セッション振り返りを実施します。"},
                {"type": "tool_use", "id": "x", "name": "Skill", "input": {"skill": skill}},
            ],
            "stop_reason": "end_turn",
        },
    }


def _write_transcript(tmp_path: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    """JSONLとしてエントリを書き込む。"""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    return transcript


def _background_bash_launch_entry(tool_use_id: str) -> dict:
    """背景Bash起動を記録するメイン側userエントリを生成する。"""
    return {
        "type": "user",
        "isSidechain": False,
        "toolUseResult": {
            "stdout": "",
            "stderr": "",
            "backgroundTaskId": "bash-task-x",
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Background command launched"}],
                }
            ],
        },
    }


_SESSION_REVIEW_SKILL = "agent-toolkit:session-review"


class TestApproveConditions:
    """approve条件: 構造的継続中 or 振り返りスキル起動済み。"""

    def test_stop_hook_active_approves(self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]):
        """`stop_hook_active`が真 → 構造判定・通知生成より前に即approve（再帰呼び出し抑止）。

        dirty repoを入力に与えても`systemMessage`（git status）と`hookSpecificOutput`を
        いずれも出力しないことを検証する。
        """
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {
                "session_id": "stop-hook-active",
                "transcript_path": str(transcript),
                "cwd": str(repo),
                "stop_hook_active": True,
            },
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "hookSpecificOutput" not in decision
        assert "systemMessage" not in decision

    @pytest.mark.parametrize("tool_name", ["Agent", "ScheduleWakeup", "Monitor"])
    def test_async_tool_approves(self, tmp_path: pathlib.Path, tool_name: str):
        """直前ターンの最後のtool_useが非同期待機系 → approve。"""
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_with_async_tool(tool_name)],
        )
        result = _run(
            {"session_id": f"async-{tool_name}", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_bash_background_approves(self, tmp_path: pathlib.Path):
        """直前ターンの最後のtool_useがBash+run_in_background=True → approve。"""
        bash_bg: dict[str, Any] = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "ジョブを起動しました。"},
                    {
                        "type": "tool_use",
                        "id": "x",
                        "name": "Bash",
                        "input": {"command": "long.sh", "run_in_background": True},
                    },
                ],
                "stop_reason": "end_turn",
            },
        }
        transcript = _write_transcript(tmp_path, [_user_entry(), bash_bg])
        result = _run(
            {"session_id": "bash-bg", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_pending_background_bash_approves(self, tmp_path: pathlib.Path):
        """過去ターンで背景Bashを起動済み・完了通知未到着 → approveのみ。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_text_only("バックグラウンドジョブを起動しました。"),
                _background_bash_launch_entry("toolu_bash_pending"),
                _user_entry("続き"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "bash-bg-pending", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_completed_background_bash_reaches_context(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """背景Bash完了通知到着済み → 通常の振り返り誘導パスへ進む。"""
        repo = make_clean_repo(tmp_path)
        bash_notify: dict[str, Any] = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "<task-notification>"
                            "<task-id>bash-task-x</task-id>"
                            "<tool-use-id>toolu_bash_done</tool-use-id>"
                            "<status>completed</status>"
                            "<summary>Background command completed</summary>"
                            "</task-notification>"
                        ),
                    }
                ],
            },
        }
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_text_only("バックグラウンドジョブを起動しました。"),
                _background_bash_launch_entry("toolu_bash_done"),
                bash_notify,
                _user_entry("続き"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "bash-bg-done", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
        body = _additional_context(decision)
        assert _SESSION_REVIEW_SKILL in body

    def test_session_review_skill_invoked_approves(self, tmp_path: pathlib.Path):
        """過去のアシスタントターンで振り返りスキルが起動済み → approve。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_with_skill(_SESSION_REVIEW_SKILL),
                _user_entry("続けて"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "review-already-done", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestContextConditions:
    """context条件: 機械ゲート通過かつスキル未起動 → 毎回additionalContextを返す。"""

    def test_clean_repo_context_review_only(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更なし → 振り返り誘導のみのadditionalContext。"""
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "clean-context", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
        body = _additional_context(decision)
        assert _SESSION_REVIEW_SKILL in body
        assert "Skill" in body
        assert "activation policy" in body
        assert "uncommitted" not in body.lower()
        assert "Only if all three conditions hold" in body
        # 振り返り誘導1件のみのため、自動生成プレフィックスは1個。
        assert body.count("[auto-generated: agent-toolkit/stop_advisor]") == 1
        assert "Auto-generated hook notice" in body

    def test_dirty_repo_context_with_both_messages(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更あり → 未コミット通知と振り返り誘導の両方を1回のadditionalContextで返す。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "dirty-context", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        body = _additional_context(decision)
        assert "uncommitted" in body.lower()
        assert _SESSION_REVIEW_SKILL in body
        assert "Only if all three conditions hold" in body
        # 2通知それぞれにプレフィックスが付与される。
        assert body.count("[auto-generated: agent-toolkit/stop_advisor]") == 2

    def test_repeats_context_each_stop(self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]):
        """同一transcriptで2回連続Stopしても、スキル未起動なら毎回additionalContextを返す。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        first = _run(
            {"session_id": "repeat", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        second = _run(
            {"session_id": "repeat", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        assert _additional_context(_parse_decision(first))
        assert _additional_context(_parse_decision(second))


class TestUncommittedTimesAfterReview:
    """振り返りスキル起動済みなら未コミット変更があってもapprove（スキル起動が優先）。"""

    def test_skill_invoked_dirty_repo_approves(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_with_skill(_SESSION_REVIEW_SKILL),
                _user_entry("引き続き"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "skill-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_approves(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_missing_transcript_emits_context(self, tmp_path: pathlib.Path):
        """transcriptが存在しない → 機械ゲートはFalse、スキル痕跡なし → additionalContextを返す。

        フックは安全側で動作し、振り返り誘導本文を返す。
        """
        result = _run(
            {"session_id": "no-transcript", "transcript_path": "/nonexistent/file"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert _SESSION_REVIEW_SKILL in _additional_context(decision)

    def test_empty_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestGitLogCheckedReset:
    """Stop時に`git_log_checked`を全エントリクリアする。"""

    @pytest.mark.parametrize(
        ("initial", "expected"),
        [
            # cwd別辞書 → 全エントリクリア
            ({"/repo/a": True, "/repo/b": True}, {}),
            # 旧形式bool True → {} （全エントリクリアで現行形式へ収束）
            (True, {}),
        ],
    )
    def test_reset_on_stop(self, tmp_path: pathlib.Path, initial: object, expected: object):
        _write_state(tmp_path, "log-reset", {"git_log_checked": initial})
        transcript = _write_transcript(tmp_path, [_user_entry()])
        _run(
            {"session_id": "log-reset", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        state_path = tmp_path / "claude-agent-toolkit-log-reset.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("git_log_checked") == expected

    def test_no_change_when_empty(self, tmp_path: pathlib.Path):
        """空dictのときはStopで他のフィールドを書き換えない。"""
        _write_state(tmp_path, "log-empty", {"git_log_checked": {}, "marker": 1})
        transcript = _write_transcript(tmp_path, [_user_entry()])
        _run(
            {"session_id": "log-empty", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        state_path = tmp_path / "claude-agent-toolkit-log-empty.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("git_log_checked") == {}
        assert state.get("marker") == 1


class TestGitStatusDisplay:
    """approve時のgit status表示。"""

    def test_dirty_repo_shows_git_status_on_approve(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """approve時かつ未コミット変更ありの場合、systemMessageでgit statusを表示する。"""
        repo = make_dirty_repo(tmp_path)
        # スキル起動済みでapproveパスに到達させる。
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_with_skill(_SESSION_REVIEW_SKILL),
                _user_entry("続き"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "gs-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" in decision
        assert "git status" in decision["systemMessage"]
        assert "file.txt" in decision["systemMessage"]

    def test_clean_repo_no_system_message(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """clean repoではsystemMessageを出力しない。"""
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_with_skill(_SESSION_REVIEW_SKILL),
                _user_entry("続き"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "gs-clean", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_no_cwd_no_system_message(self, tmp_path: pathlib.Path):
        """cwd未指定時はsystemMessageを出力しない。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_with_skill(_SESSION_REVIEW_SKILL),
                _user_entry("続き"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "gs-nocwd", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_async_pending_dirty_repo_no_system_message(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """非同期待機ツール残存などで構造的にセッション継続中は未コミット変更ありでもsystemMessageを抑止する。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_with_async_tool("Agent")],
        )
        result = _run(
            {"session_id": "gs-async-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision

    def test_untracked_only_no_system_message(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """untrackedファイルのみの場合はsystemMessageを出力しない。"""
        repo = make_clean_repo(tmp_path)
        (repo / "untracked.txt").write_text("new file")
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_with_skill(_SESSION_REVIEW_SKILL),
                _user_entry("続き"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "gs-untracked", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"
        assert "systemMessage" not in decision
