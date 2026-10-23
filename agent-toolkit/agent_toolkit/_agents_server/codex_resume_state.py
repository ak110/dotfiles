"""保存済みCodex会話の不透明な状態を、同じ生成先で再開できることを確かめる。

観測した記録形と架空fixtureの境界は`docs/development/audit-records.md`の
「agent-toolkit/agent_toolkit/_agents_server/codex_resume_state.py：会話状態の生成先：2026年10月23日」にある。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_toolkit._common import session_record_paths

INCOMPATIBLE_STATE = "threadStateIncompatible"


class IncompatibleResumeError(RuntimeError):
    """会話状態を再開先が受理できる条件を確認できなかった。"""


def _opaque(value: Any) -> bool:
    """Responsesの圧縮項目と暗号化内容を、値を解釈せず検出する。"""
    if isinstance(value, list):
        return any(_opaque(item) for item in value)
    if not isinstance(value, dict):
        return False
    return (
        value.get("type") == "compaction"
        or bool(value.get("compaction_response_id"))
        or bool(value.get("encrypted_content"))
        or any(_opaque(item) for item in value.values() if isinstance(item, dict | list))
    )


def validate_resume(
    session_id: str,
    target: str,
    confirmed_provider: str | None,
    record_path: str | None,
) -> None:
    """同一生成先への再開か、不透明状態を含まない記録だけを許可する。

    confirmed_providerは本関数が再開を許可した接続先、または新規開始で確定した接続先である。
    旧版の選択済みproviderには、状態を受理しなかった移行先も含まれるため使用しない。
    """
    if confirmed_provider == target:
        return
    paths = (Path(record_path),) if record_path else session_record_paths.find_codex_records(session_id)
    if len(paths) != 1:
        raise IncompatibleResumeError("Codexのセッション記録を一意に解決できず、再開先との状態互換性を確認できない")
    source = confirmed_provider
    metadata_found = False
    metadata_providers: set[str | None] = set()
    opaque = False
    try:
        with paths[0].open(encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("rollout record is not an object")
                payload = record.get("payload")
                if record.get("type") == "session_meta" and isinstance(payload, dict):
                    if payload.get("id") != session_id:
                        raise ValueError("rollout session id does not match")
                    metadata_found = True
                    provider = payload.get("model_provider")
                    metadata_providers.add(provider if isinstance(provider, str) and provider else None)
                elif record.get("type") == "compacted" and isinstance(payload, dict):
                    history = payload.get("replacement_history")
                    # replacement_historyがある圧縮は、その時点の再開対象の履歴を置換する。
                    opaque = _opaque(payload) if isinstance(history, list) else opaque or _opaque(payload)
                elif record.get("type") == "response_item":
                    opaque = opaque or _opaque(payload)
    except (OSError, UnicodeError, ValueError) as error:
        raise IncompatibleResumeError("Codexのセッション記録を読めず、再開先との状態互換性を確認できない") from error
    if not metadata_found:
        raise IncompatibleResumeError("Codexのセッション記録に開始情報が無く、再開先との状態互換性を確認できない")
    if source is None and len(metadata_providers) == 1:
        source = next(iter(metadata_providers))
    if opaque and source != target:
        raise IncompatibleResumeError("Codexの会話に再開先との互換性を確認できない圧縮状態または暗号化状態がある")


def handoff_failure(status: str, error: Any) -> bool:
    """状態互換性の拒否と、旧版が通信切断として保持した終端を引き継ぎ対象とする。"""
    if status != "failed" or not isinstance(error, dict):
        return False
    message = error.get("message")
    return error.get("codexErrorInfo") == INCOMPATIBLE_STATE or (
        isinstance(message, str)
        and (message.startswith("Codex App Server stopped:") or message == "Codex connection rejected the request")
    )
