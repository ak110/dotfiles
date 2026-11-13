"""hook要求内の入力解析結果を共有する。"""

from __future__ import annotations

import contextvars
import functools
import json
import os
from collections.abc import Callable
from typing import cast

from agent_toolkit._common import read_triggers
from agent_toolkit._common.plugin_roots import (
    plugin_resolution_scope,
)

_INPUT_CACHE: contextvars.ContextVar[dict[tuple, object] | None] = contextvars.ContextVar(
    "read_delivery_input_cache", default=None
)
HOOK_PAYLOAD: contextvars.ContextVar[dict | None] = contextvars.ContextVar("read_delivery_payload", default=None)

type _HookEntry[Result] = Callable[[dict, str, dict, str], Result]


def _input_scope[R](function: _HookEntry[R]) -> _HookEntry[R]:
    """同じhook内の不変入力だけを共有し、次の操作では資料・cwd・主体を再評価する。

    宣言一覧・取得済み資料の宣言の再解決・plugin導入情報の照会も、同じ要求内では結果を共有する。
    共有は1回のhook呼出しで閉じ、セッション間のcacheや常駐サービスは持たない。
    """

    @functools.wraps(function)
    def scoped(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> R:
        token = _INPUT_CACHE.set({})
        payload_token = HOOK_PAYLOAD.set(payload)
        try:
            with plugin_resolution_scope(session_id=session_id), read_triggers.resolution_scope():
                return function(payload, tool_name, tool_input, session_id)
        finally:
            HOOK_PAYLOAD.reset(payload_token)
            _INPUT_CACHE.reset(token)

    return scoped


def _input_cached[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(function)
    def cached(*args: P.args, **kwargs: P.kwargs) -> R:
        cache = _INPUT_CACHE.get()
        if cache is None:
            return function(*args, **kwargs)
        key = (function, json.dumps((args, kwargs), sort_keys=True, default=os.fspath))
        if key not in cache:
            cache[key] = function(*args, **kwargs)
        return cast(R, cache[key])

    return cached
