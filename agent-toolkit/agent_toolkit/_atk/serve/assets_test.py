"""serveのassetsに関するmutationsの試験。"""

# pylint: disable=protected-access

import contextlib
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment
from agent_toolkit._testing import wi_entry_files as wi_files
from agent_toolkit._testing.serve_support import (
    _BATCH_TEXT,
    _disable_wi_git,
    _patch_batch_repo_operations,
    _patch_comment_edit_dependencies,
    _session_review_awi,
)


def test_web_transition_rejects_commit_without_resolvable_worktree(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Web採否はcommitを検証できる作業ツリーが無ければ状態変更しない。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "awi.md").write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n本文\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)

    with pytest.raises(wi_operations.WebApiInputError, match="指定したエントリを操作できません"):
        wi_operations.Operations(tmp_path).transition("adopt", ["awi.md"], commit="abcdef1")

    assert (tmp_path / "inbox/awi.md").is_file()
    assert not (tmp_path / "adopted/awi.md").exists()
    assert "github.com/example/foo" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_answer_api_rejects_awi_entry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AWIエントリへの回答送信は拒否する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries/answer",
        json={"filename": "entry.md", "answer": "回答"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_answer_api_returns_edit_conflict_for_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答対象が非UTF-8へ書き換えられた場合は409の競合として返し、元のバイト列を保つ。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    target = inbox / "question.md"
    target.write_bytes(b"\xff")
    original = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries/answer",
        json={
            "filename": target.name,
            "state": "inbox",
            "answer": "回答",
            "expected_content": original,
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert target.read_bytes() == b"\xff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload", "filename", "initial_content"),
    [
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": None},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": ""},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "put",
            "/api/entries/inbox/awi.md",
            {"content": "更新本文", "expected_content": "  "},
            "awi.md",
            "変更前の本文\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": None},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": ""},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
        (
            "post",
            "/api/entries/answer",
            {"filename": "question.md", "answer": "回答", "expected_content": "\t"},
            "question.md",
            "## 質問\n\n質問？\n\n## 回答\n\n",
        ),
    ],
)
async def test_edit_and_answer_apis_reject_invalid_specified_expected_content(
    tmp_path: pathlib.Path,
    method: str,
    path: str,
    payload: dict[str, object],
    filename: str,
    initial_content: str,
) -> None:
    """明示した`expected_content`のnull・空値を400で拒否し、対象を変更しない。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    entry = inbox / filename
    entry.write_text(initial_content, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await getattr(app.test_client(), method)(path, json=payload)
    assert response.status_code == 400
    assert entry.read_text(encoding="utf-8") == initial_content


def test_add_omits_frontmatter_when_source_is_the_only_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """投入元だけのfrontmatterを除いて本文を保存し、空の区切りを残さない。"""
    _disable_wi_git(monkeypatch)
    filenames = wi_operations.Operations(tmp_path).add(
        ["---\nsource: add-awi\n---\n\n本文"],
        entry_type="awi",
        target_repo="github.com/example/repo",
    )
    assert len(filenames) == 1
    content = wi_files.entry_path(tmp_path, filenames[0]).read_text(encoding="utf-8")
    parsed = wi_entries.parse_frontmatter(content)
    assert parsed is not None
    metadata, body = parsed
    assert metadata["target_repo"] == "github.com/example/repo"
    assert metadata["type"] == "awi"
    assert "source" not in metadata
    assert body.strip() == "本文"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{}, {"text": ""}, {"text": "  "}, {"text": 1}, {"text": "ただの本文\n"}, {"text": _BATCH_TEXT, "type": "awi"}],
)
async def test_batch_api_rejects_invalid_inputs(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    """必須キー・型・空文字・未知キー・show形式外の入力を400で拒否する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json=payload)

    assert response.status_code == 400
    assert not (tmp_path / "inbox").exists() or not list((tmp_path / "inbox").iterdir())


def test_user_comment_pure_update_ignores_fenced_heading_and_appends_once() -> None:
    """コードフェンス内の同名文字列を見出しと誤認せず、予約節を1件だけ追記する。"""
    original = _session_review_awi("```markdown\n## ユーザーコメント\n```\n\n通常本文\n")

    updated = user_comment.update_user_comment(original, "コメント")

    assert updated.count("## ユーザーコメント") == 2
    assert updated.endswith("## ユーザーコメント\n\nコメント\n")
    assert user_comment.extract_user_comment(updated) == "コメント"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "editable"),
    [(None, False), ("human", True), ("alert-monitor", True), ("plan", True)],
)
async def test_user_comment_api_matches_agent_source_classification(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str | None,
    editable: bool,
) -> None:
    """詳細表示と保存APIが同じエージェント由来判定を使う。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("本文\n", source=source)
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    assert detail["entry"]["user_comment_editable"] is editable
    response = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "追加コメント",
            "expected_content": original,
        },
    )
    assert response.status_code == (200 if editable else 400)
    if editable:
        assert user_comment.extract_user_comment(path.read_text(encoding="utf-8")) == "追加コメント"
    else:
        assert path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_user_comment_api_requires_expected_content_and_rejects_comment_h2(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """expected_contentの省略・空値とコメント内H2を保存前に拒否する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for payload in (
        {"state": "inbox", "filename": "awi.md", "comment": "コメント"},
        {
            "state": "inbox",
            "filename": "awi.md",
            "comment": "コメント",
            "expected_content": "",
        },
        {
            "state": "inbox",
            "filename": "awi.md",
            "comment": "## 禁止見出し",
            "expected_content": original,
        },
    ):
        response = await client.post("/api/entries/user-comment", json=payload)
        assert response.status_code == 400
        assert path.read_text(encoding="utf-8") == original
