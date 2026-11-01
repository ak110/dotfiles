"""agent-toolkit/agent_toolkit/hook.py のテスト。

フック共通エントリポイントが、モジュール読込段階の失敗と`main()`実行中の例外を
区別して扱うことを検証する。配送先が確定した入力では内部例外を標識付き通知へ変換し、
Stop・SessionEndやイベント不明の入力では出力を停止する。
Stopとprocess-loopを呼び出した場合の親子セッションの中断処理も検証する。
Python 3.15の遅延読み込みでは、公開hookが処理分岐で使わない依存を読み込まずに同じ応答を返すことと、
同一プロセスで反復して呼んでも状態に従って応答が変わることを検証する。
"""

# 共通entrypointとのサブコマンド契約をテスト側にも固定するため意図的に重複する。

import contextlib
import importlib
import io
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, cast

import pytest

from agent_toolkit import atk
from agent_toolkit import hook as _hook
from agent_toolkit._atk import agents_exit_session as _agents_exit_session
from agent_toolkit._atk import config as _config
from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._common import (
    inherited_venv,
    plugin_environment,
    session_activity,
    session_registry,
    session_state,
    state_paths,
)
from agent_toolkit._common import plugin_roots as resource_roots
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._common.plugin_roots import _inventories, _plugin_inventory, _reusable
from agent_toolkit._hooks import notice
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli
from agent_toolkit._testing.helpers import auto_message_opening_attributes
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process, codex_execution_payload
from agent_toolkit.atk_test import _setup_notes

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"

_HOOK_DEFINITIONS = tuple(_SCRIPT.parents[1] / "hooks" / name for name in ("hooks.json", "hooks.codex.json"))


def test_entry_restores_launch_environment_for_children(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hook.main`は開始時に、起動層が変えた値を呼出元の値へ戻し、hookが起動する子へ渡さない。"""
    monkeypatch.setenv("PYTHONPATH", "/plugin-root:/caller")
    monkeypatch.setenv(inherited_venv.LAUNCH_RESTORE_KEY, json.dumps({"PYTHONPATH": ["/caller", "/plugin-root:/caller"]}))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert _hook.main(["unknown-command"]) == 0
    assert os.environ["PYTHONPATH"] == "/caller"
    assert inherited_venv.LAUNCH_RESTORE_KEY not in os.environ


@pytest.mark.parametrize("argv", [[], ["unknown-command"]])
def test_unknown_hook_warning_once_per_payload_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """hook入力の会話識別子ごとに、同じ引数不整合の警告を1回だけ表示する。"""
    for identifier in ("first", "first", "second", None, None):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": identifier})))
        assert _hook.main(argv) == 0
        captured = capsys.readouterr()
        assert not captured.err and not captured.out


def _registered_subcommands() -> tuple[str, ...]:
    """Claude Code・Codexのhook定義が`hook.py`（`atk-hook`）へ渡すサブコマンドの和集合を返す。"""
    names: set[str] = set()
    for path in _HOOK_DEFINITIONS:
        definitions = json.loads(path.read_text(encoding="utf-8"))["hooks"]
        for entries in definitions.values():
            for entry in entries:
                for hook in entry["hooks"]:
                    names.add(hook["command"].removesuffix(" || exit 1").split()[-1])
    return tuple(sorted(names))


_SUBCOMMANDS = _registered_subcommands()

# hook定義の起動コマンドが持つ末尾。起動の失敗（終了コード2を含む非0）を非遮断の終了コード1へ変換する。
_HOOK_FAILURE_SUFFIX = " || exit 1"


def _definition_commands() -> list[tuple[str, str]]:
    """両方のhook定義の（定義ファイル名、command）を返す。"""
    commands: list[tuple[str, str]] = []
    for path in _HOOK_DEFINITIONS:
        for entries in json.loads(path.read_text(encoding="utf-8"))["hooks"].values():
            commands.extend((path.name, hook["command"]) for entry in entries for hook in entry["hooks"])
    return commands


def test_every_hook_command_converts_launch_failure_to_non_blocking() -> None:
    """全hookの起動コマンドが、起動失敗を非遮断へ変換する末尾を持つ。"""
    missing = [f"{name}: {command}" for name, command in _definition_commands() if not command.endswith(_HOOK_FAILURE_SUFFIX)]
    assert not missing


@pytest.mark.skipif(os.name == "nt", reason="登録コマンドをPOSIXシェルで検証する")
@pytest.mark.parametrize("definition", _HOOK_DEFINITIONS, ids=lambda path: path.name)
@pytest.mark.parametrize("case", ["pattern-kill", "unread-rewrite"])
def test_registered_pretooluse_command_preserves_normal_denial(definition, tmp_path, case) -> None:
    shell = shutil.which("sh")
    assert shell is not None
    command = json.loads(definition.read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    launcher = "uv run --no-config --no-project --python 3.15 python "
    assert command.startswith(launcher)
    command = shlex.quote(sys.executable) + " " + command.removeprefix(launcher)
    environment = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(_SCRIPT.parents[1]), "PLUGIN_ROOT": str(_SCRIPT.parents[1])}
    # 依存導入を実行せず、試験を実行中のPythonを準備済み環境の境界へ接続する。
    environment[plugin_environment.RUNTIME_DATA_ENV] = str(tmp_path / "runtime")
    selected = plugin_environment.project_environment(_SCRIPT.parents[1], environment)
    runner = plugin_environment.python_path(selected)
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n', encoding="utf-8")
    runner.chmod(0o700)
    (selected / ".agent-toolkit-dependencies-ready").write_text("ready\n", encoding="utf-8")
    payload = {"session_id": "registered-denial", "tool_name": "Bash", "tool_input": {"command": "pkill -f worker"}}
    expected = "pkill"
    if case == "unread-rewrite":
        payload.update(tool_name="Write", tool_input={"file_path": str(tmp_path / "note.md"), "content": "本文"})
        first = subprocess.run(
            [shell, "-c", command],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env=environment,
            cwd=tmp_path,
        )
        assert first.returncode == 0 and not first.stderr
        assert "additionalContext" in json.loads(first.stdout)["hookSpecificOutput"]
        expected = "未取得"
    result = subprocess.run(
        [shell, "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=environment,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert expected in specific["permissionDecisionReason"]


@pytest.mark.parametrize("subcommand", ["pretooluse", "posttooluse", "user_prompt_submit"])
@pytest.mark.parametrize("partial", [False, True])
def test_failure_merges_with_partial_output_as_one_json(monkeypatch, subcommand, partial) -> None:
    module = importlib.import_module(f"agent_toolkit._hooks.{subcommand}")

    def fail(_payload, **_kwargs):
        if partial:
            print(json.dumps({"hookSpecificOutput": {"additionalContext": "既存の通知"}}))
        raise RuntimeError("故障注入")

    monkeypatch.setattr(module, "main", fail)
    result = _run_hook_in_process(subcommand, {"session_id": "failure-merge"})
    assert result.returncode == 0
    assert not result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert 'source="hook" kind="warn"' in context
    assert "故障注入" in context
    assert ("既存の通知" in context) is partial


def test_discarded_codex_stop_notice_is_not_recorded(monkeypatch) -> None:
    module = importlib.import_module("agent_toolkit._hooks.stop")
    advisor = importlib.import_module("agent_toolkit._hooks.termination_order_advisor")

    def evaluate(_payload, **_kwargs):
        return "notify", notice.formatter("discarded")("通知本文")

    monkeypatch.setattr(module, "CODEX_CHECK_MODULE_NAMES", ("termination_order_advisor",))
    monkeypatch.setattr(advisor, "evaluate", evaluate)
    result = _run_hook_in_process("stop", {"session_id": "discarded-stop", "turn_id": "turn"})
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert not result.stderr
    assert not session_state.read_state("discarded-stop").get("notice_fingerprints")


def test_module_failure_never_records_previous_session(monkeypatch):
    """handler読込前の障害も、現在のpayloadの会話だけへ記録する。"""
    original = importlib.import_module

    def fail(name, *args, **kwargs):
        if name == "agent_toolkit._hooks.pretooluse":
            raise ImportError("モジュール読込の故障注入")
        return original(name, *args, **kwargs)

    notice.set_warning_session_id("previous-payload")
    monkeypatch.setattr(importlib, "import_module", fail)
    result = _run_hook_in_process("pretooluse", {"session_id": "current-payload"})
    assert result.returncode == 0 and not result.stderr
    assert "モジュール読込の故障注入" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert session_state.read_state("current-payload")["notice_fingerprints"]
    assert not session_state.read_state("previous-payload").get("notice_fingerprints")


@pytest.mark.parametrize(
    ("subcommand", "module_name", "function", "payload"),
    [
        ("pretooluse", "termination_evidence", "observe_tool", {"tool_name": "Read", "tool_input": {}}),
        ("posttooluse", "termination_evidence", "observe_tool", {"tool_name": "Read", "tool_input": {}}),
        ("user_prompt_submit", "termination_evidence", "observe_user", {"prompt": "確認する"}),
        ("user_prompt_submit", "work_contract", "observe_input", {"prompt": "確認する"}),
    ],
)
def test_internal_observation_failure_uses_one_marked_context(monkeypatch, subcommand, module_name, function, payload):
    """個別処理が捕捉する内部障害も、単一JSONの追加コンテキストへ配送する。"""
    module = importlib.import_module("agent_toolkit._hooks." + module_name)

    def fail(*_args, **_kwargs):
        raise OSError("観測の故障注入")

    monkeypatch.setattr(module, function, fail)
    result = _run_hook_in_process(subcommand, {"session_id": "internal-failure", **payload})
    assert result.returncode == 0
    assert not result.stderr
    response = json.loads(result.stdout)
    context = response["hookSpecificOutput"]["additionalContext"]
    assert f'source="{subcommand}" kind="warn"' in context
    assert "観測の故障注入" in context
    assert "次の操作:" in context
    assert response.get("decision") != "block"


@pytest.mark.skipif(os.name == "nt", reason="POSIXシェルでの起動コマンドの終了コードを確かめる")
@pytest.mark.parametrize(("name", "command"), _definition_commands())
def test_hook_launch_failure_exits_with_non_blocking_code(tmp_path: pathlib.Path, name: str, command: str) -> None:
    """起動スクリプトが無い配布rootでhookを起動すると、非遮断の終了コード1で終わる。

    スクリプトが無いとPythonは終了コード2で終わり、ホストはPreToolUseなどの2を遮断として扱う。
    """
    del name
    shell = shutil.which("sh")
    assert shell is not None
    launcher = "uv run --no-config --no-project --python 3.15 python "
    assert command.startswith(launcher)
    # uvの解決は実行環境（miseのshimなど）に左右されるため、同じPythonの起動に置き換えて末尾の変換だけを確かめる。
    resolved = (
        shlex.quote(sys.executable)
        + " "
        + command.removeprefix(launcher)
        .replace("${CLAUDE_PLUGIN_ROOT}", str(tmp_path))
        .replace("${PLUGIN_ROOT}", str(tmp_path))
    )
    without_suffix = subprocess.run(  # noqa: S603
        [shell, "-c", resolved.removesuffix(_HOOK_FAILURE_SUFFIX)], input="{}", capture_output=True, text=True, check=False
    )
    completed = subprocess.run([shell, "-c", resolved], input="{}", capture_output=True, text=True, check=False)  # noqa: S603
    assert without_suffix.returncode == 2, without_suffix.stderr
    assert completed.returncode == 1, completed.stderr
    assert "launch.py" in completed.stderr


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
    for name in ("notice.py", "message_format.py", "agent_id.py"):
        (hooks / name).write_text((source_hooks / name).read_text(encoding="utf-8"), encoding="utf-8")
    return entrypoint


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
        assert "[pretooluse] 想定外エラー: RuntimeError: boom" in context
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


def test_native_subagent_stop_keeps_next_process_loop_session_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開hookで子のStopを処理した後も、公開CLIが次のセッションを起動する。"""
    monkeypatch.setattr(session_activity, "scan_activity", lambda **_kwargs: session_activity.ActivitySnapshot())
    notes = _setup_notes(tmp_path)
    myrepo = git_repository.init_repository(tmp_path / "myrepo", origin="https://github.com/example/myrepo.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(myrepo)]))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    entries: list[pathlib.Path] = []

    def add_entry(number: int) -> None:
        path = notes / "2026/10" / f"native-stop-{number}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/myrepo\n---\n\n# 作業\n",
            encoding="utf-8",
        )
        entries.append(path)

    add_entry(1)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _path: True)
    monkeypatch.setattr(_pl_watch, "wait_for_start_permission", lambda *_args: True)
    monkeypatch.setattr(_pl_session, "resolve_orchestrator_specs", lambda: [("claude", "model", "medium")])
    monkeypatch.setattr(
        _pl_session, "select_available_orchestrator", lambda *_args, **_kwargs: ("claude", "model", "medium", None)
    )
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

    def fake_cli_session(
        cmd: list[str], _env: dict[str, str], cwd: pathlib.Path, **_kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert cmd[0] == "claude" and cwd == pathlib.Path.cwd()
        prompt = next(argument for argument in cmd if argument.startswith("/goal "))
        start = prompt.index("開始時入力のmanifest ") + len("開始時入力のmanifest ")
        manifest = pathlib.Path(prompt[start : prompt.index(" で取得済み候補", start)])
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert len(data["repositories"]) == 1
        target = data["repositories"][0]
        assert pathlib.Path(target["worktree"]) == myrepo
        candidates = json.loads(pathlib.Path(target["candidate_file"]).read_text(encoding="utf-8"))
        assert [candidate["filename"] for candidate in candidates] == [entries[-1].name]
        if not session_calls:
            for count in range(1, 4):
                response = invoke_stop("agent-review", count)
                assert response.get("decision") != "block"
                child_responses.append(response)
            assert not abort_path.exists()
        session_calls.append(cmd)
        entry = entries[-1]
        entry.write_text(entry.read_text(encoding="utf-8").replace("state: inbox", "state: adopted"), encoding="utf-8")
        records = {target["target_repo"]: {"status": "completed", "writer_sessions": []}}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": records}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(_pl_session, "_run_cli_session", fake_cli_session)

    def stop_wait(*_args: object, **_kwargs: object) -> bool:
        if len(session_calls) == 1:
            add_entry(2)
            return True
        raise KeyboardInterrupt

    monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)

    with pytest.raises(SystemExit) as loop_exit:
        atk.main(["wi", "process-loop", "--no-update", "--no-alerts"], home=tmp_path)

    assert loop_exit.value.code == 0
    assert len(session_calls) == 2
    identifiers = [command[command.index("--session-id") + 1] for command in session_calls]
    assert identifiers[0] != identifiers[1]
    assert len(child_responses) == 3
    assert all("systemMessage" not in response for response in child_responses)
    assert not abort_path.exists()
    assert not termination_calls

    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    parent_responses = [invoke_stop(None, count) for count in range(1, 4)]
    assert "systemMessage" in parent_responses[-1]
    assert not abort_path.exists()
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
        directory = managed_temp.resolve_session_temp("lazy-session")
        (directory / "work-contract.md").write_text("目的: 依存読込の検証\n", encoding="utf-8")
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
    """同じプロセス内で公開hookを呼び、対象の出力を返す。"""
    del monkeypatch
    result = _run_hook_in_process(subcommand, payload)
    assert result.returncode == expected_code
    return result.stderr if expected_code == 2 else result.stdout


@pytest.mark.parametrize("host", ["claude", "codex", "agy", "opencode"])
@pytest.mark.parametrize("command", ["pretooluse", "posttooluse", "stop", "permissionrequest"])
def test_public_hook_owns_one_inventory_scope_per_session(tmp_path, monkeypatch, command, host):
    """各handlerの消費側で一覧を共有し、別sessionと例外後の要求を混ぜない。"""
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: str(executable))
    monkeypatch.setattr(_hook, "_record_activity", lambda *_args, **_kwargs: None)
    acquired = []
    observed = []
    fail = False

    # 設定済みmarketplaceが無いCodexでは、marketplace一覧の取得だけで導入情報の取得を終える。
    codex_cli = codex_plugin_cli([], marketplaces=[])

    def inventory(args, **kwargs):
        acquired.append(args)
        if host == "codex":
            return codex_cli(args, **kwargs)
        return subprocess.CompletedProcess(args, 0, "[]", "")

    def native(_host):
        acquired.append(_host)
        return []

    def consume(_module, _text, payload, _args):
        _plugin_inventory(host, tmp_path)
        _plugin_inventory(host, tmp_path)
        cache = _reusable.get()
        observed.append((payload["session_id"], cache is not None))
        if fail:
            raise RuntimeError("消費側の失敗")
        return 0

    monkeypatch.setattr(resource_roots.process_tree, "run_with_timeout", inventory)
    monkeypatch.setattr(resource_roots, "_native_inventory", native)
    monkeypatch.setattr(_hook, "_invoke_module", consume)
    for session_id in ("one", "one", "two"):
        _call_hook_in_process(monkeypatch, command, {"session_id": session_id, "hook_event_name": "PostToolUse"})
    assert observed == [("one", True), ("one", True), ("two", True)]
    assert len(acquired) == (2 if host == "claude" else 3)
    fail = True
    _call_hook_in_process(monkeypatch, command, {"session_id": "three", "hook_event_name": "PostToolUse"})
    assert _inventories.get() is None and _reusable.get() is None
    fail = False
    _call_hook_in_process(monkeypatch, command, {"session_id": "four", "hook_event_name": "PostToolUse"})
    assert len(acquired) == (4 if host == "claude" else 5)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"session_id": ""},
        {"session_id": 1},
        {"session_id": "../other"},
        {"session_id": "one", "hook_event_name": "SessionStart"},
    ],
)
def test_public_hook_does_not_attach_persistent_inventory_to_invalid_or_start_payload(monkeypatch, payload):
    seen = []
    monkeypatch.setattr(_hook, "_record_activity", lambda *_args, **_kwargs: None)

    def consume(*_args):
        seen.append(_reusable.get())
        return 0

    monkeypatch.setattr(_hook, "_invoke_module", consume)
    result = _run_hook_in_process("pretooluse", payload)
    assert result.returncode == 0
    assert seen == [None]
    assert _inventories.get() is None and _reusable.get() is None


@pytest.mark.parametrize("codex", [False, True])
def test_repeated_calls_in_one_process_follow_input_and_session_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, codex: bool
) -> None:
    """依存を読み込んだ後の反復呼び出しでも、スキルの取得の判定と未配送通知の判定が入力と状態に従う。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    empty_transcript = tmp_path / "empty.jsonl"
    empty_transcript.write_text("", encoding="utf-8")
    for session in ("repeat-session", "other-session"):
        directory = managed_temp.resolve_session_temp(session)
        (directory / "work-contract.md").write_text("目的: 操作反復の検証\n", encoding="utf-8")

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
                **({"turn_id": "repeat-turn"} if codex else {}),
            },
            expected_code=expected_code,
        )

    # 関係する初回操作に資料または読取の案内を届け、通常の操作を継続する。
    context = pretooluse("repeat-session", "grep -rn needle .")
    message = json.loads(context)["hookSpecificOutput"]["additionalContext"]
    assert 'kind="block"' not in message
    pending = pretooluse("repeat-session", "ls")
    assert "未取得資料" in json.loads(pending)["hookSpecificOutput"]["additionalContext"]
    assert '<atk-auto source="read-delivery" kind="document">' not in message
    if not codex:
        assert "agent-basics:search" in message
    pretooluse("repeat-session", "grep -rn needle .")
    for _attempt in range(12):
        records = session_state.read_state("repeat-session")[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
        targets = [
            target
            for target in read_delivery.selected_targets(records, "main")
            if not read_delivery.target_ready(records, target, "main")
        ]
        if not targets:
            assert _attempt > 0
            break
        for target in targets:
            skill = read_delivery.skill_name(target.path)
            event: dict[str, object] = {
                "session_id": "repeat-session",
                "cwd": str(tmp_path),
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash" if codex else "Skill" if skill else "Read",
                "tool_input": {"command": f"cat -- {shlex.quote(str(target.path))}"}
                if codex
                else {"skill": skill}
                if skill
                else {"file_path": str(target.path)},
                "tool_response": {"stdout": target.path.read_text(encoding="utf-8"), "exit_code": 0},
                **({"turn_id": "repeat-turn"} if codex else {}),
            }
            reading = {key: value for key, value in event.items() if key not in {"hook_event_name", "tool_response"}}
            assert not _call_hook_in_process(monkeypatch, "pretooluse", reading)
            if codex:
                event = codex_execution_payload(event, tmp_path, exit_code=0)
            _call_hook_in_process(monkeypatch, "posttooluse", event)
            acquired = session_state.read_state("repeat-session")[read_delivery.rules_context.OPERATION_SKILL_READY_KEY]
            assert read_delivery.target_ready(acquired, target, "main")
        pretooluse("repeat-session", "ls")
    else:
        pytest.fail("成功した全文取得後も未取得資料が残った")
    assert not pretooluse("repeat-session", "ls")
    assert not pretooluse("repeat-session", "grep -rn needle .")
    assert pretooluse("other-session", "grep -rn needle .") == context

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
