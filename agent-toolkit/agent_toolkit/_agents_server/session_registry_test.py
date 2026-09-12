"""agents_serverのプロセス間session登録簿を検証する。"""

# テストでは状態ディレクトリ解決の内部境界を差し替える。
# pylint: disable=protected-access

import asyncio
import datetime
import json
import pathlib

import pytest

from agent_toolkit import agents_server_mcp
from agent_toolkit._agents_server import session_registry as subject
from agent_toolkit._agents_server import state


def test_publish_and_observe_terminal_state(tmp_path: pathlib.Path) -> None:
    """同じsessionの実行中状態を終端状態で置き換えて観測する。"""
    subject.publish("child-session", terminal=False, state_root=tmp_path)
    path = subject.registry_directory(tmp_path) / "child-session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == 2
    assert payload["session_id"] == "child-session"
    assert payload["terminal"] is False
    datetime.datetime.fromisoformat(payload["updated_at"])
    assert subject.resolve("child-session", state_root=tmp_path).state is subject.Resolution.RUNNING

    subject.publish("child-session", terminal=True, state_root=tmp_path)
    assert subject.resolve("child-session", state_root=tmp_path).state is subject.Resolution.TERMINAL


def test_remove_discards_observed_session(tmp_path: pathlib.Path) -> None:
    """観測済みsessionと空になった登録簿ディレクトリを取り除く。"""
    subject.publish("child-session", terminal=True, state_root=tmp_path)
    subject.remove("child-session", state_root=tmp_path)

    assert subject.resolve("child-session", state_root=tmp_path).state is subject.Resolution.MISSING
    assert subject.registry_directory(tmp_path).exists() is False


@pytest.mark.asyncio
async def test_session_state_publishes_state_transitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """session状態は進捗更新を重複書込せず、実行中と終端の遷移を公開する。"""
    monkeypatch.setattr(subject._atk_config, "state_dir", lambda: tmp_path)
    session = state.SessionState("published-session", str(tmp_path), publish_registry=True)

    session.touch()
    first = json.loads((subject.registry_directory() / "published-session.json").read_text(encoding="utf-8"))
    session.set_progress("進捗")
    second = json.loads((subject.registry_directory() / "published-session.json").read_text(encoding="utf-8"))
    assert second == first

    session.status = "completed"
    session.turn_completed = True
    session.touch()
    assert subject.resolve("published-session").state is subject.Resolution.TERMINAL


@pytest.mark.asyncio
async def test_observing_wait_keeps_record_and_stop_removes_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終端を観測した待機経路はレコードを残し、所有主体の破棄だけが削除する。"""
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

    assert subject.resolve("child-session").state is subject.Resolution.MISSING
    await manager.close()


@pytest.mark.asyncio
async def test_retention_expiry_removes_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限へ到達したsessionのレコードを所有主体が削除する。"""
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

    assert subject.resolve("expiring-session").state is subject.Resolution.MISSING
    await manager.close()


class _ReleaseOnlyBackend:
    """自動再開の配送とbackend資源の解放だけを受け取る検体用backend。"""

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
