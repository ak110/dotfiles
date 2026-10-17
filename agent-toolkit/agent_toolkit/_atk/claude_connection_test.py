"""役割別接続の選択と、認証環境を持たない候補の除外を検証する。"""

import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk import claude_connection as connection
from agent_toolkit._common import claude_connection as settings


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
    path.write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", mode)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path) if api else "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription" if subscription else "")
    choices = connection.candidates(role)
    expected = (
        {
            ("pay-as-you-go", "orchestrator"): ["api"],
            ("api-credits", "orchestrator"): ["subscription"],
            ("pay-as-you-go", "delegate"): ["subscription", "api"],
            ("api-credits", "delegate"): ["api", "subscription"],
        }[(mode, role)]
        if api and subscription
        else [name for name, present in [("api", api), ("subscription", subscription)] if present]
    )
    assert [item.kind for item in choices.connections] == expected
    if not expected:
        with pytest.raises(ValueError, match="claude auth login"):
            choices.require_first()


@pytest.mark.parametrize("invalid", ["", "missing", "directory", "malformed", "unreadable", "empty-file", "comments-only"])
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


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_subscription_login_without_environment_token(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    """ファイル保存とKeychainをCLIが解決するmacOSのログインを認証ありとして扱う。"""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    credentials = tmp_path / ".credentials.json"
    credentials.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "test-login", "refreshToken": "test-refresh"}}), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def status(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"authMethod": "claude.ai", "loggedIn": True}))

    monkeypatch.setattr(subprocess, "run", status)
    assert settings.subscription_available(platform=platform)
    assert bool(calls) is (platform == "darwin")
