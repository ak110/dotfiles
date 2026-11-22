from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import json
import pathlib
import typing
from collections.abc import AsyncGenerator
from types import TracebackType
from typing import Any

from agent_toolkit._agents_server import backends, launch_requests, resume_waits, shared_layout, state
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError


def install_backend(manager: server_manager.AgentsServerManager, engine: str, backend: object) -> None:
    manager._backends[engine] = typing.cast(backends.Backend, backend)  # pylint: disable=protected-access


_FORBIDDEN_PUBLIC_KEYS = {"turn_id", "result_available"}


_REAL_PLUGIN_PREFLIGHT = launch_requests._check_plugin_commands_sync


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        assert _FORBIDDEN_PUBLIC_KEYS.isdisjoint(value)
        for nested in value.values():
            _assert_no_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_forbidden_keys(nested)


def _actionable_message(error: BaseException) -> str:
    assert isinstance(error, ActionableError)
    assert f"\n{NEXT_ACTION_PREFIX}" in error.message
    return error.message


def _complete(session: state.SessionState, *, message: str = "完了", error: Any = None) -> None:
    session.status = "failed" if error is not None else "completed"
    session.agent_message = message
    session.error = error
    session.record_turn_completed()
    session.touch()


def _default_identity_fields(engine: str = "codex") -> dict[str, object]:
    return {
        "engine": engine,
        "model": None,
        "effort": None,
        "model_type": None,
        "launch_identity": {
            "engine": engine,
            "model": None,
            "effort": None,
            "source": "launch_candidate",
        },
        "observed_identity": None,
    }


def _write_notice(directory: pathlib.Path, session_id: str, sequence: int, sent_at: str, body: str) -> None:
    """待機テスト用の未回収通知を保存する。"""
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "session_id": session_id, "sent_at": sent_at, "body": body}
    (directory / f"{session_id}.{sequence}.json").write_text(json.dumps(payload), encoding="utf-8")


class ManualObserver:
    """変更を試験から`mark_changed`で通知するための、OSの監視資源を使わないObserverの代用。

    実Observerはホストのinotifyインスタンスを消費し、上限に達した環境では監視が無効になる。
    無効になると`mark_changed`が変更を記録しないため、OSの通知を確かめない試験の合否がホストの資源量で変わる。
    OSの通知そのものや監視threadの終了を確かめる試験は、実Observerを使う。
    """

    ident = None

    def __init__(self) -> None:
        self.watches: set[tuple[str, bool]] = set()

    def schedule(self, _handler: Any, path: str, *, recursive: bool = False, **_kwargs: Any) -> tuple[str, bool]:
        self.watches.add((path, recursive))
        return path, recursive

    def unschedule(self, watch: tuple[str, bool]) -> None:
        self.watches.remove(watch)

    def remove_handler_for_watch(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class FakeBackend:
    """共有MCP層の契約だけを検証するバックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str, delivery: str = "reply_started") -> None:
        self.sessions = sessions
        self.engine = engine
        self.delivery = delivery
        self.interrupt_calls = 0
        self.send_calls = 0
        self.resume_calls: list[str] = []
        self.release_calls: list[str] = []
        self.start_calls: list[tuple[str | None, str | None, str]] = []
        self.prompts: list[str] = []

        self.publish_registry = False
        self.pending: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> FakeBackend:
        return self

    async def __aexit__(
        self, _exc_type: type[BaseException] | None, _exc: BaseException | None, _traceback: TracebackType | None
    ) -> None:
        await self.close()

    async def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "model": model,
                "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in efforts],
            }
            for model, efforts in (
                ("gpt-6-sol", ("medium", "high")),
                ("gpt-6-luna", ("medium", "xhigh")),
                ("gpt-5.6-terra", ("medium", "high")),
                ("gpt-6-astra", ("medium", "high")),
            )
        ]

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        model_type: str | None = None,
        launch_kind: state.LaunchKind = "delegate",
        excluded_candidates: frozenset[state.ModelCandidate] = frozenset(),
        request_prompt: str | None = None,
        result_contract_recovery_consumed: bool = False,
    ) -> state.SessionState:
        self.prompts.append(prompt)
        self.start_calls.append((model, effort, launch_kind))
        session_number = len(self.start_calls)
        session = state.SessionState(
            session_id=f"{self.engine}-session" if session_number == 1 else f"{self.engine}-session-{session_number}",
            cwd=cwd,
            model=model,
            effort=effort,
            engine=self.engine,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            prompt=request_prompt or "",
            result_contract_recovery_consumed=result_contract_recovery_consumed,
            turn_seq=1,
        )
        self.sessions[session.session_id] = session
        state.initialize_turn(session)
        return session

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        model_type: str | None = None,
        launch_kind: state.LaunchKind = "delegate",
        excluded_candidates: frozenset[state.ModelCandidate] = frozenset(),
        request_prompt: str | None = None,
        result_contract_recovery_consumed: bool = False,
        turn_seq: int = 0,
        fast_mode: bool | None = None,
        handoff_prompt: str = "",
    ) -> state.SessionState:
        del handoff_prompt

        async def accept_prompt(value: str) -> None:
            del value

        self.resume_calls.append(session_id)
        session = state.SessionState(
            session_id=session_id,
            cwd=cwd,
            model=model,
            effort=effort,
            engine=self.engine,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            prompt=request_prompt or "",
            result_contract_recovery_consumed=result_contract_recovery_consumed,
            turn_seq=turn_seq + 1,
            fast_mode=fast_mode,
            publish_registry=self.publish_registry,
        )
        self.sessions[session_id] = session
        state.initialize_turn(session)
        if self.publish_registry:
            session.touch()
        await prompt.deliver(accept_prompt)
        return session

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.send_calls += 1
        if resume_waits.HeldTurn.capture(session) is not None:
            state.begin_reply(session, preserve_waits=True)
            return {"delivery": self.delivery}
        if session.terminal:
            previous = session.previous_result()
            state.begin_reply(session)
            return {"delivery": self.delivery, "previous_result": previous}
        return {"delivery": "steered"}

    async def interrupt(self, session: state.SessionState) -> None:
        del session
        self.interrupt_calls += 1

    async def release_session(self, session: state.SessionState) -> None:
        self.release_calls.append(session.session_id)

    async def close(self) -> None:
        tasks = tuple(self.pending)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
            if failures:
                for failure in failures[1:]:
                    failures[0].add_note(f"背景処理の別の失敗: {failure!r}")
                raise failures[0]


def _without_body_path(result: dict[str, Any]) -> dict[str, Any]:
    body = dict(result)
    assert pathlib.Path(body.pop("agent_message_path")).read_text(encoding="utf-8") == body["agent_message"]
    return body


def _without_root(response: dict[str, Any]) -> dict[str, Any]:
    body = dict(response)
    assert shared_layout.valid_session_id(body.pop("root_session_id"))
    return body


@contextlib.asynccontextmanager
async def _manager_with_fake(
    engine: str, delivery: str = "reply_started"
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, FakeBackend]]:
    async with server_manager.AgentsServerManager() as manager:
        backend = FakeBackend(manager.sessions, engine, delivery)
        _install_backend(manager, engine, backend)
        yield manager, backend


def _install_backend(
    manager: server_manager.AgentsServerManager, engine: str, backend: FakeBackend | claude_backend.ClaudeServerManager
) -> None:
    if engine == "codex":
        install_backend(manager, "codex", backend)
    elif engine == "agy":
        install_backend(manager, "agy", backend)
    else:
        install_backend(manager, "claude", backend)


def _set_wait_timeout(manager: server_manager.AgentsServerManager, timeout: float) -> None:
    """導出を経由せずに指定したmanagerの待機上限を確定する。"""
    manager._wait_timeouts["main"] = timeout
