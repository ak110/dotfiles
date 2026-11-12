"""利用上限の拒否時だけ、工程の現在設定に従うモデル切替を判定する。"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from agent_toolkit._agents_server import engine_availability, unavailable_candidates
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._atk import config, config_settings
from agent_toolkit._common import codex_models

_LOG = logging.getLogger("agent-toolkit.agents-server.configured-model")


class ConfiguredModelChanged(Exception):
    """下位CLIの再試行を終了し、委譲元へ引き継ぎ起動を返す。"""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error["message"])
        self.error = error


async def changed_model_error(session: SessionState) -> dict[str, Any] | None:
    """工程設定の先頭の利用可能候補が別engine/modelなら、既存の可用性失敗へ変換する。

    呼出側が利用上限の拒否を確定してから呼ぶ。effortだけの変更と候補列の直接指定は切替の契機にしない。
    """
    model_type = session.model_type
    if (
        session.model is None
        or model_type is None
        or model_type == "orchestrate"
        or f"{model_type}_model" not in config_settings.MODEL_DEFAULTS
    ):
        return None
    try:
        candidates = await run_file_operation(config.parse_unresolved_model_candidates, model_type)
        if codex_models.needs_catalog(candidates):
            catalog = await codex_models.list_models_from_app_server()
            candidates = codex_models.resolve_candidates(candidates, catalog)
        recorded = await run_file_operation(
            unavailable_candidates.load_unavailable_candidates,
            model_type,
            session.launch_kind,
            now=datetime.datetime.now(datetime.UTC),
        )
    except (OSError, ValueError) as error:
        # 不正な設定や照会失敗をモデル変更の指示と推測せず、元の上限対応を維持する。
        _LOG.warning(
            "利用上限時のモデル設定を解決できない: %s。"
            "`atk config get %s_model`で設定を確認し、診断が示す設定値またはモデル一覧の照会先を修正せよ。",
            error,
            model_type,
        )
        return None
    remaining = [
        candidate
        for candidate in candidates
        if candidate not in recorded
        or candidate[0] == "claude"
        and recorded[candidate] == engine_availability.LEGACY_CLAUDE_RATE_LIMIT_REASON
    ]
    if not remaining:
        return None
    selected = remaining[0]
    if selected[:2] == (session.engine, session.model):
        return None
    return {
        "message": "利用上限で拒否され、工程設定のモデルが変更されているため、前任の記録を後任へ引き継ぐ",
        "apiErrorStatus": 429,
        "configuredModelChanged": True,
    }
