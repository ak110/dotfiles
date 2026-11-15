"""共有管理サービスの呼出元環境を、taskと同期workerへ非永続で引き継ぐ。"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import os
import pathlib
import types
from collections.abc import Generator, Mapping


@dataclasses.dataclass(frozen=True)
class CallerContext:
    """呼出元の環境とcwd。認証値をreprや復旧記録へ含めない。"""

    environment: Mapping[str, str] = dataclasses.field(repr=False)
    cwd: pathlib.Path
    credit_shortages: dict[str, float] = dataclasses.field(default_factory=dict, repr=False, compare=False)
    connection_id: str | None = dataclasses.field(default=None, repr=False, compare=False)
    parent_settings: str | None = dataclasses.field(default=None, repr=False, compare=False)
    host_process: dict | None = dataclasses.field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", types.MappingProxyType(dict(self.environment)))
        if not self.cwd.is_absolute():
            raise ValueError("呼出元のcwdは絶対パスで指定する")


_CALLER: contextvars.ContextVar[CallerContext | None] = contextvars.ContextVar("agents_server_caller", default=None)


def current() -> CallerContext | None:
    """共有管理で明示的に結び付けた呼出元を返す。通常のCLIではNone。"""
    return _CALLER.get()


def environment() -> Mapping[str, str]:
    """呼出元の環境を返し、通常のCLIでは既存のプロセス環境を使う。"""
    caller = current()
    return os.environ if caller is None else caller.environment


def home() -> pathlib.Path:
    """明示環境のホームを返し、他会話のHOMEで補完しない。"""
    caller = current()
    if caller is None:
        return pathlib.Path.home()
    value = caller.environment.get("USERPROFILE" if os.name == "nt" else "HOME")
    if not value or not pathlib.Path(value).is_absolute():
        raise ValueError("呼出元環境のホームが不明。MCP接続から環境を再提供する")
    return pathlib.Path(value)


@contextlib.contextmanager
def bind(caller: CallerContext) -> Generator[None]:
    """task生成とto_threadが捕捉する範囲に呼出元を結び付ける。"""
    token = _CALLER.set(caller)
    try:
        yield
    finally:
        _CALLER.reset(token)
