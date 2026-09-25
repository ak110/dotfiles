"""agent-toolkit/agent_toolkit/hook.py のテスト。

フック共通エントリポイントが、モジュール読込段階の失敗と`main()`実行中の例外を
区別して扱うことを検証する。読込失敗では素のtracebackだけを標準エラー出力へ書き、
`main()`実行中の例外では要約1行とtracebackを書いたうえでStop共通入口の空JSON応答を返す。
Stop入口とprocess-loop入口を通した親子セッションの中断経路も検証する。
"""

# 共通entrypointとのサブコマンド契約をテスト側にも固定するため意図的に重複する。

import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any, NoReturn, cast

import pytest

from agent_toolkit import atk
from agent_toolkit import hook as _hook
from agent_toolkit._atk import agents_exit_session as _agents_exit_session
from agent_toolkit._atk import config as _config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._atk.wi import process_loop as _process_loop
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit.atk_test import _setup_notes

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"

_SUBCOMMANDS = (
    "pretooluse",
    "posttooluse",
    "stop",
    "autonomous_exit",
    "plan_save_advisor",
    "agents_server_session_advisor",
    "pending_question_advisor",
    "subagent_stop_advisor",
    "session_end_cleanup",
    "stopfailure_notifier",
    "permissionrequest",
    "permissionrequest_codex",
    "rules_context",
    "rules_context_codex",
    "user_prompt_submit",
)


def _copy_entrypoint(tmp_path: pathlib.Path) -> pathlib.Path:
    """依存する通知モジュールと共通entrypointを検証用ディレクトリへ複製する。"""
    entrypoint = tmp_path / "hook.py"
    entrypoint.write_text(
        _SCRIPT.read_text(encoding="utf-8").replace("agent_toolkit._hooks", "_hooks"),
        encoding="utf-8",
    )
    hooks = tmp_path / "_hooks"
    hooks.mkdir()
    (hooks / "__init__.py").write_text("", encoding="utf-8")
    source_hooks = _SCRIPT.parent / "_hooks"
    for name in ("notice.py", "message_format.py"):
        (hooks / name).write_text((source_hooks / name).read_text(encoding="utf-8"), encoding="utf-8")
    return entrypoint


class TestEntrypointExceptionStages:
    """共通エントリポイントが例外の発生段階に応じて出力を分けることを検証する。"""

    _copy_entrypoint = staticmethod(_copy_entrypoint)

    @pytest.mark.parametrize("subcommand", ["stop"])
    def test_main_import_error_emits_summary_traceback_and_empty_json(
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
        assert result.stdout == "{}\n"
        assert auto_message_opening_attributes(result.stderr) == {"source": "agent-toolkit/hook", "kind": "warn"}
        assert f"\n[{subcommand}] 想定外エラー: ImportError: main failure" in result.stderr
        assert "Traceback (most recent call last):" in result.stderr

    def test_module_import_error_emits_only_traceback(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "_hooks" / "autonomous_exit.py").write_text(
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
        assert not result.stdout
        assert auto_message_opening_attributes(result.stderr) == {"source": "agent-toolkit/hook", "kind": "warn"}
        assert "\n[pretooluse] 想定外エラー: RuntimeError: boom" in result.stderr
        assert "Traceback (most recent call last):" in result.stderr

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
        assert auto_message_opening_attributes(result.stderr) == {"source": "agent-toolkit/hook", "kind": "warn"}
        assert f"\n[{subcommand}] 想定外エラー: RuntimeError: boom" in result.stderr


class TestStandardInputAndPayloadDump:
    """共通入口のUTF-8境界とpayloadダンプを検証する。"""

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
        assert auto_message_opening_attributes(stderr) == {"source": "agent-toolkit/hook", "kind": "warn"}
        assert "\nhook定義と実装が不整合:" in stderr
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
        stderr = result.stderr.decode("utf-8")
        assert auto_message_opening_attributes(stderr) == {"source": "agent-toolkit/hook", "kind": "warn"}
        assert "\nusage: hook.py <" in stderr

    def test_entrypoint_inherits_predecessor_session_state(self, tmp_path: pathlib.Path) -> None:
        """各サブコマンドへ渡す前に共通入口が前身状態を継承する。"""
        entrypoint = self._copy_entrypoint(tmp_path)
        self._write_echo_module(tmp_path)
        source_directory = _SCRIPT.parent
        (tmp_path / "_common").mkdir()
        (tmp_path / "_common/__init__.py").write_text("", encoding="utf-8")
        shutil.copy2(source_directory / "_hooks/session_state.py", tmp_path / "_hooks/session_state.py")
        shutil.copy2(source_directory / "_common/atomic_file.py", tmp_path / "_common/atomic_file.py")
        shutil.copy2(source_directory / "_common/file_lock.py", tmp_path / "_common/file_lock.py")
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


def test_native_subagent_stop_keeps_next_process_loop_session_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開hook入口で子のStopを処理した後も、公開CLIが次のセッションを起動する。"""
    _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    monkeypatch.setattr(_managed_temp, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(_process_loop.shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_process_loop, "_pull_private_notes", lambda _path: True)
    monkeypatch.setattr(_wait_schedule, "get_prompt_cache_ttl", lambda _bucket: "1h")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    abort_path = tmp_path / "state" / "agent-toolkit" / "process-wi-abort"
    session_calls: list[list[str]] = []
    child_responses: list[dict[str, Any]] = []
    termination_calls: list[str] = []

    def invoke_stop(agent_id: str | None, count: int) -> dict[str, Any]:
        transcript = tmp_path / f"stop-{agent_id or 'main'}-{count}.jsonl"
        entry = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "進行中"}]}}
        transcript.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for _ in range(count)) + "\n", encoding="utf-8")
        payload: dict[str, object] = {"session_id": "loop-stop-integration", "transcript_path": str(transcript)}
        if agent_id is not None:
            payload["agent_id"] = agent_id
        output = io.StringIO()
        with monkeypatch.context() as context:
            stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")), encoding="utf-8")
            context.setattr(sys, "stdin", stdin)
            with contextlib.redirect_stdout(output):
                assert _hook.main(["stop"]) == 0
        return cast(dict[str, Any], json.loads(output.getvalue()))

    def fake_termination() -> tuple[str, None]:
        termination_calls.append("requested")
        return "unavailable", None

    monkeypatch.setattr(_agents_exit_session, "request_termination", fake_termination)

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd[:1] == ["claude"] and "-p" not in cmd:
            if not session_calls:
                for count in range(1, 4):
                    response = invoke_stop("agent-review", count)
                    assert response.get("decision") != "block"
                    child_responses.append(response)
                assert not abort_path.exists()
            session_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            remote = (
                "https://github.com/example/myrepo.git\n" if kwargs.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=remote, stderr="" if kwargs.get("text") else b"")
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: 1 if len(session_calls) < 2 else 0)

    def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(_process_loop, "_wait_for_changes", stop_wait)

    with pytest.raises(SystemExit) as loop_exit:
        atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

    assert loop_exit.value.code == 0
    assert len(session_calls) == 2
    assert len(child_responses) == 3
    assert all("systemMessage" not in response for response in child_responses)
    assert not abort_path.exists()
    assert not termination_calls

    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    parent_responses = [invoke_stop(None, count) for count in range(1, 4)]
    assert "systemMessage" in parent_responses[-1]
    assert abort_path.exists()
    assert termination_calls == ["requested"]
