"""status_fileのheartbeatの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import datetime
import json
import os
import pathlib
import typing

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import shared_layout, shared_roots, state
from agent_toolkit._agents_server import status_file as subject
from agent_toolkit._testing.agents_server_status_file_support import _until

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


@pytest.mark.asyncio
async def test_root_writer_removes_stale_files_on_activate(tmp_path: pathlib.Path) -> None:
    """ルートwriterは自身と保持期限切れの共有ファイルだけを除く。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    other_writer = directory / "other.json"
    other_writer.write_text("{}", encoding="utf-8")
    other_temporary = directory / ".other.json.token.tmp"
    other_temporary.write_text("temporary", encoding="utf-8")
    results = directory / "results"
    results.mkdir()
    stale_result = results / "stale-session.json"
    stale_result.write_text("{}", encoding="utf-8")
    retained_result = results / "retained-session.json"
    retained_result.write_text("{}", encoding="utf-8")
    notices = directory / "notices"
    notices.mkdir()
    stale_notice = notices / "stale-session.1.json"
    stale_notice.write_text("{}", encoding="utf-8")
    retained_notice = notices / "retained-session.1.json"
    retained_notice.write_text("{}", encoding="utf-8")
    stale_at = datetime.datetime.now(datetime.UTC).timestamp() - state.RESULT_RETENTION_SECONDS - 1
    for path in (stale_result, stale_notice):
        os.utime(path, (stale_at, stale_at))
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()
    assert other_writer.exists()
    assert other_temporary.exists()
    assert retained_result.exists()
    assert retained_notice.exists()
    assert stale_result.exists()
    assert not stale_notice.exists()
    writer.deactivate()
    assert other_writer.exists()
    assert other_temporary.exists()
    assert retained_result.exists()
    assert retained_notice.exists()
    assert stale_result.exists()


@pytest.mark.asyncio
async def test_root_writer_activates_when_temporary_file_vanishes_after_listing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """列挙した自身の名前の一時ファイルが削除前に消えても、起動時の不要ファイルの削除を続けて起動する。"""
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    vanished = directory / ".root.json.renamed.tmp"
    original_glob = pathlib.Path.glob

    def glob_listing_vanished(self: pathlib.Path, pattern: str, **kwargs: typing.Any) -> typing.Iterator[pathlib.Path]:
        if self == directory and pattern == ".root.json.*.tmp":
            return iter([vanished])
        return original_glob(self, pattern, **kwargs)

    monkeypatch.setattr(pathlib.Path, "glob", glob_listing_vanished)

    writer.activate()
    try:
        assert writer.path.exists()
    finally:
        writer.deactivate()


@pytest.mark.asyncio
async def test_writer_removes_state_file_with_expired_heartbeat(tmp_path: pathlib.Path) -> None:
    """生存の印が失効した他の状態ファイルを削除する。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    stale = directory / "stale.json"
    stale.write_text(
        json.dumps(
            {
                "heartbeat_at": (
                    datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=subject.HEARTBEAT_EXPIRY_SECONDS + 1)
                ).isoformat()
            }
        ),
        encoding="utf-8",
    )
    live = directory / "live.json"
    live.write_text(
        json.dumps({"heartbeat_at": datetime.datetime.now(datetime.UTC).isoformat()}),
        encoding="utf-8",
    )
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()

    assert not stale.exists()
    assert live.exists()
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_preserves_state_file_without_heartbeat(tmp_path: pathlib.Path) -> None:
    """旧形式の状態ファイルは他の書込主体が回収しない。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    legacy = directory / "legacy.json"
    legacy.write_text("{}", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()
    writer.flush()
    writer.deactivate()

    assert legacy.exists()


@pytest.mark.asyncio
async def test_manager_refreshes_heartbeat_until_close(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """managerは稼働中に生存の印を定期更新し、終了時に更新タスクを回収する。"""
    monkeypatch.setattr(subject, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        manager.activate()
        initial = json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"]

        # 定期更新の保存はファイル操作のスレッドを経て完了するため、固定時間でなく生存の印の更新を待つ。
        await _until(lambda: json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"] > initial)

        await manager.close()
        assert manager._heartbeat_task is None


@pytest.mark.asyncio
async def test_nested_writer_preserves_root_file_on_deactivate(tmp_path: pathlib.Path) -> None:
    """入れ子の書込主体は自身のファイルだけを回収する。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    root_file = directory / "root.json"
    root_file.write_text("{}", encoding="utf-8")
    notices = directory / "notices"
    notices.mkdir()
    notice_file = notices / "child.1.json"
    notice_file.write_text("{}", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "child.json", "child"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    writer.deactivate()
    assert root_file.exists()
    assert not writer.path.exists()
    assert notice_file.exists()
