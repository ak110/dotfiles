"""上り通知が、誤登録された祖先の待機に消費されないことを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._agents_server import agents_wait, notify, shared_layout, shared_roots, wait_targets
from agent_toolkit._common import session_registry


def test_ancestor_wait_preserves_notice_for_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """祖先の誤登録が残っていても、本来の起動主体だけが通知を受け取る。"""
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)
    monkeypatch.setattr(agents_wait, "is_agent_environment", lambda: False)
    root = shared_layout.status_directory("ancestor", tmp_path)
    root.mkdir(parents=True)
    (root / "root.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")
    (root / "launcher.json").write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "child", "status": "running"}]}),
        encoding="utf-8",
    )
    session_registry.publish("child", terminal=False, launcher_session_id="launcher", state_root=tmp_path)
    wait_targets.retain_wait_targets("ancestor", "root.json", ["child"], tmp_path)
    assert (
        notify.send_notification(
            "起動主体への連絡",
            environment={"AGENT_TOOLKIT_OWNER_SESSION": "ancestor", "CODEX_THREAD_ID": "child"},
            state_root=tmp_path,
        )
        == 0
    )
    capsys.readouterr()

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "ancestor"}, state_root=tmp_path) == 10
    assert not capsys.readouterr().out
    assert any(shared_layout.notices_directory("ancestor", tmp_path).iterdir())

    assert (
        agents_wait.wait_for_result(
            environment={"AGENT_TOOLKIT_OWNER_SESSION": "ancestor", "AGENT_TOOLKIT_STATUS_HOST_SESSION": "launcher"},
            state_root=tmp_path,
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert response["session_id"] == "child"
    assert "起動主体への連絡" in response["notices"][0]["body"]
    assert not any(shared_layout.notices_directory("ancestor", tmp_path).iterdir())


@pytest.mark.parametrize("explicit_root", [None, "ancestor"])
def test_delegate_without_host_identity_does_not_become_root(tmp_path: pathlib.Path, explicit_root: str | None) -> None:
    """所有ルートだけが分かる委譲先を最上位の書込主体と取り違えない。"""
    shared_layout.status_directory("ancestor", tmp_path).mkdir(parents=True)
    shared_roots.write_root_alias("ancestor", "ancestor", tmp_path)
    assert shared_roots.resolve_wait_identity({"AGENT_TOOLKIT_OWNER_SESSION": "ancestor"}, explicit_root, tmp_path) is None
