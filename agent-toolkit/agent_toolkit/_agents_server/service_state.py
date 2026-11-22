"""世代間で保留理由と追跡集合を保ち、実行中taskの所有は移さない。"""

from __future__ import annotations

# 明示した状態型だけを往復し、SessionStateの保存taskを含む内部状態も検収する。
# pylint: disable=protected-access,unidiomatic-typecheck
import dataclasses
import enum
from typing import Any

from agent_toolkit._agents_server import state, turn_state
from agent_toolkit._agents_server.service_resources import DataclassCodec
from agent_toolkit._common import claude_usage_limit, session_registry

_NON_WIRE_FIELDS = {
    "turn_control_lock",
    "_registration_task",
    "_registration_lock",
    "_active_item",
    "_effort_resume",
    "_overload_retry",
    "_usage_limit_retry",
}
_STATE_PROJECTIONS = (
    "current_item",
    "current_item_started_at",
    "effort_resume_turn",
    "effort_resuming",
    "overload_resume_count",
    "overload_first_at",
    "usage_limit_wait_count",
    "usage_limit_first_at",
)


class _StateCodec(DataclassCodec):
    def __init__(self) -> None:
        super().__init__(
            {
                "resume": state.SessionResumeState,
                "registry_resume": state.RegistryResumeState,
                "live_task": state.LiveTask,
                "background": turn_state.BackgroundWait,
                "overload": turn_state.OverloadWait,
                "usage": turn_state.UsageLimitWait,
                "claude_connection": turn_state.ClaudeConnectionWait,
                "codex_provider": turn_state.CodexProviderWait,
                "result_contract": turn_state.ResultContractWait,
                "interrupted": turn_state.InterruptedWait,
                "usage_limit": claude_usage_limit.UsageLimitState,
                "process": session_registry.ProcessIdentity,
                "launch": session_registry.LaunchInfo,
            }
        )
        self._enums = {
            "turn_start": turn_state.TurnStart,
            "turn_end": turn_state.TurnEnd,
            "publication": session_registry.PublicationState,
        }

    def _field_names(self, cls: type) -> tuple[str, ...]:
        names = super()._field_names(cls)
        if issubclass(cls, state.SessionResumeState):
            # 別版の受信側も同じフィールド集合を使うため、表示用の値は送らない。
            return tuple(name for name in names if name != "last_action")
        return names

    def encode(self, value: Any) -> Any:
        if isinstance(value, enum.Enum):
            name = next((name for name, kind in self._enums.items() if type(value) is kind), None)
            if name is None:
                raise TypeError("未対応のsession状態enum")
            return {"kind": "enum", "type": name, "name": value.name}
        if type(value) in (set, frozenset):
            return {"kind": "frozenset" if type(value) is frozenset else "set", "items": [self.encode(item) for item in value]}
        return super().encode(value)

    def decode(self, value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("kind") == "enum":
                return self._enums[value["type"]][value["name"]]
            if value.get("kind") in ("set", "frozenset"):
                items = [self.decode(item) for item in value["items"]]
                return frozenset(items) if value["kind"] == "frozenset" else set(items)
        return super().decode(value)


def _read_retry(count: object, first_at: object) -> state.RetryChain | None:
    return state.RetryChain(count, first_at) if type(count) is int and count > 0 and isinstance(first_at, str) else None


async def snapshot(session: state.SessionState) -> dict[str, Any]:
    """登録公開の完了を待ってから、保留・期限・追跡・配送段階を損失なく写す。

    完了を待つ間に、届いた通知が新しい登録公開を始めることがある。
    未保存の登録を写して移譲しないよう、未完了が無いことを確かめてから待機なしで写す。
    2026-11-17、世代移譲の状態保存の応答を待つ間に始まった公開を写せず、継続指示の実行が失敗した。
    """
    while session.registration_pending:
        await session.drain_registration()
    codec = _StateCodec()
    fields = [field.name for field in dataclasses.fields(session) if field.name not in _NON_WIRE_FIELDS]
    # 稼働中の旧世代も厳密に同じキーを要求する。
    return {name: codec.encode(getattr(session, name)) for name in (*fields, *_STATE_PROJECTIONS)}


def restore(payload: dict[str, Any]) -> state.SessionState:
    """受信側の既知schemaだけを復元し、同期資源と公開差分cacheは受信側で作る。"""
    dataclass_fields = {field.name for field in dataclasses.fields(state.SessionState)}
    allowed = (dataclass_fields - _NON_WIRE_FIELDS) | set(_STATE_PROJECTIONS)
    if set(payload) != allowed:
        raise ValueError("移譲元と移譲先のsession状態schemaが一致しません")
    codec = _StateCodec()
    values = {name: codec.decode(value) for name, value in payload.items()}
    item = values.pop("current_item")
    started_at = values.pop("current_item_started_at")
    active_item = state.ActiveItem(item, started_at) if isinstance(item, dict) and isinstance(started_at, str) else None
    pending = values.pop("effort_resume_turn")
    resuming = values.pop("effort_resuming")
    effort_resume = (
        turn_state.EffortResumeRunning()
        if resuming
        else turn_state.EffortResumePending(pending)
        if pending is not None
        else None
    )
    overload = _read_retry(values.pop("overload_resume_count"), values.pop("overload_first_at"))
    usage = _read_retry(values.pop("usage_limit_wait_count"), values.pop("usage_limit_first_at"))
    session = state.SessionState(
        _active_item=active_item, _effort_resume=effort_resume, _overload_retry=overload, _usage_limit_retry=usage, **values
    )
    # 新しいwriterが既存登録簿へ所有を公開する。内容のcacheだけで保存を省略しない。
    for field in dataclasses.fields(session):
        if field.name.startswith("_published_registry_"):
            default = field.default
            if default is not dataclasses.MISSING:
                setattr(session, field.name, default)
    return session
