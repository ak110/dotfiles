"""所有MCPの監視処理から、effort要求と自己変更後の1回の再開を処理する。"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState, utc_now
from agent_toolkit._common import effort, effort_requests, message_format, session_registry

_LOG = logging.getLogger("agent-toolkit.agents-server.effort")
# 返却形式の違反による自動引き上げを、本人・親の操作と区別して変更履歴へ残す操作主体。
_RESULT_CONTRACT_ACTOR = "agents-server"


async def process_requests(
    sessions: Mapping[str, SessionState],
    catalog: Callable[[str], Awaitable[list[dict[str, Any]]]],
    apply: Callable[[SessionState, str], Awaitable[None]],
    *,
    state_root: pathlib.Path | None = None,
) -> None:
    """所有する通常sessionを更新し、登録を永続化してから応答する。"""
    for path, payload in await run_file_operation(effort_requests.pending, state_root=state_root):
        session_id = payload.get("session_id")
        session = sessions.get(session_id) if isinstance(session_id, str) else None
        if session is not None and path.stem in session.effort_request_results:
            try:
                await run_file_operation(effort_requests.respond, path, session.effort_request_results[path.stem])
            except OSError:
                _LOG.exception("effort応答を保存できない。保存先の権限を確認する: path=%s", path)
            continue
        response: dict[str, Any] = {"session_id": session_id}
        try:
            if session is None:
                raise ValueError("所有MCPに対象sessionが無い。親へ同じ会話の再開を依頼する")
            actor = payload.get("actor")
            if not isinstance(actor, str) or not actor:
                raise ValueError("操作主体の会話IDが不正")
            record_path = session_registry.registry_directory(state_root) / f"{session_id}.json"
            record = json.loads(await run_file_operation(record_path.read_text, encoding="utf-8"))
            if not isinstance(record, dict) or not effort_requests.authorized(actor, session.session_id, record):
                raise ValueError("effortを変更できるのは自身と直接の親だけ")
            if session.launch_kind != "delegate" or session.model_type == "orchestrate":
                raise ValueError("effort操作はorchestrateを除くtask/delegateだけが対象")
            levels = session.effort_levels
            if not levels:
                async with asyncio.timeout(max(0.0, payload["deadline"] - time.monotonic())):
                    levels = (
                        effort.catalog_levels(session.model or "", await catalog(session.engine))
                        if session.engine == "codex"
                        else await run_file_operation(effort.cli_levels, session.engine, session.model)
                    )
            async with session.turn_control_lock:
                if time.monotonic() >= payload["deadline"] or not await run_file_operation(path.exists):
                    continue
                previous = session.effort
                direction = payload.get("direction")
                if not isinstance(direction, str):
                    raise ValueError("effort操作はupかdownを指定する")
                updated = effort.relative(previous, direction, levels)
                session.effort_levels = levels
                if updated != previous:
                    async with asyncio.timeout(max(0.0, payload["deadline"] - time.monotonic())):
                        await apply(session, updated)
                    session.effort = updated
                    session.effort_history += ((utc_now(), previous or "", updated, actor),)
                    if actor == session.session_id:
                        session.effort_resume_turn = session.turn_seq
                    session.touch()
                response.update(
                    previous_effort=previous,
                    effort=updated,
                    changed=updated != previous,
                    at_boundary=updated == previous,
                    applies="next_turn",
                    auto_resume=actor == session.session_id and updated != previous,
                )
            warning = await session.publish_registration_async()
            if warning is not None:
                raise OSError(f"effort変更後の登録を保存できない: {warning}")
            await session.drain_registration()
        except Exception as error:  # pylint: disable=broad-exception-caught
            response["error"] = str(error)
            response["next_action"] = "atk agents showで対象と現在値を確認し、受理段が不明なら親へモデルの対応確認を依頼する"
        if session is not None:
            session.effort_request_results[path.stem] = response
        try:
            await run_file_operation(effort_requests.respond, path, response)
        except OSError:
            _LOG.exception("effort応答を保存できない。次の監視で保存だけ再試行する: path=%s", path)


async def raise_for_result_contract(
    session: SessionState,
    catalog: Callable[[str], Awaitable[list[dict[str, Any]]]],
    apply: Callable[[SessionState, str], Awaitable[None]],
) -> tuple[str, str] | None:
    """返却形式の違反で回復入力を送る前に、通常委譲のeffortを1段上げる。

    低い推論量の担当が作業途中で返却形式外の本文を返したとき、同じ推論量のまま促しても同じ早期返却を繰り返しやすい。
    相対操作の対象（orchestrateを除くdelegate）だけを上げ、最上段・段の取得失敗では変更せずに回復入力を任せる。
    変更した場合は変更前後の値を返す。
    """
    if session.launch_kind != "delegate" or session.model_type == "orchestrate":
        return None
    try:
        levels = session.effort_levels
        if not levels:
            async with asyncio.timeout(30):
                levels = (
                    effort.catalog_levels(session.model or "", await catalog(session.engine))
                    if session.engine == "codex"
                    else await run_file_operation(effort.cli_levels, session.engine, session.model)
                )
        async with session.turn_control_lock:
            previous = session.effort
            updated = effort.relative(previous, "up", levels)
            session.effort_levels = levels
            if updated == previous:
                return None
            async with asyncio.timeout(30):
                await apply(session, updated)
            session.effort = updated
            session.effort_history += ((utc_now(), previous or "", updated, _RESULT_CONTRACT_ACTOR),)
            session.touch()
        warning = await session.publish_registration_async()
        if warning is not None:
            _LOG.warning("effort引き上げ後の登録を保存できない: session_id=%s %s", session.session_id, warning)
    except Exception:  # pylint: disable=broad-exception-caught
        # 引き上げは回復の補助であり、失敗しても回復入力は送る。原因は所有MCPのログで確かめる。
        _LOG.exception("返却形式の回復前にeffortを上げられない: session_id=%s", session.session_id)
        return None
    return previous or "", updated


async def resume_changed(
    sessions: Mapping[str, SessionState], send: Callable[[SessionState, str], Awaitable[dict[str, Any]]]
) -> None:
    """自己変更したturnの終了後だけ継続入力を1回送る。別経路が再開済みなら予約を除く。"""
    for session in tuple(sessions.values()):
        requested_turn = session.effort_resume_turn
        if requested_turn is None:
            continue
        if session.turn_seq != requested_turn or session.interrupt_requested:
            session.effort_resume_turn = None
            continue
        if not session.terminal or not session.turn_completed:
            continue
        # 応答喪失時にも重複送信せず、失敗はsessionに残して親へ返す。
        session.effort_resume_turn = None
        session.effort_resuming = True
        body = message_format.auto_message(
            "自身が要求したeffort変更を次のturnへ適用して同じ会話を再開した。"
            "引き継ぎ記録と直前の成果を確認し、残る作業を続けよ。",
            source="agents-server",
            kind="effort-resume",
        )
        try:
            async with asyncio.timeout(90):
                result = await send(session, body)
            if result.get("delivery") not in {"reply_started", "steered", "reply_ambiguous"}:
                raise RuntimeError(f"effort変更後の再開を確認できない: {result}")
        except Exception as error:  # pylint: disable=broad-exception-caught
            session.error = {
                "message": str(error),
                "nextAction": "atk agents showで状態を確認し、親からsend_messageで続きを指示する",
            }
            session.touch()
        finally:
            session.effort_resuming = False
            session.touch()
