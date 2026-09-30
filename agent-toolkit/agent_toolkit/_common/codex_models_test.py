"""Codex App Serverのモデル一覧と系列指定の契約テスト。"""

from typing import Any

import pytest

from agent_toolkit._common import codex_models, next_action

_UNPATCHED_LIST_MODELS = codex_models.list_models
"""conftestが固定一覧へ差し替える前の`list_models`。取得失敗の出力を検証するテストが使う。"""


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


def test_family_resolution_failure_names_candidate_change() -> None:
    """系列を解決できない失敗は候補を変える操作を次の操作として持つ。"""
    with pytest.raises(next_action.ActionableError) as exc_info:
        codex_models.resolve_candidates([("codex", "terra", "medium")], [])
    assert "atk config set" in exc_info.value.next_action
    assert "--model-type" in exc_info.value.next_action


def test_model_list_failure_names_login_check_and_candidate_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """App Serverを起動できない失敗はログインの確認と候補の変更を次の操作として持つ。"""
    monkeypatch.setattr(codex_models, "_APP_SERVER_COMMAND", ("agent-toolkit-missing-codex-executable",))
    with pytest.raises(next_action.ActionableError) as exc_info:
        _UNPATCHED_LIST_MODELS()
    assert "codex login status" in exc_info.value.next_action
    assert "atk config set" in exc_info.value.next_action
