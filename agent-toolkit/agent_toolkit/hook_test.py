"""agent-toolkit/agent_toolkit/hook.py のテスト。

フック共通エントリポイントが、モジュール読込段階の失敗と`main()`実行中の例外を
区別して扱うことを検証する。読込失敗では素のtracebackだけを標準エラー出力へ書き、
`main()`実行中の例外では要約1行とtracebackを書いたうえでStop共通処理の空JSON応答を返す。
Stopとprocess-loopを呼び出した場合の親子セッションの中断処理も検証する。
Python 3.15の遅延読み込みでは、公開hookが処理分岐で使わない依存を読み込まずに同じ応答を返すことと、
同一プロセスで反復して呼んでも状態に従って応答が変わることを検証する。
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
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._common import session_activity, session_registry, state_paths
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit.atk_test import _setup_notes

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"

_HOOK_DEFINITIONS = tuple(_SCRIPT.parents[1] / "hooks" / name for name in ("hooks.json", "hooks.codex.json"))


@pytest.mark.parametrize("argv", [[], ["unknown-command"]])
def test_unknown_hook_warning_once_per_payload_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """hook入力の会話識別子ごとに、同じ引数不整合の警告を1回だけ表示する。"""
    for identifier, expected in (("first", True), ("first", False), ("second", True), (None, True), (None, True)):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": identifier})))
        assert _hook.main(argv) == 0
        assert bool(capsys.readouterr().err) is expected


def _registered_subcommands() -> tuple[str, ...]:
    """Claude Code・Codexのhook定義が`hook.py`（`atk-hook`）へ渡すサブコマンドの和集合を返す。"""
    names: set[str] = set()
    for path in _HOOK_DEFINITIONS:
        definitions = json.loads(path.read_text(encoding="utf-8"))["hooks"]
        for entries in definitions.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    names.add(hook["command"].split()[-1])
    return tuple(sorted(names))


_SUBCOMMANDS = _registered_subcommands()


def _copy_entrypoint(tmp_path: pathlib.Path) -> pathlib.Path:
    """依存する通知モジュールと共通entrypointを検証用ディレクトリへ複製する。"""
    entrypoint = tmp_path / "hook.py"
    entrypoint.write_text(
        _SCRIPT.read_text(encoding="utf-8")
        .replace("agent_toolkit._hooks", "_hooks")
        .replace("agent_toolkit._common.session_activity", "_hooks.session_activity"),
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
        assert auto_message_opening_attributes(result.stderr) == {"source": "hook", "kind": "warn"}
        assert f"\n[{subcommand}] 想定外エラー: ImportError: main failure" in result.stderr
        assert "Traceback (most recent call last):" in result.stderr

    def test_module_import_error_emits_only_traceback(self, tmp_path: pathlib.Path) -> None:
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
        assert result.stderr.startswith("Traceback (most recent call last):")
        assert "[session_end_cleanup] 想定外エラー" not in result.stderr

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
        assert auto_message_opening_attributes(result.stderr) == {"source": "hook", "kind": "warn"}
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
        assert auto_message_opening_attributes(result.stderr) == {"source": "hook", "kind": "warn"}
        assert f"\n[{subcommand}] 想定外エラー: RuntimeError: boom" in result.stderr


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
        assert auto_message_opening_attributes(stderr) == {"source": "hook", "kind": "warn"}
        assert "\nhook定義と実装が不整合:" in stderr
        assert "stop_advisor" in stderr
        # hook.pyが受け付けるサブコマンドは、hook定義（hooks.json・hooks.codex.json）が登録するものと一致する。
        assert f"現行のサブコマンド: {'|'.join(_SUBCOMMANDS)}。" in stderr

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
        assert auto_message_opening_attributes(stderr) == {"source": "hook", "kind": "warn"}
        assert "\nusage: hook.py <" in stderr

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
    """公開hookで子のStopを処理した後も、公開CLIが次のセッションを起動する。"""
    monkeypatch.setattr(session_activity, "scan_activity", lambda **_kwargs: session_activity.ActivitySnapshot())
    _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _path: True)
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

    def fake_termination(*, session_id: str) -> tuple[str, None]:
        assert session_id == "loop-stop-integration"
        termination_calls.append("requested")
        return "unsupported", None

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
    monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1 if len(session_calls) < 2 else 0)

    def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)

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


# 公開hookを子プロセスで実行し、終了時点の`sys.modules`を記録する。`eager`ではPEP 810の
# `sys.set_lazy_imports_filter`で全ての遅延読み込みを無効化する。PEP 810が記す`-X lazy_imports=none`は
# Python 3.15.0が`expected 'all' or 'normal'`で拒否するため、同じ効果を持つ公開APIを対照に使う。
_MODULE_RECORDING_RUNNER = """\
import os
import runpy
import sys

mode, record, script, *arguments = sys.argv[1:]
if mode == "eager":
    sys.set_lazy_imports_filter(lambda *_: False)
sys.argv = [script, *arguments]
sys.path[0] = os.path.dirname(script)
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exc:
    code = exc.code
with open(record, "w", encoding="utf-8") as file:
    file.write("\\n".join(sorted(sys.modules)))
sys.exit(code)
"""

# 終了工程の観測などが対象外の入力でも読み込んでいた、委譲・バックグラウンドタスク・シェル判定の依存。
_UNUSED_BRANCH_MODULES = (
    "agent_toolkit._agents_server.state",
    "agent_toolkit._hooks.agents_server_observations",
    "agent_toolkit._hooks.background_tasks",
    "agent_toolkit._hooks.pretooluse.shell_checks",
)


def _run_with_module_record(
    tmp_path: pathlib.Path, mode: str, subcommand: str, payload: bytes
) -> tuple[subprocess.CompletedProcess[bytes], set[str]]:
    """公開hookを子プロセスで実行し、応答と読み込んだモジュール名を返す。"""
    runner = tmp_path / "record_modules.py"
    runner.write_text(_MODULE_RECORDING_RUNNER, encoding="utf-8")
    record = tmp_path / f"modules-{mode}.txt"
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    env.pop("AGENT_TOOLKIT_DELEGATED_SESSION", None)
    env.pop("AGENT_TOOLKIT_OWNER_SESSION", None)
    result = subprocess.run(
        [sys.executable, str(runner), mode, str(record), str(_SCRIPT), subcommand],
        input=payload,
        capture_output=True,
        check=False,
        env=env,
    )
    return result, set(record.read_text(encoding="utf-8").splitlines())


class TestLazyDependencyLoading:
    """公開hookが処理分岐で使わない依存を読み込まず、遅延を無効化した場合と同じ応答を返すことを検証する。"""

    @pytest.mark.parametrize("subcommand", _SUBCOMMANDS)
    def test_each_hook_loads_fewer_modules_with_same_response(self, tmp_path: pathlib.Path, subcommand: str) -> None:
        lazy, lazy_modules = _run_with_module_record(tmp_path, "lazy", subcommand, b"{}")
        eager, eager_modules = _run_with_module_record(tmp_path, "eager", subcommand, b"{}")

        assert (lazy.returncode, lazy.stdout, lazy.stderr) == (eager.returncode, eager.stdout, eager.stderr)
        assert len(lazy_modules) < len(eager_modules)
        # session_idを持たない入力はどのhookでも委譲・バックグラウンドタスク・報告解析へ到達しない。
        assert not set(_UNUSED_BRANCH_MODULES) & lazy_modules

    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "used_modules"),
        [
            ("Read", {"file_path": "read-target.txt"}, ()),
            ("Bash", {"command": "ls"}, ("agent_toolkit._hooks.pretooluse.shell_checks",)),
        ],
        ids=["normal", "bash-pass"],
    )
    def test_pretooluse_skips_dependencies_of_unused_branches(
        self,
        tmp_path: pathlib.Path,
        tool_name: str,
        tool_input: dict[str, str],
        used_modules: tuple[str, ...],
    ) -> None:
        target = tmp_path / "read-target.txt"
        target.write_text("内容\n", encoding="utf-8")
        resolved_input = {key: str(target) if key == "file_path" else value for key, value in tool_input.items()}
        payload = json.dumps(
            {"session_id": "lazy-session", "cwd": str(tmp_path), "tool_name": tool_name, "tool_input": resolved_input}
        ).encode("utf-8")

        lazy, lazy_modules = _run_with_module_record(tmp_path, "lazy", "pretooluse", payload)
        eager, eager_modules = _run_with_module_record(tmp_path, "eager", "pretooluse", payload)

        assert (lazy.returncode, lazy.stdout, lazy.stderr) == (0, b"", b"")
        assert (eager.returncode, eager.stdout, eager.stderr) == (0, b"", b"")
        assert len(lazy_modules) < len(eager_modules)
        unused = set(_UNUSED_BRANCH_MODULES) - set(used_modules)
        assert unused <= eager_modules
        assert not unused & lazy_modules


def _call_hook_in_process(
    monkeypatch: pytest.MonkeyPatch, subcommand: str, payload: dict[str, object], expected_code: int = 0
) -> str:
    """公開hookの`main`を同一プロセスで呼び、標準出力を返す。"""
    output = io.StringIO()
    errors = io.StringIO()
    with monkeypatch.context() as context:
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")), encoding="utf-8")
        context.setattr(sys, "stdin", stdin)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            assert _hook.main([subcommand]) == expected_code
    return errors.getvalue() if expected_code == 2 else output.getvalue()


def test_repeated_calls_in_one_process_follow_input_and_session_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """依存を読み込んだ後の反復呼び出しでも、スキルの取得の判定と未配送通知の判定が入力と状態に従う。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    empty_transcript = tmp_path / "empty.jsonl"
    empty_transcript.write_text("", encoding="utf-8")

    def pretooluse(session_id: str, command: str, expected_code: int = 0) -> str:
        return _call_hook_in_process(
            monkeypatch,
            "pretooluse",
            {
                "session_id": session_id,
                "transcript_path": str(empty_transcript),
                "cwd": str(tmp_path),
                "tool_name": "Bash",
                "tool_input": {"command": command},
            },
            expected_code=expected_code,
        )

    # 取得の観測までは同じ操作を止め、Skill起動の観測の後は同じセッションの再発行を通す。別セッションは再び止める。
    context = pretooluse("repeat-session", "grep -rn needle .", expected_code=2)
    assert auto_message_opening_attributes(context) == {"source": "pretooluse", "kind": "block"}
    assert not pretooluse("repeat-session", "ls")
    assert pretooluse("repeat-session", "grep -rn needle .", expected_code=2) == context
    skill = {"session_id": "repeat-session", "tool_name": "Skill", "tool_input": {"skill": "agent-toolkit:search"}}
    _call_hook_in_process(monkeypatch, "posttooluse", {"hook_event_name": "PostToolUse", **skill})
    assert not pretooluse("repeat-session", "grep -rn needle .")
    assert pretooluse("other-session", "grep -rn needle .", expected_code=2) == context

    # 未配送の完了通知が残るtranscriptでは配送のためにターンを終え、残らないtranscriptでは何も返さない。
    # キューの記録の形は`_hooks/queued_notification_advisor_test.py`の生成と同じとする。
    notification = (
        "<task-notification>\n<task-id>repeat-task</task-id>\n"
        "<output-file>/tmp/repeat-task.output</output-file>\n<status>completed</status>\n</task-notification>"
    )
    queued = tmp_path / "queued.jsonl"
    queued.write_text(
        json.dumps({"type": "queue-operation", "operation": "enqueue", "content": notification}) + "\n", encoding="utf-8"
    )

    def stop(transcript: pathlib.Path) -> dict[str, Any]:
        output = _call_hook_in_process(monkeypatch, "stop", {"session_id": "stop-session", "transcript_path": str(transcript)})
        return cast(dict[str, Any], json.loads(output))

    assert stop(queued) == {"continue": False}

    assert stop(empty_transcript) == {}
    assert stop(queued) == {"continue": False}


def test_activity_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """公開hookの通常応答を保持しつつ、Stop継続とターン終了を活動取得へ渡す。"""
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    host = session_activity.HostProcess("codex", identity)
    monkeypatch.setattr(session_activity, "host_process", lambda: host)
    monkeypatch.setattr(session_activity, "host_processes", lambda: ())
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    transcript = tmp_path / "activity.jsonl"
    transcript.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n",
        encoding="utf-8",
    )
    payload: dict[str, object] = {"session_id": "activity-integration", "transcript_path": str(transcript), "turn_id": "t1"}
    response: dict[str, object] = {"decision": "block", "reason": "継続"}
    stop_module = _hook.importlib.import_module("agent_toolkit._hooks.stop")

    def emit_stop(_payload: str) -> int:
        print(json.dumps(response))
        return 0

    monkeypatch.setattr(stop_module, "main", emit_stop)

    def call(event: str, command: str = "session_activity", **extra: object) -> str:
        return _call_hook_in_process(monkeypatch, command, {**payload, "hook_event_name": event, **extra})

    assert not call("UserPromptSubmit")
    assert session_activity.scan_activity().has_activity
    assert json.loads(call("Stop", "stop")) == response
    assert session_activity.scan_activity().has_activity
    response.clear()
    assert json.loads(call("Stop", "stop")) == {}
    assert not session_activity.scan_activity().has_activity
    call("SubagentStart", agent_id="native-child")
    assert session_activity.scan_activity().has_activity
    call("SubagentStop", agent_id="native-child")
    assert not session_activity.scan_activity().has_activity
    call("UserPromptSubmit")
    assert session_activity.scan_activity().has_activity
    call("SessionEnd")
    assert not session_activity.scan_activity().has_activity
