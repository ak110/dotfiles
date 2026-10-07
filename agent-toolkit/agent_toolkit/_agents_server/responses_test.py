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
    """終端応答の観測identityは一意な物理記録の最後の観測値を使う。"""
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
