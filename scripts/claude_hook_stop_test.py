"""scripts/claude_hook_stop.py のテスト。

dotfiles 個人環境専用の Stop フックのテスト。
独立スクリプトなのでfork-server経由（フォールバック時はsubprocess）で起動しstdout (JSON) を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
import _fork_runner  # noqa: E402  # pylint: disable=wrong-import-position
from _message_format import SESSION_REVIEW_PRECHECK  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook_stop.py"

_EXTENSION_SKILL = "session-review-dotfiles"
_TARGET_SESSION_REVIEW = "agent-toolkit:session-review"


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    """セッション状態ファイルを書き込む。"""
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


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
    return _fork_runner.run_script(_SCRIPT, input=text, env=env)


def _parse_decision(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def _decision_kind(decision: dict) -> str:
    """approve / context のいずれかを返す。context は`decision: "block"`＋`reason`形式の振り返り誘導応答を指す。"""
    if "decision" not in decision:
        return "approve"
    if decision.get("decision") == "block" and isinstance(decision.get("reason"), str):
        return "context"
    raise AssertionError(f"unexpected decision payload: {decision!r}")


def _block_reason(decision: dict) -> str:
    """`decision: block`の`reason`本文を取り出す。"""
    assert decision.get("decision") == "block"
    body = decision.get("reason")
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


# `agent-toolkit/scripts/stop_advisor_test.py`と同一のtranscriptエントリ生成関数を意図的に複製する。
# claude_hook_stop_test.pyはdotfiles個人環境専用、stop_advisor_test.pyは配布物agent-toolkitプラグイン側の
# テストであり、プラグイン境界を越えた依存を持ち込まないため共通モジュール化しない。
# pylint: disable=duplicate-code
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


# pylint: enable=duplicate-code


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
        """uv run pyfltr ... の形式を検出して`decision: "block"`＋`reason`を返す。"""
        transcript = _transcript_pyfltr_then_text(tmp_path)
        result = _run(
            {"session_id": "detect-uv-pyfltr", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        body = _block_reason(decision)
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
        assert "decision" not in decision

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
        assert "decision" not in decision

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
        assert "decision" not in decision


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
        assert "decision" not in decision

    def test_stop_hook_active_after_block_approves(self, tmp_path: pathlib.Path):
        """`stop_hook_active`が真の場合、直前のblock後の再呼び出しでもapproveを返す。"""
        transcript = _transcript_agent_toolkit_skill_then_text(tmp_path)
        # 1回目: block を返す（stop_hook_active 未設定）
        result_first = _run(
            {"session_id": "active-after-block", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision_first = _parse_decision(result_first)
        assert _decision_kind(decision_first) == "context"
        # 2回目: stop_hook_active=True → approve のみ返す
        result_second = _run(
            {
                "session_id": "active-after-block",
                "transcript_path": str(transcript),
                "stop_hook_active": True,
            },
            state_dir=tmp_path,
        )
        decision_second = _parse_decision(result_second)
        assert "decision" not in decision_second


class TestStopGateDelegation:
    """`is_pending_async_work` とsession_stateの`session_review_invoked`への委譲テスト。

    同値分割: {pyfltr/agent-toolkit使用検出あり/なし} × {機械ゲート通過/不通過} ×
    {対象スキル起動済み/未起動}。スキル起動状態はsession_stateの`session_review_invoked`辞書で表現する。
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
        if pending:
            entries.append(_assistant_with_async_tool("Agent"))
        else:
            entries.append(_assistant_text_only())
        transcript = _write_transcript(tmp_path, entries)
        sid = f"matrix-{detected}-{pending}-{skill_invoked}"
        if skill_invoked:
            _write_state(tmp_path, sid, {"session_review_invoked": {_EXTENSION_SKILL: True}})
        result = _run(
            {"session_id": sid, "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert _decision_kind(decision) == expected
        if expected == "context":
            body = _block_reason(decision)
            assert _EXTENSION_SKILL in body
            assert _TARGET_SESSION_REVIEW in body


class TestRepeatContext:
    """同一transcriptで複数回Stopしても、対象スキル未起動なら毎回`decision: "block"`＋`reason`を返す。"""

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
        first_decision = _parse_decision(first)
        second_decision = _parse_decision(second)
        assert _decision_kind(first_decision) == "context"
        assert _decision_kind(second_decision) == "context"
        assert _EXTENSION_SKILL in _block_reason(first_decision)
        assert _EXTENSION_SKILL in _block_reason(second_decision)


class TestContextContents:
    """context発火時の`reason`本文に必要な要素が含まれることを確認する。"""

    def test_context_invokes_both_skills(self, tmp_path: pathlib.Path):
        transcript = _transcript_agent_toolkit_skill_then_text(tmp_path)
        result = _run(
            {"session_id": "context", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        body = _block_reason(decision)
        assert _EXTENSION_SKILL in body
        assert _TARGET_SESSION_REVIEW in body
        assert "Skill" in body
        assert "activation policy" in body
        assert SESSION_REVIEW_PRECHECK in body


class TestSessionReviewDotfilesCommandInvocation:
    """スラッシュコマンド起動痕跡（`/session-review-dotfiles`）による代替検出。"""

    def test_command_invocation_in_transcript_approves(self, tmp_path: pathlib.Path):
        """使用検出ありでもtranscript内にコマンド起動痕跡があればapprove。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run foo.py"),
                _user_entry("<command-name>/session-review-dotfiles</command-name>"),
                _assistant_text_only(),
            ],
        )
        result = _run(
            {"session_id": "dotfiles-command-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_no_command_invocation_returns_context(self, tmp_path: pathlib.Path):
        """コマンド起動痕跡が無い場合は通常通りcontextを返す。"""
        transcript = _transcript_pyfltr_then_text(tmp_path)
        result = _run(
            {"session_id": "dotfiles-command-not-invoked", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert _decision_kind(decision) == "context"


class TestAppendStopLog:
    """`append_stop_log`が最終判定分岐ごとに呼び出されることの検証（ログファイル1行確認）。"""

    def _read_log_lines(self, tmp_path: pathlib.Path, session_id: str) -> list[str]:
        path = tmp_path / f"claude-agent-toolkit-stop-{session_id}.log"
        return path.read_text(encoding="utf-8").splitlines()

    def test_stop_hook_active_logs_decision(self, tmp_path: pathlib.Path):
        transcript = _transcript_pyfltr_then_text(tmp_path)
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

    def test_no_usage_logs_decision(self, tmp_path: pathlib.Path):
        transcript = _write_transcript(
            tmp_path,
            [_user_entry(), _assistant_entry_with_bash("echo hello"), _user_entry("確認"), _assistant_text_only()],
        )
        _run(
            {"session_id": "log-no-usage", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        lines = self._read_log_lines(tmp_path, "log-no-usage")
        assert len(lines) == 1
        assert "decision=approve_no_pyfltr" in lines[0]

    def test_context_logs_decision(self, tmp_path: pathlib.Path):
        transcript = _transcript_pyfltr_then_text(tmp_path)
        _run(
            {"session_id": "log-context", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        lines = self._read_log_lines(tmp_path, "log-context")
        # is_pending_async_work自身の"is_pending_async_work_result"行と、
        # 最終判定"block_session_review"行の2行が記録される。
        assert len(lines) == 2
        assert "decision=is_pending_async_work_result" in lines[0]
        assert "decision=block_session_review" in lines[1]


class TestEdgeCases:
    """エッジケース。"""

    def test_invalid_json_approves(self, tmp_path: pathlib.Path):
        result = _run("not json", state_dir=tmp_path)
        assert result.returncode == 0
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_missing_transcript_approves(self, tmp_path: pathlib.Path):
        result = _run(
            {"session_id": "no-transcript", "transcript_path": "/nonexistent/file"},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_empty_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"session_id": "", "transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert "decision" not in decision

    def test_missing_session_id_approves(self, tmp_path: pathlib.Path):
        result = _run({"transcript_path": "/x"}, state_dir=tmp_path)
        decision = _parse_decision(result)
        assert "decision" not in decision


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
