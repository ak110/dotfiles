"""共有したCodexのprovider IDを、端末ごとの`config.toml`と照合する。  pyfltr: allow-comment"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping

from agent_toolkit._common import host_homes

BUILTIN_PROVIDERS = frozenset({"openai", "ollama", "lmstudio"})
"""`config.toml`へ定義しなくても使えるCodex組み込みのprovider ID。  pyfltr: allow-comment"""


def provider_warnings(providers: Mapping[str, str], env: Mapping[str, str]) -> list[dict[str, str]]:
    """共有したprovider IDが、この端末の`config.toml`で使えない場合の警告を返す。  pyfltr: allow-comment

    未設定と組み込みのIDは警告しない。`config.toml`を読めない場合は未定義と区別して「点検できない」を返す。
    接続や認証の確認は行わない（課金を伴う接続を避けるため）。
    """
    path = host_homes.codex_home(env) / "config.toml"
    configured = {key: value for key, value in providers.items() if value}
    if not configured:
        return []
    warnings: list[dict[str, str]] = []
    values = list(configured.values())
    for key, value in configured.items():
        if values.count(value) > 1:
            warnings.append(
                {"key": key, "state": "conflict", "message": f"{key}が別の種別と同じprovider ID（{value}）を指している"}
            )
    pending = {key: value for key, value in configured.items() if value not in BUILTIN_PROVIDERS}
    if not pending:
        return warnings
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        loaded = {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        return warnings + [
            {"key": key, "state": "unchecked", "message": f"{path}を読めないため{key}（{value}）を点検できない: {error}"}
            for key, value in pending.items()
        ]
    defined = loaded.get("model_providers")
    defined = defined if isinstance(defined, dict) else {}
    warnings.extend(
        {"key": key, "state": "undefined", "message": f"{key}のprovider ID（{value}）が{path}のmodel_providersに無い"}
        for key, value in pending.items()
        if not isinstance(defined.get(value), dict)
    )
    return warnings
