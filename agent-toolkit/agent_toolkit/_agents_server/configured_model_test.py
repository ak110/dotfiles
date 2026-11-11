"""設定の解決失敗で元の上限処理を保ち、診断から設定確認へ到達できる。"""

import io
import logging

import pytest

from agent_toolkit._agents_server import configured_model, state


@pytest.mark.asyncio
async def test_config_resolution_failure_keeps_limit_path_and_reports_next_action(monkeypatch, tmp_path):
    """不正設定をモデル変更へ変換せず、対象キーの公開照会を警告で案内する。"""

    def fail(_model_type, **_kwargs):
        raise ValueError("候補の書式が不正")

    monkeypatch.setattr(configured_model.config, "parse_unresolved_model_candidates", fail)
    session = state.SessionState("unchanged", str(tmp_path), engine="claude", model="before", model_type="chores")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger = logging.getLogger("agent-toolkit.agents-server.configured-model")
    logger.addHandler(handler)
    try:
        assert await configured_model.changed_model_error(session) is None
    finally:
        logger.removeHandler(handler)
        handler.close()
    message = output.getvalue()
    assert "候補の書式が不正" in message
    assert "atk config get chores_model" in message and "修正せよ" in message
    assert session.model == "before" and session.error is None
