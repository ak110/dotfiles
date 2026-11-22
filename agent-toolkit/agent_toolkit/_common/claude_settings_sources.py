"""Claudeのユーザー・project・local設定の所在を共有する。"""

from __future__ import annotations

import json
import pathlib

from agent_toolkit._common import host_homes


def paths(repo: pathlib.Path, *, project: bool = True) -> tuple[pathlib.Path, ...]:
    user = (host_homes.claude_config_dir() / "settings.json",)
    return user + (repo / ".claude/settings.json", repo / ".claude/settings.local.json") if project else user


def _loaded(cwd: str | pathlib.Path | None, project: bool) -> list[object]:
    repo = pathlib.Path(cwd) if cwd is not None else pathlib.Path.cwd()
    if project:
        repo = next((path for path in (repo, *repo.parents) if (path / ".git").exists()), repo)
    loaded: list[object] = []
    for path in paths(repo, project=project):
        try:
            loaded.append(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            continue
    return loaded


def environment_keys(cwd: str | pathlib.Path | None, *, project: bool = True) -> frozenset[str]:
    keys: set[str] = set()
    for settings in _loaded(cwd, project):
        env = settings.get("env", {}) if isinstance(settings, dict) else {}
        if isinstance(env, dict):
            keys.update(env)
    return frozenset(keys)


def setting_keys(cwd: str | pathlib.Path | None, *, project: bool = True) -> frozenset[str]:
    return frozenset(key for settings in _loaded(cwd, project) if isinstance(settings, dict) for key in settings)
