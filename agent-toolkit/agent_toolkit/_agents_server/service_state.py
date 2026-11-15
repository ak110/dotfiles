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

_RUNTIME_FIELDS = {"turn_control_lock", "_registration_task", "_registration_lock"}


class _StateCodec(DataclassCodec):
    def __init__(self) -> None:
        super().__init__(
            {
                "resume": state.SessionResumeState,
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


def snapshot(session: state.SessionState) -> dict[str, Any]:
    """所有側の保存完了後に呼び、保留・期限・追跡・配送段階を損失なく写す。"""
    if session._registration_task is not None and not session._registration_task.done():
        raise RuntimeError("session登録の保存完了前には移譲できません")
    codec = _StateCodec()
    return {
        field.name: codec.encode(getattr(session, field.name))
        for field in dataclasses.fields(session)
        if field.name not in _RUNTIME_FIELDS
    }


def restore(payload: dict[str, Any]) -> state.SessionState:
    """受信側の既知schemaだけを復元し、同期資源と公開差分cacheは受信側で作る。"""
    allowed = {field.name for field in dataclasses.fields(state.SessionState)} - _RUNTIME_FIELDS
    if set(payload) != allowed:
        raise ValueError("移譲元と移譲先のsession状態schemaが一致しません")
    codec = _StateCodec()
    values = {name: codec.decode(value) for name, value in payload.items()}
    session = state.SessionState(**values)
    # 新しいwriterが既存登録簿へ所有を公開する。内容のcacheだけで保存を省略しない。
    for field in dataclasses.fields(session):
        if field.name.startswith("_published_registry_"):
            default = field.default
            if default is not dataclasses.MISSING:
                setattr(session, field.name, default)
    return session
