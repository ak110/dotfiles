import asyncio
import typing
from collections.abc import Callable

import quart

from agent_toolkit._atk.serve.agent_records import agent_ids, locate_agent_records
from agent_toolkit._atk.serve.sessions_models import SessionsContext
from agent_toolkit._atk.serve.sessions_remote import _remote_call, is_safe_remote_record_path
from agent_toolkit._atk.serve.shell_routes import json_no_store, no_store, render_index, safe_base_path
from agent_toolkit._atk.wi.web_input import WebInputError


def _attach_record(
    row: dict[str, typing.Any],
    records: dict[str, list[dict[str, str]]],
    host: str,
    *,
    unavailable: bool,
) -> None:
    engine = row.get("engine")
    candidates = [record for record in records.get(row["id"], []) if not engine or engine == record["engine"]]
    if len(candidates) == 1:
        row["record"] = {**candidates[0], "host": host}
    elif engine and engine not in {"claude", "codex"}:
        row["record_error"] = "このエンジンの記録はセッション画面で扱いません。"
        row["record_unsupported"] = True
    elif unavailable:
        row["record_error"] = "記録の所在を確認できません。次の一覧取得で再確認します。"
    elif candidates:
        row["record_error"] = "同じIDの記録が複数あり、特定できません。"


AgentListing = Callable[[bool], list[dict[str, typing.Any]]]


def _empty_listing(_include_terminated: bool) -> list[dict[str, typing.Any]]:
    return []


def register_agent_routes(app: quart.Quart, context: SessionsContext, listing: AgentListing | None) -> None:
    @app.get("/agents")
    async def agents_index() -> quart.Response:
        return no_store(
            render_index(app.config["PLANS_CONTEXT"], safe_base_path(quart.request.root_path), "agents"),
            "text/html; charset=utf-8",
        )

    @app.get("/api/agents/hosts")
    async def agent_hosts() -> quart.Response:
        return json_no_store({"local": context.hostname, "remote": list(context.remote_hosts)})

    @app.get("/api/agents/list")
    async def agents_list() -> quart.Response:
        host = quart.request.args.get("host")
        include_terminated = quart.request.args.get("terminated", "0")
        if include_terminated not in {"0", "1"} or (host is not None and host not in context.remote_hosts):
            raise WebInputError("ホストまたは表示条件が不正です", next_action="設定済みのホストと表示条件を選び直す")
        unavailable = False
        if host is None:
            roots = await asyncio.to_thread(listing or _empty_listing, include_terminated == "1")
            try:
                records = await asyncio.to_thread(
                    locate_agent_records, context.claude_home, context.codex_home, agent_ids(roots)
                )
            except OSError:
                records = {}
                unavailable = True
        else:
            try:
                op = "agents-list-all" if include_terminated == "1" else "agents-list"
                payload = await _remote_call(context, host, op, {})
                roots = payload["roots"]
                if not isinstance(roots, list):
                    raise ValueError("一覧の形式が不正です")
            except Exception:  # noqa: BLE001
                app.logger.info("エージェント一覧を取得できません: %s", host, exc_info=True)
                response = json_no_store({"error": "エージェント一覧を取得できません。"})
                response.status_code = 503
                return response
            raw_records = payload.get("records")
            unavailable = not isinstance(raw_records, dict)
            records = (
                {
                    ident: [
                        item
                        for item in items
                        if isinstance(item, dict)
                        and item.get("engine") in ("claude", "codex")
                        and isinstance(item.get("path"), str)
                        and is_safe_remote_record_path(item["path"])
                    ]
                    for ident, items in raw_records.items()
                    if isinstance(items, list)
                }
                if isinstance(raw_records, dict)
                else {}
            )
        for row in roots:
            _attach_record(row, records, host or context.hostname, unavailable=unavailable)
            for child in row["children"]:
                _attach_record(child, records, host or context.hostname, unavailable=unavailable)
        return json_no_store({"roots": roots})
