"""全ツール呼び出しで動くhookが読み込むモジュール数を、登録した予算で守る。

モジュールの読込はhook 1回の所要の大きな部分を占める（2026-11-17の計測ではpydanticだけで約0.2秒）。
判定に使わない重い依存の追加を、実時間ではなく決定的に数えられる読込数で検出する。
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

_HOOK = pathlib.Path(__file__).resolve().parent / "hook.py"
# 2026-11-20のBash lsの実測はPreToolUse 254件、PostToolUse 253件（読込表の配送と受信の追跡を撤去した後）。
# 予算を引き上げる変更は、増えた依存と必要な理由をここへ記録する。
_MODULE_BUDGETS = {"pretooluse": 280, "posttooluse": 280}


@pytest.mark.skipif(os.name == "nt", reason="読込数の予算はLinuxの実測で定める")
@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
def test_plain_command_hook_stays_within_module_budget(tmp_path: pathlib.Path, event: str) -> None:
    """通常のBashコマンドのPre・PostToolUseは予算内のモジュールだけを読み込み、承認判定器や委譲観測の依存を読まない。"""
    subcommand = event.lower()
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
    environment = dict(os.environ, XDG_STATE_HOME=str(tmp_path / "state"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-X", "importtime", str(_HOOK), subcommand],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    modules = [line.rsplit("|", 1)[-1].strip() for line in result.stderr.splitlines() if line.startswith("import time:")]
    assert "pydantic" not in modules
    if event == "PostToolUse":
        assert "agent_toolkit._common.task_documents" not in modules
        assert "agent_toolkit._common.effort" not in modules
    budget = _MODULE_BUDGETS[subcommand]
    assert len(modules) <= budget, f"{event}の読込{len(modules)}件が予算{budget}件を超えた"
