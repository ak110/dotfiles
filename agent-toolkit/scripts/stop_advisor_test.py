"""agent-toolkit/scripts/stop_advisor.py のテスト。

`is_pending_async_work`とsession_stateの`session_review_invoked`によるapprove条件、
未コミット変更通知とセッション振り返り誘導の組み合わせを検証する。

scope-escalation検出テストの入力フレーズは
`agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt`
から動的に読み込む（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節。
検出語そのものをテストコード本文へ転記しない）。
"""

import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest
from _scope_escalation_test_helpers import load_scope_escalation_inputs

_SCRIPT = pathlib.Path(__file__).resolve().parent / "stop_advisor.py"

_SCOPE_ESCALATION_INPUTS = load_scope_escalation_inputs()


def _pick_scope_escalation_text(category: str = "process-omission") -> str:
    """指定カテゴリの最小マッチ入力を1件返す。フィクスチャ不在時は空文字列。"""
    for text, cat in _SCOPE_ESCALATION_INPUTS:
        if cat == category:
            return text
    return ""


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


def _block_reason(decision: dict) -> str:
    """`decision: block`の`reason`本文を取り出す。"""
    assert decision.get("decision") == "block"
    body = decision.get("reason")
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
        assert "decision" not in decision
        assert "hookSpecificOutput" not in decision
        assert "systemMessage" not in decision

    def test_block_then_active_approves(self, tmp_path: pathlib.Path):
        """`stop_hook_active`が真の場合、直前のblock後の再呼び出しでもapproveを返す。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        # 1回目: block を返す（stop_hook_active 未設定）
        result_first = _run(
            {"session_id": "block-then-active", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision_first = _parse_decision(result_first)
        assert decision_first.get("decision") == "block"
        # 2回目: stop_hook_active=True → approve のみ返す
        result_second = _run(
            {
                "session_id": "block-then-active",
                "transcript_path": str(transcript),
                "stop_hook_active": True,
            },
            state_dir=tmp_path,
        )
        decision_second = _parse_decision(result_second)
        assert "decision" not in decision_second

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
        assert "decision" not in decision

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
        assert "decision" not in decision

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
        assert "decision" not in decision
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
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body

    def test_session_review_skill_invoked_approves(self, tmp_path: pathlib.Path):
        """session_stateで振り返りスキル起動済み → approve。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(
            tmp_path,
            "review-already-done",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        result = _run(
            {"session_id": "review-already-done", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision


class TestSessionReviewCommandInvocation:
    """スラッシュコマンド起動痕跡（`/agent-toolkit:session-review`）による代替検出。"""

    def test_command_invocation_in_transcript_approves(self, tmp_path: pathlib.Path):
        """transcript内にコマンド起動痕跡があるとapprove（session_state未記録でも成立）。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry("<command-name>/agent-toolkit:session-review</command-name>"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "command-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_no_command_invocation_blocks(self, tmp_path: pathlib.Path):
        """コマンド起動痕跡が無い場合は通常通りblockされる。"""
        transcript = _write_transcript(tmp_path, [_user_entry("通常の作業依頼"), _assistant_text_only()])
        result = _run(
            {"session_id": "command-not-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"


class TestAppendStopLog:
    """`append_stop_log`が最終判定分岐ごとに呼び出されることの検証（ログファイル1行確認）。"""

    def _read_log_lines(self, tmp_path: pathlib.Path, session_id: str) -> list[str]:
        path = tmp_path / f"claude-agent-toolkit-stop-{session_id}.log"
        return path.read_text(encoding="utf-8").splitlines()

    def test_stop_hook_active_logs_decision(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _run(
            {
                "session_id": "log-stop-hook-active",
                "transcript_path": str(transcript),
                "stop_hook_active": True,
            },
            state_dir=tmp_path,
        )
        lines = self._read_log_lines(tmp_path, "log-stop-hook-active")
        assert len(lines) == 1
        assert "decision=approve_stop_hook_active" in lines[0]

    def test_block_logs_decision(self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]):
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _run(
            {"session_id": "log-block", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        lines = self._read_log_lines(tmp_path, "log-block")
        # is_pending_async_work自身の"is_pending_async_work_result"行と、
        # 最終判定"block_session_review"行の2行が記録される。
        assert len(lines) == 2
        assert "decision=is_pending_async_work_result" in lines[0]
        assert "decision=block_session_review" in lines[1]

    def test_review_invoked_logs_decision(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(
            tmp_path,
            "log-review-invoked",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        _run(
            {"session_id": "log-review-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        lines = self._read_log_lines(tmp_path, "log-review-invoked")
        assert len(lines) == 2
        assert "decision=is_pending_async_work_result" in lines[0]
        assert "decision=approve_review_invoked" in lines[1]


class TestContextConditions:
    """block条件: 機械ゲート通過かつスキル未起動 → 毎回`decision: block`＋`reason`を返す。"""

    def test_clean_repo_context_review_only(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更なし → 振り返り誘導のみの`reason`フィールド。"""
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "clean-context", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body
        assert "Skill" in body
        assert "activation policy" in body
        assert "uncommitted" not in body.lower()
        assert "end the turn silently" in body

    def test_dirty_repo_context_with_both_messages(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更あり → `reason`に振り返り誘導、`systemMessage`にgit statusを返す。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        result = _run(
            {"session_id": "dirty-context", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body
        assert "end the turn silently" in body
        assert "systemMessage" in decision
        assert "file.txt" in decision["systemMessage"]

    def test_repeats_context_each_stop(self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]):
        """同一transcriptで2回連続Stopしても、スキル未起動なら毎回`decision: block`＋`reason`を返す。"""
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
        assert _block_reason(_parse_decision(first))
        assert _block_reason(_parse_decision(second))


class TestUncommittedChangesAfterReview:
    """振り返りスキル起動済みなら未コミット変更があってもapprove（スキル起動が優先）。"""

    def test_skill_invoked_dirty_repo_approves(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(
            tmp_path,
            "skill-dirty",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        result = _run(
            {"session_id": "skill-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision


class TestExtensionPending:
    """`session_review_extension_pending`フラグが真のとき振り返り誘導を抑制する。"""

    def test_extension_pending_dirty_repo_emits_git_status_only(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """フラグ真かつ未コミット変更あり → approveと`systemMessage`にgit statusのみ（振り返り誘導なし）。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, "ext-dirty", {"session_review_extension_pending": True})
        result = _run(
            {"session_id": "ext-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
        assert "reason" not in decision
        assert "systemMessage" in decision
        assert "git status" in decision["systemMessage"]
        assert _SESSION_REVIEW_SKILL not in decision.get("systemMessage", "")

    def test_extension_pending_clean_repo_approves(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """フラグ真かつ未コミット変更なし → approveのみ（`reason`なし）。"""
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, "ext-clean", {"session_review_extension_pending": True})
        result = _run(
            {"session_id": "ext-clean", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
        assert "hookSpecificOutput" not in decision


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_approves(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_missing_transcript_emits_context(self, tmp_path: pathlib.Path):
        """transcriptが存在しない → 機械ゲートはFalse、スキル痕跡なし → `decision: block`＋`reason`を返す。

        フックは安全側で動作し、振り返り誘導本文を返す。
        """
        result = _run(
            {"session_id": "no-transcript", "transcript_path": "/nonexistent/file"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        assert _SESSION_REVIEW_SKILL in _block_reason(decision)

    def test_empty_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert "decision" not in decision


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
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(
            tmp_path,
            "gs-dirty",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        result = _run(
            {"session_id": "gs-dirty", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
        assert "systemMessage" in decision
        assert "git status" in decision["systemMessage"]
        assert "file.txt" in decision["systemMessage"]

    def test_clean_repo_no_system_message(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """clean repoではsystemMessageを出力しない。"""
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(
            tmp_path,
            "gs-clean",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        result = _run(
            {"session_id": "gs-clean", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
        assert "systemMessage" not in decision

    def test_no_cwd_no_system_message(self, tmp_path: pathlib.Path):
        """cwd未指定時はsystemMessageを出力しない。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(
            tmp_path,
            "gs-nocwd",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        result = _run(
            {"session_id": "gs-nocwd", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
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
        assert "decision" not in decision
        assert "systemMessage" not in decision

    def test_untracked_only_no_system_message(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """untrackedファイルのみの場合はsystemMessageを出力しない。"""
        repo = make_clean_repo(tmp_path)
        (repo / "untracked.txt").write_text("new file")
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(
            tmp_path,
            "gs-untracked",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        result = _run(
            {"session_id": "gs-untracked", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision
        assert "systemMessage" not in decision


class TestScopeEscalationDetection:
    """直近アシスタントターンの応答テキストへのscope-escalation検出。

    - 検出時は`decision: "block"`＋矯正指示`reason`を返す
    - 非該当時は通常フロー（振り返り誘導block）へ進む
    - `stop_hook_active`が真の場合は即approve（検出処理をバイパス）
    - transcript読み取り失敗はfail-open（本checkは通過し通常フローへ進む）
    """

    def test_detects_scope_escalation_and_blocks(self, tmp_path: pathlib.Path):
        """縮退表明フレーズを含む応答テキスト → blockで矯正指示を返す。"""
        phrase = _pick_scope_escalation_text()
        if not phrase:
            pytest.skip("scope-escalation fixture not available")
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_text_only(phrase)],
        )
        result = _run(
            {"session_id": "escalation-detected", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        # カテゴリ名またはリファレンス誘導が本文に含まれる。
        assert "scope-escalation-phrases" in body or "縮退表明" in body

    def test_non_matching_text_falls_through_to_review_block(self, tmp_path: pathlib.Path):
        """非該当テキスト → 通常の振り返り誘導blockへ進む（`session-review`が本文へ含まれる）。"""
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only("通常の進捗を報告します。")])
        result = _run(
            {"session_id": "escalation-clean", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body

    def test_stop_hook_active_bypasses_escalation_check(self, tmp_path: pathlib.Path):
        """`stop_hook_active=True`ならescalation検出以前にapproveされる。"""
        phrase = _pick_scope_escalation_text()
        if not phrase:
            pytest.skip("scope-escalation fixture not available")
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_text_only(phrase)],
        )
        result = _run(
            {
                "session_id": "escalation-active",
                "transcript_path": str(transcript),
                "stop_hook_active": True,
            },
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_transcript_read_failure_is_fail_open(self, tmp_path: pathlib.Path):
        """存在しないtranscript_pathでも例外を送出せず、通常フローへ進む（振り返り誘導block）。"""
        missing = tmp_path / "no-such-transcript.jsonl"
        result = _run(
            {"session_id": "escalation-fail-open", "transcript_path": str(missing)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        # 検出処理は空イテレーターとなり、後続の通常フロー（block_session_review）へ進む。
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body

    def test_detects_scope_escalation_when_session_review_invoked(self, tmp_path: pathlib.Path):
        """振り返りスキル起動済み状態でもscope-escalationフレーズを検出しblockする。

        scope-escalation検出分岐がsession_review_invoked分岐より前に配置されているため、
        以降のセッションでもscope-escalation発話は検出時点で矯正される。
        """
        phrase = _pick_scope_escalation_text()
        if not phrase:
            pytest.skip("scope-escalation fixture not available")
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_text_only(phrase)],
        )
        _write_state(
            tmp_path,
            "escalation-after-review",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        result = _run(
            {"session_id": "escalation-after-review", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert "scope-escalation-phrases" in body or "縮退表明" in body

    @pytest.mark.parametrize(
        "category",
        [
            "priority-consult",
            "next-cycle-defer",
            "approach-confirm",
            "workload",
            "split-execution",
            "fabricated-metrics",
        ],
    )
    def test_non_focus_categories_do_not_block(self, tmp_path: pathlib.Path, category: str):
        """Stop経路の照合対象外カテゴリのフレーズは通常フロー（振り返り誘導block）へ進む。

        `_STOP_FOCUS_CATEGORIES`（`process-omission`単独）以外の
        カテゴリは自由文脈で誤検出リスクがあるためStop経路の対象外とする。
        `fabricated-metrics`もStop経路の対象外に含まれる。
        対象外カテゴリのフレーズはscope-escalation判定を通過し、
        後続の通常フロー（`block_session_review`）へ進むことを検証する。
        """
        phrase = _pick_scope_escalation_text(category)
        if not phrase:
            pytest.skip(f"scope-escalation fixture for {category} not available")
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_text_only(phrase)],
        )
        result = _run(
            {"session_id": f"non-focus-{category}", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        # 通常フローへ進み、振り返り誘導blockが返る（scope-escalation矯正blockではない）。
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body
        # scope-escalation矯正の本文は含まれない。
        assert "縮退表明" not in body

    def test_logs_block_scope_escalation(self, tmp_path: pathlib.Path):
        """検出時に`append_stop_log`へ`block_scope_escalation`が記録される。"""
        phrase = _pick_scope_escalation_text()
        if not phrase:
            pytest.skip("scope-escalation fixture not available")
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_text_only(phrase)],
        )
        _run(
            {"session_id": "escalation-log", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        log_path = tmp_path / "claude-agent-toolkit-stop-escalation-log.log"
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert any("block_scope_escalation" in line for line in lines)
