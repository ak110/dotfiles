"""委譲先から委譲元のルートセッションへ通知を送る。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import uuid
from collections.abc import Mapping

from agent_toolkit._agents_server import status_file
from agent_toolkit._atk import outcome
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message
from agent_toolkit._common.next_action import report

DELIVERY_ELEMENT = AUTO_INSERTED_ELEMENT


def send_notification(
    body: str,
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """共有状態ディレクトリへ通知を1件保存する。"""
    if not body.strip():
        report(
            "通知本文は空文字列または空白だけにできません",
            next_action="空でない本文を`--body`か`--body-file`で渡して再実行する",
        )
        return 5

    identity = status_file.resolve_status_file_identity(os.environ if environment is None else environment)
    if identity is None or identity.host_session_id is None:
        report(
            "委譲先のsession識別子またはルートsessionを解決できません",
            next_action=(
                "`atk agents notify`はagents_serverが起動した委譲先の中でだけ使える。"
                "ルートのsessionでは通知は不要なため、結果は通常の応答で返す"
            ),
        )
        return 4

    directory = status_file.notices_directory(identity.root_session_id, state_root)
    directory.mkdir(parents=True, exist_ok=True)
    # 本文は委譲元の会話文脈へ入るため、配送元と作成主体を示す境界で囲む。
    delivery_body = auto_message(
        body,
        source="agents-notify",
        kind="delivery",
        attributes={"from": f"delegate:{identity.host_session_id}"},
    )
    payload = (
        json.dumps(
            {
                "version": 1,
                "session_id": identity.host_session_id,
                "sent_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "body": delivery_body,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    sent_at = datetime.datetime.now(datetime.UTC)
    path = directory / f"{identity.host_session_id}.{sent_at.strftime('%Y%m%dT%H%M%S%f')}.{uuid.uuid4().hex[:16]}.json"
    atomic_write(path, payload)
    outcome.report_success("委譲元へ通知を1件保存した")
    return 0
