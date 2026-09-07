"""agents_serverのプロセス間session登録簿を検証する。"""

# テストでは状態ディレクトリ解決の内部境界を差し替える。
# pylint: disable=protected-access

import datetime
import json
import pathlib

import pytest

from _agents_server import session_registry as subject
from _agents_server import state


def test_publish_and_observe_terminal_state(tmp_path: pathlib.Path) -> None:
    """同じsessionの実行中状態を終端状態で置き換えて観測する。"""
    subject.publish("child-session", terminal=False, state_root=tmp_path)
    path = subject.registry_directory(tmp_path) / "child-session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == 1
    assert payload["session_id"] == "child-session"
    assert payload["terminal"] is False
    datetime.datetime.fromisoformat(payload["updated_at"])
    assert subject.is_terminal("child-session", state_root=tmp_path) is False

    subject.publish("child-session", terminal=True, state_root=tmp_path)
    assert subject.is_terminal("child-session", state_root=tmp_path) is True


def test_remove_discards_observed_session(tmp_path: pathlib.Path) -> None:
    """観測済みsessionと空になった登録簿ディレクトリを取り除く。"""
    subject.publish("child-session", terminal=True, state_root=tmp_path)
    subject.remove("child-session", state_root=tmp_path)

    assert subject.is_terminal("child-session", state_root=tmp_path) is False
    assert subject.registry_directory(tmp_path).exists() is False


@pytest.mark.asyncio
async def test_session_state_publishes_only_terminal_transitions(
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
    assert subject.is_terminal("published-session") is True


@pytest.mark.parametrize("session_id", ["", "../child", "child/session"])
def test_registry_rejects_invalid_session_id(session_id: str, tmp_path: pathlib.Path) -> None:
    """登録簿ディレクトリ外を指し得る識別子を拒否する。"""
    with pytest.raises(ValueError, match="invalid session_id"):
        subject.publish(session_id, terminal=False, state_root=tmp_path)
