"""serveのappに関するmutationsの試験。"""

# pylint: disable=protected-access

import asyncio
import contextlib
import pathlib
import subprocess
import threading
import typing

import filelock
import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations, wi_routes
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as wi_web_input
from agent_toolkit._atk.wi.layout import entry_path, entry_state_of
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.serve_app_support import (
    _REBASE_AGENT_AWI,
    _REBASE_AWI,
    _REBASE_UWI,
    _fixed_path,
    _notes_snapshot,
    _rebasing_notes,
    _serve_app,
)
from agent_toolkit._testing.serve_support import (
    _BATCH_TEXT,
    AWI_TEXT,
    UNANSWERED_UWI_TEXT,
    _disable_wi_git,
    make_inbox_and_processing,
)


@pytest.mark.asyncio
async def test_wi_mutation_logs_operation_without_body(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """成功と入力エラーを記録し、編集本文がログに含まれないことを検証する。"""
    operations = wi_operations.Operations(tmp_path)
    monkeypatch.setattr(operations, "edit", lambda *_args: True)
    monkeypatch.setattr(operations, "transition", lambda _action, filenames, **_kwargs: filenames)
    app = serve_app.create_app(
        tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path), operations=operations
    )
    client = app.test_client()

    with caplog.at_level("INFO", logger=wi_routes.logger.name):
        edit = await client.put("/api/entries/inbox/entry.md", json={"content": "秘匿本文", "expected_content": "旧本文"})
        transition = await client.post("/api/entries/hold", json={"filenames": ["entry.md"]})
        invalid = await client.put("/api/entries/inbox/entry.md", json={"content": ""})
        invalid_transition = await client.post("/api/entries/hold", json={"filenames": ["entry.md"], "bogus": True})

    assert [response.status_code for response in (edit, transition, invalid, invalid_transition)] == [200, 200, 400, 400]
    messages = "\n".join(record.message for record in caplog.records if record.name == wi_routes.logger.name)
    assert "WI更新: 操作=edit_entry 対象=entry.md 結果=True" in messages
    assert "WI更新: 操作=hold 対象=entry.md 結果=完了" in messages
    assert "WI更新失敗: 操作=edit_entry 対象=entry.md HTTP=400" in messages
    assert "WI更新失敗: 操作=hold 対象=entry.md HTTP=400" in messages
    assert "秘匿本文" not in messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("put", "/api/entries/inbox/entry.md", {"content": "変更後の本文\n", "expected_content": _REBASE_AWI}),
        (
            "post",
            "/api/entries/user-comment",
            {"state": "inbox", "filename": "agent.md", "comment": "コメント", "expected_content": _REBASE_AGENT_AWI},
        ),
        ("post", "/api/entries", {"type": "awi", "messages": ["新しい本文"], "target_repo": "github.com/example/repo"}),
        ("post", "/api/entries/batch", {"text": _BATCH_TEXT}),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "state": "inbox", "answer": "その対応で問題無い", "expected_content": _REBASE_UWI},
        ),
        ("post", "/api/entries/commit", {}),
        ("post", "/api/sync", None),
        ("post", "/api/entries/start-processing", {"filenames": ["entry.md"]}),
        ("post", "/api/entries/return-to-inbox", {"filenames": ["working.md"]}),
        ("post", "/api/entries/hold", {"filenames": ["entry.md"]}),
        ("post", "/api/entries/unhold", {"filenames": ["held.md"]}),
        ("post", "/api/entries/adopt", {"filenames": ["working.md"], "note": "対応済み"}),
        ("post", "/api/entries/reject", {"filenames": ["entry.md"], "note": "不要"}),
        ("post", "/api/entries/remove", {"filenames": ["entry.md"], "expected_content": _REBASE_AWI, "force": False}),
    ],
)
async def test_mutations_reject_rebase_in_progress_with_409(
    tmp_path: pathlib.Path,
    make_clean_repo: typing.Callable[..., pathlib.Path],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    """rebaseの中間状態が残る間、Webの全更新操作は変更前に拒否し、理由と解消操作を409で返す。

    確認がcommit時だけだと、WIを書き換えた後に拒否して作業ツリーへ変更が残り、未処理の例外が500になる。
    `edit_conflict`と同じcodeにすると、画面が詳細の開き直しを促す誤った回復文を付ける。
    """
    notes = _rebasing_notes(tmp_path, make_clean_repo)
    before = _notes_snapshot(notes)
    client = _serve_app(notes).test_client()

    response = await getattr(client, method)(path, json=payload)

    assert response.status_code == 409
    body = await response.get_json()
    assert body["code"] == "rebase_in_progress"
    assert "rebase中" in body["error"]
    assert "git -c submodule.recurse=false rebase --continue" in body["error"]
    assert "git -c submodule.recurse=false rebase --abort" in body["error"]
    assert _notes_snapshot(notes) == before


@pytest.mark.asyncio
async def test_mutation_waits_for_sync_lock(
    tmp_path: pathlib.Path, make_clean_repo: typing.Callable[..., pathlib.Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """共通ロックを保持する同期の間に届いた更新は、同期の終了を待ってから保存する。

    待たずに書き込むと、同期が作業ツリーを書き換えている途中の状態へ変更を書き込む。
    """
    notes = _rebasing_notes(tmp_path, make_clean_repo)
    rebase_dir = subprocess.run(
        ["git", "-C", str(notes), "rev-parse", "--git-path", "rebase-merge"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (notes / rebase_dir).rmdir()
    client = _serve_app(notes).test_client()
    locked = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with _wi_sync.repo_lock(notes):
            locked.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert await asyncio.to_thread(locked.wait, 10)
    attempting = asyncio.Event()
    loop = asyncio.get_running_loop()
    repo_lock = _wi_sync.repo_lock

    def observed_lock(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        loop.call_soon_threadsafe(attempting.set)
        return repo_lock(*args, **kwargs)

    monkeypatch.setattr(_wi_sync, "repo_lock", observed_lock)
    request = asyncio.ensure_future(
        client.put(
            "/api/entries/inbox/entry.md",
            json={"content": _REBASE_AWI.replace("本文", "変更後の本文"), "expected_content": _REBASE_AWI},
        )
    )
    await asyncio.wait_for(attempting.wait(), timeout=5)
    assert not request.done()
    assert _fixed_path(notes, "entry.md").read_text(encoding="utf-8") == _REBASE_AWI
    release.set()
    response = await request
    await asyncio.to_thread(holder.join, 10)

    assert response.status_code == 200
    assert "変更後の本文" in _fixed_path(notes, "entry.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_commit_api_keeps_changed_boolean(
    tmp_path: pathlib.Path,
    make_clean_repo: typing.Callable[..., pathlib.Path],
) -> None:
    """commit結果が詳細を持っても、Web APIは外部編集をcommitした真偽だけを返す。"""
    notes = make_clean_repo(tmp_path, "notes")
    setup = notes / "setup.txt"
    setup.write_text("初期状態\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(notes), "add", setup.name], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(notes), "commit", "-m", "local setup"], check=True, capture_output=True)
    client = _serve_app(notes).test_client()
    empty = await client.post("/api/entries/commit", json={})
    assert empty.status_code == 200
    assert await empty.get_json() == {"changed": False}
    (notes / "external.txt").write_text("外部編集\n", encoding="utf-8")
    changed = await client.post("/api/entries/commit", json={})
    assert changed.status_code == 200
    assert await changed.get_json() == {"changed": True}
    repeated = await client.post("/api/entries/commit", json={})
    assert await repeated.get_json() == {"changed": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_state", ["inbox", "adopted", "rejected"])
async def test_web_edits_commit_locally_before_remote_sync(tmp_path: pathlib.Path, entry_state: str) -> None:
    """画面の連続編集はremoteの応答を待たず、明示同期で両commitを反映する。"""
    remote = tmp_path / "remote.git"
    notes = tmp_path / "private-notes"

    git_repository.init_bare_repository(remote)
    git_repository.git_output(tmp_path, "clone", str(remote), str(notes))
    git_repository.git_output(notes, "config", "user.name", "Test")
    git_repository.git_output(notes, "config", "user.email", "test@example.com")
    inbox = _fixed_path(notes, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    original = f"---\nstate: {entry_state}\ntype: awi\ntarget_repo: example/repo\n---\n\n元の本文\n"
    first_content = original.replace("元の本文", "最初の編集")
    second_content = original.replace("元の本文", "次の編集")
    (inbox / "entry.md").write_text(original, encoding="utf-8")
    git_repository.git_output(notes, "add", ".")
    git_repository.git_output(notes, "commit", "-m", "seed")
    git_repository.git_output(notes, "push", "-u", "origin", "main")
    remote_before = git_repository.git_output(tmp_path, "--git-dir", str(remote), "rev-parse", "refs/heads/main")
    app = serve_app.create_app(notes, config.ServeConfig("127.0.0.1", 28766), state.ServeState(notes))
    client = app.test_client()

    for previous, updated in ((original, first_content), (first_content, second_content)):
        response = await client.put(
            f"/api/entries/{entry_state}/entry.md", json={"content": updated, "expected_content": previous}
        )
        assert response.status_code == 200
    assert (inbox / "entry.md").read_text(encoding="utf-8") == second_content
    assert git_repository.git_output(tmp_path, "--git-dir", str(remote), "rev-parse", "refs/heads/main") == remote_before
    assert git_repository.git_output(notes, "rev-parse", "HEAD") != remote_before

    response = await client.post("/api/sync", json={})
    assert response.status_code == 200
    assert await response.get_json() == {"synced": True}
    assert git_repository.git_output(
        tmp_path, "--git-dir", str(remote), "rev-parse", "refs/heads/main"
    ) == git_repository.git_output(notes, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_uwi_reject_transition_succeeds(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UWIエントリも他種別と同様に不採用遷移が成功する。"""

    _disable_wi_git(monkeypatch)
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "entry.md").write_text(
        "---\nstate: inbox\ntype: uwi\ntarget_repo: github.com/example/foo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post("/api/entries/reject", json={"filenames": ["entry.md"]})
    assert response.status_code == 200
    assert (inbox / "entry.md").is_file()
    assert entry_state_of(inbox / "entry.md") == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question_metadata", ["question_type: post-approval", "question_type: choice\nchoices: [その対応で問題無い, 問題がある]"]
)
@pytest.mark.parametrize(
    ("answer", "saved_state"),
    [
        (" その対応で問題無い\n", "adopted"),
        ("問題がある\n是正してください。", "inbox"),
        ("その対応で問題無い\n追加の確認があります。", "inbox"),
    ],
)
async def test_answer_api_returns_saved_post_approval_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    question_metadata: str,
    answer: str,
    saved_state: str,
) -> None:
    """回答APIは肯定だけを採用し、是正・追記を残した実際の保存先を返す。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    content = (
        f"---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\n{question_metadata}\n---\n\n"
        "## 質問\n\n実施済みの対応を承認しますか。\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    inbox = _fixed_path(tmp_path, "post-approval.md")
    inbox.write_text(content, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    response = await client.post(
        "/api/entries/answer",
        json={
            "filename": inbox.name,
            "state": "inbox",
            "answer": answer,
            "expected_content": content,
        },
    )
    active_response = await client.get("/api/entries?status=active")

    assert response.status_code == 200
    assert await response.get_json() == {"changed": True, "state": saved_state}
    assert active_response.status_code == 200
    assert len((await active_response.get_json())["entries"]) == (0 if saved_state == "adopted" else 1)
    saved = inbox.read_text(encoding="utf-8")
    assert entry_state_of(inbox) == saved_state
    assert answer.strip() in saved
    assert ("- 採否: adopted" in saved) == (saved_state == "adopted")
    assert inbox.is_file()
    assert entry_path(tmp_path, inbox.name) == inbox


@pytest.mark.asyncio
async def test_answer_api_waits_for_lock_held_longer_than_background_sync_timeout(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答APIは定期同期の待機上限を超えて保持されたロックの解放を待ち、競合で失敗しない。

    低速な環境では定期同期のfetch・pushが数秒に及ぶため、その間の保存を409で失敗させない。
    """
    lock_path = tmp_path / "repo.lock"
    attempting: asyncio.Queue[float] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(wi_operations, "_BACKGROUND_SYNC_LOCK_TIMEOUT", 0.02)

    class ObservedLock(filelock.FileLock):
        def _acquire(self) -> None:
            super()._acquire()
            if not self.is_locked:
                loop.call_soon_threadsafe(attempting.put_nowait, loop.time())

    def lock(_path: pathlib.Path, *, timeout: float = -1) -> filelock.FileLock:
        return ObservedLock(str(lock_path), timeout=timeout)

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    content = (
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\n---\n\n"
        "## 質問\n\n進めますか。\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    inbox = _fixed_path(tmp_path, "question.md")
    inbox.write_text(content, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with filelock.FileLock(str(lock_path)):
            acquired.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    request: asyncio.Task[typing.Any] | None = None
    try:
        assert await asyncio.to_thread(acquired.wait, 5)
        request = asyncio.create_task(
            client.post(
                "/api/entries/answer",
                json={"filename": inbox.name, "state": "inbox", "answer": "はい", "expected_content": content},
            )
        )
        async with asyncio.timeout(5):
            first_attempt = await attempting.get()
            # 定期同期の短い期限を越えても取得失敗が続くことを観測してから解放する。
            while await attempting.get() - first_attempt < wi_operations._BACKGROUND_SYNC_LOCK_TIMEOUT:
                pass
        assert not request.done()
        release.set()
        response = await asyncio.wait_for(request, timeout=5)
    finally:
        release.set()
        if request is not None:
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        await asyncio.to_thread(holder.join, 5)
        assert not holder.is_alive()

    assert response.status_code == 200
    assert "はい" in inbox.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_remove_api_uses_user_permissions_for_terminal_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web APIは実行環境変数にかかわらず人間主体として終了状態を削除する。"""

    _disable_wi_git(monkeypatch)
    monkeypatch.setenv("AI_AGENT", "1")
    adopted = _fixed_path(tmp_path, "entry.md").parent
    content = "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n"
    target = adopted / "entry.md"
    content = content.replace("state: inbox", "state: adopted")
    target.write_text(content, encoding="utf-8")
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))

    response = await app.test_client().post(
        "/api/entries/remove",
        json={"filenames": [target.name], "state": "adopted", "expected_content": content, "force": False},
    )

    assert response.status_code == 200
    assert not target.exists()


@pytest.mark.asyncio
async def test_remove_api_returns_edit_conflict_before_target_repo_validation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得後に非UTF-8へ書き換えられた削除対象は、対象リポジトリの一致を確かめる前に削除の競合として返す。"""

    _disable_wi_git(monkeypatch)
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / "unreadable.md"
    target.write_bytes(b"\xff")
    original = "---\nstate: inbox\ntype: awi\ntarget_repo: github.com/example/repo\n---\n\n取得時本文\n"
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries/remove",
        json={
            "filenames": [target.name],
            "state": "inbox",
            "target_repo": "github.com/example/repo",
            "expected_content": original,
            "force": False,
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert response.content_type == "application/json"
    assert target.read_bytes() == b"\xff"


@pytest.mark.asyncio
async def test_answer_and_remove_apis_return_edit_conflict_when_concurrent_change_moves_target(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別主体が移動した回答・削除対象を409競合として保全する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        for path in (answer_inbox, remove_inbox):
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("state: inbox", "state: processing"), encoding="utf-8")
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    inbox, _processing = make_inbox_and_processing(tmp_path)
    uwi_content = UNANSWERED_UWI_TEXT
    awi_content = AWI_TEXT
    answer_inbox = inbox / "answer.md"
    remove_inbox = inbox / "remove.md"
    answer_inbox.write_text(uwi_content, encoding="utf-8")
    remove_inbox.write_text(awi_content, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    answer_response = await client.post(
        "/api/entries/answer",
        json={
            "filename": answer_inbox.name,
            "state": "inbox",
            "answer": "回答",
            "expected_content": uwi_content,
        },
    )
    remove_response = await client.post(
        "/api/entries/remove",
        json={
            "filenames": [remove_inbox.name],
            "state": "inbox",
            "expected_content": awi_content,
            "force": False,
        },
    )

    for response in (answer_response, remove_response):
        assert response.status_code == 409
        assert await response.get_json() == {
            "code": "edit_conflict",
            "error": "編集中に他プロセスが対象を変更しました",
        }
    assert answer_inbox.read_text(encoding="utf-8") == uwi_content.replace("state: inbox", "state: processing")
    assert remove_inbox.read_text(encoding="utf-8") == awi_content.replace("state: inbox", "state: processing")


@pytest.mark.asyncio
async def test_web_input_error_response_keeps_next_action_from_any_validation(tmp_path: pathlib.Path) -> None:
    """共通の例外応答は、どの入力検証が送出した`WebInputError`でも理由と次の操作の組を保つ。"""
    app = _serve_app(tmp_path)

    @app.get("/test-only/web-input-error")
    async def raise_web_input_error() -> str:
        raise wi_web_input.WebInputError("別の入力検証の理由", next_action="別の入力を直して再実行する")

    response = await app.test_client().get("/test-only/web-input-error")

    assert response.status_code == 400
    assert await response.get_json() == {"error": "別の入力検証の理由", "next_action": "別の入力を直して再実行する"}
