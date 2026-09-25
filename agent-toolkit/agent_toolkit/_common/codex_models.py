"""Codex App Serverのモデル一覧から系列指定を完全なモデルIDへ解決する。"""

import asyncio
import json
import re
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

FAMILIES = frozenset({"astra", "sol", "terra", "luna"})
_VERSIONED_MODEL = re.compile(r"^gpt-(\d+)(?:\.(\d+))?-(astra|sol|terra|luna)$")
_MODEL_LIST_TIMEOUT_SECONDS = 30.0
_APP_SERVER_COMMAND = ("codex", "app-server", "--stdio")


def needs_catalog(candidates: list[tuple[str, str, str]]) -> bool:
    """系列名を持つCodex候補が1件以上あるか返す。"""
    return any(engine == "codex" and model in FAMILIES for engine, model, _effort in candidates)


def resolve_candidates(candidates: list[tuple[str, str, str]], catalog: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """表示可能なモデルの同系列最新版を選び、指定effortを検証する。"""
    resolved: list[tuple[str, str, str]] = []
    for engine, model, effort in candidates:
        if engine != "codex" or model not in FAMILIES:
            resolved.append((engine, model, effort))
            continue
        available: list[tuple[tuple[int, int], str, dict[str, Any]]] = []
        for item in catalog:
            if item.get("hidden") is True:
                continue
            candidate_id = item.get("model") or item.get("id")
            if not isinstance(candidate_id, str):
                continue
            match = _VERSIONED_MODEL.fullmatch(candidate_id)
            if match is None or match.group(3) != model:
                continue
            available.append(((int(match.group(1)), int(match.group(2) or 0)), candidate_id, item))
        if not available:
            raise ValueError(f"Codex App Serverのmodel/listに利用可能な{model}系列がありません")
        _version, selected_id, selected = max(available, key=lambda item: (item[0], item[1]))
        efforts = selected.get("supportedReasoningEfforts")
        supported = (
            {item.get("reasoningEffort") for item in efforts if isinstance(item, dict)} if isinstance(efforts, list) else set()
        )
        if effort not in supported:
            raise ValueError(f"Codexモデル{selected_id}はreasoning effort {effort}を受理しません")
        resolved.append((engine, selected_id, effort))
    return resolved


async def fetch_catalog(request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """`model/list`を最終ページまで取得し、応答形式を検証する。"""
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        params: dict[str, Any] = {"limit": 100, "includeHidden": False}
        if cursor is not None:
            params["cursor"] = cursor
        response = await request("model/list", params)
        data = response.get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("Codex App Serverのmodel/listがモデル配列を返しませんでした")
        result.extend(data)
        next_cursor = response.get("nextCursor")
        if next_cursor is None:
            return result
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise ValueError("Codex App Serverのmodel/listのページ送りが不正です")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


async def list_models_from_app_server() -> list[dict[str, Any]]:
    """短命なApp Server接続で、独立した`atk config`経路の一覧を取得する。"""
    with tempfile.TemporaryFile(mode="w+b") as diagnostics:
        try:
            process = await asyncio.create_subprocess_exec(
                *_APP_SERVER_COMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=diagnostics,
            )
        except OSError as error:
            raise ValueError(f"Codex App Serverを起動できません: {error}") from error
        stdin = process.stdin
        stdout = process.stdout
        assert stdin is not None and stdout is not None
        next_id = 0

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal next_id
            next_id += 1
            request_id = next_id
            message = {"id": request_id, "method": method, "params": params}
            stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            await stdin.drain()
            while True:
                try:
                    line = await asyncio.wait_for(stdout.readline(), _MODEL_LIST_TIMEOUT_SECONDS)
                except TimeoutError as error:
                    raise ValueError(f"Codex App Serverの{method}が時間内に応答しませんでした") from error
                if not line:
                    diagnostics.seek(0)
                    detail = diagnostics.read(4096).decode("utf-8", errors="replace").strip()
                    raise ValueError(f"Codex App Serverが{method}の応答前に終了しました: {detail}")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError("Codex App Serverが不正なJSONを返しました") from error
                if not isinstance(response, dict) or response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise ValueError(f"Codex App Serverの{method}が失敗しました: {response['error']}")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise ValueError(f"Codex App Serverの{method}が不正な結果を返しました")
                return result

        try:
            await request(
                "initialize",
                {"clientInfo": {"name": "agent-toolkit-config", "version": "1.0"}, "capabilities": {}},
            )
            stdin.write(b'{"method":"initialized","params":{}}\n')
            await stdin.drain()
            return await fetch_catalog(request)
        finally:
            stdin.close()
            if process.returncode is None:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


def list_models() -> list[dict[str, Any]]:
    """同期的な設定CLIから短命App Serverを使って一覧を取得する。"""
    try:
        return asyncio.run(list_models_from_app_server())
    except (OSError, ValueError) as error:
        raise ValueError(f"Codexの利用可能モデル一覧を取得できません: {error}") from error
