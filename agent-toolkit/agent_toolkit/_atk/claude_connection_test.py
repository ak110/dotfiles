"""役割別接続の選択と、認証環境を持たない候補の除外を検証する。"""

import argparse
import json
import pathlib
import subprocess
from typing import Any, cast

import pytest

from agent_toolkit._atk import claude_connection as connection
from agent_toolkit._atk import orchestrator
from agent_toolkit._atk.wi import process_loop_env, process_loop_session
from agent_toolkit._common import claude_connection as settings
from agent_toolkit._common import state_paths


@pytest.mark.parametrize("mode", ["pay-as-you-go", "api-credits"])
@pytest.mark.parametrize("role", ["orchestrator", "delegate"])
@pytest.mark.parametrize("api,subscription", [(False, False), (True, False), (False, True), (True, True)])
def test_candidate_matrix_by_environment_and_role(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    role: str,
    api: bool,
    subscription: bool,
) -> None:
    """一方だけの認証・両認証・認証なしを、両モードの両役割で評価する。"""
    path = tmp_path / "api.env"
    if api:
        path.write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", mode)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription" if subscription else "")
    choices = connection.candidates(role)
    order = {
        ("pay-as-you-go", "orchestrator"): ["api"],
        ("api-credits", "orchestrator"): ["subscription"],
        ("pay-as-you-go", "delegate"): ["subscription", "api"],
        ("api-credits", "delegate"): ["api", "subscription"],
    }[(mode, role)]
    expected = [name for name in order if {"api": api, "subscription": subscription}[name]]
    assert [item.kind for item in choices.connections] == expected
    if not expected:
        with pytest.raises(ValueError, match="Claude接続候補がありません"):
            choices.require_first()


@pytest.mark.parametrize("invalid", ["missing", "directory", "malformed", "unreadable", "empty-file", "comments-only"])
def test_invalid_env_file_disables_api_candidate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    """無効パスは保存値を保ち、起動時の候補から除いて復旧操作を返す。"""
    path = tmp_path / invalid
    if invalid == "directory":
        path.mkdir()
    elif invalid == "malformed":
        path.write_text("invalid content", encoding="utf-8")
    elif invalid == "empty-file":
        path.write_text("", encoding="utf-8")
    elif invalid == "comments-only":
        path.write_text("# APIキーは未記入\n\n", encoding="utf-8")
    elif invalid == "unreadable":

        def denied(_path: str) -> dict[str, str]:
            raise PermissionError("権限なし")

        monkeypatch.setattr(settings, "read_api_env_file", denied)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path) if invalid else "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription")
    choices = connection.candidates("delegate")
    assert [item.kind for item in choices.connections] == ["subscription"]
    if invalid:
        assert str(path) in choices.diagnostics[0]
        assert "atk config set claude_api_env_file" in choices.diagnostics[0]


@pytest.mark.parametrize("orchestrator_api", [True, False])
@pytest.mark.parametrize("worker_api_first", [True, False])
def test_role_settings_are_independent(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, orchestrator_api: bool, worker_api_first: bool
) -> None:
    """2設定の全組合せでオーケストレーターの固定と委譲先の優先順を独立に解決する。"""
    path = tmp_path / "api.env"
    path.write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_API_ONLY", str(orchestrator_api).lower())
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_API_FIRST", str(worker_api_first).lower())
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription")
    assert [item.kind for item in connection.candidates("orchestrator").connections] == [
        "api" if orchestrator_api else "subscription"
    ]
    assert [item.kind for item in connection.candidates("delegate").connections] == (
        ["api", "subscription"] if worker_api_first else ["subscription", "api"]
    )


def test_subscription_only_orchestrator_rejects_api_only_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """明示したサブスク固定はAPIだけ使える環境でも維持する。"""
    path = tmp_path / "api.env"
    path.write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_API_ONLY", "false")
    monkeypatch.setattr(connection, "subscription_available", lambda *_args, **_kwargs: False)
    with pytest.raises(ValueError, match="claude auth login"):
        connection.candidates("orchestrator").require_first()


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_subscription_login_without_environment_token(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    """ファイル保存とKeychainをCLIが解決するmacOSのログインを認証ありとして扱う。"""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CUSTOM_CONNECTION", "parent")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    credentials = tmp_path / ".credentials.json"
    credentials.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "test-login", "refreshToken": "test-refresh"}}), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def status(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path)
        assert "CUSTOM_CONNECTION" not in kwargs["env"]
        assert "CUSTOM_CONNECTION" not in json.loads(argv[argv.index("--settings") + 1])["env"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({"authMethod": "claude.ai", "loggedIn": True}))

    monkeypatch.setattr(subprocess, "run", status)
    assert settings.subscription_available(platform=platform, isolation_keys=settings.AUTH_ENV | {"CUSTOM_CONNECTION"})
    assert bool(calls) is (platform == "darwin")


def test_process_loop_reclaims_settings_when_session_start_log_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動前のログ失敗でも、作成済みの対話起動設定を回収する。"""
    with cast(
        settings.SettingsFile, settings.merge_settings(None, {"CUSTOM_CONNECTION": "value"}, None, force_file=True)
    ) as owned:
        monkeypatch.setattr(process_loop_session, "_build_session_argv", lambda *_args, **_kwargs: (["claude", owned], None))

        def failed_log(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("log failed")

        monkeypatch.setattr(process_loop_session._process_loop_log, "append", failed_log)  # pylint: disable=protected-access
        with pytest.raises(RuntimeError, match="log failed"):
            process_loop_session.run_process_session(
                argparse.Namespace(no_update=True),
                tmp_path,
                "調査",
                {},
                orchestrator="claude",
                model="model",
                effort="low",
                resume_pending=False,
                dotfiles_root=None,
            )
        assert not pathlib.Path(owned).exists()


@pytest.mark.parametrize("failure_stage", ["debug", "registry"])
def test_process_loop_preparation_failure_leaves_no_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failure_stage: str
) -> None:
    """argvの準備が失敗した場合も、後続が回収できない設定ファイルを残さない。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=key\nCUSTOM_CONNECTION=selected\n", encoding="utf-8")
    monkeypatch.setattr(
        connection, "candidates", lambda *_args, **_kwargs: connection.Candidates((connection.Connection("api", str(api)),))
    )

    def failed_debug(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"{failure_stage} failed")

    if failure_stage == "debug":
        monkeypatch.setattr(process_loop_env, "create_hook_debug_log", failed_debug)
    else:
        monkeypatch.setattr(process_loop_session._registry, "publish", failed_debug)  # pylint: disable=protected-access
    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        process_loop_session.run_process_session(
            argparse.Namespace(no_update=True, resume=None),
            tmp_path,
            "調査",
            {},
            orchestrator="claude",
            model="model",
            effort="low",
            resume_pending=False,
            dotfiles_root=None,
        )
    assert not list(state_paths.state_dir().glob("agents-server/**/settings-*.json"))


def test_probe_settings_are_reclaimed_on_interrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """probeの子実行が中断しても、API接続の合成設定を回収する。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=key\nCUSTOM_CONNECTION=selected\n", encoding="utf-8")
    monkeypatch.setattr(
        connection, "candidates", lambda *_args, **_kwargs: connection.Candidates((connection.Connection("api", str(api)),))
    )
    monkeypatch.setattr(orchestrator, "_probe_model", lambda *_args: "model")
    captured: list[pathlib.Path] = []

    def interrupted(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(pathlib.Path(argv[argv.index("--settings") + 1]))
        assert captured[0].is_file()
        assert kwargs["env"]["CUSTOM_CONNECTION"] == "selected"
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        orchestrator.select_available(
            [("claude", "model", "low")],
            {orchestrator.PROCESS_LOOP_SESSION_ENV: "1"},
            tmp_path,
            orchestrator.ProbeContext("test", "調査"),
        )
    assert captured
    assert not captured[0].exists()
