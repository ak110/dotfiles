from __future__ import annotations

import contextlib
import contextvars
import dataclasses
from collections.abc import Callable, Generator
from typing import Any

from agent_toolkit._common.process_identity import ProcessIdentity


@dataclasses.dataclass(frozen=True)
class PublicationIdentity:
    owner: ProcessIdentity | None
    server_instance: str | None
    effort_receiver: ProcessIdentity | None


_IDENTITY: contextvars.ContextVar[PublicationIdentity | None] = contextvars.ContextVar("registry_owner", default=None)
_FORWARD: contextvars.ContextVar[Callable[[str, dict[str, Any]], tuple[bool, Any]] | None] = contextvars.ContextVar(
    "registry_forwarding", default=None
)


def identity() -> PublicationIdentity | None:
    return _IDENTITY.get()


def forward(operation: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    callback = _FORWARD.get()
    return (False, None) if callback is None else callback(operation, arguments)


@contextlib.contextmanager
def bind(
    callback: Callable[[str, dict[str, Any]], tuple[bool, Any]] | None,
    *,
    owner: PublicationIdentity | None = None,
) -> Generator[None]:
    publication = _IDENTITY.set(owner)
    forwarding = _FORWARD.set(callback)
    try:
        yield
    finally:
        _FORWARD.reset(forwarding)
        _IDENTITY.reset(publication)
