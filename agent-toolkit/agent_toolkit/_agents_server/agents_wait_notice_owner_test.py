"""上り通知が、誤登録された祖先の待機に消費されないことを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._agents_server import (
    agents_wait,
    notice_inbox,
    notify,
    shared_layout,
    shared_roots,
    state,
    status_file,
    wait_targets,
)
from agent_toolkit._common import session_registry


@pytest.mark.parametrize("parent_published", [False, True])
@pytest.mark.parametrize("notice_root", ["ancestor", "previous"])
def test_ancestor_wait_preserves_notice_for_launcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    parent_published: bool,
    notice_root: str,
) -> None:
    """祖先の誤登録が残っていても、本来の起動主体だけが通知を受け取る。"""
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)
    monkeypatch.setattr(agents_wait, "is_agent_environment", lambda: False)
    root = shared_layout.status_directory("ancestor", tmp_path)
    root.mkdir(parents=True)
    (root / "root.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")
    if parent_published:
        (root / "launcher.json").write_text(
            json.dumps({"version": 1, "sessions": [{"session_id": "child", "status": "running"}]}),
            encoding="utf-8",
        )
    session_registry.publish(
        "child", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="launcher", state_root=tmp_path
    )
    wait_targets.retain_wait_targets("ancestor", "root.json", ["child"], tmp_path)
    wait_targets.retain_wait_targets("ancestor", "launcher.json", ["child"], tmp_path)
    assert (
        notify.send_notification(
            "起動主体への連絡",
            environment={"AGENT_TOOLKIT_OWNER_SESSION": notice_root, "CODEX_THREAD_ID": "child"},
            state_root=tmp_path,
        )
        == 0
    )
    capsys.readouterr()

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "ancestor"}, state_root=tmp_path) == 10
    assert not capsys.readouterr().out
    assert any(shared_layout.notices_directory(notice_root, tmp_path).iterdir())

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
    assert not any(shared_layout.notices_directory(notice_root, tmp_path).iterdir())


def test_stashed_notice_keeps_launcher_ownership(tmp_path: pathlib.Path) -> None:
    """退避後の再開でも、起動主体と異なる受取側は本文と原本を保持する。"""
    session_registry.publish(
        "child", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="parent", state_root=tmp_path
    )
    stash = tmp_path / "stash"
    stash.mkdir()
    path = stash / "child.notice.json"
    path.write_text(
        json.dumps({"version": 1, "session_id": "child", "sent_at": "2026-11-02T00:00:00Z", "body": "親への連絡"}),
        encoding="utf-8",
    )
    assert not notice_inbox.take_notices(
        "root", "child", tmp_path, recipient_session_id="ancestor", owner_status_file="root.json", stash_directory=stash
    )
    assert path.is_file()
    assert notice_inbox.take_notices(
        "root", "child", tmp_path, recipient_session_id="parent", owner_status_file="parent.json", stash_directory=stash
    ) == [{"body": "親への連絡"}]


def test_notice_without_owner_or_launcher_keeps_original(tmp_path: pathlib.Path) -> None:
    """所有投影と起動主体の両方が不明な旧記録では、通知を保持する。"""
    session_registry.publish("child", state=session_registry.PublicationState.PENDING_RUNNING, state_root=tmp_path)
    directory = shared_layout.notices_directory("root", tmp_path)
    directory.mkdir(parents=True)
    path = directory / "child.notice.json"
    path.write_text(
        json.dumps({"version": 1, "session_id": "child", "sent_at": "2026-11-02T00:00:00Z", "body": "親への連絡"}),
        encoding="utf-8",
    )
    assert not notice_inbox.take_notices(
        "root", "child", tmp_path, recipient_session_id="parent", owner_status_file="parent.json"
    )
    assert path.is_file()


@pytest.mark.asyncio
async def test_writer_collects_notice_from_its_renamed_projection(tmp_path, capsys):
    """状態ファイルの衝突で表示先を別名にしたMCPも、自身の通知を回収する。"""
    session = state.SessionState("child", str(tmp_path), announced=True)
    writer = status_file.StatusFileWriter(
        {"child": session}, shared_roots.StatusFileIdentity("root", "parent.json", "parent"), state_root=tmp_path
    )
    writer.activate()
    try:
        writer._projection_path = writer.path.with_name("parent-server.json")  # pylint: disable=protected-access
        writer.path.unlink()
        writer.flush()
        assert (
            notify.send_notification(
                "親への連絡",
                environment={"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "child"},
                state_root=tmp_path,
            )
            == 0
        )
        capsys.readouterr()
        assert "親への連絡" in writer.take_notices("child")[0]["body"]
    finally:
        await writer.deactivate_async()


@pytest.mark.parametrize("explicit_root", [None, "ancestor"])
def test_delegate_without_host_identity_does_not_become_root(tmp_path: pathlib.Path, explicit_root: str | None) -> None:
    """所有ルートだけが分かる委譲先を最上位の書込主体と取り違えない。"""
    shared_layout.status_directory("ancestor", tmp_path).mkdir(parents=True)
    shared_roots.write_root_alias("ancestor", "ancestor", tmp_path)
    assert shared_roots.resolve_wait_identity({"AGENT_TOOLKIT_OWNER_SESSION": "ancestor"}, explicit_root, tmp_path) is None


@pytest.mark.parametrize("projection_file", ["current.json", "current-server.json"])
def test_current_writer_receives_notice_after_owner_changes(monkeypatch, tmp_path, capsys, projection_file):
    """別の主体が現在turnを所有する場合、作成時の親情報で現在の配送を拒否しない。"""
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(agents_wait, "is_agent_environment", lambda: False)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)
    root = shared_layout.status_directory("root", tmp_path)
    root.mkdir(parents=True)
    (root / projection_file).write_text(
        json.dumps({"version": 1, "host_session_id": "current", "sessions": [{"session_id": "child", "status": "running"}]}),
        encoding="utf-8",
    )
    session_registry.publish(
        "child",
        state=session_registry.PublicationState.PENDING_RUNNING,
        launcher_session_id="original-parent",
        state_root=tmp_path,
    )
    assert (
        notify.send_notification(
            "現在の依頼への連絡",
            environment={"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "child"},
            state_root=tmp_path,
        )
        == 0
    )
    capsys.readouterr()
    assert (
        agents_wait.wait_for_result(
            environment={"AGENT_TOOLKIT_OWNER_SESSION": "root", "AGENT_TOOLKIT_STATUS_HOST_SESSION": "current"},
            state_root=tmp_path,
        )
        == 0
    )
    assert "現在の依頼への連絡" in json.loads(capsys.readouterr().out)["notices"][0]["body"]
