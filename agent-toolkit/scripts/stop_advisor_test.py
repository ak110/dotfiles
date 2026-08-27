"""agent-toolkit/scripts/stop_advisor.py のテスト。

`is_pending_async_work`とsession_stateの`session_review_invoked`によるapprove条件、
未コミット変更通知とセッション振り返り誘導の組み合わせを検証する。
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

import _fork_runner
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _write_transcript

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook.py"


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
        env["HOME"] = str(state_dir)
        env["USERPROFILE"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, argv=("stop_advisor",), input=text, env=env)


def _parse_decision(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _block_reason(decision: dict) -> str:
    """`decision: block`の`reason`本文を取り出す。"""
    assert decision.get("decision") == "block"
    body = decision.get("reason")
    assert isinstance(body, str)
    return body


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> pathlib.Path:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return path


def _write_lock(state_dir: pathlib.Path, session_id: str) -> pathlib.Path:
    filename = SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path = state_dir / f"{filename}.lock"
    path.write_text("", encoding="utf-8")
    return path


def _set_stale(path: pathlib.Path) -> None:
    stamp = time.time() - 15 * 24 * 60 * 60
    os.utime(path, (stamp, stamp))


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


def _codex_message(role: str, text: str) -> dict:
    content_type = "input_text" if role == "user" else "output_text"
    return {
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"type": content_type, "text": text}]},
    }


def _codex_started_marker() -> dict:
    return {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "status": "completed",
                "aggregated_output": "[auto-generated: agent-toolkit/session-review-started]\n",
            },
        },
    }


def _background_bash_launch_entry(tool_use_id: str) -> dict:
    """背景Bash起動を記録する計画ファイル（メイン）側userエントリを生成する。"""
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
        _write_state(tmp_path, "block-then-active", {"process_feedbacks_skill_invoked": True})
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

    def test_pending_mcp_background_task_approves(self, tmp_path: pathlib.Path):
        """MCPタイムアウトで背景化したタスクも完了通知まで`approve`する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_mcp",
                                "name": "mcp__agents_server__start",
                                "input": {},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_mcp",
                                "content": [{"type": "text", "text": "moved to the background as task mcp-task-1"}],
                            }
                        ],
                    },
                },
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "mcp-bg-pending", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert "decision" not in _parse_decision(result)

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
        _write_state(tmp_path, "bash-bg-done", {"process_feedbacks_skill_invoked": True})
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

    def test_no_command_invocation_approves_without_feedback_processing(self, tmp_path: pathlib.Path):
        """対象スキルを実行していない通常セッションは自動振り返りを誘導しない。"""
        transcript = _write_transcript(tmp_path, [_user_entry("通常の作業依頼"), _assistant_text_only()])
        result = _run(
            {"session_id": "command-not-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    @pytest.mark.parametrize(
        "flag",
        [
            "process_feedbacks_skill_invoked",
            "plan_and_add_feedback_skill_invoked",
            "add_feedback_skill_invoked",
        ],
    )
    def test_feedback_processing_flags_enable_automatic_review(self, tmp_path: pathlib.Path, flag: str):
        """自動振り返り起点スキルの各フラグが立つセッションは振り返りを誘導する。"""
        session_id = f"eligible-{flag}"
        transcript = _write_transcript(tmp_path, [_user_entry("通常の作業依頼"), _assistant_text_only()])
        _write_state(tmp_path, session_id, {flag: True})
        result = _run(
            {"session_id": session_id, "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )

        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        assert _SESSION_REVIEW_SKILL in _block_reason(decision)

    def test_codex_manual_invocation_in_rollout_approves_without_state(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(
            tmp_path,
            [_codex_message("user", "$agent-toolkit:session-review"), _codex_message("assistant", "振り返り中")],
        )
        result = _run(
            {
                "session_id": "codex-manual-invoked",
                "transcript_path": str(transcript),
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert "decision" not in _parse_decision(result)

    def test_codex_started_marker_approves_after_state_expiry(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(
            tmp_path,
            [
                _codex_message("assistant", "作業完了"),
                _codex_message("user", "[auto-generated: agent-toolkit/stop_advisor] 振り返り誘導"),
                _codex_started_marker(),
            ],
        )
        result = _run(
            {
                "session_id": "codex-started-marker",
                "transcript_path": str(transcript),
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert "decision" not in _parse_decision(result)

    def test_codex_stop_does_not_collect_stale_states(self, tmp_path: pathlib.Path):
        """期限切れ状態の回収はSessionEndへ集約し、Stopでは重複実行しない。"""
        stale_state = _write_state(tmp_path, "codex-stale", {"marker": True})
        stale_lock = _write_lock(tmp_path, "codex-stale")
        for path in (stale_state, stale_lock):
            _set_stale(path)
        fresh_state = _write_state(
            tmp_path,
            "codex-fresh",
            {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
        )
        fresh_lock = _write_lock(tmp_path, "codex-fresh")
        transcript = _write_transcript(tmp_path, [_codex_message("assistant", "作業完了")])

        result = _run(
            {
                "session_id": "codex-fresh",
                "transcript_path": str(transcript),
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert "decision" not in _parse_decision(result)
        assert stale_state.exists()
        assert stale_lock.exists()
        assert fresh_state.exists()
        assert fresh_lock.exists()

    def test_codex_stop_notice_without_started_marker_blocks(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(
            tmp_path,
            [
                _codex_message("assistant", "未完了"),
                _codex_message("user", "[auto-generated: agent-toolkit/stop_advisor] 最初の誘導"),
                _codex_message("assistant", "追加作業後に完了"),
            ],
        )
        _write_state(tmp_path, "codex-stop-notice-only", {"process_feedbacks_skill_invoked": True})
        result = _run(
            {
                "session_id": "codex-stop-notice-only",
                "transcript_path": str(transcript),
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert _parse_decision(result).get("decision") == "block"


class TestManagedTempNotice:
    """管理対象一時領域の残存警告を検証する。"""

    @pytest.mark.parametrize(
        ("entries", "expected"),
        [
            ([], ""),
            ([{"path": "/tmp/managed-one", "prefix": None, "created_at": None}], "1"),
            (
                [
                    {"path": "/tmp/managed-one", "prefix": None, "created_at": None},
                    {"path": "/tmp/managed-two", "prefix": "review", "created_at": "2026-08-27T00:00:00Z"},
                ],
                "2",
            ),
        ],
        ids=["none", "one", "multiple"],
    )
    def test_notice_uses_count_without_embedding_paths(
        self,
        entries: list[dict[str, str | None]],
        expected: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        def fake_list_managed_temp(prefix: str | None = None) -> list[dict[str, str | None]]:
            assert prefix is None
            return entries

        monkeypatch.setattr(  # pylint: disable=protected-access
            stop_advisor._managed_temp,
            "list_managed_temp",
            fake_list_managed_temp,
        )

        notice = stop_advisor._managed_temp_notice()  # pylint: disable=protected-access

        if expected:
            assert f"managed temporary cleanup candidates remain: {expected}" in notice
            assert "`atk managed-temp list`" in notice
            assert "`atk managed-temp cleanup --path <path>`" in notice
            assert "do not assume they were forgotten" in notice
            assert "/tmp/managed-one" not in notice
            assert "/tmp/managed-two" not in notice
        else:
            assert notice == ""

    def test_list_failure_keeps_notice_empty(self, monkeypatch: pytest.MonkeyPatch):
        def raise_list_error(prefix: str | None = None) -> list[dict[str, str | None]]:
            del prefix
            raise RuntimeError("test failure")

        monkeypatch.setattr(  # pylint: disable=protected-access
            stop_advisor._managed_temp,
            "list_managed_temp",
            raise_list_error,
        )

        assert not stop_advisor._managed_temp_notice()  # pylint: disable=protected-access

    def test_list_failure_keeps_existing_review_guidance(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        session_id = "managed-temp-list-failure"
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, session_id, {"process_feedbacks_skill_invoked": True})

        def raise_list_error(prefix: str | None = None) -> list[dict[str, str | None]]:
            del prefix
            raise RuntimeError("test failure")

        monkeypatch.setattr(  # pylint: disable=protected-access
            stop_advisor._managed_temp,
            "list_managed_temp",
            raise_list_error,
        )
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

        assert stop_advisor.main(json.dumps({"session_id": session_id, "transcript_path": str(transcript)})) == 0

        reason = _block_reason(json.loads(capsys.readouterr().out))
        assert _SESSION_REVIEW_SKILL in reason
        assert "managed temporary cleanup candidates" not in reason
        assert "`atk managed-temp" not in reason

    @pytest.mark.parametrize(
        "host_fields",
        [{}, {"model": "gpt-5"}],
        ids=["claude", "codex"],
    )
    def test_block_notice_includes_verified_entries_for_both_hosts(
        self,
        tmp_path: pathlib.Path,
        host_fields: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        session_id = f"managed-temp-{len(host_fields)}"
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, session_id, {"process_feedbacks_skill_invoked": True})
        entries: list[dict[str, str | None]] = [
            {"path": "/tmp/managed-one", "prefix": None, "created_at": None},
            {"path": "/tmp/managed-two", "prefix": "review", "created_at": "2026-08-27T00:00:00Z"},
        ]

        def fake_list_managed_temp(prefix: str | None = None) -> list[dict[str, str | None]]:
            assert prefix is None
            return entries

        monkeypatch.setattr(  # pylint: disable=protected-access
            stop_advisor._managed_temp,
            "list_managed_temp",
            fake_list_managed_temp,
        )
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

        payload: dict[str, str] = {
            "session_id": session_id,
            "transcript_path": str(transcript),
            **host_fields,
        }
        assert stop_advisor.main(json.dumps(payload, ensure_ascii=False)) == 0

        decision = json.loads(capsys.readouterr().out)
        reason = _block_reason(decision)
        assert "managed temporary cleanup candidates remain: 2" in reason
        assert "`atk managed-temp list`" in reason
        assert "`atk managed-temp cleanup --path <path>`" in reason
        assert "/tmp/managed-one" not in reason
        assert "/tmp/managed-two" not in reason

    def test_block_notice_suppresses_partial_list_diagnostic(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        session_id = "managed-temp-partial-list"
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, session_id, {"process_feedbacks_skill_invoked": True})

        def list_with_diagnostic(prefix: str | None = None) -> list[dict[str, str | None]]:
            del prefix
            print("warning: invalid registry /tmp/example", file=sys.stderr)
            return [{"path": "/tmp/managed-one", "prefix": None, "created_at": None}]

        monkeypatch.setattr(  # pylint: disable=protected-access
            stop_advisor._managed_temp,
            "list_managed_temp",
            list_with_diagnostic,
        )
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

        assert stop_advisor.main(json.dumps({"session_id": session_id, "transcript_path": str(transcript)})) == 0

        captured = capsys.readouterr()
        decision = json.loads(captured.out)
        reason = _block_reason(decision)
        assert captured.err == ""
        assert "managed temporary cleanup candidates remain: 1" in reason
        assert "/tmp/example" not in reason

    def test_existing_allow_branches_skip_managed_temp_lookup(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """候補が残っていても既存のapprove分岐では列挙処理へ到達しない。"""
        call_count = 0

        def list_with_candidate(prefix: str | None = None) -> list[dict[str, str | None]]:
            nonlocal call_count
            assert prefix is None
            call_count += 1
            return [{"path": "/tmp/managed-one", "prefix": None, "created_at": None}]

        monkeypatch.setattr(  # pylint: disable=protected-access
            stop_advisor._managed_temp,
            "list_managed_temp",
            list_with_candidate,
        )
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

        cases = [
            (
                "managed-temp-stop-hook-active",
                [_user_entry(), _assistant_text_only()],
                {"stop_hook_active": True},
                None,
            ),
            (
                "managed-temp-pending-async",
                [_user_entry(), _assistant_with_async_tool("Agent")],
                {},
                None,
            ),
            (
                "managed-temp-review-invoked",
                [_user_entry(), _assistant_text_only()],
                {},
                {"session_review_invoked": {_SESSION_REVIEW_SKILL: True}},
            ),
            (
                "managed-temp-not-eligible",
                [_user_entry(), _assistant_text_only()],
                {},
                None,
            ),
        ]
        for session_id, entries, fields, state in cases:
            transcript = _write_transcript(tmp_path, entries)
            if state is not None:
                _write_state(tmp_path, session_id, state)

            payload: dict[str, object] = {
                "session_id": session_id,
                "transcript_path": str(transcript),
                **fields,
            }
            assert stop_advisor.main(json.dumps(payload, ensure_ascii=False)) == 0

            captured = capsys.readouterr()
            decision = json.loads(captured.out)
            assert "decision" not in decision
            assert "managed temporary cleanup candidates" not in captured.out
            assert captured.err == ""
            assert call_count == 0


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
        _write_state(tmp_path, "log-block", {"process_feedbacks_skill_invoked": True})
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
    """block条件: 機械ゲート通過かつ自動振り返り起点スキル実行済み。"""

    def test_clean_repo_context_review_only(
        self, tmp_path: pathlib.Path, make_clean_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更なし → 振り返り誘導のみの`reason`フィールド。"""
        repo = make_clean_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, "clean-context", {"process_feedbacks_skill_invoked": True})
        result = _run(
            {"session_id": "clean-context", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body
        assert "activation policy" in body
        assert "uncommitted" not in body.lower()
        assert "user-question mechanism" in body
        assert "end the turn silently" in body
        assert "Fix: " in body
        assert "session_id=clean-context" in body
        assert str(transcript) in body

    def test_dirty_repo_context_with_both_messages(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """未コミット変更あり → `reason`に振り返り誘導、`systemMessage`にgit status件数サマリーを返す。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, "dirty-context", {"process_feedbacks_skill_invoked": True})
        result = _run(
            {"session_id": "dirty-context", "transcript_path": str(transcript), "cwd": str(repo)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision.get("decision") == "block"
        body = _block_reason(decision)
        assert _SESSION_REVIEW_SKILL in body
        assert "user-question mechanism" in body
        assert "end the turn silently" in body
        assert str(transcript) in body
        assert "systemMessage" in decision
        assert "changed file(s)" in decision["systemMessage"]

    def test_repeats_context_each_stop(self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]):
        """同一transcriptで2回連続Stopしても、スキル未起動なら毎回`decision: block`＋`reason`を返す。"""
        repo = make_dirty_repo(tmp_path)
        transcript = _write_transcript(tmp_path, [_user_entry(), _assistant_text_only()])
        _write_state(tmp_path, "repeat", {"process_feedbacks_skill_invoked": True})
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

    def test_codex_payload_does_not_parse_claude_background_fixture(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_with_async_tool("Agent")],
        )
        _write_state(tmp_path, "codex-ignore-claude-background", {"process_feedbacks_skill_invoked": True})
        result = _run(
            {
                "session_id": "codex-ignore-claude-background",
                "transcript_path": str(transcript),
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert "decision" not in _parse_decision(result)


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
        _write_state(tmp_path, "no-transcript", {"process_feedbacks_skill_invoked": True})
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


class TestGitLogCheckedNotResetOnStop:
    """Stopは`git_log_checked`をリセットしない（対象コミットの親子関係を変えないため）。

    リセット対象は`posttooluse.py` `_GIT_LOG_RESET_SUBCOMMANDS`が定める
    commit / rebase / resetのみに限定する。
    """

    @pytest.mark.parametrize(
        "initial",
        [
            # cwd別辞書はそのまま維持される
            {"/repo/a": True, "/repo/b": True},
            # 旧形式bool Trueもそのまま維持される
            True,
        ],
    )
    def test_no_reset_on_stop(self, tmp_path: pathlib.Path, initial: object):
        _write_state(tmp_path, "log-no-reset", {"git_log_checked": initial})
        transcript = _write_transcript(tmp_path, [_user_entry()])
        _run(
            {"session_id": "log-no-reset", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="log-no-reset")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("git_log_checked") == initial

    def test_no_change_when_empty(self, tmp_path: pathlib.Path):
        """空dictのときはStopで他のフィールドを書き換えない。"""
        _write_state(tmp_path, "log-empty", {"git_log_checked": {}, "marker": 1})
        transcript = _write_transcript(tmp_path, [_user_entry()])
        _run(
            {"session_id": "log-empty", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="log-empty")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state.get("git_log_checked") == {}
        assert state.get("marker") == 1


class TestGitStatusDisplay:
    """approve時のgit status表示。"""

    def test_dirty_repo_shows_git_status_on_approve(
        self, tmp_path: pathlib.Path, make_dirty_repo: Callable[[pathlib.Path], pathlib.Path]
    ):
        """approve時かつ未コミット変更ありの場合、systemMessageでgit status件数サマリーを表示する。"""
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
        assert "changed file(s)" in decision["systemMessage"]

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
