from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Mapping

from agent_toolkit._common import runtime_environment

REFERENCE_PREFIX = "atk-env:"
MASK = "[REDACTED]"
SETTING_FILES = {
    "claude_credits_env": "claude-credits.env",
    "claude_payg_env": "claude-payg.env",
    "weekly_research_x_env": "x-api.env",
}


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if not separator or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None or "\0" in value:
            raise ValueError(f"{number}行目をKEY=VALUE形式にする")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"{number}行目の値を囲む引用符を閉じる")
            value = value[1:-1]
        values[key] = value
    return values


def encode(values: Mapping[str, str]) -> str:
    return "\n".join(
        f"{key}=" + (f"'{value}'" if value != value.strip() or value.startswith(("'", '"')) else value)
        for key, value in values.items()
    )


def secret(name: str) -> bool:
    return any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


def public(key: str, value: str) -> str:
    if key not in SETTING_FILES:
        return value
    try:
        values = parse(value)
    except ValueError:
        return "[設定値の書式が不正]"
    return encode({name: MASK if secret(name) and text else text for name, text in values.items()})


def restore(key: str, value: str, current: str) -> str:
    if key not in SETTING_FILES:
        return value
    old, changed = parse(current), parse(value)
    for name, text in changed.items():
        if secret(name) and text == MASK:
            if name not in old:
                raise ValueError(f"{name}の保持する値がない。最新値を取得し直すか、新しい値を入力する")
            changed[name] = old[name]
    return encode(changed)


def reference(path: pathlib.Path, key: str) -> str:
    return REFERENCE_PREFIX + json.dumps([str(path), key], ensure_ascii=False)


def read(source: str) -> dict[str, str]:
    if source.startswith(REFERENCE_PREFIX):
        path, key = json.loads(source.removeprefix(REFERENCE_PREFIX))
        if value := runtime_environment.environment().get("AGENT_TOOLKIT_CONFIG_" + key.upper()):
            return parse(value)
        return parse(json.loads(pathlib.Path(path).read_text(encoding="utf-8")).get(key, ""))
    return parse(pathlib.Path(source).read_text(encoding="utf-8"))
