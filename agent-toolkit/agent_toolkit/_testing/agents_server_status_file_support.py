"""status_fileの分割試験が共有する準備処理。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import datetime
import json
import os
import pathlib
import typing

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import shared_layout, shared_roots, state
from agent_toolkit._agents_server import status_file as subject
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import atomic_file
from agent_toolkit._testing.agents_server_support import FakeBackend


async def _wait_now(manager: server_manager.AgentsServerManager) -> dict[str, typing.Any]:
    """待機せずに現在の終了状態を返すwaitを発行する。"""
    manager._wait_timeouts["main"] = 0.0  # pylint: disable=protected-access
    return await manager.wait()


class _WindowsAccessDenied(PermissionError):
    """Windowsの`os.replace`が返すWinError 5と同じ識別値を持つ置換拒否。"""

    winerror = 5


def _completed_with_retention_deadline(session_id: str, tmp_path: pathlib.Path, *, turn_seq: int = 0) -> state.SessionState:
    """完了した結果を持ち、0.03秒後に保持期限へ達するsessionを返す。"""
    session = state.SessionState(session_id, str(tmp_path), announced=True, turn_seq=turn_seq)
    session.status = "completed"
    session.agent_message = "完了"
    session.record_turn_completed()
    session.touch()
    session.retention_deadline = asyncio.get_running_loop().time() + 0.03
    return session


def _root_writer(sessions: dict[str, state.SessionState], tmp_path: pathlib.Path) -> subject.StatusFileWriter:
    """ルート`root`の状態ファイルを`tmp_path`配下へ即時に書くwriterを返す。"""
    return subject.StatusFileWriter(
        sessions,
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )


def _write_descendant_file(
    tmp_path: pathlib.Path,
    file_name: str,
    host_session_id: str,
    statuses: dict[str, str],
    *,
    heartbeat_age: float = 0,
) -> pathlib.Path:
    """別の書込主体が書いた子孫の状態ファイルを置く。"""
    path = shared_layout.status_directory("root", tmp_path) / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=heartbeat_age)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "host_session_id": host_session_id,
                "heartbeat_at": heartbeat.isoformat(),
                "updated_at": heartbeat.isoformat(),
                "sessions": [{"session_id": session_id, "status": status} for session_id, status in statuses.items()],
            }
        ),
        encoding="utf-8",
    )
    return path


def _terminal_parent(tmp_path: pathlib.Path, status: str) -> state.SessionState:
    """turnが終了した親sessionを返す。"""
    session = state.SessionState("parent", str(tmp_path), announced=True, turn_seq=1, label="lane-01-exec")
    session.status = status
    session.agent_message = "待機中: grandchild"
    session.record_turn_completed()
    session.touch()
    return session


def _shown_sessions(writer: subject.StatusFileWriter) -> dict[str, str]:
    """状態ファイルへ書かれたsessionの識別子と状態を返す。"""
    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    return {item["session_id"]: item["status"] for item in payload["sessions"]}


def _use_candidates(monkeypatch: pytest.MonkeyPatch, *candidates: tuple[str, str, str]) -> None:
    """session開始時に解決するモデル候補列を固定する。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: list(candidates))


def _status_writer(tmp_path: pathlib.Path) -> subject.StatusFileWriter:
    """公開managerを通した操作に使うルートwriterを返す。"""
    return subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )


class _FakeStatusBackend:
    """状態ファイルの公開フローだけを通す偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState]) -> None:
        self.sessions = sessions
        self.count = 0
        self.release_calls: list[str] = []

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
        **kwargs: typing.Any,
    ) -> state.SessionState:
        self.count += 1
        session = state.SessionState(
            f"session-{self.count}",
            cwd,
            model=model,
            effort=effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            turn_seq=1,
            prompt=kwargs.get("request_prompt", _prompt),
        )
        self.sessions[session.session_id] = session
        state.initialize_turn(session)
        return session

    async def close(self) -> None:
        """外部資源を持たないため何もしない。"""

    async def release_session(self, session: state.SessionState) -> None:
        """解放対象を検証用に記録する。"""
        self.release_calls.append(session.session_id)

    async def send_message(self, session: state.SessionState, _prompt: str) -> dict[str, object]:
        """新しいreply turnを開始する。"""
        session.turn_seq += 1
        state.initialize_turn(session)
        return {"delivery": "reply_started", "previous_result": None}


class _UnavailableStatusBackend(_FakeStatusBackend):
    """指定モデルをengine利用不能として終了させる偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], *, unavailable_models: set[str]) -> None:
        super().__init__(sessions)
        self._unavailable_models = unavailable_models

    async def start(self, *args: typing.Any, **kwargs: typing.Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        if session.model in self._unavailable_models:
            session.status = "failed"
            session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
            session.record_turn_completed()
            session.touch()
        return session


class _DelayedUnavailableStatusBackend(_FakeStatusBackend):
    """起動応答後に指定モデルを利用不能として終了させる偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        *,
        condition: asyncio.Condition,
        unavailable_models: set[str],
    ) -> None:
        super().__init__(sessions)
        self._condition = condition
        self._unavailable_models = unavailable_models
        self._pending: list[asyncio.Task[None]] = []

    async def start(self, *args: typing.Any, **kwargs: typing.Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        if session.model in self._unavailable_models:
            self._pending.append(asyncio.create_task(self._fail_after_response(session)))
        return session

    async def _fail_after_response(self, session: state.SessionState) -> None:
        await asyncio.sleep(0)
        session.status = "failed"
        session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
        session.record_turn_completed()
        session.touch()
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        await asyncio.gather(*self._pending)


class _ReplaceRefusal:
    """指定先への置換だけを、Windowsの継続するアクセス拒否として共通保存の置換段階で失敗させる。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, destination: pathlib.Path) -> None:
        self.destination = destination
        self.active = True
        self._original = os.replace
        monkeypatch.setattr(atomic_file.os, "replace", self._replace)

    def _replace(self, source: typing.Any, target: typing.Any) -> None:
        if not self.active or pathlib.Path(target) != self.destination:
            self._original(source, target)
            return
        error = _WindowsAccessDenied(13, "アクセスが拒否されました", str(source), None, str(target))
        raise error


async def _until(condition: typing.Callable[[], bool], *, limit: float = 5.0) -> None:
    """Event loopを進めながら条件の成立を上限付きで待つ。"""
    deadline = asyncio.get_running_loop().time() + limit
    while not condition():
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.01)


def _replace_failure_logged(log_text: str, destination: pathlib.Path) -> bool:
    """置換元の一時ファイルと置換先を、同じ失敗の診断として読めるかを返す。"""
    source = f"'{destination.parent}/.{destination.name}."
    return f"{source}" in log_text and f".tmp' -> '{destination}'" in log_text


class _TerminalBeforeResponseBackend(FakeBackend):
    """起動応答を返す前にturnが終了し、登録簿へ公開するsessionを返す。"""

    async def start(self, *args: typing.Any, **kwargs: typing.Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.publish_registry = True
        session.status = "completed"
        session.agent_message = "応答前の報告"
        session.record_turn_completed()
        session.touch()
        return session
