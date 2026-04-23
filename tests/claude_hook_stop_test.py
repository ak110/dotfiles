"""scripts/claude_hook_stop.py のテスト。

dotfiles 個人環境専用の Stop フックのテスト。
独立スクリプトなので subprocess で起動し stdout (JSON) を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook_stop.py"


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


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / f"claude-dotfiles-stop-{session_id}.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _write_transcript(tmp_path: pathlib.Path, lines: list[dict]) -> pathlib.Path:
    """dict のリストを JSONL 形式の transcript として書き出す。"""
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
                {"type": "text", "text": "作業が完了しました。"},
                {"type": "tool_use", "id": "x", "name": "Bash", "input": tool_input},
            ],
        },
    }


def _assistant_entry_completion_only() -> dict:
    """完了文言のみの assistant エントリを生成する（ツールなし）。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "作業が完了しました。"}],
        },
    }


def _user_entry(text: str = "hello") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


class TestPyfltrDetection:
    """pyfltr 使用検出ロジックのテスト。"""

    def test_detects_uv_run_pyfltr(self, tmp_path: pathlib.Path):
        """uv run pyfltr ... の形式を検出して block する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run-for-agent some_file.py"),
                _assistant_entry_completion_only(),
            ],
        )
        result = _run(
            {"session_id": "detect-uv-pyfltr", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
        assert "pyfltr" in decision.get("reason", "")
        # LLM 宛てメッセージ規約の検証
        assert "[auto-generated: dotfiles/claude_hook_stop]" in decision["reason"]
        assert "Auto-generated hook notice" in decision["reason"]

    def test_detects_plain_pyfltr(self, tmp_path: pathlib.Path):
        """単純な pyfltr コマンドを検出して block する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("pyfltr run foo.py"),
                _assistant_entry_completion_only(),
            ],
        )
        result = _run(
            {"session_id": "detect-plain-pyfltr", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"

    def test_no_pyfltr_approves(self, tmp_path: pathlib.Path):
        """pyfltr を使用していないセッションは approve する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("echo hello"),
                _assistant_entry_completion_only(),
            ],
        )
        result = _run(
            {"session_id": "no-pyfltr", "transcript_path": str(transcript)},
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
                _assistant_entry_completion_only(),
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
                _assistant_entry_completion_only(),
            ],
        )
        result = _run(
            {"session_id": "sidechain-pyfltr", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"


class TestStateOnceLimit:
    """1 セッション 1 回の制限テスト。"""

    def test_second_stop_approves(self, tmp_path: pathlib.Path):
        """pyfltr_advice_given = true の状態では即 approve する。"""
        _write_state(tmp_path, "once", {"pyfltr_advice_given": True})
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run foo.py"),
                _assistant_entry_completion_only(),
            ],
        )
        result = _run(
            {"session_id": "once", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_first_stop_blocks_second_approves(self, tmp_path: pathlib.Path):
        """1 回目は block し、2 回目は approve する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run foo.py"),
                _assistant_entry_completion_only(),
            ],
        )
        first = _run(
            {"session_id": "two-stops", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        second = _run(
            {"session_id": "two-stops", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        assert _parse_decision(first)["decision"] == "block"
        assert _parse_decision(second)["decision"] == "approve"


class TestStopGateDelegation:
    """_stop_gate.is_real_session_end への委譲テスト。"""

    def test_waiting_keyword_approves(self, tmp_path: pathlib.Path):
        """待機語を含むアシスタントターンは pyfltr 使用があっても approve する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run foo.py"),
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "作業が完了しました。バックグラウンドで処理中です。完了を待ちます。"}
                        ],
                    },
                },
            ],
        )
        result = _run(
            {"session_id": "stop-gate-waiting", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_no_completion_keyword_approves(self, tmp_path: pathlib.Path):
        """完了文言がないアシスタントターンは approve する。

        pyfltr 使用は transcript 内の過去のターンで検出される。
        直前アシスタントターンは完了文言なしのため is_real_session_end が False を返す。
        """
        # 完了文言を含まないアシスタントターン（pyfltr 呼び出しも完了文言なし）
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_pyfltr_call",
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "pyfltr を実行します。"},
                            {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "uv run pyfltr run foo.py"}},
                        ],
                    },
                },
                _user_entry("結果を確認中"),
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_investigating",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "調査を続けます。"}],
                    },
                },
            ],
        )
        result = _run(
            {"session_id": "stop-gate-no-completion", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_async_agent_tool_approves(self, tmp_path: pathlib.Path):
        """最後の tool_use が Agent のターンは pyfltr 使用があっても approve する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run foo.py"),
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "作業が完了しました。"},
                            {"type": "tool_use", "id": "x", "name": "Agent", "input": {}},
                        ],
                    },
                },
            ],
        )
        result = _run(
            {"session_id": "stop-gate-agent", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_bash_background_approves(self, tmp_path: pathlib.Path):
        """最後の tool_use が Bash+run_in_background=True のターンは approve する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run foo.py"),
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "作業が完了しました。"},
                            {
                                "type": "tool_use",
                                "id": "x",
                                "name": "Bash",
                                "input": {"command": "long_task.sh", "run_in_background": True},
                            },
                        ],
                    },
                },
            ],
        )
        result = _run(
            {"session_id": "stop-gate-bg-bash", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "approve"

    def test_completion_without_waiting_blocks(self, tmp_path: pathlib.Path):
        """完了文言あり・待機語なし・非同期ツールなし・pyfltr 使用ありは block する。"""
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run foo.py"),
                _assistant_entry_completion_only(),
            ],
        )
        result = _run(
            {"session_id": "stop-gate-pass", "transcript_path": str(transcript)},
            state_dir=tmp_path,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"


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
        transcript = _write_transcript(
            tmp_path,
            [
                _user_entry(),
                _assistant_entry_with_bash("uv run pyfltr run-for-agent some_file.py"),
                _assistant_entry_completion_only(),
            ],
        )
        result = _run(
            {"session_id": "home-independent", "transcript_path": str(transcript)},
            state_dir=tmp_path,
            home=fake_home,
        )
        decision = _parse_decision(result)
        assert decision["decision"] == "block"
