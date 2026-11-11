# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""統括の認証接続とproviderの選択・保存・再開を検証する。"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

import pytest

from agent_toolkit._atk import lane
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._common import session_registry
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.process_loop_session_support import (
    _ORIGINAL_CODEX_SESSION,
    _codex_probes,
    _configure_codex_orchestrator,
    _connection_launch,
    _provider_option,
    _run_claude_session,
)
from agent_toolkit._testing.process_loop_session_support import _isolate_process_loop_commands as _isolate_process_loop_commands


@pytest.mark.parametrize("resume", [None, "inherit", "legacy"])
def test_unconfigured_process_session_preserves_parent_auth(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, resume: str | None
) -> None:
    """未設定時とその会話の再開では、親のAPIキーと設定による通常の認証解決を保つ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-test-key")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    if resume:
        session_registry.publish(
            resume,
            state=session_registry.PublicationState.RESULT_COMPLETED,
            engine="claude",
            cwd=str(tmp_path),
            claude_connection="inherit" if resume == "inherit" else None,
        )
    observed: dict[str, Any] = {}

    def launch(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed.update(env=kwargs["env"], settings=json.loads(argv[argv.index("--settings") + 1]))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(_pl_session, "_run_cli_session", lambda argv, env, cwd, **_kwargs: launch(argv, env=env, cwd=cwd))
    monkeypatch.setattr(lane, "cleanup_session", lambda _session, **_kwargs: {"retained": False})
    _pl_session.run_process_session(
        argparse.Namespace(resume=resume, no_update=True),
        tmp_path,
        "作業",
        dict(os.environ),
        orchestrator="claude",
        model="sonnet",
        effort="high",
        resume_pending=resume is not None,
    )
    assert observed["env"]["ANTHROPIC_API_KEY"] == "parent-test-key"
    assert observed["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert "apiKeyHelper" not in observed["settings"]
    assert "ANTHROPIC_API_KEY" not in observed["settings"]["env"]


@pytest.mark.parametrize("content", [None, "", "# 未記入\n"])
def test_invalid_env_file_warning_reaches_process_loop_stderr(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    """無効なAPI環境ファイルでは、process-loopの起動がサブスクリプションで続き、標準エラーへパスと修正操作を示す。"""
    path = tmp_path / "invalid.env"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    observed: dict[str, Any] = {}

    def launch(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed.update(env=kwargs["env"])
        return subprocess.CompletedProcess(argv, 0)

    _run_claude_session(tmp_path, monkeypatch, launch)
    stderr = capsys.readouterr().err
    assert str(path) in stderr
    assert "atk config set claude_payg_env_file" in stderr
    assert observed["env"]["CLAUDE_CODE_OAUTH_TOKEN"]


def test_payg_orchestrator_uses_api_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """従量課金だけの使用順はAPIを使い、対話確認を避けるhelperへ鍵の所在だけを渡す。"""
    observed, settings = _connection_launch(tmp_path, monkeypatch, "payg")
    assert "ANTHROPIC_API_KEY" not in observed["env"]
    assert not observed["env"].get("CLAUDE_CODE_OAUTH_TOKEN")
    assert observed["env"]["ANTHROPIC_BASE_URL"] == "https://api.example.test"
    assert str(tmp_path / "api.env") in settings["apiKeyHelper"]
    assert "selected-test-key" not in str(observed["argv"])


def test_subscription_orchestrator_uses_subscription_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """API環境ファイルを適用せずサブスクリプションの認証を渡す。"""
    observed, settings = _connection_launch(tmp_path, monkeypatch, "subscription")
    assert observed["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "test-subscription"
    assert not settings["apiKeyHelper"]
    assert "api.example.test" not in str(observed) + str(settings)


def test_subscription_orchestrator_drops_inherited_api_auth(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """親のAPIキー・トークンを本起動の環境とsettingsへ流入させない。"""
    observed, settings = _connection_launch(tmp_path, monkeypatch, "subscription")
    assert "ANTHROPIC_API_KEY" not in observed["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in observed["env"]
    assert "ANTHROPIC_API_KEY" not in settings["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in settings["env"]


def test_orchestrator_resume_probe_keeps_selected_connection(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """使用順の変更後も、保存した種別が使用順に含まれる会話は同じAPI接続で可用性を確認する。"""
    path = tmp_path / "selected.env"
    path.write_text("ANTHROPIC_API_KEY=selected-key\n", encoding="utf-8")
    session_registry.publish(
        "resume-api",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="claude",
        model="sonnet",
        effort="high",
        cwd=str(tmp_path),
        claude_api_env_file=str(path),
        claude_connection="api",
        claude_role="orchestrator",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    observed: list[dict[str, Any]] = []

    def probe(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", probe)
    candidate = ("claude", "sonnet", "high")
    selected = _pl_session.select_available_orchestrator(
        [candidate],
        {**os.environ, _pl_env.PROCESS_LOOP_SESSION_ENV: "1"},
        tmp_path,
        resume_session_id="resume-api",
    )
    assert selected[:4] == (*candidate, None)
    assert selected[4] is not None and selected[4].api_env_file == str(path)
    assert observed[0]["ANTHROPIC_API_KEY"] == "selected-key"
    assert not observed[0].get("CLAUDE_CODE_OAUTH_TOKEN")


def test_codex_orchestrator_probe_start_and_resume_use_selected_provider(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """可用性判定・本起動・再開の起動引数へ同じproviderを渡し、判定へ`config.toml`の定義も渡す。"""
    env = _configure_codex_orchestrator(monkeypatch, tmp_path, "subscription,credits")
    probes = _codex_probes(monkeypatch, chatgpt=False)
    candidate = ("codex", "gpt-5.6-sol", "medium")
    selected = _pl_session.select_available_orchestrator([candidate], env, tmp_path)
    # APIキーのアカウントではsubscriptionを除いて診断を出し、使用順の次のクレジットを選ぶ。
    assert selected == (*candidate, "my-credits", None)
    assert "codex_payg_provider openai" in capsys.readouterr().err
    assert len(probes) == 1 and "--ignore-user-config" in probes[0]
    assert _provider_option(probes[0]) == "my-credits"
    assert any(item.startswith("model_providers.my-credits={") and "credits.example.test" in item for item in probes[0])
    for resume_pending, resume in ((False, None), (True, "orchestrator-thread")):
        argv, _ = _pl_session._build_session_argv(  # pylint: disable=protected-access  # noqa: SLF001
            argparse.Namespace(resume=resume),
            "/goal 作業",
            dict(env),
            orchestrator="codex",
            model="gpt-5.6-sol",
            effort="medium",
            resume_pending=resume_pending,
            cwd=tmp_path,
            codex_provider="my-credits",
        )
        assert (argv[1] == "resume") is resume_pending
        assert _provider_option(argv) == "my-credits"


def test_codex_orchestrator_session_record_keeps_provider(tmp_path: pathlib.Path) -> None:
    """統括担当の起動中に公開するセッション記録へ、選んだproviderを保存する。"""
    env = {**os.environ, _pl_env.PROCESS_LOOP_SESSION_ID_ENV: "orchestrator-thread", "CODEX_HOME": str(tmp_path)}
    release = tmp_path / "provider.release"

    def published(_identifier: str) -> None:
        release.touch()

    result = _ORIGINAL_CODEX_SESSION(
        [sys.executable, "-c", late_descendant.held_source(release)],
        env,
        tmp_path,
        orchestrator="codex",
        model="gpt-5.6-sol",
        effort="medium",
        codex_provider="my-credits",
        on_session_started=published,
    )
    assert result.returncode == 0
    info = session_registry.resolve("orchestrator-thread").resume_info
    assert info is not None and info.codex_model_provider == "my-credits"
    assert info.codex_subscription_provider is None


@pytest.mark.parametrize(("order", "expected"), [("subscription,credits", "my-credits"), ("subscription", "openai")])
def test_codex_orchestrator_resume_keeps_saved_provider_until_removed_from_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, order: str, expected: str
) -> None:
    """保存した接続で再開し、その種別を使用順から外した場合だけ使用順の先頭の利用可能な候補へ移る。"""
    env = _configure_codex_orchestrator(monkeypatch, tmp_path, order)
    probes = _codex_probes(monkeypatch, chatgpt=True)
    session_registry.publish(
        "orchestrator-thread",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="codex",
        model="gpt-5.6-sol",
        effort="medium",
        cwd=str(tmp_path),
        model_type="orchestrate",
        codex_model_provider="my-credits",
    )
    selected = _pl_session.select_available_orchestrator(
        [("codex", "other-model", "high")], env, tmp_path, resume_session_id="orchestrator-thread"
    )
    assert selected == ("codex", "gpt-5.6-sol", "medium", expected, None)
    assert [_provider_option(argv) for argv in probes] == [expected]


@pytest.mark.parametrize("resume", [False, True])
def test_limited_claude_probe_selects_next_connection_for_same_session_launch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    """上限中の接続をスキップし、probeで成功した接続を同一会話の本起動へ渡す。"""
    api = tmp_path / "payg.env"
    api.write_text("ANTHROPIC_API_KEY=selected-test-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(api))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    if resume:
        session_registry.publish(
            "saved-claude",
            state=session_registry.PublicationState.RESULT_COMPLETED,
            engine="claude",
            cwd=str(tmp_path),
            model="sonnet",
            effort="high",
            claude_connection="subscription",
            claude_role="orchestrator",
        )
    probes: list[str] = []
    launched: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--no-session-persistence" in argv:
            if kwargs["env"].get("CLAUDE_CODE_OAUTH_TOKEN"):
                probes.append("subscription")
                event = {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "rejected",
                        "rateLimitType": "five_hour",
                        "resetsAt": int(time.time()) + 3600,
                    },
                }
                return subprocess.CompletedProcess(argv, 1, json.dumps(event), "")
            probes.append("payg")
            assert kwargs["env"]["ANTHROPIC_API_KEY"] == "selected-test-key"
            return subprocess.CompletedProcess(argv, 0, "OK", "")
        launched.append(argv)
        assert not kwargs["env"].get("CLAUDE_CODE_OAUTH_TOKEN")
        raw = argv[argv.index("--settings") + 1]
        settings = json.loads(pathlib.Path(raw).read_text(encoding="utf-8"))
        assert str(api) in settings["apiKeyHelper"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    env = dict(os.environ)
    engine, model, effort, provider, connection = _pl_session.select_available_orchestrator(
        [("claude", "sonnet", "high")],
        env,
        tmp_path,
        resume_session_id="saved-claude" if resume else None,
    )
    assert probes == ["subscription", "payg"]
    assert connection is not None and connection.kind == "payg"
    assert _pl_session.run_process_session(
        argparse.Namespace(resume="saved-claude" if resume else None),
        tmp_path,
        "続行する",
        env,
        orchestrator=engine,
        model=model,
        effort=effort,
        resume_pending=resume,
        codex_provider=provider,
        claude_connection=connection,
    )
    assert len(launched) == 1
    if resume:
        assert "--resume=saved-claude" in launched[0]
    assert env.get("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS") == "subscription,payg"
