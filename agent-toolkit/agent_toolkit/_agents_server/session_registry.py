"""agents_serverが起動したsessionの終端状態をプロセス間で共有する。

所有側のagents_serverがsessionを解放した場合は、レコードを削除せず解放済みレコードへ置き換える。
照会した別プロセスが「所有側が正常に解放した」と「どのagents_serverにも記録が無い」を区別できるようにするためである。
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import json
import pathlib
import re
from typing import Any, Literal

from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_launchers as _session_launchers
from agent_toolkit._common.atomic_file import atomic_write

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
_STATUSES = frozenset({"starting", "running", "completed", "failed", "interrupted"})
ReleaseReason = Literal["retention_expired", "stopped"]
_RELEASE_REASONS: frozenset[str] = frozenset({"retention_expired", "stopped"})
LAUNCHER_KEY = _session_launchers.LAUNCHER_KEY


class Resolution(enum.StrEnum):
    """登録簿からsessionを観測した区分。"""

    TERMINAL = "terminal"
    RUNNING = "running"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    RELEASED = "released"


@dataclasses.dataclass(frozen=True)
class LaunchInfo:
    """`start`の時点で決まり、同じsessionを継続する間は再開と再起動をまたいでも変えない起動情報。

    再開で`SessionState`を再生成する処理と、session登録簿の書込・復元は、この定義の項目を走査して値を写す。
    項目を個別に列挙すると、項目を足したときに一部の処理が値を写さなくなり、型やテストでは検出されないためである。
    項目の値はいずれも文字列か`None`とし、`SessionState`と`SessionResumeState`は同名の属性を持つ。
    """

    label: str = ""
    prompt: str = ""
    # 項目を持たない旧形式の登録簿から復元したsessionでは開始時刻が不明なため`None`とする。
    created_at: str | None = None

    @classmethod
    def of(cls, source: object) -> LaunchInfo:
        """同名の属性を持つsessionの状態から起動情報を取り出す。"""
        return cls(**{field.name: getattr(source, field.name) for field in dataclasses.fields(cls)})

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> LaunchInfo:
        """登録簿のレコードから読み、文字列でない項目と無い項目はフィールドの初期値（空文字列か`None`）とする。"""
        return cls(
            **{field.name: payload[field.name] for field in dataclasses.fields(cls) if isinstance(payload.get(field.name), str)}
        )

    def as_kwargs(self) -> dict[str, Any]:
        """同名の引数を持つ生成処理へ渡す項目を返す。"""
        return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}

    def apply_to(self, target: object) -> None:
        """再開で再生成したsessionへ値を写す。`None`の項目は写さず、生成時の値を残す。"""
        for name, value in self.as_kwargs().items():
            if value is not None:
                setattr(target, name, value)


@dataclasses.dataclass(frozen=True)
class ResumeInfo:
    """再起動後のsession再開へ必要な実行条件。"""

    engine: str
    cwd: str
    model: str | None
    effort: str | None
    model_type: str | None
    launch_kind: Literal["delegate", "explore", "shell", "write"]
    turn_seq: int
    status: Literal["starting", "running", "completed", "failed", "interrupted"]
    launch_info: LaunchInfo = LaunchInfo()
    # 項目を持たない旧形式のレコードでは`None`とする。
    started_at: str | None = None
    session_updated_at: str | None = None
    # 最後に公開した時点のturnを表すengine固有の識別子（Codexのturn id）。
    # 再起動後に残存記録の終端を委譲先CLIの記録と照らすために使う。項目を持たない記録とCodex以外では`None`とする。
    turn_id: str | None = None
    fast_mode: bool | None = None


@dataclasses.dataclass(frozen=True)
class SessionResolution:
    """登録簿の解決結果と、利用可能な再開情報を保持する。"""

    state: Resolution
    resume_info: ResumeInfo | None = None
    # `RELEASED`の場合だけ、解放の理由と時刻（UTCのISO 8601）を保持する。
    released_reason: ReleaseReason | None = None
    released_at: str | None = None


def registry_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """session登録簿のディレクトリを返す。"""
    return _session_launchers.registry_directory(_atk_config.state_dir() if state_root is None else state_root)


def publish(
    session_id: str,
    *,
    terminal: bool,
    engine: str = "codex",
    cwd: str = "",
    model: str | None = None,
    effort: str | None = None,
    fast_mode: bool | None = None,
    model_type: str | None = None,
    launch_kind: Literal["delegate", "explore", "shell", "write"] = "delegate",
    turn_seq: int = 0,
    status: Literal["starting", "running", "completed", "failed", "interrupted"] | None = None,
    launch_info: LaunchInfo | None = None,
    started_at: str | None = None,
    session_updated_at: str | None = None,
    turn_id: str | None = None,
    launcher_session_id: str | None = None,
    state_root: pathlib.Path | None = None,
) -> None:
    """sessionの終端可否と再開条件を原子的に公開する。

    `launcher_session_id`はsessionを作成した時点の委譲元sessionの識別子である。
    省略した場合は既存のレコードが持つ値を引き継ぐ。`atk serve`の一覧が、親のセッション記録に
    起動結果が残らない委譲先の親を結ぶために読むため、状態の更新で失わないようにする。
    """
    _validate_session_id(session_id)
    if not isinstance(terminal, bool):
        raise TypeError("terminal must be a bool")
    status = ("completed" if terminal else "running") if status is None else status
    if status not in _STATUSES:
        raise ValueError(f"invalid status: {status}")
    payload = {
        "version": 2,
        "session_id": session_id,
        "terminal": terminal,
        "engine": engine,
        "cwd": cwd,
        "model": model,
        "effort": effort,
        "model_type": model_type,
        "launch_kind": launch_kind,
        "turn_seq": turn_seq,
        "status": status,
        "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    if launch_info is not None:
        # 版数2の任意項目として加え、項目を持たない旧形式のレコードは`LaunchInfo.from_record`が空文字列か`None`として読む。
        payload.update((name, value) for name, value in launch_info.as_kwargs().items() if value is not None)
    if started_at is not None:
        payload["started_at"] = started_at
    if session_updated_at is not None:
        payload["session_updated_at"] = session_updated_at
    if turn_id:
        payload["turn_id"] = turn_id
    if engine == "codex" and fast_mode is not None:
        payload["fast_mode"] = fast_mode
    path = registry_directory(state_root) / f"{session_id}.json"
    launcher = launcher_session_id if launcher_session_id is not None else _recorded_launcher(path)
    if launcher is not None:
        payload[LAUNCHER_KEY] = launcher
    atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


_recorded_launcher = _session_launchers.read_launcher


def resolve(session_id: str, *, state_root: pathlib.Path | None = None) -> SessionResolution:
    """登録済みsessionを終端・実行中・解放済み・不在・読取不能へ区分して返す。"""
    _validate_session_id(session_id)
    path = registry_directory(state_root) / f"{session_id}.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SessionResolution(Resolution.MISSING)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return SessionResolution(Resolution.UNREADABLE)
    if (
        not isinstance(payload, dict)
        or payload.get("session_id") != session_id
        or not isinstance(payload.get("updated_at"), str)
    ):
        return SessionResolution(Resolution.UNREADABLE)
    if payload.get("version") == 3:
        reason = payload.get("released_reason")
        if reason not in _RELEASE_REASONS:
            return SessionResolution(Resolution.UNREADABLE)
        return SessionResolution(Resolution.RELEASED, released_reason=reason, released_at=payload["updated_at"])
    if payload.get("version") == 1:
        if not isinstance(payload.get("terminal"), bool):
            return SessionResolution(Resolution.UNREADABLE)
        return SessionResolution(Resolution.TERMINAL if payload["terminal"] else Resolution.RUNNING)
    if payload.get("version") != 2 or not isinstance(payload.get("terminal"), bool):
        return SessionResolution(Resolution.UNREADABLE)
    info = _resume_info(payload)
    if info is None:
        return SessionResolution(Resolution.UNREADABLE)
    return SessionResolution(Resolution.TERMINAL if payload["terminal"] else Resolution.RUNNING, info)


def release(session_id: str, *, reason: ReleaseReason, state_root: pathlib.Path | None = None) -> None:
    """所有側が解放したsessionの登録を、再開条件を持たない解放済みレコードへ置き換える。

    解放済みレコードは復元の対象にならず、7日超の共有状態の掃引で他のレコードと同じく回収される。
    """
    _validate_session_id(session_id)
    if reason not in _RELEASE_REASONS:
        raise ValueError(f"invalid release reason: {reason}")
    path = registry_directory(state_root) / f"{session_id}.json"
    payload: dict[str, Any] = {
        "version": 3,
        "session_id": session_id,
        "released_reason": reason,
        "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    launcher = _recorded_launcher(path)
    if launcher is not None:
        payload[LAUNCHER_KEY] = launcher
    atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id}")


def _resume_info(payload: dict[str, Any]) -> ResumeInfo | None:
    status = payload.get("status")
    launch_kind = payload.get("launch_kind")
    if (
        not isinstance(payload.get("engine"), str)
        or not isinstance(payload.get("cwd"), str)
        or payload.get("model") is not None
        and not isinstance(payload.get("model"), str)
        or payload.get("effort") is not None
        and not isinstance(payload.get("effort"), str)
        or payload.get("model_type") is not None
        and not isinstance(payload.get("model_type"), str)
        or launch_kind not in {"delegate", "explore", "shell", "write"}
        or not isinstance(payload.get("turn_seq"), int)
        or isinstance(payload.get("turn_seq"), bool)
        or status not in _STATUSES
    ):
        return None
    return ResumeInfo(
        engine=payload["engine"],
        cwd=payload["cwd"],
        model=payload["model"],
        effort=payload["effort"],
        model_type=payload["model_type"],
        launch_kind=launch_kind,
        turn_seq=payload["turn_seq"],
        status=status,
        launch_info=LaunchInfo.from_record(payload),
        started_at=payload.get("started_at") if isinstance(payload.get("started_at"), str) else None,
        session_updated_at=payload.get("session_updated_at") if isinstance(payload.get("session_updated_at"), str) else None,
        turn_id=payload.get("turn_id") if isinstance(payload.get("turn_id"), str) and payload.get("turn_id") else None,
        fast_mode=payload.get("fast_mode")
        if payload["engine"] == "codex" and isinstance(payload.get("fast_mode"), bool)
        else None,
    )
