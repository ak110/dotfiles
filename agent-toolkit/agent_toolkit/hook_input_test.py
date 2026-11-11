"""hook_inputの契約テスト。"""

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from agent_toolkit._common import state_paths
from agent_toolkit._testing.hook_support import _copy_entrypoint, _registered_subcommands

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"


_SUBCOMMANDS = _registered_subcommands()


class TestStandardInputAndPayloadDump:
    """共通処理によるUTF-8のデコードとpayloadダンプを検証する。"""

    _copy_entrypoint = staticmethod(_copy_entrypoint)

    @staticmethod
    def _write_echo_module(tmp_path: pathlib.Path, subcommand: str = "pretooluse") -> None:
        (tmp_path / "_hooks" / f"{subcommand}.py").write_text(
            "def main(payload_text: str) -> int:\n    print(payload_text, end='')\n    return 0\n",
            encoding="utf-8",
        )

    @pytest.mark.parametrize("subcommand", _SUBCOMMANDS)
    def test_utf8_japanese_is_decoded_before_module_call(
        self,
        tmp_path: pathlib.Path,
        subcommand: str,
    ) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        self._write_echo_module(tmp_path, subcommand)
        payload = '{"prompt":"日本語"}'.encode()

        result = subprocess.run(
            [sys.executable, str(entrypoint), subcommand],
            input=payload,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0
        assert result.stdout == payload
        assert not result.stderr

    def test_invalid_utf8_skips_module_call(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        marker = tmp_path / "called"
        (tmp_path / "_hooks" / "pretooluse.py").write_text(
            "import pathlib\n\n"
            "def main(payload_text: str) -> int:\n"
            "    del payload_text\n"
            f"    pathlib.Path({str(marker)!r}).touch()\n"
            "    return 0\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input=b"\xff",
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
        assert not result.stderr
        assert not marker.exists()

    def test_unknown_subcommand_reports_definition_mismatch(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "stop_advisor"],
            input=b"{}",
            capture_output=True,
            check=False,
        )

        stderr = result.stderr.decode("utf-8")
        assert result.returncode == 0
        assert not result.stdout
        assert not stderr

    def test_no_subcommand_reports_usage(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            input=b"{}",
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
        stderr = result.stderr.decode("utf-8")
        assert not stderr

    def test_entrypoint_inherits_predecessor_session_state(self, tmp_path: pathlib.Path) -> None:
        """各サブコマンドへ渡す前に共通処理が前身状態を継承する。"""
        entrypoint = self._copy_entrypoint(tmp_path)
        self._write_echo_module(tmp_path)
        source_directory = _SCRIPT.parent
        (tmp_path / "_common").mkdir()
        (tmp_path / "_common/__init__.py").write_text("", encoding="utf-8")
        shutil.copy2(source_directory / "_common/session_state.py", tmp_path / "_common/session_state.py")
        shutil.copy2(source_directory / "_common/atomic_file.py", tmp_path / "_common/atomic_file.py")
        shutil.copy2(source_directory / "_common/file_lock.py", tmp_path / "_common/file_lock.py")
        temp_directory = tmp_path / "temp"
        temp_directory.mkdir()
        state_directory = state_paths.state_dir() / "hook-sessions"
        state_directory.mkdir(parents=True, exist_ok=True)
        (state_directory / "claude-agent-toolkit-previous.json").write_text(
            json.dumps({"plan_mode_skill_invoked": True}),
            encoding="utf-8",
        )
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps({"sessionId": "previous"}) + "\n", encoding="utf-8")
        payload = json.dumps({"session_id": "current", "transcript_path": str(transcript)})
        env = os.environ.copy()
        env["TMPDIR"] = str(temp_directory)

        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0
        assert result.stdout == payload
        assert not result.stderr
        assert json.loads((state_directory / "claude-agent-toolkit-current.json").read_text(encoding="utf-8")) == {
            "plan_mode_skill_invoked": True,
            "inherited_from_session_id": "previous",
        }
        assert (state_directory / ".state.lock").exists()
        assert not (temp_directory / "claude-agent-toolkit-current.json").exists()

    def test_module_import_error_uses_utf8_stderr(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "_hooks" / "pretooluse.py").write_text(
            "raise ImportError('日本語の読込失敗')\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "cp932"

        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input=b"{}",
            capture_output=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0
        assert "日本語の読込失敗" in result.stdout.decode("utf-8")
        assert not result.stderr

    def test_dump_preserves_input_bytes(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        self._write_echo_module(tmp_path)
        dump_directory = tmp_path / "dump"
        dump_directory.mkdir()
        payload = '{"prompt":"日本語"}'.encode()
        env = os.environ.copy()
        env["AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP"] = str(dump_directory)

        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input=payload,
            capture_output=True,
            check=False,
            env=env,
        )

        dumps = list(dump_directory.glob("pretooluse-*.json"))
        assert result.returncode == 0
        assert len(dumps) == 1
        assert dumps[0].read_bytes() == payload

    def test_unset_dump_environment_creates_no_dump(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        self._write_echo_module(tmp_path)
        env = os.environ.copy()
        env.pop("AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP", None)

        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input=b"{}",
            capture_output=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0
        assert not list(tmp_path.glob("pretooluse-*.json"))

    def test_dump_failure_does_not_skip_module_call(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        self._write_echo_module(tmp_path)
        not_a_directory = tmp_path / "dump-file"
        not_a_directory.write_text("not a directory", encoding="utf-8")
        env = os.environ.copy()
        env["AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP"] = str(not_a_directory)

        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input=b"{}",
            capture_output=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0
        assert result.stdout == b"{}"
        assert not result.stderr
