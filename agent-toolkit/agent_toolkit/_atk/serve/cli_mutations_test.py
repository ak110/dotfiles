"""serveのcliに関するmutationsの試験。"""

# pylint: disable=protected-access

import contextlib
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import user_comment
from agent_toolkit._atk.wi import uwi as wi_uwi
from agent_toolkit._testing import wi_entry_files as wi_files
from agent_toolkit._testing.serve_support import _disable_wi_git, _patch_comment_edit_dependencies, _session_review_awi


@pytest.mark.asyncio
async def test_transition_routes_forward_only_supported_explicit_states(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """明示状態の対象だけを遷移・削除し、省略時の解決と禁止状態の拒否を保つ。"""
    _disable_wi_git(monkeypatch)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    other = wi_files.write_awi(tmp_path, "other.md", state="inbox")
    unchanged = other.read_bytes()
    for action, initial, expected in (
        ("start-processing", "hold", "processing"),
        ("return-to-inbox", "rejected", "inbox"),
        ("adopt", "hold", "adopted"),
        ("reject", "hold", "rejected"),
        ("remove", "hold", None),
    ):
        path = wi_files.write_awi(tmp_path, f"{action}.md", state=initial)
        response = await client.post(f"/api/entries/{action}", json={"filenames": [path.name], "state": initial})
        assert response.status_code == 200
        if expected is None:
            assert not path.exists()
        else:
            assert wi_entries.entry_state_of(path) == expected
        assert other.read_bytes() == unchanged

    path = wi_files.write_awi(tmp_path, "implicit.md")
    implicit = await client.post("/api/entries/adopt", json={"filenames": [path.name]})
    assert implicit.status_code == 200
    assert wi_entries.entry_state_of(path) == "adopted"
    before = path.read_bytes()
    invalid = await client.post("/api/entries/adopt", json={"filenames": [path.name], "state": "inbox"})
    assert invalid.status_code == 400
    assert path.read_bytes() == before
    assert other.read_bytes() == unchanged


@pytest.mark.asyncio
async def test_answer_and_remove_apis_target_state_and_keep_legacy_resolution(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定の有無にかかわらず一意なfilenameへ作用し、processingの削除を保護する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    wi_files.write_uwi(tmp_path, "answer-state.md", state="inbox", answer=wi_uwi.ANSWER_MARKER)
    wi_files.write_uwi(tmp_path, "answer-legacy.md", state="processing", answer=wi_uwi.ANSWER_MARKER)
    for filename, state_name in (
        ("remove-state.md", "inbox"),
        ("remove-legacy.md", "processing"),
        ("remove-protected.md", "processing"),
    ):
        wi_files.write_awi(tmp_path, filename, state=state_name)
    untouched = wi_files.write_awi(tmp_path, "other.md", state="processing")
    untouched_content = untouched.read_text(encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    state_answer = await client.post(
        "/api/entries/answer",
        json={
            "filename": "answer-state.md",
            "state": "inbox",
            "answer": "未処理側への回答",
            "expected_content": wi_files.entry_path(tmp_path, "answer-state.md").read_text(encoding="utf-8"),
        },
    )
    assert state_answer.status_code == 200, await state_answer.get_json()
    assert wi_files.entry_path(tmp_path, "answer-state.md").read_text(encoding="utf-8").endswith("未処理側への回答\n")

    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "answer-legacy.md", "answer": "従来経路の回答"},
    )
    assert legacy_answer.status_code == 200
    assert wi_files.entry_path(tmp_path, "answer-legacy.md").read_text(encoding="utf-8").endswith("従来経路の回答\n")

    state_remove = await client.post(
        "/api/entries/remove",
        json={
            "filenames": ["remove-state.md"],
            "state": "inbox",
            "expected_content": wi_files.entry_path(tmp_path, "remove-state.md").read_text(encoding="utf-8"),
            "force": False,
        },
    )
    assert state_remove.status_code == 200
    assert not wi_files.entry_path(tmp_path, "remove-state.md").exists()

    protected = await client.post(
        "/api/entries/remove",
        json={"filenames": ["remove-protected.md"], "force": False},
    )
    assert protected.status_code == 400
    assert await protected.get_json() == {"error": "指定したエントリを操作できません"}

    legacy_remove = await client.post(
        "/api/entries/remove",
        json={"filenames": ["remove-legacy.md"], "force": True},
    )
    assert legacy_remove.status_code == 200
    assert not wi_files.entry_path(tmp_path, "remove-legacy.md").exists()
    assert untouched.read_text(encoding="utf-8") == untouched_content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/entries", {"type": "awi", "messages": ["awi"], "target_repo": None}),
        (
            "/api/entries",
            {
                "type": "uwi",
                "messages": ["UWIですか？"],
                "scope": "test",
                "question_type": "free-form",
                "target_repo": None,
            },
        ),
    ],
)
async def test_web_add_mutations_reject_null_target_repo(
    tmp_path: pathlib.Path,
    path: str,
    payload: dict[str, object],
) -> None:
    """`target_repo`キーが存在しても値が`null`の場合は必須検証を回避できず400になる。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


def test_user_comment_pure_update_preserves_frontmatter_and_previous_body() -> None:
    """予約節の置換でfrontmatterと通常本文を保持し、再抽出できる。"""
    original = _session_review_awi("通常本文\n\n## 実装メモ\n\n既存の見出し本文\n\n## ユーザーコメント\n\n旧コメント\n")

    updated = user_comment.update_user_comment(original, "\n\n新しいコメント\n\n2行目\n\n")

    assert updated == _session_review_awi(
        "通常本文\n\n## 実装メモ\n\n既存の見出し本文\n\n## ユーザーコメント\n\n新しいコメント\n\n2行目\n"
    )
    assert user_comment.extract_user_comment(updated) == "新しいコメント\n\n2行目"


@pytest.mark.asyncio
async def test_user_comment_api_appends_and_replaces_inbox_and_hold_session_review_awi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """専用APIがinboxとholdの対象本文へ追記し、同じ節だけを置換する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    original = _session_review_awi("通常本文\n")
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    detail_response = await client.get("/api/entries/inbox/awi.md")
    detail = await detail_response.get_json()
    assert detail_response.status_code == 200
    assert detail["entry"]["user_comment"] is None
    assert detail["entry"]["user_comment_editable"] is True

    first = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "最初のコメント",
            "expected_content": original,
        },
    )
    assert first.status_code == 200
    assert await first.get_json() == {"changed": True}
    after_first = path.read_text(encoding="utf-8")
    assert after_first == _session_review_awi("通常本文\n\n## ユーザーコメント\n\n最初のコメント\n")

    second = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "置換後のコメント",
            "expected_content": after_first,
        },
    )
    assert second.status_code == 200
    assert await second.get_json() == {"changed": True}
    after_second = path.read_text(encoding="utf-8")
    assert after_second == _session_review_awi("通常本文\n\n## ユーザーコメント\n\n置換後のコメント\n")

    final_detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    assert final_detail["entry"]["user_comment"] == "置換後のコメント"

    hold = tmp_path / "hold"
    hold.mkdir()
    held_path = hold / "held.md"
    held_path.write_text(original, encoding="utf-8")
    held_detail = await (await client.get("/api/entries/hold/held.md")).get_json()
    assert held_detail["entry"]["user_comment_editable"] is True
    held = await client.post(
        "/api/entries/user-comment",
        json={
            "state": "hold",
            "filename": "held.md",
            "comment": "保留中のコメント",
            "expected_content": original,
        },
    )
    assert held.status_code == 200
    assert user_comment.extract_user_comment(held_path.read_text(encoding="utf-8")) == "保留中のコメント"
