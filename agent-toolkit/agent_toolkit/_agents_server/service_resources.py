"""所有元に固定した生I/Oと、移譲可能な制御所有権を分離する。"""

from __future__ import annotations

# JSONの組込み型と許可したSDK型だけを受理し、同名の派生型を混ぜない。
# pylint: disable=unidiomatic-typecheck
import asyncio
import dataclasses
import inspect
import math
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast


class ResourceBroker:
    """移譲中のI/Oを完走させ、未保存イベントへbackpressureを掛ける。"""

    def __init__(self, origin_generation: str, *, owner_identity: str | None = None) -> None:
        self.origin_generation = origin_generation
        self.owner_identity = owner_identity or origin_generation
        self._resources: dict[str, dict[str, Any]] = {}
        self._event_limit = 256

    def register(self, operations: dict[str, Callable[..., Awaitable[Any]]]) -> str:
        """closeを含む生I/O操作を、所有元世代に登録する。"""
        if "close" not in operations or any(not callable(value) for value in operations.values()):
            raise ValueError("資源にはcloseを含む非同期操作が必要です")
        identifier = f"{self.origin_generation}:{uuid.uuid4()}"
        self._resources[identifier] = {
            "owner": self.owner_identity,
            "operations": dict(operations),
            "lock": asyncio.Lock(),
            "condition": asyncio.Condition(),
            "events": [],
            "sequence": 0,
            "released": False,
            "moving": False,
            "closing": False,
            "inflight": 0,
        }
        return identifier

    def _owned(self, resource: dict[str, Any], owner: str) -> None:
        if resource["owner"] != owner:
            raise PermissionError("資源の制御所有世代が一致しません")

    async def call(self, resource_id: str, owner_generation: str, operation: str, arguments: dict) -> Any:
        arguments = _json_value(arguments)
        if type(arguments) is not dict:
            raise TypeError("I/O操作の引数にはobjectが必要です")
        resource = self._resources[resource_id]
        async with resource["condition"]:
            await resource["condition"].wait_for(lambda: not resource["moving"])
            self._owned(resource, owner_generation)
            if resource["released"]:
                raise PermissionError("資源は解放されています")
            if operation == "close":
                raise ValueError("closeはreleaseから呼び出してください")
            function = resource["operations"][operation]
            resource["inflight"] += 1
        try:
            return _json_value(await function(**arguments))
        finally:
            async with resource["condition"]:
                resource["inflight"] -= 1
                resource["condition"].notify_all()

    async def transfer(self, resource_id: str, expected_owner: str, new_owner: str) -> bool:
        resource = self._resources[resource_id]
        async with resource["lock"], resource["condition"]:
            if resource["released"] or resource["owner"] != expected_owner:
                return False
            resource["moving"] = True
            try:
                await resource["condition"].wait_for(lambda: resource["inflight"] == 0)
                resource["owner"] = new_owner
                return True
            finally:
                resource["moving"] = False
                resource["condition"].notify_all()

    async def emit(self, resource_id: str, payload: Any) -> None:
        payload = _json_value(payload)
        resource = self._resources[resource_id]
        async with resource["condition"]:
            await resource["condition"].wait_for(
                lambda: resource["released"] or resource["closing"] or len(resource["events"]) < self._event_limit
            )
            if resource["released"]:
                raise PermissionError("資源は解放されています")
            if len(resource["events"]) >= self._event_limit:
                # 解放中は空きを待たない。読む主体が止まると空きは生じず、解放はこの操作の終了を待ち続ける。
                raise PermissionError("解放中の資源のイベントが上限に達しています")
            resource["sequence"] += 1
            resource["events"].append({"sequence": resource["sequence"], "payload": payload})
            resource["condition"].notify_all()

    def backlog(self, resource_id: str) -> tuple[int, int] | None:
        """未確認イベント数と、emitが空きを待ち始める上限を返す。解放済みの資源はNoneを返す。"""
        resource = self._resources.get(resource_id)
        return None if resource is None else (len(resource["events"]), self._event_limit)

    async def events(self, resource_id: str, owner_generation: str, after_sequence: int, wait: float = 0) -> list[dict]:
        resource = self._resources[resource_id]
        async with resource["condition"]:
            self._owned(resource, owner_generation)
            if wait and not any(event["sequence"] > after_sequence for event in resource["events"]):
                try:
                    async with asyncio.timeout(wait):
                        await resource["condition"].wait_for(
                            lambda: (
                                resource["released"]
                                or resource["owner"] != owner_generation
                                or any(event["sequence"] > after_sequence for event in resource["events"])
                            )
                        )
                except TimeoutError:
                    return []
                self._owned(resource, owner_generation)
            return _json_value([event for event in resource["events"] if event["sequence"] > after_sequence])

    async def acknowledge(self, resource_id: str, owner_generation: str, sequence: int) -> None:
        resource = self._resources[resource_id]
        async with resource["condition"]:
            self._owned(resource, owner_generation)
            if sequence < 0 or sequence > resource["sequence"]:
                raise ValueError("確認するsequenceが資源の範囲外です")
            resource["events"] = [event for event in resource["events"] if event["sequence"] > sequence]
            if resource["released"] and not resource["events"]:
                self._resources.pop(resource_id, None)
            resource["condition"].notify_all()

    async def discard(self, resource_id: str, owner_generation: str) -> None:
        """解放後に読む主体の無くなった未確認イベントを捨て、資源の登録を除く。除かれた資源には何もしない。"""
        resource = self._resources.get(resource_id)
        if resource is None:
            return
        async with resource["condition"]:
            self._owned(resource, owner_generation)
            if not resource["released"]:
                raise PermissionError("解放前の資源のイベントは捨てられません")
            resource["events"].clear()
            self._resources.pop(resource_id, None)
            resource["condition"].notify_all()

    async def release(self, resource_id: str, owner_generation: str) -> None:
        resource = self._resources[resource_id]
        async with resource["lock"]:
            async with resource["condition"]:
                self._owned(resource, owner_generation)
                if resource["released"]:
                    raise PermissionError("資源は解放されています")
                resource["moving"] = resource["closing"] = True
                resource["condition"].notify_all()
                try:
                    await resource["condition"].wait_for(lambda: resource["inflight"] == 0)
                except BaseException:
                    resource["moving"] = resource["closing"] = False
                    resource["condition"].notify_all()
                    raise
            try:
                await resource["operations"]["close"]()
                resource["released"] = True
                resource["operations"].clear()
                if not resource["events"]:
                    self._resources.pop(resource_id, None)
            finally:
                async with resource["condition"]:
                    resource["moving"] = resource["closing"] = False
                    resource["condition"].notify_all()


def _json_value(value: Any) -> Any:
    """可変値を複製し、JSONで意味を保てない値を拒否する。"""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return [_json_value(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise TypeError(f"JSON互換値ではありません: {type(value).__name__}")


class DataclassCodec:
    """明示したSDK公開型の対応表だけから、全フィールドを往復する。"""

    def __init__(self, types: dict[str, type]) -> None:
        if any(not inspect.isclass(cls) or not dataclasses.is_dataclass(cls) for cls in types.values()):
            raise TypeError("対応表には公開dataclass型を指定してください")
        if len(set(types.values())) != len(types):
            raise ValueError("同じ型を複数の名前に登録できません")
        self._types = dict(types)
        self._names = {cls: name for name, cls in types.items()}

    def _field_names(self, cls: type) -> tuple[str, ...]:
        return tuple(field.name for field in dataclasses.fields(cast(Any, cls)))

    def encode(self, value: Any) -> Any:
        if type(value) in self._names:
            return {
                "kind": "dataclass",
                "type": self._names[type(value)],
                "fields": {name: self.encode(getattr(value, name)) for name in self._field_names(type(value))},
            }
        if type(value) is dict:
            if not all(type(key) is str for key in value):
                raise TypeError("SDKの辞書キーには文字列が必要です")
            return {"kind": "dict", "items": {key: self.encode(item) for key, item in value.items()}}
        if type(value) is list:
            return {"kind": "list", "items": [self.encode(item) for item in value]}
        if type(value) is tuple:
            return {"kind": "tuple", "items": [self.encode(item) for item in value]}
        return {"kind": "value", "value": _json_value(value)}

    def decode(self, value: Any) -> Any:
        if not isinstance(value, dict):
            raise TypeError("codecの値にはobjectが必要です")
        kind = value["kind"]
        if kind == "value":
            return _json_value(value["value"])
        if kind in ("list", "tuple"):
            items = [self.decode(item) for item in value["items"]]
            return tuple(items) if kind == "tuple" else items
        if kind == "dict":
            return {key: self.decode(item) for key, item in value["items"].items()}
        if kind == "dataclass":
            cls = self._types[value["type"]]
            fields = value["fields"]
            allowed = set(self._field_names(cls))
            declared = (field for field in dataclasses.fields(cast(Any, cls)) if field.name in allowed)
            # SDKの更新で省略できるフィールドが増えても、旧版が保存したイベントを読めるようにする。
            required = {
                field.name
                for field in declared
                if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
            }
            if not required <= set(fields) <= allowed:
                raise ValueError("SDKイベントのフィールドが一致しません")
            return cls(**{key: self.decode(item) for key, item in fields.items()})
        raise ValueError("未対応のcodec種別です")


def sdk_event_codec() -> DataclassCodec:
    """実行オプションやcallbackを除き、SDK公開イベントと内容の型を許可する。"""
    import claude_agent_sdk as sdk  # pylint: disable=import-outside-toplevel  # Claude接続時だけSDK型を読み込む。

    names = (
        "AssistantMessage",
        "UserMessage",
        "SystemMessage",
        "ResultMessage",
        "StreamEvent",
        "TextBlock",
        "ThinkingBlock",
        "ToolUseBlock",
        "ToolResultBlock",
        "ServerToolUseBlock",
        "ServerToolResultBlock",
        "DeferredToolUse",
        "RateLimitEvent",
        "RateLimitInfo",
        "TaskNotificationMessage",
        "TaskProgressMessage",
        "TaskStartedMessage",
        "TaskUpdatedMessage",
        "HookEventMessage",
        "ConversationResetMessage",
        "MirrorErrorMessage",
    )
    return DataclassCodec({name: getattr(sdk, name) for name in names})
