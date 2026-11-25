"""Codex App Serverのモデル一覧から系列指定を完全なモデルIDへ解決する。"""

import asyncio
import json
import re
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common import process_tree, stream_lines

FAMILIES = frozenset({"astra", "sol", "terra", "luna"})
_VERSIONED_MODEL = re.compile(r"^gpt-(\d+)(?:\.(\d+))?-(astra|sol|terra|luna)$")
_MODEL_LIST_TIMEOUT_SECONDS = 30.0
_APP_SERVER_COMMAND = ("codex", "app-server", "--stdio")
_CHANGE_CANDIDATES_NEXT_ACTION = (
    "`atk config set <KEY> <VALUE>`で工程別モデル設定の候補を変えるか、`--model-type`に別の候補を指定して再実行する"
)
"""系列を完全IDへ解決できない場合に添える次の操作。"""


class CatalogResponseError(ValueError):
    pass


class CatalogResolutionError(_next_action.ActionableError):
    pass


def requires_catalog(candidate: tuple[str, str, str]) -> bool:
    engine, model, _effort = candidate
    return engine == "codex" and model in FAMILIES


def needs_catalog(candidates: list[tuple[str, str, str]]) -> bool:
    """系列名を持つCodex候補が1件以上あるか返す。"""
    return any(map(requires_catalog, candidates))


def _latest_model(family: str, catalog: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    available: list[tuple[tuple[int, int], str, dict[str, Any]]] = []
    for item in catalog:
        if item.get("hidden") is True:
            continue
        candidate_id = item.get("model") or item.get("id")
        if not isinstance(candidate_id, str):
            continue
        match = _VERSIONED_MODEL.fullmatch(candidate_id)
        if match is None or match.group(3) != family:
            continue
        available.append(((int(match.group(1)), int(match.group(2) or 0)), candidate_id, item))
    if not available:
        raise ValueError(f"Codex App Serverのmodel/listに利用可能な{family}系列がありません")
    _version, identifier, item = max(available, key=lambda entry: (entry[0], entry[1]))
    return identifier, item


def latest_model(family: str, catalog: list[dict[str, Any]]) -> str:
    return _latest_model(family, catalog)[0]


def resolve_candidate(candidate: tuple[str, str, str], catalog: list[dict[str, Any]]) -> tuple[str, str, str]:
    if not requires_catalog(candidate):
        return candidate
    engine, model, effort = candidate
    try:
        selected_id, selected = _latest_model(model, catalog)
    except ValueError as error:
        raise CatalogResolutionError(str(error), next_action=_CHANGE_CANDIDATES_NEXT_ACTION) from error
    efforts = selected.get("supportedReasoningEfforts")
    if not isinstance(efforts, list) or not any(
        isinstance(item, dict) and item.get("reasoningEffort") == effort for item in efforts
    ):
        raise CatalogResolutionError(
            f"Codexモデル{selected_id}はreasoning effort {effort}を受理しません",
            next_action=_CHANGE_CANDIDATES_NEXT_ACTION,
        )
    return engine, selected_id, effort


def resolve_candidates(candidates: list[tuple[str, str, str]], catalog: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [resolve_candidate(candidate, catalog) for candidate in candidates]


async def fetch_catalog(request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        params: dict[str, Any] = {"limit": 100, "includeHidden": False}
        if cursor is not None:
            params["cursor"] = cursor
        response = await request("model/list", params)
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise CatalogResponseError("Codex App Serverのmodel/listがモデル配列を返しませんでした")
        result.extend(data)
        next_cursor = response.get("nextCursor")
        if next_cursor is None:
            return result
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise CatalogResponseError("Codex App Serverのmodel/listのページ送りが不正です")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


async def with_app_server(
    operation: Callable[[Callable[..., Awaitable[dict[str, Any]]]], Awaitable[Any]],
    *,
    command: tuple[str, ...] | None = None,
    cwd: str | None = None,
) -> Any:
    with tempfile.TemporaryFile(mode="w+b") as diagnostics:
        try:
            process = await asyncio.create_subprocess_exec(
                *(command if command is not None else _APP_SERVER_COMMAND),
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=diagnostics,
            )
        except OSError as error:
            raise ValueError(f"Codex App Serverを起動できません: {error}") from error
        stdin = process.stdin
        stdout = process.stdout
        assert stdin is not None and stdout is not None
        lines = stream_lines.iter_lines(stdout)
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
                    line = await asyncio.wait_for(anext(lines, b""), _MODEL_LIST_TIMEOUT_SECONDS)
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
            return await operation(request)
        finally:
            stdin.close()

            result = None
            if process.returncode is None:
                result = await asyncio.to_thread(process_tree.stop_tree_by_pid, process.pid)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
            if result is not None:
                process_tree.log_residual(result.descriptions(exclude_pid=process.pid), context="model/list")


async def list_models_from_app_server() -> list[dict[str, Any]]:
    return await with_app_server(fetch_catalog)


def list_models_at(command: tuple[str, ...], *, cwd: str | None = None) -> list[dict[str, Any]]:
    return asyncio.run(with_app_server(fetch_catalog, command=command, cwd=cwd))


def list_models() -> list[dict[str, Any]]:
    """同期的な設定CLIから短命App Serverを使って一覧を取得する。"""
    try:
        return asyncio.run(list_models_from_app_server())
    except (OSError, ValueError) as error:
        raise _next_action.ActionableError(
            f"Codexの利用可能モデル一覧を取得できません: {error}",
            next_action=(
                "`codex login status`で`codex`へのログインを確認して再実行するか、"
                "`atk config set <KEY> <VALUE>`でCodex以外の候補へ変える"
            ),
        ) from error
