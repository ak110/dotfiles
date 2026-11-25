import asyncio
from typing import Any

from agent_toolkit._agents_server import backends, state
from agent_toolkit._testing.agents_server_support import FakeBackend


async def wait_for_checkpoint(entered: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(entered.wait(), 5)
    except TimeoutError:
        # 通知後も、待機の再開より先にハング検知の期限が到来し得る。  # pyfltr: allow-comment
        if not entered.is_set():
            raise


class WindowsReplaceError(OSError):
    winerror: int

    def __init__(self, winerror: int) -> None:
        super().__init__("共有違反")
        self.winerror = winerror


class StartedBackend(FakeBackend):
    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model_output_observed = True
        session.publish_registry = True
        return session


class UnavailableBackend(FakeBackend):
    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        del args, kwargs
        raise backends.StartCandidateUnavailable("exploreSandboxNamespaceDenied")


class TerminalUnavailableBackend(StartedBackend):
    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.status = "failed"
        session.record_turn_completed()
        session.error = {"codexErrorInfo": "usageLimitExceeded"}
        session.touch()
        return session
