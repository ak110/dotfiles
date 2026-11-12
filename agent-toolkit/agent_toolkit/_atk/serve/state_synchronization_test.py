"""serveのstateに関するsynchronizationの試験。"""

# pylint: disable=protected-access

import asyncio
import pathlib
import typing

import pytest
import watchdog.events

from agent_toolkit._atk.serve import state, wi_operations
from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.serve_support import _BlockingSync, _FakeTimer, _sync_app


@pytest.mark.asyncio
async def test_state_publishes_at_max_wait_deadline_and_restarts_debounce(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値未満の間隔で変更が続いても最大待機時間で1回通知し、以降は新しい保留期間を開始する。

    実時間のスケジューリング遅延で結果が変わらないよう、単調時計とタイマーを注入して検証する。
    """
    debounce = 0.05
    max_wait = debounce * state.ServeState._MAX_DEBOUNCE_FACTOR
    clock = [1_000.0]
    timers: list[_FakeTimer] = []

    def _make_timer(interval: float, function: typing.Callable[..., None], args: tuple[typing.Any, ...] = ()) -> _FakeTimer:
        timer = _FakeTimer(interval, function, args)
        timers.append(timer)
        return timer

    current = state.ServeState(
        tmp_path,
        debounce_seconds=debounce,
        monotonic=lambda: clock[0],
        timer_factory=_make_timer,
    )
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    # 閾値未満の間隔で変更を送り続け、期限に到達するまで発行されないことを確認する。
    deadline = clock[0] + max_wait
    while clock[0] < deadline:
        current.on_modified(event)
        # 直前に設定したタイマーの発火時刻が期限を超えないこと（期限が上界であること）。
        assert clock[0] + timers[-1].interval <= deadline
        await asyncio.sleep(0)
        assert not published
        clock[0] += 0.02

    # 期限に到達した変更はタイマーを介さず直ちに発行する。
    pending_before = len(timers)
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    assert len(timers) == pending_before

    # 以降は新しい保留期間となり、静穏期間の経過で再び1回発行する。
    clock[0] += 0.01
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    latest = timers[-1]
    assert latest.interval == debounce
    latest.function(*latest.args)
    await asyncio.sleep(0)
    assert published == ["changed", "changed"]


@pytest.mark.asyncio
async def test_cancelled_sync_request_does_not_cancel_shared_sync(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開始要求のキャンセル後も共有同期が継続し、別要求へ同じ結果を返す。"""
    operations = wi_operations.Operations(tmp_path)
    sync = _BlockingSync()
    monkeypatch.setattr(operations, "sync", sync)
    app = _sync_app(tmp_path, operations)
    first = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.to_thread(sync.started.wait)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.sleep(0)
    sync.release.set()
    response = await second
    assert sync.calls == 1
    assert await response.get_json() == {"synced": True}


@pytest.mark.asyncio
async def test_serve_state_watches_all_queue_states(tmp_path: pathlib.Path) -> None:
    """年月配下の各状態のファイルを追加・変更・削除すると、購読者へ通知する。"""
    directory = tmp_path / "2026" / "11"
    directory.mkdir(parents=True)
    async with state.ServeState(tmp_path, debounce_seconds=0.01) as current:
        events = current.events(heartbeat=30)
        assert (await anext(events)).startswith("event: subscribed")
        try:
            for state_name in wi_constants.WI_STATES:
                path = directory / f"{state_name}.md"
                original = f"---\ntype: awi\nstate: {state_name}\n---\n\n元の本文\n"
                await asyncio.to_thread(path.write_text, original, encoding="utf-8")
                assert (await asyncio.wait_for(anext(events), timeout=5)).startswith("event: changed")
                await asyncio.to_thread(path.write_text, original + "追記\n", encoding="utf-8")
                assert (await asyncio.wait_for(anext(events), timeout=5)).startswith("event: changed")
                await asyncio.to_thread(path.unlink)
                assert (await asyncio.wait_for(anext(events), timeout=5)).startswith("event: changed")
        finally:
            await events.aclose()


def test_sync_ignores_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """連続した明示同期も未送信commitを送信し、直後の遠隔変更を取り込む。"""
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    notes = git_repository.init_repository(
        tmp_path / "notes", initial_branch="main", origin=str(remote), files={"seed.md": "初期本文"}, commit_message="初期化"
    )
    git_repository.run_git(notes, "push", "-u", "origin", "main")
    peer = tmp_path / "peer"
    git_repository.run_git(tmp_path, "clone", str(remote), str(peer))
    operations = wi_operations.Operations(notes)
    for index in range(2):
        outgoing = notes / f"local-{index}.md"
        outgoing.write_text(f"送信本文{index}", encoding="utf-8")
        git_repository.commit_all(notes, "ローカル更新")
        assert operations.sync() is True
        assert git_repository.git_output(remote, "show", f"main:{outgoing.name}") == f"送信本文{index}"

        git_repository.run_git(peer, "pull", "--ff-only")
        incoming = peer / f"remote-{index}.md"
        incoming.write_text(f"受信本文{index}", encoding="utf-8")
        git_repository.commit_all(peer, "遠隔更新")
        git_repository.run_git(peer, "push")
        assert operations.sync() is True
        assert (notes / incoming.name).read_text(encoding="utf-8") == f"受信本文{index}"
        assert outgoing.read_text(encoding="utf-8") == f"送信本文{index}"

    pending = notes / "pending.md"
    pending.write_text("送信失敗でも保持する本文", encoding="utf-8")
    local_head = git_repository.commit_all(notes, "未送信の更新")
    (peer / "after-failure.md").write_text("取得してはいけない遠隔変更", encoding="utf-8")
    git_repository.commit_all(peer, "新しい遠隔変更")
    git_repository.run_git(peer, "push")

    def fail_push(_notes: pathlib.Path) -> None:
        raise RuntimeError("送信失敗")

    monkeypatch.setattr(_wi_sync, "push_pending_commits", fail_push)
    with pytest.raises(RuntimeError, match="送信失敗"):
        operations.sync()
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == local_head
    assert pending.read_text(encoding="utf-8") == "送信失敗でも保持する本文"
    assert not (notes / "after-failure.md").exists()


def test_markdown_change_marks_index_before_debounced_publish(tmp_path: pathlib.Path) -> None:
    """Markdownの変更は通知の間引きを待たずに索引の変更記録へ渡し、Markdown以外とディレクトリは渡さない。

    間引き後の通知で画面が再取得したときに、索引が古い一覧を返さないようにするためである。
    """
    current = state.ServeState(tmp_path, debounce_seconds=60, timer_factory=_FakeTimer)
    calls: list[str] = []
    current.change_listener = lambda: calls.append("changed")

    current.on_created(watchdog.events.FileCreatedEvent(str(tmp_path / "2026" / "10" / "a.md")))
    current.on_modified(watchdog.events.FileModifiedEvent(str(tmp_path / "note.txt")))
    current.on_created(watchdog.events.DirCreatedEvent(str(tmp_path / "2026" / "11")))

    assert calls == ["changed"]
