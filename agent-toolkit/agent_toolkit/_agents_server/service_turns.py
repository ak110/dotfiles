"""実行中turnを止めず、受付世代の公開前に新turnの生成を静止させる。"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import uuid
from collections.abc import AsyncGenerator
from typing import Any


class MoveRequired(BaseException):
    """未配送の入力を現行世代へ渡す。通常のbackend失敗と区別する。"""

    def __init__(self, target: dict[str, Any]) -> None:
        super().__init__("新しいturnの受付先が変更されました")
        self.target = dict(target)


@dataclasses.dataclass(frozen=True)
class Ticket:
    """受付済み操作をSDK所有taskへ引き継ぐための非永続の予約。"""

    identifier: str
    generation: str
    new_turn: bool
    session_id: str | None


_TICKET: contextvars.ContextVar[Ticket | None] = contextvars.ContextVar("agents_server_turn_ticket", default=None)
_GATE: contextvars.ContextVar[TurnGate | None] = contextvars.ContextVar("agents_server_turn_gate", default=None)


class TurnGate:
    """新turnを生む操作の回収と、長く続くモデル実行を別に管理する。"""

    def __init__(self, generation: str, target: dict[str, Any]) -> None:
        self.generation = generation
        self.current = dict(target)
        self._condition = asyncio.Condition()
        self._freezes: set[str] = set()
        self._tickets: dict[str, tuple[Ticket, int]] = {}
        self._paused: set[str] = set()

    def capture(self) -> Ticket | None:
        """現在も実行中の予約だけを、別の所有taskへ渡す。"""
        ticket = _TICKET.get()
        return ticket if ticket is not None and ticket.identifier in self._tickets else None

    @contextlib.asynccontextmanager
    async def admit(
        self, *, new_turn: bool, session_id: str | None = None, ticket: Ticket | None = None
    ) -> AsyncGenerator[Ticket]:
        """受付済み操作を再利用し、静止後の新規生成は公開確定まで保留する。"""
        async with self._condition:
            inherited = ticket or self.capture()
            active = self._tickets.get(inherited.identifier) if inherited is not None else None
            if session_id in self._paused and active is None:
                raise MoveRequired(self.current)
            if new_turn:
                await self._condition.wait_for(
                    lambda: (
                        self.current["generation"] != self.generation
                        or not self._freezes
                        or active is not None
                        and active[0].new_turn
                    )
                )
                if self.current["generation"] != self.generation:
                    raise MoveRequired(self.current)
            if active is None:
                selected = Ticket(uuid.uuid4().hex, self.generation, new_turn, session_id)
                self._tickets[selected.identifier] = (selected, 1)
            elif new_turn and not active[0].new_turn:
                selected = Ticket(uuid.uuid4().hex, self.generation, True, session_id)
                self._tickets[selected.identifier] = (selected, 1)
            else:
                selected = active[0]
                self._tickets[selected.identifier] = (selected, active[1] + 1)
        current_token = _TICKET.set(selected)
        gate_token = _GATE.set(self)
        try:
            yield selected
        finally:
            _GATE.reset(gate_token)
            _TICKET.reset(current_token)
            async with self._condition:
                selected, references = self._tickets[selected.identifier]
                if references == 1:
                    del self._tickets[selected.identifier]
                else:
                    self._tickets[selected.identifier] = (selected, references - 1)
                self._condition.notify_all()

    async def freeze(self, token: str) -> None:
        """モデルの終了を待たず、新turn生成の受付済み操作だけを回収する。"""
        async with self._condition:
            self._freezes.add(token)
            await self._condition.wait_for(lambda: not any(ticket.new_turn for ticket, _ in self._tickets.values()))

    async def thaw(self, token: str) -> None:
        """準備失敗した1起動だけを解除し、他の更新の静止を維持する。"""
        async with self._condition:
            self._freezes.discard(token)
            self._condition.notify_all()

    async def observe_current(self, target: dict[str, Any]) -> None:
        """遅れた通知で受付を戻さず、待機中の入力へ確定した移譲先を示す。"""
        async with self._condition:
            if target["epoch"] >= self.current["epoch"]:
                self.current = dict(target)
                self._condition.notify_all()

    async def drain_session(self, session_id: str) -> None:
        """旧backendへ入った制御要求の保存・応答を回収してから状態を移す。"""
        async with self._condition:
            await self._condition.wait_for(
                lambda: not any(ticket.session_id == session_id for ticket, _ in self._tickets.values())
            )

    async def pause_session(self, session_id: str, *, drain: bool = True) -> None:
        """後着の操作を未配送で返す。同じ所有者で通知を再開する場合は配送済みの操作を待たない。"""
        async with self._condition:
            self._paused.add(session_id)
        if drain:
            await self.drain_session(session_id)

    async def resume_session(self, session_id: str) -> None:
        """所有を変更していない移譲の失敗だけを解除する。"""
        async with self._condition:
            self._paused.discard(session_id)
            self._condition.notify_all()


def current() -> TurnGate | None:
    """共有管理で予約を付けた処理だけに、所有元の受付制御を公開する。"""
    return _GATE.get()


@contextlib.contextmanager
def bind(gate: TurnGate):
    """受付世代の判定を、生成する制御taskにも引き継ぐ。"""
    token = _GATE.set(gate)
    try:
        yield
    finally:
        _GATE.reset(token)
