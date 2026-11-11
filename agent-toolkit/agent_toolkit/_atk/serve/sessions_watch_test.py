"""serveのsessionsに関するwatchの試験。"""

# pylint: disable=protected-access

import asyncio
import json
import pathlib

import pytest

from agent_toolkit._atk.serve import sessions
from agent_toolkit._testing.serve_sessions_support import _context, _write


@pytest.mark.asyncio
async def test_refresh_notification_is_delivered_to_subscribers(tmp_path: pathlib.Path) -> None:
    """SSE購読者へ一覧の再取得を促す通知を配信する。"""
    context = _context(tmp_path)
    queue = await sessions.subscribe(context.state)
    try:
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}
        # 未配信の通知がある間に重ねて配信しても、一覧の再取得は1件にまとまり、配信で待たない。
        await sessions.deliver_refresh(context.state)
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}
        assert queue.empty()
    finally:
        await sessions.unsubscribe(context.state, queue)
    assert context.state.subscribers == set()


@pytest.mark.asyncio
async def test_local_watch_notifies_new_records_and_appends(tmp_path: pathlib.Path) -> None:
    """ローカルの記録の追加で一覧の再取得を、一覧に載る記録への追記で記録1件の更新を通知する。"""
    # 未作成の記録rootは最も近い祖先（tmp_path）の直下を監視するため、一覧を保存する状態ディレクトリを先に作り、
    # 保存先の新設を記録の追加と区別できない通知にしない。
    (tmp_path / "state").mkdir()
    context = _context(tmp_path, state_dir=tmp_path / "state")
    (tmp_path / "claude" / "projects").mkdir(parents=True)
    sessions.start_local_watch(context)
    queue = await sessions.subscribe(context.state)
    try:
        path = _write(tmp_path / "claude" / "projects" / "repo" / "new.jsonl", [{"type": "user", "message": {"content": "a"}}])
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=5)) == {"type": "refresh"}
        await asyncio.to_thread(sessions.list_local_sessions, context)
        while not queue.empty():
            queue.get_nowait()

        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "assistant", "message": {"content": "b"}}) + "\n")

        message = json.loads(await asyncio.wait_for(queue.get(), timeout=5))
        assert message == {"type": "record", "host": "local-host", "engine": "claude", "path": str(path)}
    finally:
        await sessions.unsubscribe(context.state, queue)
        sessions.stop_local_watch(context)
    assert context.state.record_watch is None
