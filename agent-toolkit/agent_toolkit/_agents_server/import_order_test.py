"""各モジュールを新しいプロセスで最初に読み込んでも循環importで失敗しないことを確かめる。

同じプロセスの試験は先に読み込まれたモジュールの順序に依存し、入口ごとの初期化順の循環を見逃す
（2026-11-17、codexを最初に読み込む管理サービスの起動だけが失敗した）。
"""

import subprocess
import sys

import pytest

# 起動経路が最初に読み込むモジュール（管理サービス・転送・MCP・CLI・各backend）。
_ENTRY_MODULES = (
    "service",
    "service_forward",
    "mcp_tools",
    "commands",
    "manager",
    "codex",
    "claude",
    "opencode",
    "antigravity",
)


@pytest.mark.cpu_limit(15.0, reason="起動経路ごとに新しいPythonを起動して初期化順を確かめる")
def test_entry_modules_import_first_in_a_fresh_process() -> None:
    """起動経路のモジュールを1つずつ最初に読み込み、失敗したモジュールと原因を列挙する。"""
    failures = []
    for name in _ENTRY_MODULES:
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", f"import agent_toolkit._agents_server.{name}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            failures.append(f"{name}: {result.stderr.strip().splitlines()[-1] if result.stderr.strip() else result.returncode}")
    assert not failures, "\n".join(failures)
