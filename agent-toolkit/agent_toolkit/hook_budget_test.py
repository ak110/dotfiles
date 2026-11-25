import json
import os
import pathlib
import subprocess
import sys

import pytest

_HOOK = pathlib.Path(__file__).resolve().parent / "hook.py"
# 2026-11-23のBash lsの実測はPreToolUse 230件、PostToolUse 231件
# （活動記録と`managed_temp`の読込から登録簿本体の読込を外した後）。
# 予算を引き上げる変更は、増えた依存と必要な理由をここへ記録する。
_MODULE_BUDGETS = {"PreToolUse": 250, "PostToolUse": 250}
# 登録簿の本体と期限掃引は、登録簿の照会と一時領域の回収だけが使う。通常のツール呼び出しの活動記録は、
# 定義元の`process_identity`だけでホスト祖先を記録する。
_UNUSED_ON_PLAIN_CALLS = ("agent_toolkit._common.session_registry", "agent_toolkit._atk.managed_temp.sweep")


def _loaded_modules(tmp_path: pathlib.Path, arguments: list[str], payload: dict) -> list[str]:
    environment = dict(os.environ, XDG_STATE_HOME=str(tmp_path / "state"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-X", "importtime", str(_HOOK), *arguments],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [line.rsplit("|", 1)[-1].strip() for line in result.stderr.splitlines() if line.startswith("import time:")]


def _assert_within_budget(event: str, modules: list[str]) -> None:
    assert "pydantic" not in modules
    assert [name for name in _UNUSED_ON_PLAIN_CALLS if name in modules] == []
    budget = _MODULE_BUDGETS[event]
    assert len(modules) <= budget, f"{event}の読込{len(modules)}件が予算{budget}件を超えた"


@pytest.mark.skipif(os.name == "nt", reason="読込数の予算はLinuxの実測で定める")
@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
def test_plain_command_hook_stays_within_module_budget(tmp_path: pathlib.Path, event: str) -> None:
    payload = {
        "session_id": "00000000-0000-4000-8000-000000000001",
        "transcript_path": str(tmp_path / "none.jsonl"),
        "cwd": str(tmp_path),
        "hook_event_name": event,
        "tool_name": "Bash",
        "tool_input": {"command": "ls", "description": "List"},
    }
    if event == "PostToolUse":
        payload["tool_response"] = {"stdout": "a", "stderr": "", "interrupted": False}
    modules = _loaded_modules(tmp_path, [event.lower()], payload)
    if event == "PreToolUse":
        assert "agent_toolkit._hooks.stop_session" not in modules
        assert "agent_toolkit._hooks.message_format" not in modules
    else:
        assert "agent_toolkit._common.task_documents" not in modules
        assert "agent_toolkit._common.effort" not in modules
    _assert_within_budget(event, modules)


@pytest.mark.skipif(os.name == "nt", reason="読込数の予算はLinuxの実測で定める")
@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
def test_antigravity_plain_command_hook_stays_within_module_budget(tmp_path: pathlib.Path, event: str) -> None:
    """agyの通常のコマンド実行のPre・PostToolUseも、Claude Codeと同じ予算と読まない依存の制約を守る。"""
    call: dict[str, object] = {"name": "run_command", "args": {"CommandLine": "ls"}}
    if event == "PostToolUse":
        call["result"] = '{"stdout":"a"}'
    payload = {
        "conversationId": "00000000-0000-4000-8000-000000000001",
        "workspacePaths": [str(tmp_path)],
        "transcriptPath": str(tmp_path / "transcript.jsonl"),
        "toolCall": call,
    }
    _assert_within_budget(event, _loaded_modules(tmp_path, ["antigravity", event], payload))
