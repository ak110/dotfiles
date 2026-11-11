"""hookの契約テスト。"""

import importlib
import io
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

import pytest

from agent_toolkit import hook as _hook
from agent_toolkit._common import inherited_venv, plugin_environment, session_state
from agent_toolkit._hooks import notice
from agent_toolkit._testing.hook_support import _definition_commands
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"


_HOOK_DEFINITIONS = tuple(_SCRIPT.parents[1] / "hooks" / name for name in ("hooks.json", "hooks.codex.json"))


_HOOK_FAILURE_SUFFIX = " || exit 1"


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


def test_every_hook_command_converts_launch_failure_to_non_blocking() -> None:
    """全hookの起動コマンドが、起動失敗を非遮断へ変換する末尾を持つ。"""
    missing = [f"{name}: {command}" for name, command in _definition_commands() if not command.endswith(_HOOK_FAILURE_SUFFIX)]
    assert not missing


@pytest.mark.skipif(os.name == "nt", reason="登録コマンドをPOSIXシェルで検証する")
@pytest.mark.parametrize("definition", _HOOK_DEFINITIONS, ids=lambda path: path.name)
@pytest.mark.parametrize("case", ["pattern-kill", "unread-rewrite"])
def test_registered_pretooluse_command_preserves_denial_and_read_guidance(definition, tmp_path, case) -> None:
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
    if case == "unread-rewrite":
        specific = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert specific.get("permissionDecision") != "deny"
        assert "未取得" not in specific.get("additionalContext", "")
        return
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
