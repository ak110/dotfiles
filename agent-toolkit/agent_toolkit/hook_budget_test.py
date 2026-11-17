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
# 2026-11-17のPreToolUse（Bash ls）の実測は379件。予算を引き上げる変更は、増えた依存と必要な理由をここへ記録する。
_PRETOOLUSE_MODULE_BUDGET = 420


@pytest.mark.skipif(os.name == "nt", reason="読込数の予算はLinuxの実測で定める")
def test_pretooluse_for_plain_command_stays_within_module_budget(tmp_path: pathlib.Path) -> None:
    """通常のBashコマンドのPreToolUseは予算内のモジュールだけを読み込み、承認判定器の依存を読まない。"""
    payload = {
        "session_id": "00000000-0000-4000-8000-000000000001",
        "transcript_path": str(tmp_path / "none.jsonl"),
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls", "description": "List"},
    }
    environment = dict(os.environ, XDG_STATE_HOME=str(tmp_path / "state"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-X", "importtime", str(_HOOK), "pretooluse"],
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
    assert len(modules) <= _PRETOOLUSE_MODULE_BUDGET, f"読込{len(modules)}件が予算{_PRETOOLUSE_MODULE_BUDGET}件を超えた"
