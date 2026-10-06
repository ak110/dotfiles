"""agents_serverのプロセス間session登録簿を検証する。"""

# テストでは状態ディレクトリ解決の内部境界を差し替える。
# pylint: disable=protected-access

import asyncio
import datetime
import json
import pathlib
import typing

import pytest

from agent_toolkit import agents_server_mcp
from agent_toolkit._agents_server import session_registry as subject
from agent_toolkit._agents_server import state


def test_publish_and_observe_terminal_state(tmp_path: pathlib.Path) -> None:
    """同じsessionの実行中状態を終端状態で置き換えて観測する。"""
    subject.publish("child-session", terminal=False, cwd=str(tmp_path), state_root=tmp_path)
    path = subject.registry_directory(tmp_path) / "child-session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == 2
    assert payload["session_id"] == "child-session"
    assert payload["terminal"] is False
    assert payload["cwd"] == str(tmp_path.resolve())
    datetime.datetime.fromisoformat(payload["updated_at"])
    assert subject.resolve("child-session", state_root=tmp_path).state is subject.Resolution.RUNNING

    subject.publish("child-session", terminal=True, state_root=tmp_path)
    assert subject.resolve("child-session", state_root=tmp_path).state is subject.Resolution.TERMINAL


def test_resume_info_carries_launch_info_and_activity_times_and_accepts_legacy_record(tmp_path: pathlib.Path) -> None:
    """登録簿は起動情報と活動時刻を別欄で渡し、項目の無い旧形式では起動情報を空文字列か`None`、活動時刻を不明として返す。"""
    launch_info = subject.LaunchInfo(label="lane-05-exec", prompt="依頼本文", created_at="2026-09-25T21:58:13+00:00")
    subject.publish(
        "created",
        terminal=True,
        cwd=str(tmp_path),
        launch_info=launch_info,
        started_at="2026-09-27T14:38:32+00:00",
        session_updated_at="2026-09-27T14:39:16+00:00",
        state_root=tmp_path,
    )
    subject.publish("legacy", terminal=True, cwd=str(tmp_path), state_root=tmp_path)

    created = subject.resolve("created", state_root=tmp_path).resume_info
    legacy = subject.resolve("legacy", state_root=tmp_path).resume_info

    assert created is not None
    assert created.launch_info == launch_info
    assert created.started_at == "2026-09-27T14:38:32+00:00"
    assert created.session_updated_at == "2026-09-27T14:39:16+00:00"
    payload = json.loads((subject.registry_directory(tmp_path) / "created.json").read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert payload["updated_at"] != created.session_updated_at
    assert legacy is not None
    assert legacy.launch_info == subject.LaunchInfo(label="", prompt="", created_at=None)
    assert legacy.started_at is None
    assert legacy.session_updated_at is None


def test_publish_and_observe_starting_state(tmp_path: pathlib.Path) -> None:
    """起動処理中のsessionを非終端の再開情報として観測する。"""
    subject.publish("child-session", terminal=False, status="starting", cwd=str(tmp_path), state_root=tmp_path)

    resolution = subject.resolve("child-session", state_root=tmp_path)

    assert resolution.state is subject.Resolution.RUNNING
    assert resolution.resume_info is not None
    assert resolution.resume_info.status == "starting"


@pytest.mark.parametrize("reason", ["retention_expired", "stopped"])
def test_release_replaces_record_with_released_state(tmp_path: pathlib.Path, reason: subject.ReleaseReason) -> None:
    """解放したsessionは不在と区別できる解放済みとして解決され、理由と時刻を持ち、再開条件を持たない。"""
    subject.publish("child-session", terminal=True, state_root=tmp_path)
    subject.release("child-session", reason=reason, state_root=tmp_path)

    resolution = subject.resolve("child-session", state_root=tmp_path)
    assert resolution.state is subject.Resolution.RELEASED
    assert resolution.released_reason == reason
    assert resolution.released_at is not None
    assert datetime.datetime.fromisoformat(resolution.released_at).tzinfo is not None
    assert resolution.resume_info is None
    assert subject.resolve("never-registered", state_root=tmp_path).state is subject.Resolution.MISSING


def test_release_rejects_unknown_reason(tmp_path: pathlib.Path) -> None:
    """解放の理由は応答文面の分岐に使うため、定義外の値を書き込まない。"""
    with pytest.raises(ValueError, match="invalid release reason"):
        subject.release("child-session", reason=typing.cast("subject.ReleaseReason", "restarted"), state_root=tmp_path)


@pytest.mark.asyncio
async def test_session_state_publishes_state_transitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """session状態は進捗更新を重複書込せず、実行中と終端の遷移を公開する。"""
    monkeypatch.setattr(subject._atk_config, "state_dir", lambda: tmp_path)
    session = state.SessionState("published-session", str(tmp_path), publish_registry=True)

    session.status = "starting"
    session.touch()
    first = json.loads((subject.registry_directory() / "published-session.json").read_text(encoding="utf-8"))
    assert first["status"] == "starting"
    session.status = "running"
    session.touch()
    first = json.loads((subject.registry_directory() / "published-session.json").read_text(encoding="utf-8"))
    assert first["status"] == "running"
    session.set_progress("進捗")
    second = json.loads((subject.registry_directory() / "published-session.json").read_text(encoding="utf-8"))
    assert second == first

    session.status = "completed"
    session.turn_completed = True
    session.touch()
    assert subject.resolve("published-session").state is subject.Resolution.TERMINAL
    restored = subject.resolve("published-session").resume_info
    assert restored is not None
    assert restored.started_at == session.started_at
    assert restored.session_updated_at == session.updated_at


@pytest.mark.asyncio
async def test_speed_change_is_published_without_status_or_turn_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """送信時に変わった速度が登録簿へ届き、再開状態も同じ値を保持する。"""
    monkeypatch.setattr(subject._atk_config, "state_dir", lambda: tmp_path)
    session = state.SessionState("speed-session", str(tmp_path), publish_registry=True)
    session.touch()
    initial = subject.resolve(session.session_id).resume_info
    assert initial is not None and initial.fast_mode is None
    for fast in (False, True, False):
        session.fast_mode = fast
        session.touch()
        info = subject.resolve(session.session_id).resume_info
        assert info is not None and info.fast_mode is fast
        assert state.SessionResumeState.from_session(session).fast_mode is fast


@pytest.mark.asyncio
async def test_observing_wait_keeps_record_and_stop_releases_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終端を観測した待機処理はレコードを残し、所有主体による破棄だけが解放済みへ置き換える。"""
    monkeypatch.setattr(subject._atk_config, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(agents_server_mcp._wait_schedule, "get_wait_timeout", lambda _request_bucket: 0.0)
    subject.publish("child-session", terminal=True, engine="codex", cwd=str(tmp_path))
    manager = agents_server_mcp.AgentsServerManager(status_writer=None)
    manager._codex = _ReleaseOnlyBackend()
    parent = state.SessionState("parent-session", str(tmp_path), engine="codex")
    parent.live_child_session_ids.add("child-session")
    state.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
    manager.sessions[parent.session_id] = parent

    await manager.wait()

    assert subject.resolve("child-session").state is subject.Resolution.TERMINAL
    assert parent.terminal_child_session_ids == {"child-session"}

    await manager.stop("child-session")

    released = subject.resolve("child-session")
    assert released.state is subject.Resolution.RELEASED
    assert released.released_reason == "stopped"
    await manager.close()


@pytest.mark.asyncio
async def test_retention_expiry_releases_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限へ到達したsessionのレコードを所有主体が解放済みへ置き換える。"""
    monkeypatch.setattr(subject._atk_config, "state_dir", lambda: tmp_path)
    manager = agents_server_mcp.AgentsServerManager(status_writer=None)
    session = state.SessionState("expiring-session", str(tmp_path), engine="codex", publish_registry=True)
    session.status = "completed"
    session.turn_completed = True
    session.touch()
    manager.sessions[session.session_id] = session
    assert subject.resolve("expiring-session").state is subject.Resolution.TERMINAL

    session.retention_deadline = asyncio.get_running_loop().time() - 1.0
    manager._expire_session(session.session_id)

    released = subject.resolve("expiring-session")
    assert released.state is subject.Resolution.RELEASED
    assert released.released_reason == "retention_expired"
    await manager.close()


class _ReleaseOnlyBackend:
    """自動再開の配送とbackend資源の解放だけを受け取るテスト用backend。"""

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, object]:
        del session, prompt
        return {"delivery": "reply_started"}

    async def release_session(self, session_id: str) -> None:
        del session_id

    async def close(self) -> None:
        """外部資源を持たないため何もしない。"""


@pytest.mark.parametrize("session_id", ["", "../child", "child/session"])
def test_registry_rejects_invalid_session_id(session_id: str, tmp_path: pathlib.Path) -> None:
    """登録簿ディレクトリ外を指し得る識別子を拒否する。"""
    with pytest.raises(ValueError, match="invalid session_id"):
        subject.publish(session_id, terminal=False, state_root=tmp_path)
