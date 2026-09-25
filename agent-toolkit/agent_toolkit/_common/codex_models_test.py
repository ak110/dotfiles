"""Codex App Serverのモデル一覧と系列指定の契約検体。"""

from typing import Any

import pytest

from agent_toolkit._common import codex_models


def _model(model: str, *efforts: str, hidden: bool = False) -> dict[str, Any]:
    return {
        "model": model,
        "hidden": hidden,
        "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in efforts],
    }


@pytest.mark.asyncio
async def test_family_resolution_uses_all_visible_pages_and_keeps_explicit_id() -> None:
    """一覧の最終ページにある同系列最新版を選び、完全IDは固定する。"""
    calls: list[dict[str, Any]] = []

    async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "model/list"
        calls.append(params)
        if "cursor" not in params:
            return {"data": [_model("gpt-5.6-sol", "medium"), _model("gpt-7-sol", "medium", hidden=True)], "nextCursor": "next"}
        return {"data": [_model("gpt-6-sol", "medium"), _model("gpt-5.6-terra", "high")], "nextCursor": None}

    catalog = await codex_models.fetch_catalog(request)
    result = codex_models.resolve_candidates(
        [("codex", "sol", "medium"), ("codex", "gpt-5.6-sol", "medium"), ("claude", "opus", "high")],
        catalog,
    )
    assert result == [
        ("codex", "gpt-6-sol", "medium"),
        ("codex", "gpt-5.6-sol", "medium"),
        ("claude", "opus", "high"),
    ]
    assert calls == [
        {"limit": 100, "includeHidden": False},
        {"limit": 100, "includeHidden": False, "cursor": "next"},
    ]


def test_family_resolution_reports_unavailable_family_and_effort() -> None:
    """推測したIDや旧版への暗黙の退避をせず、利用不能の理由を示す。"""
    catalog = [_model("gpt-5.6-sol", "high"), _model("gpt-6-sol", "medium")]
    with pytest.raises(ValueError, match="sol.*high"):
        codex_models.resolve_candidates([("codex", "sol", "high")], catalog)
    with pytest.raises(ValueError, match="terra系列"):
        codex_models.resolve_candidates([("codex", "terra", "medium")], catalog)


@pytest.mark.asyncio
async def test_model_list_rejects_repeated_cursor() -> None:
    """ページ送りが循環したときは一部の一覧を完全な結果として扱わない。"""

    async def request(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {"data": [], "nextCursor": "same"}

    with pytest.raises(ValueError, match="ページ送りが不正"):
        await codex_models.fetch_catalog(request)
