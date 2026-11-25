from __future__ import annotations

import json
import pathlib
from typing import cast

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as config_module
from agent_toolkit._common import codex_models


def _recovery_old_values(*, multiple=False):
    values = {
        "review_engine": "opencode",
        "opencode_medium_tier_model": "openai/gpt-4.1",
        "write_model": "agy:gemini-4-argon/high",
        "codex_fast_mode": "true",
        "dotfiles_mode": "minimal",
        "other_setting": "keep",
        "review_fast_model": "claude:stale",
    }
    if multiple:
        values["chores_engine"] = "opencode"
    return values


_LEGACY_KEYS = ("high_tier_model", "medium_tier_model", "low_tier_model")

_DEFAULTS = {
    "execute_model": "codex:sol/medium,claude:opus[1m]/medium",
    "review_model": "codex:sol/medium,claude:sonnet[1m]/medium",
    "review_fast_model": "codex:luna/medium,claude:haiku/medium",
    "chores_model": "codex:sol/low,claude:sonnet[1m]/low",
    "chores_fast_model": "codex:luna/low,claude:haiku/low",
    "orchestrate_model": "claude:opus[1m]/medium,codex:sol/medium",
    "write_model": "agy:gemini-3.8-flash/low,claude:claude-opus-5-5/low",
}

_CONNECTION_KEYS = (
    "claude_orchestrator_connections",
    "claude_worker_connections",
    "codex_credits_provider",
    "codex_payg_provider",
    "codex_orchestrator_connections",
    "codex_worker_connections",
)


@pytest.fixture(autouse=True)
def _isolate_platformdirs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    monkeypatch.setattr(config_module.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))
    monkeypatch.setattr(config_module.platformdirs, "user_data_dir", lambda _name, **_kwargs: str(tmp_path / "data"))
    for name in (
        *config_module.config_environment_variable_names(),
        *(f"AGENT_TOOLKIT_CONFIG_{key.upper()}" for key in _LEGACY_KEYS),
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        codex_models,
        "list_models",
        lambda: [
            {"model": "gpt-6-sol", "supportedReasoningEfforts": [{"reasoningEffort": value} for value in ("low", "medium")]},
            {
                "model": "gpt-6-luna",
                "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("low", "medium", "xhigh")],
            },
            {"model": "gpt-5.6-terra", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
        ],
    )


def _main(tmp_path: pathlib.Path, *arguments: str) -> int:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(list(arguments), home=tmp_path)
    return cast(int, exc_info.value.code)


def _config_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "config" / "config.json"


def _write_config(tmp_path: pathlib.Path, values: dict[str, str]) -> pathlib.Path:
    path = _config_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values) + "\n", encoding="utf-8")
    return path


def _saved(tmp_path: pathlib.Path) -> dict[str, str]:
    return json.loads(_config_file(tmp_path).read_text(encoding="utf-8"))


_SERVE_TOML = (
    'host = "toml-host"\n'
    "port = 3000\n"
    "\n"
    "[plans]\n"
    'root = "/srv/plans"\n'
    'remote_hosts = ["remote-host", "stheno"]\n'
    "\n"
    "[sessions]\n"
    'claude_home = "/srv/claude"\n'
    'codex_home = "/srv/codex"\n'
    'remote_hosts = ["remote-host"]\n'
)


@pytest.fixture(name="_isolate_serve_environment")
def _isolate_serve_environment_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧`serve.toml`の指定と`atk serve`の待受の上書きを実環境から隔離する。"""
    for name in ("AGENT_TOOLKIT_SERVE_CONFIG", "AGENT_TOOLKIT_SERVE_HOST", "AGENT_TOOLKIT_SERVE_PORT"):
        monkeypatch.delenv(name, raising=False)
