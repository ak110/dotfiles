"""Codex会話の役割と設定から、接続の種別とprovider IDの候補を決める。

接続はサブスクリプション（`subscription`）、有限のAPIクレジット（`credits`）、従量課金API（`payg`）の3種別で、
`subscription`はprovider `openai`をChatGPTログインで使う場合だけを指す。`credits`と`payg`は`config.toml`の
`model_providers`に定義したprovider ID（`openai`も可）を`codex_credits_provider`・`codex_payg_provider`で指定する。
役割ごとの使用順は`codex_orchestrator_connections`・`codex_worker_connections`が持つ。
4キーのどれも設定していない環境は、`config.toml`の通常設定に従い代替試行しない。

provider定義と認証はCodexが持ち、本モジュールは種別とprovider IDの対応だけを扱う。
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Mapping
from typing import Any

from agent_toolkit._atk import config
from agent_toolkit._common import host_homes, process_tree

SUBSCRIPTION_PROVIDER = "openai"
"""サブスクリプション（ChatGPTログイン）で使うprovider ID。"""

API_KEY_SUBSCRIPTION_DIAGNOSTIC = (
    "Codexの有効なアカウントがChatGPTログインではないため、subscription候補から除いた。"
    "APIキーのアカウントを従量課金で使う場合は`atk config set codex_payg_provider openai`、"
    "APIクレジットとして使う場合は`atk config set codex_credits_provider openai`を設定する"
)
"""ChatGPTログイン以外のアカウントでsubscriptionを除いたことを示す診断。"""

_PROVIDER_KEYS = {"credits": "codex_credits_provider", "payg": "codex_payg_provider"}
_CHATGPT_LOGIN_STATUS = "Logged in using ChatGPT"
"""`codex login status`がChatGPTログインを示す出力（Codex CLI 0.165.0の本体文字列で確認）。"""

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def managed() -> bool:
    """Codexの接続を設定で管理するか（provider 2つと使用順2つのいずれかを設定したか）を返す。"""
    return config.connections_configured("codex")


def role_of(model_type: str | None) -> str:
    """agents_serverのmodel_typeから役割を返す。`orchestrate`は統括担当、それ以外は委譲先とする。"""
    return "orchestrator" if model_type == "orchestrate" else "delegate"


def _api_providers() -> dict[str, str]:
    """設定済みのAPI種別とprovider IDの対応を返す。同じIDを2種別へ設定した場合は`ValueError`を送出する。"""
    providers = {kind: config.resolve_mutable_setting(key) for kind, key in _PROVIDER_KEYS.items()}
    if providers["credits"] and providers["credits"] == providers["payg"]:
        raise ValueError(
            f"codex_credits_providerとcodex_payg_providerに同じprovider ID（{providers['credits']}）を設定している。"
            "接続先とキーが別のproviderを`config.toml`へ定義し、それぞれへ設定する"
        )
    return {kind: provider for kind, provider in providers.items() if provider}


def provider_entries(role: str) -> tuple[tuple[str, str], ...]:
    """役割の使用順のうち、providerを特定できる種別を`(種別, provider ID)`の順序で返す。

    `subscription`は`openai`に対応させ、provider IDを設定していないAPI種別は診断なしで除く。
    アカウント種別と認証の有無は呼び出し側が評価する。
    """
    api = _api_providers()
    entries: list[tuple[str, str]] = []
    for kind in config.connection_order("codex", role):
        provider = SUBSCRIPTION_PROVIDER if kind == "subscription" else api.get(kind)
        if provider:
            entries.append((kind, provider))
    return tuple(entries)


def provider_kind(provider: str | None, *, subscription_provider: str | None) -> str | None:
    """保持したproviderの種別を、サブスクリプションで開始した記録と現在の設定から返す。判定できなければ`None`。"""
    if provider is None:
        return None
    if subscription_provider is not None and provider == subscription_provider:
        return "subscription"
    return next((kind for kind, value in _api_providers().items() if value == provider), None)


def outside_order(provider: str | None, role: str, *, subscription_provider: str | None) -> bool:
    """保持したproviderの種別が、管理中の使用順に含まれないかを返す。providerを保持しない会話は対象外とする。"""
    if provider is None or not managed():
        return False
    kind = provider_kind(provider, subscription_provider=subscription_provider)
    return kind is None or kind not in config.connection_order("codex", role)


def subscription_provider_for(provider: str | None) -> str | None:
    """選んだproviderがサブスクリプションならそのIDを返す。`openai`をAPI種別へ設定した場合はAPIとして扱う。"""
    if provider != SUBSCRIPTION_PROVIDER or SUBSCRIPTION_PROVIDER in _api_providers().values():
        return None
    return provider


def chatgpt_login(env: Mapping[str, str]) -> bool:
    """`codex login status`の公開出力から、有効なアカウントがChatGPTログインかを返す。"""
    try:
        result = process_tree.run_with_timeout(
            ["codex", "login", "status"],
            env=dict(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and _CHATGPT_LOGIN_STATUS in result.stdout + result.stderr


def orchestrator_providers(env: Mapping[str, str]) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """`codex` CLIで起動する統括担当の候補`(種別, provider ID)`と、除いた候補の診断を返す。

    `subscription`はChatGPTログインの場合だけ候補にする。API種別の認証は可用性判定の起動で確かめる。
    """
    entries = provider_entries("orchestrator")
    if not any(kind == "subscription" for kind, _ in entries) or chatgpt_login(env):
        return entries, ()
    return tuple(entry for entry in entries if entry[0] != "subscription"), (API_KEY_SUBSCRIPTION_DIAGNOSTIC,)


def provider_arguments(provider: str) -> list[str]:
    """`codex`へproviderを選ばせる公開オプション`-c model_provider=<ID>`を返す。"""
    return ["-c", f"model_provider={json.dumps(provider)}"]


def probe_definition_arguments(provider: str, env: Mapping[str, str]) -> list[str]:
    """`config.toml`を読まない可用性判定へ、本起動と同じprovider定義を渡す`-c`オプションを返す。

    可用性判定は`--ignore-user-config`で起動するため、`config.toml`にだけ定義したproviderは判定で解決できない。
    本起動が読む`config.toml`の`model_providers.<ID>`をそのまま渡し、判定と本起動で同じ定義を評価させる。
    定義が無いID（組み込みのprovider）と、読めない`config.toml`では何も加えない。
    """
    path = host_homes.codex_home(env) / "config.toml"
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, tomllib.TOMLDecodeError:
        return []
    providers = loaded.get("model_providers")
    definition = providers.get(provider) if isinstance(providers, dict) else None
    if not isinstance(definition, dict):
        return []
    return ["-c", f"model_providers.{_toml_key(provider)}={_toml_value(definition)}"]


def _toml_key(key: str) -> str:
    """TOMLのキーを、裸のキーで書けなければ基本文字列で返す。"""
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key)


def _toml_value(value: Any) -> str:
    """`-c`の値として渡すTOMLのインライン表記を返す。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # JSONの文字列表記はTOMLの基本文字列として解釈できる。
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_toml_key(str(key))} = {_toml_value(item)}" for key, item in value.items()) + "}"
    raise ValueError(f"config.tomlのprovider定義に-cで渡せない値がある: {type(value).__name__}")
