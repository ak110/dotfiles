"""`atk agents notify`が置いた上り通知（`notices/`）の回収。

上り通知は共有状態ディレクトリ（`shared_layout.notices_directory`）を媒体とし、
別のMCPサーバーが管理する多段委譲でも同じ通知を回収できるようにする。
共有ルートの一致と、通知を受け取る起動主体の一致は分けて確認する。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import pathlib

from agent_toolkit._agents_server import status_reader
from agent_toolkit._agents_server.result_projection import public_notice
from agent_toolkit._agents_server.shared_layout import notices_directory
from agent_toolkit._common import session_launchers, session_registry


def recipient_matches(
    root_session_id: str,
    session_id: str,
    recipient_session_id: str | None,
    owner_status_file: str | None,
    state_root: pathlib.Path | None = None,
) -> bool | None:
    """起動主体または状態の所有者を比較し、どちらも不明なら未確定を返す。"""
    owner = status_reader.load_root(root_session_id, state_root).sessions.get(session_id, {}).get("owner_status_file")
    if owner is not None:
        return owner == owner_status_file
    launcher = session_launchers.read_launcher(session_registry.registry_directory(state_root) / f"{session_id}.json")
    return None if launcher is None else launcher == recipient_session_id


@dataclasses.dataclass(frozen=True)
class PendingNotice:
    path: pathlib.Path
    sent_at: str
    body: str


def _read_notice(path: pathlib.Path, session_id: str, source: pathlib.Path) -> PendingNotice | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        reason = str(exc)
    else:
        if (
            isinstance(payload, dict)
            and payload.get("version") == 1
            and payload.get("session_id") == session_id
            and isinstance(payload.get("sent_at"), str)
            and isinstance(payload.get("body"), str)
        ):
            return PendingNotice(path, payload["sent_at"], payload["body"])
        reason = "必須項目が不正です"
    if path.name.startswith(f"{session_id}."):
        return PendingNotice(path, "", f"破損した上り通知を削除しました: {source / path.name}: {reason}")
    return None


def read_notices(
    root_session_id: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
    *,
    recipient_session_id: str | None,
    owner_status_file: str | None,
    stash_directory: pathlib.Path | None = None,
) -> list[PendingNotice]:
    """宛先を確認し、通知を送信時刻順に読む。原本とCLIの退避物は変更しない。"""
    directory = notices_directory(root_session_id, state_root)
    paths = {path.name: path for path in directory.glob("*.json") if path.is_file()}
    if stash_directory is not None:
        paths.update(
            (path.name, path)
            for path in stash_directory.glob("*.json")
            if path.name.startswith(f"{session_id}.") and path.is_file()
        )
    if (
        not paths
        or recipient_matches(root_session_id, session_id, recipient_session_id, owner_status_file, state_root) is not True
    ):
        return []
    notices = [notice for path in paths.values() if (notice := _read_notice(path, session_id, directory)) is not None]
    return sorted(notices, key=lambda notice: (notice.sent_at, notice.path.name))


def consume_notices(pending: list[PendingNotice]) -> list[dict[str, str]]:
    """応答を返す時点で原本を取得できた通知だけを載せる。"""
    taken = []
    for notice in pending:
        try:
            notice.path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            # 他の通知や確定済みの終了結果は返し、この原本は次回に残す。
            _LOG.exception("上り通知を回収できません: %s", notice.path)
            continue
        taken.append(public_notice({"body": notice.body}))
    return taken


def take_notices(
    root_session_id: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
    *,
    recipient_session_id: str | None,
    owner_status_file: str | None,
    stash_directory: pathlib.Path | None = None,
) -> list[dict[str, str]]:
    """CLIの同じ待機runでは退避済みの通知も再読する。"""
    pending = read_notices(
        root_session_id,
        session_id,
        state_root,
        recipient_session_id=recipient_session_id,
        owner_status_file=owner_status_file,
        stash_directory=stash_directory,
    )
    if stash_directory is None:
        return consume_notices(pending)
    taken = []
    for notice in pending:
        if notice.path.parent == stash_directory:
            # 旧CLIが退避済みで、削除前に停止した原本。
            (notices_directory(root_session_id, state_root) / notice.path.name).unlink(missing_ok=True)
        else:
            stash_directory.mkdir(parents=True, exist_ok=True)
            try:
                notice.path.replace(stash_directory / notice.path.name)
            except FileNotFoundError:
                continue
        taken.append(public_notice({"body": notice.body}))
    return taken


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")
