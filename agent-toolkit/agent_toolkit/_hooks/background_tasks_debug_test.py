"""background_tasks_debugの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._hooks.background_tasks import is_pending_async_work
from agent_toolkit._testing.background_tasks_support import (
    _assistant_entry,
    _bash_no_bg,
    _user_async_launched_entry,
    _user_entry,
    _user_task_notification_entry,
)
from agent_toolkit._testing.helpers import _write_transcript

_TEXT = "作業途中です。"


class TestDebugOutput:
    """`AGENT_TOOLKIT_STOP_GATE_DEBUG`環境変数によるstderrデバッグ出力の検証。

    Stop hookの誤判定時に原因を特定する手段として、判定根拠を1行出力する機能を確認する。
    同値分割: 環境変数値 × 残差有無 で代表ケースを抽出する。
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """環境変数の事前削除でテスト間の状態混入を防ぐ。"""
        monkeypatch.delenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", raising=False)

    def test_no_output_when_env_unset(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """環境変数が未設定の場合はstderr出力なし。"""
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        is_pending_async_work(str(t), "", session_state=None)
        captured = capsys.readouterr()
        assert captured.err == ""

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "True"])
    def test_output_when_env_truthy(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """環境変数が真値（小文字一致）の場合はstderrへ1行出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", value)
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        is_pending_async_work(str(t), "", session_state=None)
        captured = capsys.readouterr()
        assert "_stop_gate result=False" in captured.err
        assert "last_tool=-" in captured.err
        assert "launched=0" in captured.err
        assert "pending=0" in captured.err
        assert "pending_ids=-" in captured.err
        assert "payload_valid=0" in captured.err
        assert "payload_non_teammate=0" in captured.err
        assert "source=none" in captured.err

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_no_output_when_env_falsy(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """環境変数が偽値の場合はstderr出力なし。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", value)
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        is_pending_async_work(str(t), "", session_state=None)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_output_with_pending_remainder(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """残差がある場合は`result=True`・残差件数・残差IDを出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _user_async_launched_entry("toolu_bg1"),
                _user_async_launched_entry("toolu_bg2"),
                _user_task_notification_entry("toolu_bg1"),
                _user_entry("続き"),
                _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
            ],
        )
        is_pending_async_work(str(t), "", session_state=None)
        captured = capsys.readouterr()
        assert "_stop_gate result=True" in captured.err
        assert "last_tool=Bash(bg=False)" in captured.err
        assert "launched=2" in captured.err
        assert "pending=1" in captured.err
        assert "pending_ids=toolu_bg2" in captured.err
        assert "payload_valid=0" in captured.err
        assert "payload_non_teammate=0" in captured.err
        assert "source=transcript" in captured.err

    def test_output_with_background_tasks_omits_task_body(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """payload taskの件数と判定源だけを出力し、任意フィールドを記録しない。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        transcript = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])],
        )
        is_pending_async_work(
            str(transcript),
            "",
            background_tasks=[
                {"type": "teammate", "id": "ignored-team"},
                {"type": "subagent", "id": "secret-agent", "description": "secret-description"},
                {"type": "shell", "status": "completed"},
                {"id": "invalid", "description": "secret-invalid"},
            ],
            session_state=None,
        )
        captured = capsys.readouterr()
        assert "payload_valid=3" in captured.err
        assert "payload_non_teammate=2" in captured.err
        assert "source=background_tasks" in captured.err
        assert "secret-agent" not in captured.err
        assert "secret-description" not in captured.err
        assert "secret-invalid" not in captured.err

    def test_output_with_background_bash(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """直前tool_useが背景Bash（`run_in_background=true`）の場合は`Bash(bg=True)`を出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _TEXT},
                        {
                            "type": "tool_use",
                            "id": "x",
                            "name": "Bash",
                            "input": {"command": "echo bg", "run_in_background": True},
                        },
                    ]
                ),
            ],
        )
        is_pending_async_work(str(t), "", session_state=None)
        captured = capsys.readouterr()
        assert "_stop_gate result=True" in captured.err
        assert "last_tool=Bash(bg=True)" in captured.err

    def test_output_with_async_wait_tool(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """直前tool_useが非同期待機系の場合はそのtool名を出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _TEXT},
                        {"type": "tool_use", "id": "x", "name": "Agent", "input": {}},
                    ]
                ),
            ],
        )
        is_pending_async_work(str(t), "", session_state=None)
        captured = capsys.readouterr()
        assert "_stop_gate result=True" in captured.err
        assert "last_tool=Agent" in captured.err
