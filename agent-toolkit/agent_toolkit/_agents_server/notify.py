"""委譲先から委譲元のルートセッションへ通知を送る。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import uuid
from collections.abc import Mapping

from agent_toolkit._agents_server import status_file
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.message_format import AUTO_INSERTED_ELEMENT, auto_message

DELIVERY_ELEMENT = AUTO_INSERTED_ELEMENT
COMPOSED_BY_CALLER = "caller"


def send_notification(
    body: str,
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """共有状態ディレクトリへ通知を1件保存する。"""
    if not body.strip():
        print("通知本文は空文字列又は空白だけにできません", file=sys.stderr)
        return 5

    identity = status_file.resolve_status_file_identity(os.environ if environment is None else environment)
    if identity is None or identity.host_session_id is None:
        print("委譲先のsession識別子又はルートsessionを解決できません", file=sys.stderr)
        return 4

    directory = status_file.notices_directory(identity.root_session_id, state_root)
    directory.mkdir(parents=True, exist_ok=True)
    # 本文は委譲元の会話文脈へ入るため、配送元と作成主体を示す境界で囲む。
    delivery_body = auto_message(
        body,
        source="agent-toolkit/agents-notify",
        kind="agent-delivery",
        attributes={"from": f"delegate:{identity.host_session_id}", "composed-by": COMPOSED_BY_CALLER},
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
    return 0
