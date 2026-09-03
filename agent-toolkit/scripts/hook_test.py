"""agent-toolkit/scripts/hook.py のテスト。

フック共通エントリポイントが、モジュール読込段階の失敗と`main()`実行中の例外を
区別して扱うことを検証する。読込失敗では素のtracebackだけを標準エラー出力へ書き、
`main()`実行中の例外では要約1行とtracebackを書いたうえでStop系サブコマンドの空JSON応答を返す。
"""

# pylint: disable=duplicate-code  # 共通entrypointとのサブコマンド契約をテスト側にも固定するため意図的に重複する。

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"

_SUBCOMMANDS = (
    "pretooluse",
    "posttooluse",
    "autonomous_exit",
    "plan_save_advisor",
    "agents_server_session_advisor",
    "pending_question_advisor",
    "subagent_stop_advisor",
    "session_end_cleanup",
    "stopfailure_notifier",
    "permissionrequest",
    "permissionrequest_codex",
    "quality_checkpoint",
    "user_prompt_submit",
)


class TestEntrypointExceptionStages:
    """共通エントリポイントが例外の発生段階に応じて出力を分けることを検証する。"""

    @staticmethod
    def _copy_entrypoint(tmp_path: pathlib.Path) -> pathlib.Path:
        entrypoint = tmp_path / "hook.py"
        shutil.copy2(_SCRIPT, entrypoint)
        return entrypoint

    @pytest.mark.parametrize(
        "subcommand",
        ["autonomous_exit", "plan_save_advisor", "agents_server_session_advisor", "pending_question_advisor"],
    )
    def test_main_import_error_emits_summary_traceback_and_empty_json(
        self,
        tmp_path: pathlib.Path,
        subcommand: str,
    ) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        approve_body = "    return None\n" if subcommand == "agents_server_session_advisor" else "    print(json.dumps({}))\n"
        (tmp_path / f"{subcommand}.py").write_text(
            "import json\n\n"
            "def _approve() -> None:\n"
            f"{approve_body}\n"
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
        expected_stdout = "" if subcommand == "agents_server_session_advisor" else "{}\n"
        assert result.stdout == expected_stdout
        assert result.stderr.startswith(f"[{subcommand}] 想定外エラー: ImportError: main failure")
        assert "Traceback (most recent call last):" in result.stderr

    def test_module_import_error_emits_only_traceback(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "autonomous_exit.py").write_text(
            "raise ImportError('module failure')\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(entrypoint), "autonomous_exit"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
        assert result.stderr.startswith("Traceback (most recent call last):")
        assert "[autonomous_exit] 想定外エラー" not in result.stderr

    def test_non_approve_fallback_subcommand_exception_returns_0_without_json(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """approve対象外のサブコマンドは例外時もJSONなしでfail-openする。"""
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "pretooluse.py").write_text(
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
        assert not result.stdout
        assert result.stderr.startswith("[pretooluse] 想定外エラー: RuntimeError: boom")
        assert "Traceback (most recent call last):" in result.stderr

    def test_session_end_cleanup_exception_returns_0_without_json(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        subcommand = "session_end_cleanup"
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / f"{subcommand}.py").write_text(
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
        assert result.stderr.startswith(f"[{subcommand}] 想定外エラー: RuntimeError: boom")


class TestStandardInputAndPayloadDump:
    """共通入口のUTF-8境界とpayloadダンプを検証する。"""

    @staticmethod
    def _copy_entrypoint(tmp_path: pathlib.Path) -> pathlib.Path:
        entrypoint = tmp_path / "hook.py"
        shutil.copy2(_SCRIPT, entrypoint)
        return entrypoint

    @staticmethod
    def _write_echo_module(tmp_path: pathlib.Path, subcommand: str = "pretooluse") -> None:
        (tmp_path / f"{subcommand}.py").write_text(
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
        (tmp_path / "pretooluse.py").write_text(
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
        assert "UTF-8" in result.stderr.decode("utf-8")
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
        assert stderr.startswith("[auto-generated: agent-toolkit/hook] hook定義と実装が不整合:")
        assert "stop_advisor" in stderr
        assert "|".join(sorted(_SUBCOMMANDS)) in stderr

    def test_no_subcommand_reports_usage(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            input=b"{}",
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
        assert result.stderr.decode("utf-8").startswith("[auto-generated: agent-toolkit/hook] usage: hook.py <")

    def test_entrypoint_inherits_predecessor_session_state(self, tmp_path: pathlib.Path) -> None:
        """各サブコマンドへ渡す前に共通入口が前身状態を継承する。"""
        entrypoint = self._copy_entrypoint(tmp_path)
        self._write_echo_module(tmp_path)
        source_directory = _SCRIPT.parent
        for name in ("_session_state.py", "_atomic_file.py", "_file_lock.py"):
            shutil.copy2(source_directory / name, tmp_path / name)
        temp_directory = tmp_path / "temp"
        temp_directory.mkdir()
        (temp_directory / "claude-agent-toolkit-previous.json").write_text(
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
        assert json.loads((temp_directory / "claude-agent-toolkit-current.json").read_text(encoding="utf-8")) == {
            "plan_mode_skill_invoked": True,
            "inherited_from_session_id": "previous",
        }

    def test_module_import_error_uses_utf8_stderr(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "pretooluse.py").write_text(
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
        assert "日本語の読込失敗" in result.stderr.decode("utf-8")

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
