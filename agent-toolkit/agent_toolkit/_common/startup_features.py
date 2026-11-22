"""起動単位で止める追加機能を、CLIとSDK・App Serverで共有する。"""

import contextlib
import contextvars
from collections.abc import Generator
from typing import Any, Literal

type StartupProfile = Literal["lightweight", "default", "full"]
_REQUESTED: contextvars.ContextVar[StartupProfile | None] = contextvars.ContextVar("startup_profile", default=None)

CODEX_DISABLED = (
    "apps",
    "remote_plugin",
    "image_generation",
    "skill_mcp_dependency_install",
    "plugin_sharing",
    "skill_search",
)

CLAUDE_SETTINGS = {
    "syncClaudeAiPlugins": False,
    "syncClaudeAiSkills": False,
    "disableClaudeAiConnectors": True,
    "includeGitInstructions": False,
}


def codex_arguments() -> list[str]:
    """CLIからApp Serverへ渡さずに起動する場合の上書き。"""
    return [argument for feature in CODEX_DISABLED for argument in ("--disable", feature)]


def codex_thread_config(params: dict[str, Any]) -> dict[str, Any]:
    """他のfeaturesと接続・承認設定を保持して、追加機能だけを無効にする。"""
    config = params.get("config") or {}
    return {
        **params,
        "config": {**config, "features": {**(config.get("features") or {}), **dict.fromkeys(CODEX_DISABLED, False)}},
    }


@contextlib.contextmanager
def selection(profile: StartupProfile | None) -> Generator[None]:
    """委譲の起動要求ごとに指定を保持し、同時実行に混ぜない。"""
    token = _REQUESTED.set(profile)
    try:
        yield
    finally:
        _REQUESTED.reset(token)


def requested() -> StartupProfile | None:
    """起動要求で指定された値だけを返す。"""
    return _REQUESTED.get()


def effective(launch_kind: str, requested_profile: StartupProfile | None = None) -> StartupProfile:
    """作業の種別は変えず、独立した起動プロファイルを決める。"""
    profile = requested_profile or requested()
    lightweight_allowed = launch_kind in {"explore", "shell", "write"}
    if profile == "lightweight" and not lightweight_allowed:
        raise ValueError("lightweightは探索・書込・定型実行に限る。defaultかfullを指定する")
    return profile or ("lightweight" if lightweight_allowed else "default")
