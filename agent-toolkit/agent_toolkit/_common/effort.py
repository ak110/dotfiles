"""engine/modelが受理する推論量の順序と、相対操作の共通定義。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from agent_toolkit._common import codex_models, process_tree

ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def ordered(values: Iterable[str]) -> tuple[str, ...]:
    """受理値だけを共通順序へ並べる。未知の段は推測しない。"""
    supported = set(values)
    return tuple(value for value in ORDER if value in supported)


def relative(current: str | None, direction: str, levels: tuple[str, ...]) -> str:
    """現在値から1段動かし、端なら現在値を返す。対応不明は端と区別する。"""
    if direction not in {"up", "down"}:
        raise ValueError("effort操作はupかdownを指定する")
    if current is None or current not in levels:
        raise ValueError(f"現在のeffort {current!r}の受理段を確認できない")
    index = levels.index(current) + (1 if direction == "up" else -1)
    return levels[min(max(index, 0), len(levels) - 1)]


def catalog_levels(model: str, catalog: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Codex model/listの該当モデルの受理値だけを使う。"""
    for item in catalog:
        if item.get("model") == model or item.get("id") == model:
            values = item.get("supportedReasoningEfforts", [])
            return ordered(
                value["reasoningEffort"] for value in values if isinstance(value, dict) and "reasoningEffort" in value
            )
    raise ValueError(f"model/listにモデル{model}が無い")


def cli_levels(engine: str, model: str | None) -> tuple[str, ...]:
    """Claudeの既知モデル、またはCLIが公開するモデル一覧から受理値を求める。"""
    if not model:
        raise ValueError("モデルが未設定のためeffortの段を確認できない")
    if engine == "codex":
        return catalog_levels(model, codex_models.list_models())
    if engine == "claude":
        name = model.removesuffix("[1m]")
        if name == "claude-opus-4-7":
            return ("low", "medium", "high", "xhigh", "max")
        if name in {"opus", "claude-opus-5-5"}:
            return ("low", "medium", "high", "max")
        if name in {"sonnet", "haiku", "claude-sonnet-5-5", "claude-haiku-5-5"}:
            return ("low", "medium", "high")
        raise ValueError(f"Claudeモデル{model}の受理段が未定義")
    if engine not in {"agy", "opencode"}:
        raise ValueError(f"engine {engine}の受理段が未定義")
    command = ["agy", "models"] if engine == "agy" else ["opencode", "models", model.split("/", 1)[0], "--verbose"]
    result = process_tree.run_with_timeout(
        command, capture_output=True, encoding="utf-8", errors="replace", check=True, timeout=20
    )
    if engine == "agy":
        slugs = {line.split()[0] for line in result.stdout.splitlines() if line.strip()}
        return ordered(value for value in ORDER if f"{model}-{value}" in slugs)
    # --verboseはモデル名の行の後へJSON objectを返す。複数モデルのJSONを順に読む。
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\{", result.stdout):
        item, _end = decoder.raw_decode(result.stdout[match.start() :])
        if isinstance(item, dict) and item.get("id") in {model, model.split("/", 1)[1]}:
            return ordered(item.get("variants", {}))
    raise ValueError(f"OpenCodeモデル{model}のvariant一覧を確認できない")
