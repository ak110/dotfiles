"""agent-toolkit/agent_toolkit/_atk/codex_connection.py のテスト。"""

import pathlib
import subprocess
import tomllib
from typing import Any

import pytest

from agent_toolkit._atk import codex_connection


def test_probe_definition_arguments_reproduce_config_toml_definition(tmp_path: pathlib.Path) -> None:
    """可用性判定へ渡す`-c`の値は、`config.toml`のprovider定義と同じ内容のTOMLとして読める。"""
    definition = (
        '[model_providers."my.provider"]\nname = "名前 \\"引用\\""\nbase_url = "https://example.test/v1"\n'
        "requires_openai_auth = false\nstream_max_retries = 0\n"
        '[model_providers."my.provider".http_headers]\nX-Test = "value"\n'
    )
    (tmp_path / "config.toml").write_text(definition, encoding="utf-8")
    arguments = codex_connection.probe_definition_arguments("my.provider", {"CODEX_HOME": str(tmp_path)})
    assert arguments[0] == "-c"
    key, _, value = arguments[1].partition("=")
    assert key == 'model_providers."my.provider"'
    assert tomllib.loads(f"value = {value}")["value"] == tomllib.loads(definition)["model_providers"]["my.provider"]


@pytest.mark.parametrize("content", [None, "invalid = [", '[model_providers.other]\nname = "other"\n'])
def test_probe_definition_arguments_skip_missing_definitions(tmp_path: pathlib.Path, content: str | None) -> None:
    """`config.toml`が無い・読めない・定義が無い場合は、組み込みproviderとして何も加えない。"""
    if content is not None:
        (tmp_path / "config.toml").write_text(content, encoding="utf-8")
    assert not codex_connection.probe_definition_arguments("openai", {"CODEX_HOME": str(tmp_path)})


def test_same_provider_for_credits_and_payg_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """クレジットと従量課金へ同じprovider IDを設定すると、種別を判定できないため設定方法を示して止める。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_CREDITS_PROVIDER", "shared")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_PAYG_PROVIDER", "shared")
    with pytest.raises(ValueError, match="同じprovider ID"):
        codex_connection.provider_entries("delegate")


@pytest.mark.parametrize(
    ("credits_provider", "provider", "expected"), [("", "openai", "openai"), ("openai", "openai", None), ("", "custom", None)]
)
def test_subscription_provider_follows_api_assignment(
    monkeypatch: pytest.MonkeyPatch, credits_provider: str, provider: str, expected: str | None
) -> None:
    """`openai`はAPI種別へ設定していない場合だけサブスクリプションとして記録する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_CREDITS_PROVIDER", credits_provider)
    assert codex_connection.subscription_provider_for(provider) == expected


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "Logged in using ChatGPT\n", True),
        (0, "Logged in using an API key - sk-***\n", False),
        (1, "Not logged in\n", False),
    ],
)
def test_chatgpt_login_reads_public_status(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, expected: bool
) -> None:
    """`codex login status`の公開出力がChatGPTログインを示す場合だけ真を返す。"""

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv == ["codex", "login", "status"]
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(codex_connection.process_tree, "run_with_timeout", run)
    assert codex_connection.chatgpt_login({}) is expected
