"""委譲先で使うプラグイン起動コマンドの事前確認。"""

from __future__ import annotations

import asyncio
import subprocess

from agent_toolkit._common.next_action import ActionableError

# 委譲先のClaude Code・Codexがプラグインを起動するコマンド。MCP設定（`.mcp.json`・`.mcp.codex.json`・`mcp.json`）の
# `command`と`hooks/hooks.json`のhookの先頭語、およびCodexのhook起動器が内部で呼ぶ`uv run`を覆う。
# 委譲先は同じPATHと作業ディレクトリからこれらを解決するため、作業ディレクトリの設定（未trustのmise設定など）で
# 失敗する状態を子の起動前に検出する。起動後に失敗すると、Claude Codeはプラグインの接続失敗をホスト共通の記録へ残し、
# 同じ設定のサーバーへの接続を15分間試みない。
PREFLIGHT_COMMANDS: tuple[tuple[str, ...], ...] = (("uv", "--version"), ("uvx", "--version"))


# 事前確認の1コマンドあたりの上限秒数。成功した2コマンドの連続実行に約0.14秒かかることを確認した。

PREFLIGHT_TIMEOUT = 20.0


def _run_preflight_command(command: tuple[str, ...], cwd: str) -> tuple[str, str] | None:
    """1件の事前確認コマンドを実行し、失敗時は失敗内容の説明と原因に応じた次の操作を返す。"""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PREFLIGHT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return (
            f"command={' '.join(command)} error=実行ファイルが見つからない",
            "agents_serverを起動したホストのPATHで`uv`と`uvx`を解決できるかを確かめてから再実行する",
        )
    except subprocess.TimeoutExpired:
        return (
            f"command={' '.join(command)} error={PREFLIGHT_TIMEOUT:g}秒以内に終了しない",
            "時間をおいて再実行するか、`cwd`を別の作業ディレクトリへ変えて再実行する",
        )
    if completed.returncode != 0:
        return (
            f"command={' '.join(command)} exit_code={completed.returncode} stderr={completed.stderr.strip()}",
            "未trustのmise設定が原因の場合は、委譲元で`mise trust`の要否を判断してから再実行する。"
            "それ以外はstderrの原因を解消するか、`cwd`を変えて再実行する",
        )
    return None


def _check_plugin_commands_sync(cwd: str) -> None:
    """委譲先の作業ディレクトリでプラグインの起動コマンドが動くことを確かめる。"""
    for command in PREFLIGHT_COMMANDS:
        failure = _run_preflight_command(command, cwd)
        if failure is not None:
            description, next_action = failure
            raise ActionableError(
                "委譲先の作業ディレクトリでプラグインの起動コマンドが失敗したため委譲先を起動しない: "
                f"cwd={cwd} {description}。",
                next_action=next_action,
            )


async def check_plugin_commands(cwd: str) -> None:
    """事前確認をイベントループの外で実行する。"""
    await asyncio.to_thread(_check_plugin_commands_sync, cwd)
