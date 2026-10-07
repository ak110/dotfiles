"""`atk agents notify`が置いた上り通知（`notices/`）の回収。

上り通知の配送媒体は共有状態ディレクトリ（`shared_layout.notices_directory`）とする。Codexの委譲先にはagents_server系のMCPツールもフックの発火機構も公開されず、Claudeの委譲先へ公開されるagents_server系のMCPツールは委譲元のsession登録簿を共有しないため、engineに依存しない媒体が他に無い。2026年9月6日に両engineの委譲先を1件ずつ起動して確かめた。この前提が成立しなくなった場合は、片方のengineの委譲先から送った通知が委譲元へ届かない事象として現れる。
"""

from __future__ import annotations

import contextlib
import json
import logging
import pathlib

from agent_toolkit._agents_server.result_projection import public_notice
from agent_toolkit._agents_server.shared_layout import notices_directory
from agent_toolkit._common.atomic_file import atomic_write


def take_notices(
    root_session_id: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
    *,
    stash_directory: pathlib.Path | None = None,
) -> list[dict[str, str]]:
    """待機対象sessionの正常な通知を回収し、送信時刻順に返す。"""
    directory = notices_directory(root_session_id, state_root)
    try:
        paths = tuple(directory.iterdir())
    except FileNotFoundError:
        paths = ()
    stashed: dict[str, pathlib.Path] = {}
    if stash_directory is not None:
        with contextlib.suppress(FileNotFoundError):
            stashed = {
                path.name: path
                for path in stash_directory.iterdir()
                if path.is_file() and path.suffix == ".json" and path.name.startswith(f"{session_id}.")
            }
    matched: list[tuple[str, str, dict[str, str]]] = []
    for path in (*stashed.values(), *paths):
        if not path.is_file() or path.suffix != ".json":
            continue
        if path.parent == directory and path.name in stashed:
            path.unlink(missing_ok=True)
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            if path.name.startswith(f"{session_id}."):
                notice = {"sent_at": "", "body": f"破損した上り通知を削除しました: {directory / path.name}: {exc}"}
                if stash_directory is not None and path.parent == directory:
                    atomic_write(stash_directory / path.name, json.dumps({"version": 1, "session_id": session_id, **notice}))
                if path.parent == directory:
                    path.unlink(missing_ok=True)
                matched.append(("", path.name, notice))
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("session_id") != session_id
            or not isinstance(payload.get("sent_at"), str)
            or not isinstance(payload.get("body"), str)
        ):
            if path.name.startswith(f"{session_id}."):
                notice = {"sent_at": "", "body": f"破損した上り通知を削除しました: {directory / path.name}: 必須項目が不正です"}
                if stash_directory is not None and path.parent == directory:
                    atomic_write(stash_directory / path.name, json.dumps({"version": 1, "session_id": session_id, **notice}))
                if path.parent == directory:
                    path.unlink(missing_ok=True)
                matched.append(("", path.name, notice))
            continue
        notice = {"sent_at": payload["sent_at"], "body": payload["body"]}
        if stash_directory is not None and path.parent == directory:
            atomic_write(stash_directory / path.name, json.dumps(payload, ensure_ascii=False) + "\n", fsync=True)
        matched.append((payload["sent_at"], path.name, notice))
    matched.sort(key=lambda item: (item[0], item[1]))
    taken: list[dict[str, str]] = []
    for _sent_at, file_name, notice in matched:
        if notice["sent_at"] == "" or stash_directory is not None:
            taken.append(notice)
            if stash_directory is not None:
                (directory / file_name).unlink(missing_ok=True)
            continue
        try:
            (directory / file_name).unlink()
        except FileNotFoundError:
            continue
        taken.append(notice)
    return [public_notice(notice) for notice in taken]


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")
