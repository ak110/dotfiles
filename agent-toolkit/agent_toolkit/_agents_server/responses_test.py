"""`_agents_server/responses.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib

import pytest

from agent_toolkit._agents_server import (
    record_paths,
    responses,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_observed_session_identity_uses_last_identity_from_unique_physical_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終了応答の観測identityは一意な物理記録の最後の観測値を使う。"""
    record = tmp_path / "session.jsonl"
    # Codex 0.160.1の記録の`turn_context`行の形（最上位の`type`、`payload`の`model`と`effort`）を写した。
    entries = [
        {"type": "turn_context", "payload": {"cwd": "/work", "model": "gpt-6-sol", "effort": "high"}},
        {"type": "event_msg", "payload": {"type": "token_count"}},
        {"type": "turn_context", "payload": {"cwd": "/work", "model": "gpt-6.1-sol", "effort": "medium"}},
    ]
    record.write_text("".join(f"{json.dumps(entry)}\n" for entry in entries), encoding="utf-8")
    monkeypatch.setattr(
        record_paths,
        "find_session_record",
        lambda _session_id: record_paths.SessionRecord("codex", (record,)),
    )

    assert responses.observed_session_identity("session") == {
        "engine": "codex",
        "model": "gpt-6.1-sol",
        "effort": "medium",
        "source": "observed",
    }


@pytest.mark.parametrize("delivery", ["reply_failed", "reply_ambiguous", "reply_started"])
@pytest.mark.parametrize("error_info", [None, "threadStateIncompatible", "threadWriterConflict"])
def test_reply_guidance_distinguishes_writer_conflict(delivery: str, error_info: str | None) -> None:
    """公開射影は失敗した再開のwriter競合だけに専用の再送条件を付ける。"""
    response = responses.public_send_message_response(
        {"delivery": delivery, "label": "担当", "error": {"codexErrorInfo": error_info}}
    )
    assert "error" not in response
    if delivery == "reply_failed" and error_info == "threadWriterConflict":
        assert "未配送" in response["next_action"]
        assert "解放を確認" in response["next_action"]
        assert "同じ`session_id`へ`send_message`を再送" in response["next_action"]
    elif delivery == "reply_started":
        assert "next_action" not in response
    else:
        assert response["next_action"] == responses.REPLY_NEXT_ACTIONS[delivery]
