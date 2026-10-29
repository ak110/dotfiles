"""process-loopと可用性確認がCLIへ渡す環境とsettingsを合わせて検証する。"""

import argparse
import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk import claude_connection, orchestrator
from agent_toolkit._atk.wi import process_loop_env, process_loop_session


def _run_cli_route(route: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> None:
    """同じ入力環境をプローブまたはprocess-loopの本起動へ渡す。"""
    if route == "probe":
        orchestrator.select_available(
            [("claude", "model", "low")], environment, tmp_path, orchestrator.ProbeContext("test", "確認")
        )
    else:

        def launch(
            argv: list[str], env: dict[str, str], cwd: pathlib.Path, **_kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(argv, env=env, cwd=cwd, check=False)

        monkeypatch.setattr(process_loop_session, "_run_cli_session", launch)
        monkeypatch.setattr(process_loop_env, "create_hook_debug_log", lambda *_args, **_kwargs: tmp_path / "hooks.log")
        process_loop_session.run_process_session(
            argparse.Namespace(resume=None, no_update=True),
            tmp_path,
            "確認",
            environment,
            orchestrator="claude",
            model="model",
            effort="low",
            resume_pending=False,
        )


@pytest.mark.parametrize("route", ["probe", "session"])
@pytest.mark.parametrize("kind", ["subscription", "payg"])
@pytest.mark.parametrize("source", ["none", "user", "project", "local"])
def test_cli_descendants_receive_only_configured_empty_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, route: str, kind: str, source: str
) -> None:
    """CLIとsettingsの合成後、設定が定義する変数だけが空の値で残る。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=key\nAWS_PROFILE=profile\nCUSTOM_CONNECTION=api\n")
    user = tmp_path / "user"
    user.mkdir()
    (tmp_path / ".claude").mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user))
    locations = {
        "user": user / "settings.json",
        "project": tmp_path / ".claude/settings.json",
        "local": tmp_path / ".claude/settings.local.json",
    }
    if source != "none":
        locations[source].write_text(json.dumps({"env": {"AWS_PROFILE": "source-profile"}}))
    selected = claude_connection.Connection(kind, str(api) if kind == "payg" else None, str(api))
    monkeypatch.setattr(claude_connection, "candidates", lambda *_args, **_kwargs: claude_connection.Candidates((selected,)))
    monkeypatch.setattr(orchestrator, "_probe_model", lambda *_args, **_kwargs: "model")
    effective: list[dict[str, str]] = []

    def launched(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
        if argv[1:] == ["auth", "status"]:
            return subprocess.CompletedProcess(argv, 0, '{"authMethod": "claude.ai"}', "")
        value = argv[argv.index("--settings") + 1]
        settings = json.loads(value if value.startswith("{") else pathlib.Path(value).read_text())
        effective.append({**kwargs["env"], **settings["env"]})
        return subprocess.CompletedProcess(argv, 0, "OK", "")

    monkeypatch.setattr(subprocess, "run", launched)
    environment = {
        "AWS_PROFILE": "parent",
        "CUSTOM_CONNECTION": "parent",
        "ANTHROPIC_API_KEY": "parent",
        orchestrator.PROCESS_LOOP_SESSION_ENV: "1",
    }
    _run_cli_route(route, tmp_path, monkeypatch, environment)
    assert len(effective) == 1
    values = effective[0]
    assert values.get("AWS_PROFILE") == ("profile" if kind == "payg" else "" if source != "none" else None)
    assert values.get("CUSTOM_CONNECTION") == ("api" if kind == "payg" else None)
    assert not any(value == "" for key, value in values.items() if key in {"CUSTOM_CONNECTION", "ANTHROPIC_API_KEY"})


@pytest.mark.parametrize("role", ["delegate", "orchestrator"])
@pytest.mark.parametrize("content", [None, "", "# empty\n", "invalid", "ANTHROPIC_API_KEY=\n", "ANTHROPIC_API_KEY=key\n"])
def test_api_only_has_no_implicit_authentication(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, role: str, content: str | None
) -> None:
    """親認証があっても、従量課金だけの使用順はAPI設定だけを候補にする。"""
    api = tmp_path / "api.env"
    if content is not None:
        api.write_text(content)
    order = (
        "AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS"
        if role == "orchestrator"
        else "AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS"
    )
    monkeypatch.setenv(order, "payg")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(api) if content is not None else "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "parent-oauth")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-api")
    choices = claude_connection.candidates(role)
    if content == "ANTHROPIC_API_KEY=key\n":
        assert [value.kind for value in choices.connections] == ["payg"]
    else:
        with pytest.raises(ValueError, match="Claude接続候補がありません"):
            choices.require_first()


@pytest.mark.parametrize("route", ["probe", "session"])
def test_explicit_subscription_without_api_file_reaches_cli_isolated(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    """明示したサブスク固定はAPI環境未設定でも、プローブと本起動の実候補選択へ届く。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription")
    monkeypatch.setattr(orchestrator, "_probe_model", lambda *_args, **_kwargs: "model")
    observed: list[dict[str, str]] = []

    def launched(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, str(tmp_path) + "\n", "")
        if argv[1:] == ["auth", "status"]:
            return subprocess.CompletedProcess(argv, 0, '{"authMethod": "claude.ai", "loggedIn": true}', "")
        value = argv[argv.index("--settings") + 1]
        selected = json.loads(value if value.startswith("{") else pathlib.Path(value).read_text(encoding="utf-8"))
        effective = {**kwargs["env"], **selected["env"]}
        observed.append(effective)
        assert effective["CLAUDE_CODE_OAUTH_TOKEN"] == "subscription"
        assert not effective.get("ANTHROPIC_API_KEY")
        assert not selected["apiKeyHelper"]
        return subprocess.CompletedProcess(argv, 0, "OK", "")

    monkeypatch.setattr(subprocess, "run", launched)
    environment = {
        "ANTHROPIC_API_KEY": "parent-api",
        "CLAUDE_CODE_OAUTH_TOKEN": "subscription",
        orchestrator.PROCESS_LOOP_SESSION_ENV: "1",
    }
    _run_cli_route(route, tmp_path, monkeypatch, environment)
    assert len(observed) == 1
