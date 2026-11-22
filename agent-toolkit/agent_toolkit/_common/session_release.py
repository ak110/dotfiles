"""レーン回収の解放要求を所有MCPサーバーへ渡す、一時ファイルのプロトコル。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from agent_toolkit._common import path_ownership, registry_requests, session_layout, session_registry


class OwnedSession(Protocol):
    """所有側で解放前に再検収するsessionの属性。"""

    @property
    def terminal(self) -> bool:
        """所有側の現在の終了状態。"""
        ...

    @property
    def cwd(self) -> str:
        """所有側が保持する作業ディレクトリ。"""
        ...


def _record(session_id: str, state_root: pathlib.Path | None) -> tuple[str, tuple[session_registry.ProcessIdentity, ...]]:
    raw = (session_registry.registry_directory(state_root) / f"{session_id}.json").read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"session登録簿の形式を確認できない: {session_id}")
    processes = tuple(
        session_registry.ProcessIdentity(entry["pid"], entry["create_time"], entry.get("start_id"))
        for entry in payload.get("processes", [])
    )
    return hashlib.sha256(raw).hexdigest(), processes


def request_release(
    session_id: str,
    worktree: pathlib.Path,
    *,
    deadline: float,
    state_root: pathlib.Path | None = None,
    worktree_paths: tuple[pathlib.Path, ...] | None = None,
) -> str | None:
    """終了済みの資源の解放応答を期限まで待ち、保持が必要なら理由を返す。

    所有者のプロセスは登録順の先頭であり、解放後も存続する。後続の実行プロセスと、
    worktreeのcwd保持は呼出側の再走査と合わせて検収する。
    """
    resolution = session_registry.resolve(session_id, state_root=state_root)
    if resolution.state is not session_registry.Resolution.TERMINAL:
        return None
    digest, processes = _record(session_id, state_root)
    if not processes or not any(process.exists() for process in processes):
        return None
    request = session_registry.registry_directory(state_root) / f"{uuid.uuid4().hex}.release-request"
    response_path = request.with_suffix(".release-response")
    registry_requests.write_request(
        request,
        {
            "version": 1,
            "session_id": session_id,
            "worktree": str(worktree),
            "record_digest": digest,
            "deadline": deadline,
            "owner": processes[0].payload(legacy=True),
            "owner_identity": processes[0].payload(),
            **({"worktree_paths": [str(path) for path in worktree_paths]} if worktree_paths is not None else {}),
        },
    )
    started = time.monotonic()
    next_progress = started
    denial: PermissionError | None = None
    while True:
        current = session_registry.resolve(session_id, state_root=state_root)
        executing = any(process.exists() for process in processes[1:])
        if not executing and (current.state is session_registry.Resolution.RELEASED or not processes[0].exists()):
            return None
        if current.state is session_registry.Resolution.RUNNING:
            return "解放待機中に担当が再開したため保持した"
        if current.state is session_registry.Resolution.UNREADABLE:
            return "解放待機中の担当登録を読めないため保持した"
        text, denial = registry_requests.read_result(request, response_path)
        if text is not None:
            response = json.loads(text)
            if not isinstance(response, dict):
                return "解放応答の形式を検収できない"
            if response.get("session_id") != session_id or response.get("record_digest") != digest:
                return "解放応答の対象を検収できない"
            if response.get("error"):
                return f"所有側が解放を完了できない: {response['error']}"
            released = session_registry.resolve(session_id, state_root=state_root)
            if released.state is not session_registry.Resolution.RELEASED:
                return "解放応答後の登録簿が解放済みを示さない"
            if not any(process.exists() for process in processes[1:]):
                return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "期限までに所有側の解放応答と実行プロセスの終了を確認できない" + registry_requests.denial_detail(
                response_path, denial
            )
        now = time.monotonic()
        if now >= next_progress:
            print(
                f"session_id={session_id}: 解放待機中（経過{now - started:.1f}秒、残り{remaining:.1f}秒）",
                file=sys.stderr,
                flush=True,
            )
            next_progress = now + 5.0
        time.sleep(min(0.05, remaining))


def pending(state_root: pathlib.Path | None = None) -> list[tuple[pathlib.Path, dict[str, Any]]]:
    """稼働中の所有者に宛てた未処理の解放要求だけを返す。"""
    owner = session_registry.owner_process()
    if owner is None:
        return []
    result = []
    for path in registry_requests.request_paths(
        session_registry.registry_directory(state_root), registry_requests.RELEASE_REQUEST_SUFFIX
    ):
        try:
            payload = registry_requests.pending_payload(path, path.with_suffix(".release-response"))
            if payload is None or not isinstance(payload.get("owner"), dict):
                continue
            identity = session_registry.ProcessIdentity.from_payload(payload["owner"], stable=payload.get("owner_identity"))
            identifier = payload.get("session_id")
            if identity.compatible_with(owner) and isinstance(identifier, str) and session_layout.valid_session_id(identifier):
                result.append((path, payload))
        except OSError, ValueError, KeyError, TypeError:
            continue
    return result


async def process_requests(
    sessions: Mapping[str, OwnedSession],
    stop: Callable[..., Awaitable[dict[str, Any]]],
    *,
    state_root: pathlib.Path | None = None,
    requests: Sequence[tuple[pathlib.Path, dict[str, Any]]] | None = None,
) -> None:
    """自プロセスが所有する終了sessionの要求だけを検収し、結果を保持してstopする。"""
    for request, payload in pending(state_root) if requests is None else requests:
        response_path = request.with_suffix(".release-response")
        try:
            session_id = payload["session_id"]
            response = {"session_id": session_id, "record_digest": payload["record_digest"]}
            remaining = payload["deadline"] - time.monotonic()
            if remaining <= 0:
                continue
            resolution = session_registry.resolve(session_id, state_root=state_root)
            session = sessions.get(session_id)
            if session is None:
                # 要求の配送中に保持期限か別のstopが解放を完了した場合も、所有側の応答を返す。
                if resolution.state is session_registry.Resolution.RELEASED:
                    registry_requests.respond(request, response_path, response)
                else:
                    _digest, current_processes = _record(session_id, state_root)
                    original_owner = session_registry.ProcessIdentity.from_payload(
                        payload["owner"], stable=payload.get("owner_identity")
                    )
                    if current_processes and not current_processes[0].compatible_with(original_owner):
                        response["error"] = "要求後に所有または終了状態が変わった"
                        registry_requests.respond(request, response_path, response)
                continue
            digest, _processes = _record(session_id, state_root)
            if (
                not session.terminal
                or resolution.state is not session_registry.Resolution.TERMINAL
                or digest != payload["record_digest"]
                or path_ownership.owning_root(
                    pathlib.Path(session.cwd),
                    (pathlib.Path(value) for value in payload.get("worktree_paths", [payload["worktree"]])),
                )
                != pathlib.Path(payload["worktree"]).resolve()
            ):
                response["error"] = "要求後に所有または終了状態が変わった"
            else:
                try:
                    async with asyncio.timeout(remaining):
                        await stop(session_id, retain_result=True)
                except Exception as error:  # pylint: disable=broad-exception-caught
                    response["error"] = str(error) or type(error).__name__
            registry_requests.respond(request, response_path, response)
        except OSError, ValueError, KeyError, TypeError:
            # 消費済みの要求との競合や壊れた要求は、CLI側の有限期限と既存掃引で回収する。
            continue
