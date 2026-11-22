"""hook_failuresの契約テスト。"""

import json
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.hook_support import _copy_entrypoint
from agent_toolkit._testing.pretooluse_support import _additional_context, _run


def test_pretooluse_record_failure_preserves_operation_result_boundary(monkeypatch) -> None:
    """開始記録の失敗を元操作の失敗とせず、公開hookの最終通知へ届ける。"""

    def fail_record(*_args, **_kwargs):
        raise OSError("開始記録の試験用障害")

    monkeypatch.setattr(termination_evidence, "observe_tool", fail_record)
    result = _run({"tool_name": "ObserveDelivery", "tool_input": {}})
    assert result.returncode == 0
    assert not result.stderr
    context = _additional_context(result)
    assert "開始記録の試験用障害" in context
    assert "元の操作の成否はこの診断では分からない" in context
    assert "元の操作の結果を確認せよ" in context
    assert "permissionDecision" not in result.stdout


class TestEntrypointExceptionStages:
    _copy_entrypoint = staticmethod(_copy_entrypoint)

    @pytest.mark.parametrize("subcommand", ["stop"])
    def test_stop_main_import_error_is_silent(
        self,
        tmp_path: pathlib.Path,
        subcommand: str,
    ) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "_hooks" / f"{subcommand}.py").write_text(
            "import json\n\n"
            "def _approve() -> None:\n"
            "    print(json.dumps({}))\n\n"
            "def main(payload_text: str) -> int:\n"
            "    del payload_text\n"
            "    raise ImportError('main failure')\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(entrypoint), subcommand],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
        assert not result.stderr

    def test_session_end_module_import_error_is_silent(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "_hooks" / "session_end_cleanup.py").write_text(
            "raise ImportError('module failure')\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(entrypoint), "session_end_cleanup"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
        assert not result.stderr

    @pytest.mark.parametrize("subcommand", ["pretooluse", "posttooluse", "user_prompt_submit"])
    def test_non_approve_fallback_subcommand_exception_returns_0_without_json(
        self,
        tmp_path: pathlib.Path,
        subcommand: str,
    ) -> None:
        """通知を受理するイベントは機構の障害を単一JSONで返し、元操作の失敗と混同しない。"""
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "_hooks" / f"{subcommand}.py").write_text(
            "def main(payload_text: str) -> int:\n    del payload_text\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(entrypoint), subcommand],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert auto_message_opening_attributes(context) == {"source": "hook", "kind": "warn"}
        assert f"agent-toolkitの自動処理{subcommand}で想定外のエラーが起きた: RuntimeError: boom" in context
        assert "この診断は元の操作の成否を示さない" in context
        assert "元の操作と自動処理の成否を分けて確認せよ" in context
        assert "再発を待たずに" in context
        assert "認可の範囲内で自ら是正" in context
        assert not result.stderr

    def test_session_end_cleanup_exception_returns_0_without_json(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        subcommand = "session_end_cleanup"
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "_hooks" / f"{subcommand}.py").write_text(
            "def main(payload_text: str) -> int:\n    del payload_text\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(entrypoint), subcommand],
            input="{}",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert not result.stdout
        assert not result.stderr
