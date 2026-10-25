"""状態ファイルの読取、生存判定とsessionの重複排除を一覧と資源記録で共有する。"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib
from typing import Any

from agent_toolkit._agents_server import shared_layout

HEARTBEAT_EXPIRY_SECONDS = 120


@dataclasses.dataclass
class SessionSnapshot:
    """読み取れたsessionと、完全な集計を妨げた取得失敗を保持する。"""

    sessions: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)
    failures: list[str] = dataclasses.field(default_factory=list)


def heartbeat_cutoff() -> datetime.datetime:
    """状態ファイルの生存を判定する共通の境界時刻を返す。"""
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=HEARTBEAT_EXPIRY_SECONDS)


def heartbeat_is_current(payload: dict[str, Any], cutoff: datetime.datetime) -> bool:
    """旧形式の生存の印が無いファイルも含め、一覧が表示する生存条件を判定する。"""
    value = payload.get("heartbeat_at")
    if not isinstance(value, str):
        return True
    try:
        heartbeat = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return heartbeat.tzinfo is not None and heartbeat >= cutoff


def root_has_live_writer(
    root_session_id: str, state_root: pathlib.Path | None = None, *, cutoff: datetime.datetime | None = None
) -> bool:
    """ルートに生存の印が有効な状態ファイルがあるかを返す。読めないファイルは生存しているものとして扱う。

    読めないファイルを失効と扱うと、所有者が書込中のルートの通知や結果を他のMCPサーバーが回収するためである。
    """
    if cutoff is None:
        cutoff = heartbeat_cutoff()
    for path in shared_layout.list_status_files(root_session_id, state_root):
        try:
            payload = read_payload(path)
        except OSError, UnicodeError, ValueError:
            return True
        if heartbeat_is_current(payload, cutoff):
            return True
    return False


def ownerless_root_session_ids(exclude: str | None, state_root: pathlib.Path | None = None) -> list[str]:
    """生存の印が有効な書込主体のいないルートを、`exclude`を除いて安定順で返す。

    所有サーバーの起動単位が替わった後、旧起動単位のルートへ残った委譲先の通知と終端結果を回収する対象に使う。
    """
    cutoff = heartbeat_cutoff()
    return [
        root_session_id
        for root_session_id in shared_layout.list_root_session_ids(state_root)
        if root_session_id != exclude and not root_has_live_writer(root_session_id, state_root, cutoff=cutoff)
    ]


def read_payload(path: pathlib.Path) -> dict[str, Any]:
    """JSONオブジェクトを読み、取得不能を空の状態と区別するため例外を呼出側へ返す。"""
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("status payload is not an object")
    return payload


def load_root(
    root_session_id: str,
    state_root: pathlib.Path | None = None,
    *,
    cutoff: datetime.datetime | None = None,
) -> SessionSnapshot:
    """同じrootの状態を更新時刻で重複排除し、欠損のある観測には取得失敗を添える。"""
    snapshot = SessionSnapshot()
    if cutoff is None:
        cutoff = heartbeat_cutoff()
    try:
        paths = shared_layout.list_status_files(root_session_id, state_root, strict=True)
    except OSError as exc:
        snapshot.failures.append(f"{root_session_id}: {type(exc).__name__}")
        return snapshot
    for path in paths:
        try:
            payload = read_payload(path)
        except (OSError, UnicodeError, ValueError) as exc:
            snapshot.failures.append(f"{root_session_id}/{path.name}: {type(exc).__name__}")
            continue
        if "heartbeat_at" in payload and not isinstance(payload["heartbeat_at"], str):
            # 一覧の旧形式との互換性は保ち、壊れた生存情報を完全な資源集計とは扱わない。
            snapshot.failures.append(f"{root_session_id}/{path.name}: invalid heartbeat_at")
        if not heartbeat_is_current(payload, cutoff):
            # 古いheartbeatは通常の失効であり、欠損とは区別する。
            value = payload.get("heartbeat_at")
            try:
                heartbeat = datetime.datetime.fromisoformat(value) if isinstance(value, str) else None
            except ValueError:
                heartbeat = None
            if heartbeat is None or heartbeat.tzinfo is None:
                snapshot.failures.append(f"{root_session_id}/{path.name}: invalid heartbeat_at")
            continue
        raw_sessions = payload.get("sessions")
        if not isinstance(raw_sessions, list):
            snapshot.failures.append(f"{root_session_id}/{path.name}: invalid sessions")
            continue
        for raw in raw_sessions:
            session_id = raw.get("session_id") if isinstance(raw, dict) else None
            if not isinstance(raw, dict) or not isinstance(session_id, str):
                snapshot.failures.append(f"{root_session_id}/{path.name}: invalid session")
                continue
            host = payload.get("host_session_id")
            session: dict[str, Any] = dict(
                raw, owner_status_file=path.name, host_session_id=host if isinstance(host, str) else None
            )
            previous = snapshot.sessions.get(session_id)
            if previous is None or str(previous.get("updated_at", "")) <= str(session.get("updated_at", "")):
                snapshot.sessions[session_id] = session
    return snapshot
