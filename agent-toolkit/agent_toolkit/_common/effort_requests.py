"""相対effort要求をCLIから所有MCPへ渡す有限期限のプロトコル。"""

from __future__ import annotations

import json
import os
import pathlib
import time
import uuid
from collections.abc import Mapping
from typing import Any

import psutil

from agent_toolkit._common import atomic_file, delegated_session, process_identity, session_layout, session_registry


def _actor_session(environment: Mapping[str, str], state_root: pathlib.Path | None) -> str | None:
    """会話IDを渡さないCLIでは、同じルート内の最も近い実行祖先から本人を確定する。"""
    actor = delegated_session.current_conversation_session_id(environment)
    if actor:
        return actor
    root = delegated_session.owner_session_id(environment)
    if root is None or not session_layout.valid_session_id(root) or not delegated_session.is_delegated(environment):
        return None
    process = psutil.Process(os.getpid())
    ancestors = [process, *process_identity.parents(process)]
    matched: set[tuple[int, str]] = set()
    for path in session_layout.list_status_files(root, state_root, strict=True):
        value = json.loads(path.read_text(encoding="utf-8"))
        for item in value.get("sessions", []):
            identifier = item.get("session_id") if isinstance(item, dict) else None
            if not isinstance(identifier, str) or not session_layout.valid_session_id(identifier):
                continue
            resolution = session_registry.resolve(identifier, state_root=state_root)
            if resolution.resume_info is None or resolution.resume_info.engine not in {"agy", "opencode"}:
                continue
            for distance, ancestor in enumerate(ancestors):
                if any(identity.matches(ancestor) for identity in resolution.executor_processes):
                    matched.add((distance, identifier))
    if not matched:
        return None
    nearest = min(distance for distance, _identifier in matched)
    identifiers = {identifier for distance, identifier in matched if distance == nearest}
    if len(identifiers) != 1:
        raise ValueError("呼出元の会話ID候補が複数あるため本人を確定できない")
    return next(iter(identifiers))


def authorized(actor: str, target: str, payload: Mapping[str, Any]) -> bool:
    """自身と、登録済みの直接親だけを許可する。"""
    return actor == target or actor == payload.get(session_registry.LAUNCHER_KEY)


def request(
    direction: str, target: str | None, environment: Mapping[str, str], *, state_root: pathlib.Path | None = None
) -> dict[str, Any]:
    """所有MCPへ要求し、応答か期限切れを返す。応答不明時に自動再送しない。"""
    deadline = time.monotonic() + 30
    actor = _actor_session(environment, state_root)
    root = delegated_session.owner_session_id(environment)
    # モデルはstartの可用性確認中にもツールを呼ぶ。announcedになるまで同じ要求の期限内で待つ。
    while (
        actor is None
        and root is not None
        and session_layout.valid_session_id(root)
        and session_layout.status_directory(root, state_root).is_dir()
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)
        actor = _actor_session(environment, state_root)
    if not actor:
        raise ValueError("呼出元の会話IDを確認できない。自身または直接親の会話から実行する")
    target = target or actor
    with session_registry.registry_lock(target, state_root=state_root):
        path = session_registry.registry_directory(state_root) / f"{target}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not authorized(actor, target, payload):
        raise ValueError("effortを変更できるのは自身と直接の親だけ")
    if direction not in {"up", "down"}:
        raise ValueError("effort操作はupかdownを指定する")
    processes = payload.get("processes", [])
    if not processes:
        raise ValueError("所有MCPを確認できない。親へ同じ会話の再開を依頼する")
    declaration = payload.get("effort_receiver")
    supported = False
    try:
        owner = session_registry.ProcessIdentity.parse(processes[0])
        if isinstance(declaration, dict) and declaration.get("version") == 1:
            receiver = session_registry.ProcessIdentity.parse(declaration.get("process"))
            current = session_registry.ProcessIdentity.of(owner.pid)
            supported = receiver == owner and current == owner
    except ValueError, TypeError, KeyError:
        pass
    if not supported:
        return {
            "session_id": target,
            "error": "所有MCPの稼働プロセスはeffort要求に未対応、または対応する世代を確認できない",
            "request_sent": False,
            "next_action": "要求は未送信。他の委譲先の作業と会話を保護してから所有MCPを更新・再起動する",
        }
    token = uuid.uuid4().hex
    request_path = path.parent / f"{token}.effort-request"
    response_path = request_path.with_suffix(".effort-response")
    packet = {
        "version": 1,
        "session_id": target,
        "actor": actor,
        "direction": direction,
        "owner": processes[0],
        "deadline": deadline,
    }
    try:
        atomic_file.atomic_write(request_path, json.dumps(packet, ensure_ascii=False) + "\n")
        while time.monotonic() < deadline:
            if response_path.is_file():
                response = json.loads(response_path.read_text(encoding="utf-8"))
                if not isinstance(response, dict) or response.get("session_id") != target:
                    raise ValueError("effort応答の対象を確認できない")
                return response
            time.sleep(0.05)
        raise TimeoutError("effort変更の応答を確認できない。atk agents showで現在値を確認し、自動で再送しない")
    finally:
        request_path.unlink(missing_ok=True)
        response_path.unlink(missing_ok=True)


def pending(*, state_root: pathlib.Path | None = None) -> list[tuple[pathlib.Path, dict[str, Any]]]:
    """この所有プロセスへ宛てた未応答の要求だけを読む。"""
    owner = session_registry.owner_process()
    if owner is None:
        return []
    result = []
    for path in session_registry.registry_directory(state_root).glob("*.effort-request"):
        if path.with_suffix(".effort-response").exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                continue
            if not session_registry.ProcessIdentity.parse(payload.get("owner")).compatible_with(owner):
                continue
            if not isinstance(payload.get("deadline"), (int, float)) or payload["deadline"] <= time.monotonic():
                continue
            result.append((path, payload))
        except OSError, ValueError, TypeError, KeyError:
            continue
    return result


def respond(path: pathlib.Path, response: dict[str, Any]) -> None:
    """要求がまだ保持されている場合だけ、同名の応答を原子的に書く。"""
    if path.exists():
        atomic_file.atomic_write(path.with_suffix(".effort-response"), json.dumps(response, ensure_ascii=False) + "\n")
