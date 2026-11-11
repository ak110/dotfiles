"""hook_failuresの契約テスト。"""

import json
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.hook_support import _copy_entrypoint


class TestEntrypointExceptionStages:
    """共通エントリポイントが例外の発生段階に応じて出力を分けることを検証する。"""

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

    def test_non_approve_fallback_subcommand_exception_returns_0_without_json(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """approve対象外のサブコマンドは例外時もJSONなしでfail-openする。"""
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "_hooks" / "pretooluse.py").write_text(
            "def main(payload_text: str) -> int:\n    del payload_text\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert auto_message_opening_attributes(context) == {"source": "hook", "kind": "warn"}
        assert "agent-toolkitの自動処理pretooluseで想定外のエラーが起きた: RuntimeError: boom" in context
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
