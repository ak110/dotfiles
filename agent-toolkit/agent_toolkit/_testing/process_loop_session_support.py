"""process_loop_sessionの試験で共有する準備処理と隔離fixture。"""

import argparse
import json
import os
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._atk import codex_connection as _codex_connection
from agent_toolkit._atk import lane
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._common import session_registry
from agent_toolkit._testing.process_loop_support import (
    isolate_process_loop_commands,
)


def _run_claude_session(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launch: Callable[..., subprocess.CompletedProcess[bytes]],
) -> None:
    """Claudeのオーケストレーターを1回起動し、本起動の`subprocess.run`を`launch`で受ける。"""
    monkeypatch.setattr(_pl_session, "_run_cli_session", lambda argv, env, cwd, **_kwargs: launch(argv, env=env, cwd=cwd))
    monkeypatch.setattr(lane, "cleanup_session", lambda _session, **_kwargs: {"retained": False})
    args = argparse.Namespace(resume=None, no_update=True, worktree="custom")
    assert _pl_session.run_process_session(
        args,
        tmp_path,
        "作業する",
        dict(os.environ),
        orchestrator="claude",
        model="sonnet",
        effort="high",
        resume_pending=False,
    )


def _connection_launch(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    order: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """本起動境界へ渡されたargv・環境と、起動時に読めるsettingsを取得する。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=selected-test-key\nANTHROPIC_BASE_URL=https://api.example.test\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(api))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", order)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-test-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent-test-token")
    observed: dict[str, Any] = {}
    settings: dict[str, Any] = {}

    def launch(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed.update(argv=argv, env=kwargs["env"])
        info = session_registry.resolve(kwargs["env"][_pl_env.PROCESS_LOOP_SESSION_ID_ENV]).resume_info
        assert info is not None and info.cwd == str(tmp_path)
        raw = argv[argv.index("--settings") + 1]
        settings.update(json.loads(pathlib.Path(raw).read_text(encoding="utf-8") if not raw.startswith("{") else raw))
        return subprocess.CompletedProcess(argv, 0)

    _run_claude_session(tmp_path, monkeypatch, launch)
    return observed, settings


def _configure_codex_orchestrator(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, order: str) -> dict[str, str]:
    """Codexの統括担当の接続設定と、`config.toml`だけに定義したproviderを用意する。"""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(exist_ok=True)
    (codex_home / "config.toml").write_text(
        '[model_providers.my-credits]\nname = "my-credits"\nbase_url = "https://credits.example.test/v1"\n'
        'env_key = "MY_CREDITS_KEY"\nrequires_openai_auth = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:gpt-6-luna/medium")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_CREDITS_PROVIDER", "my-credits")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_ORCHESTRATOR_CONNECTIONS", order)
    return {**os.environ, "CODEX_HOME": str(codex_home)}


def _codex_probes(monkeypatch: pytest.MonkeyPatch, *, chatgpt: bool) -> list[list[str]]:
    """有効なアカウントがChatGPTログインかどうかを与え、可用性判定のargvを記録する。"""
    probes: list[list[str]] = []

    def run(argv: list[str], *_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        probes.append(argv)
        return subprocess.CompletedProcess(argv, 0, "OK\n", "")

    def login(_env: Any) -> bool:
        return chatgpt

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(_codex_connection, "chatgpt_login", login)
    return probes


def _provider_option(argv: list[str]) -> str | None:
    """argvの`-c model_provider=<ID>`の値を返す。"""
    values = [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == "-c"]
    return next((json.loads(value.partition("=")[2]) for value in values if value.startswith("model_provider=")), None)


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")

    def run_codex(argv: list[str], env: dict[str, str], cwd: pathlib.Path, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        # 従来の起動引数の試験は外部CLI全体をrunで代用する。実子試験は監視を含む本体へ到達する。
        if subprocess.run is _ORIGINAL_RUN:
            return _ORIGINAL_CODEX_SESSION(argv, env, cwd, **kwargs)
        return subprocess.run(argv, check=False, env=env, cwd=cwd, creationflags=_pl_env.session_creation_flags("codex"))

    monkeypatch.setattr(_pl_session, "_run_cli_session", run_codex)


_ORIGINAL_RUN = subprocess.run


_ORIGINAL_CODEX_SESSION = _pl_session._run_cli_session  # pylint: disable=protected-access  # noqa: SLF001


_PROCESS_LOOP_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"


_PROCESS_LOOP_SESSION_ID_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"


_DELEGATED_SESSION_ENV = "AGENT_TOOLKIT_DELEGATED_SESSION"
