"""Claudeの初期化で失敗したMCP接続を、所有するSDKクライアントで1回回復する。"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")


async def recover_connections(
    client: Any, initialization: dict[str, Any], session_id: str, *, report_pending: Callable[[tuple[str, ...]], None]
) -> tuple[str, ...]:
    """起動プロセスごとに失敗接続を再試行し、未回復分だけを診断として返す。"""
    servers = {
        item["name"]: item
        for item in initialization.get("mcp_servers", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    status_method = getattr(client, "get_mcp_status", None)
    initial_error = ""
    if "mcp_servers" not in initialization and status_method is not None:
        try:
            status = await status_method()
            servers.update(
                {
                    item["name"]: item
                    for item in status.get("mcpServers", [])
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                }
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            initial_error = f"MCP接続状態を取得できない: {error}。委譲元が接続設定とサーバーの診断ログを確認する"
            _LOG.warning("claude_mcp_status_failed session_id=%s: %s", session_id, initial_error)
    failed = {name: item for name, item in servers.items() if item.get("status") == "failed"}
    if not failed:
        return (initial_error,) if initial_error else ()
    report_pending(
        tuple(
            f"MCP接続を回復中: {name}: {item.get('error') or '初期化でfailed'}。同じsessionのshowで回復結果を確認する"
            for name, item in sorted(failed.items())
        )
    )

    async def reconnect(name: str) -> tuple[str, str | None]:
        try:
            await client.reconnect_mcp_server(name)
        except Exception as error:  # pylint: disable=broad-exception-caught
            return name, str(error)
        return name, None

    outcomes = dict(await asyncio.gather(*(reconnect(name) for name in failed)))
    after: dict[str, dict[str, Any]] = {}
    status_error = ""
    if status_method is not None:
        try:
            status = await status_method()
            after = {
                item["name"]: item
                for item in status.get("mcpServers", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
        except Exception as error:  # pylint: disable=broad-exception-caught
            status_error = str(error)
    warnings: list[str] = []
    for name in sorted(failed):
        current = after.get(name, {})
        if current.get("status") == "connected":
            _LOG.info("claude_mcp_recovered session_id=%s server=%s", session_id, name)
            continue
        error = (
            current.get("error")
            or outcomes[name]
            or status_error
            or failed[name].get("error")
            or "再接続後のconnected状態を確認できない"
        )
        warning = f"MCP接続を回復できない: {name}: {error}。委譲元が接続設定とサーバーの診断ログを確認する"
        warnings.append(warning)
        _LOG.warning("claude_mcp_recovery_failed session_id=%s server=%s error=%s", session_id, name, error)
    return tuple(warnings)
