"""委譲先から委譲元のルートセッションへ通知を送る。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
from collections.abc import Mapping

import _agents_server_status_file as status_file


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
    payload = (
        json.dumps(
            {
                "version": 1,
                "session_id": identity.host_session_id,
                "sent_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "body": body,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    sequence = 1
    while True:
        path = directory / f"{identity.host_session_id}.{sequence}.json"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            sequence += 1
            continue
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(payload)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return 0
