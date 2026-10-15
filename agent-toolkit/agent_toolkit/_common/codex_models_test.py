"""Codex App Serverのモデル一覧と系列指定の契約テスト。"""

import asyncio
import json
from types import SimpleNamespace
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


@pytest.mark.asyncio
async def test_model_catalog_reads_long_response_and_unterminated_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """初期化とモデル一覧を同じbufferへ受信しても、長い最終応答を欠損なく解析する。"""
    stream = asyncio.StreamReader(limit=64)
    model = {**_model("gpt-6-sol", "medium"), "description": "あ" * 50000}
    stream.feed_data(
        (
            json.dumps({"id": 1, "result": {}}) + "\n" + json.dumps({"id": 2, "result": {"data": [model]}}, ensure_ascii=False)
        ).encode()
    )
    stream.feed_eof()

    class Stdin:
        def write(self, _data: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

    async def wait() -> int:
        return 0

    async def start(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(stdin=Stdin(), stdout=stream, returncode=0, wait=wait)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    assert await codex_models.list_models_from_app_server() == [model]
