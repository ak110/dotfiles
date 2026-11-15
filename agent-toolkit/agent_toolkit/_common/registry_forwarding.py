"""旧世代の観測を現行writerへ渡し、記録する制御所有者と書込主体を分ける。"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
from collections.abc import Callable, Generator
from typing import Any

from agent_toolkit._common.process_identity import ProcessIdentity


@dataclasses.dataclass(frozen=True)
class PublicationIdentity:
    """現行writerが代理保存する、実際の制御所有者の識別。"""

    owner: ProcessIdentity | None
    server_instance: str | None
    effort_receiver: ProcessIdentity | None


_IDENTITY: contextvars.ContextVar[PublicationIdentity | None] = contextvars.ContextVar("registry_owner", default=None)
_FORWARD: contextvars.ContextVar[Callable[[str, dict[str, Any]], tuple[bool, Any]] | None] = contextvars.ContextVar(
    "registry_forwarding", default=None
)


def identity() -> PublicationIdentity | None:
    """代理保存で明示した所有者を返す。通常のCLIではNone。"""
    return _IDENTITY.get()


def forward(operation: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    """現行世代に移った管理だけを転送し、通常の保存は従来の経路で行う。"""
    callback = _FORWARD.get()
    return (False, None) if callback is None else callback(operation, arguments)


@contextlib.contextmanager
def bind(
    callback: Callable[[str, dict[str, Any]], tuple[bool, Any]] | None,
    *,
    owner: PublicationIdentity | None = None,
) -> Generator[None]:
    """workerにも継承される範囲で、代理保存または旧側の転送を選ぶ。"""
    publication = _IDENTITY.set(owner)
    forwarding = _FORWARD.set(callback)
    try:
        yield
    finally:
        _FORWARD.reset(forwarding)
        _IDENTITY.reset(publication)
