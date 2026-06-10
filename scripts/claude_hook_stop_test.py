"""scripts/claude_hook_stop.py のテスト。

dotfiles 個人環境専用の Stop フックのテスト。
独立スクリプトなので subprocess で起動し stdout (JSON) を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook_stop.py"

_EXTENSION_SKILL = "session-review-dotfiles"
_TARGET_SESSION_REVIEW = "agent-toolkit:session-review"


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path | None = None,
    home: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
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


def _decision_kind(decision: dict) -> str:
    """approve / context のいずれかを返す。"""
    if decision.get("decision") == "approve":
        return "approve"
    hook_output = decision.get("hookSpecificOutput")
    if (
        isinstance(hook_output, dict)
        and hook_output.get("hookEventName") == "Stop"
        and isinstance(hook_output.get("additionalContext"), str)
    ):
        return "context"
    raise AssertionError(f"unexpected decision payload: {decision!r}")


def _additional_context(decision: dict) -> str:
    hook_output = decision.get("hookSpecificOutput")
    assert isinstance(hook_output, dict)
    assert hook_output.get("hookEventName") == "Stop"
    body = hook_output.get("additionalContext")
    assert isinstance(body, str)
    return body


def _write_transcript(tmp_path: pathlib.Path, lines: list[dict]) -> pathlib.Path:
    """dict のリストを JSONL 形式の transcript として保存する。"""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return transcript


def _assistant_entry_with_bash(command: str, *, run_in_background: bool = False) -> dict:
    """Bash tool_use を含む assistant エントリを生成する。"""
    tool_input: dict = {"command": command}
    if run_in_background:
        tool_input["run_in_background"] = True
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Bashを実行します。"},
                {"type": "tool_use", "id": "x", "name": "Bash", "input": tool_input},
            ],
            "stop_reason": "end_turn",
        },
    }


def _assistant_text_only(text: str = "作業を継続します。") -> dict:
    """テキストのみのend_turnアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        },
    }


def _assistant_with_skill(skill: str) -> dict:
    """Skill tool_use を含む assistant エントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "スキルを起動します。"},
                {"type": "tool_use", "id": "x", "name": "Skill", "input": {"skill": skill}},
            ],
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
                {"type": "text", "text": "サブエージェントを起動します。"},
                {"type": "tool_use", "id": "x", "name": tool_name, "input": {}},
            ],
            "stop_reason": "end_turn",
        },
    }


def _user_entry(text: str = "hello") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _transcript_pyfltr_then_text(tmp_path: pathlib.Path) -> pathlib.Path:
    """過去ターンでpyfltrを実行し、最終ターンはテキストのみで終了するtranscript。"""
    return _write_transcript(
        tmp_path,
        [
            _user_entry(),
            _assistant_entry_with_bash("uv run pyfltr run foo.py"),
            _user_entry("結果を確認しました"),
            _assistant_text_only(),
        ],
    )


def _transcript_agent_toolkit_skill_then_text(tmp_path: pathlib.Path) -> pathlib.Path:
    """過去ターンでagent-toolkit:coding-standardsを起動し、最終ターンはテキストのみのtranscript。"""
    return _write_transcript(
        tmp_path,
        [
            _user_entry(),
            _assistant_with_skill("agent-toolkit:coding-standards"),
            _user_entry("確認しました"),
            _assistant_text_only(),
        ],
    )


class TestUsageDetection:
    """pyfltr / agent-toolkit 使用検出ロジックのテスト（context発火条件として）。"""

    def test_detects_uv_run_pyfltr(self, tmp_path: pathlib.Path):
        """uv run pyfltr ... の形式を検出して additionalContext を返す。"""
        transcript = _transcript_pyfltr_then_text(tmp_path)
        result = _run(
            {"session_id": "detect-uv-pyfltr", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        body = _additional_context(decision)
        assert "[auto-generated: dotfiles/claude_hook_stop]" in body
        assert "Auto-generated hook notice" in body
        assert _EXTENSION_SKILL in body
        assert _TARGET_SESSION_REVIEW in body

    def test_no_pyfltr_or_agent_toolkit_approves(self, tmp_path: pathlib.Path):
        """pyfltr も agent-toolkit も検出されないセッションは approve する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("echo hello"),
                _user_entry("確認"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "no-detection", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_pyfltr_substring_not_matched(self, tmp_path: pathlib.Path):
        """pyfltr をトークンとして含まない文字列（例: mypyfltr）は検出しない。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run mypyfltr something"),
                _user_entry("確認"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "no-pyfltr-partial", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_sidechain_not_detected(self, tmp_path: pathlib.Path):
        """subagent (isSidechain=true) の Bash 呼び出しは検出対象外。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                {
                    "type": "assistant",
                    "isSidechain": True,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "pyfltr run foo.py"}},
                        ],
                    },
                },
                _user_entry("確認"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "sidechain-pyfltr", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestStopHookActive:
    """`stop_hook_active`が真の場合は再帰呼び出し抑止のため即座にapprove。"""

    def test_stop_hook_active_approves(self, tmp_path: pathlib.Path):
        transcript = _transcript_agent_toolkit_skill_then_text(tmp_path)
        result = _run(
            {
                "session_id": "stop-hook-active",
                "transcript_path": str(transcript),
                "stop_hook_active": True,
            },
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestStopGateDelegation:
    """`is_pending_async_work` と `has_session_review_skill_invoked` への委譲テスト。

    同値分割: {pyfltr/agent-toolkit使用検出あり/なし} × {機械ゲート通過/不通過} ×
    {対象スキル起動済み/未起動}。
    """

    @pytest.mark.parametrize(
        ("detected", "pending", "skill_invoked", "expected"),
        [
            # 使用検出なし → approve（その他の条件は無関係）
            (False, False, False, "approve"),
            (False, True, False, "approve"),
            (False, False, True, "approve"),
            # 使用検出あり・機械ゲート通過（pending=True）→ approve
            (True, True, False, "approve"),
            (True, True, True, "approve"),
            # 使用検出あり・機械ゲート不通過・対象スキル起動済み → approve
            (True, False, True, "approve"),
            # 使用検出あり・機械ゲート不通過・対象スキル未起動 → context
            (True, False, False, "context"),
        ],
    )
    def test_decision_matrix(
        self,
        tmp_path: pathlib.Path,
        detected: bool,
        pending: bool,
        skill_invoked: bool,
        expected: str,
    ):
        entries: list[dict[str, Any]] = [_user_entry()]
        if detected:
            entries.append(_assistant_entry_with_bash("uv run pyfltr run foo.py"))
            entries.append(_user_entry("結果を確認"))
        if skill_invoked:
            entries.append(_assistant_with_skill(_EXTENSION_SKILL))
            entries.append(_user_entry("続き"))
        if pending:
            entries.append(_assistant_with_async_tool("Agent"))
        else:
            entries.append(_assistant_text_only())
        transcript = _write_transcript(tmp_path, entries)
        sid = f"matrix-{detected}-{pending}-{skill_invoked}"
        result = _run(
            {"session_id": sid, "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert _decision_kind(decision) == expected


class TestRepeatContext:
    """同一transcriptで複数回Stopしても、対象スキル未起動なら毎回additionalContextを返す。"""

    def test_context_repeats_each_stop(self, tmp_path: pathlib.Path):
        transcript = _transcript_pyfltr_then_text(tmp_path)
        first = _run(
            {"session_id": "repeat", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        second = _run(
            {"session_id": "repeat", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert _decision_kind(_parse_decision(first)) == "context"
        assert _decision_kind(_parse_decision(second)) == "context"


class TestContextContents:
    """context発火時のadditionalContext本文に必要な要素が含まれることを確認する。"""

    def test_context_invokes_both_skills(self, tmp_path: pathlib.Path):
        transcript = _transcript_agent_toolkit_skill_then_text(tmp_path)
        result = _run(
            {"session_id": "context", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        body = _additional_context(decision)
        assert _EXTENSION_SKILL in body
        assert _TARGET_SESSION_REVIEW in body
        assert "Skill" in body
        assert "activation policy" in body
        assert "Only if all three conditions hold" in body


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_approves(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_missing_transcript_approves(self, tmp_path: pathlib.Path):
        result = _run(
            {"session_id": "no-transcript", "transcript_path": "/nonexistent/file"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_empty_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_missing_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestHomeIndependent:
    """import パス解決が `$HOME` に依存しないことの回帰テスト。

    CI では repo チェックアウト先と `$HOME` が異なるため、`Path.home()` 起点で
    import パスを組み立てると `_stop_gate` モジュールが見つからず、スクリプトが
    モジュール評価時に ImportError で終了して stdout 空になる事故が起きた。
    起動時の `$HOME` が repo 外を指していてもスクリプトが正常に JSON 決定を返すことを固定化する。
    """

    def test_runs_with_home_outside_repo(self, tmp_path: pathlib.Path):
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        transcript = _transcript_pyfltr_then_text(tmp_path)
        result = _run(
            {"session_id": "home-independent", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            home=fake_home,
        )
        decision = _parse_decision(result)
        assert _decision_kind(decision) == "context"
