"""serveのconfigに関するmutationsの試験。"""

# pylint: disable=protected-access

import datetime
import pathlib
import re
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.wi import user_comment
from agent_toolkit._testing.serve_support import (
    _BATCH_TEXT,
    _disable_wi_git,
    _patch_batch_repo_operations,
    _patch_comment_edit_dependencies,
    _session_review_awi,
)
from agent_toolkit._testing.wi_entry_files import entry_path


@pytest.mark.asyncio
async def test_remove_api_rejects_changed_and_unreadable_expected_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内容変更と読取り不能を409で保護し、空の確認時本文も受理する。"""

    _disable_wi_git(monkeypatch)
    original = "---\ntype: awi\nstate: inbox\n---\n\n確認時本文\n"
    changed = entry_path(tmp_path, "changed.md")
    changed.write_text(original.replace("確認時", "外部更新後"), encoding="utf-8")
    unreadable = entry_path(tmp_path, "unreadable.md")
    unreadable.write_bytes(b"\xff")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for filename in (changed.name, unreadable.name):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [filename],
                "state": "inbox",
                "expected_content": original,
                "force": False,
            },
        )
        assert response.status_code == 409
        assert await response.get_json() == {
            "code": "edit_conflict",
            "error": "編集中に他プロセスが対象を変更しました",
        }
        assert entry_path(tmp_path, filename).exists()

    # 空本文にはstateを保存できないため、旧配置由来の状態を使う互換入力として確認する。
    empty = entry_path(tmp_path, "empty.md", legacy_layout=True)
    empty.write_text("", encoding="utf-8")
    empty_response = await client.post(
        "/api/entries/remove",
        json={
            "filenames": [empty.name],
            "state": "inbox",
            "expected_content": "",
            "force": False,
        },
    )
    assert empty_response.status_code == 200
    assert not empty.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["---\ntarget_repo:\n- github.com/example/repo\n---\n\n本文", "本文だけ"],
)
async def test_add_api_rejects_omitted_target_repo_without_frontmatter_value(
    tmp_path: pathlib.Path,
    message: str,
) -> None:
    """frontmatterのtarget_repoが欠落・非文字列の場合は400で拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries", json={"type": "awi", "messages": [message]})

    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.parametrize(
    "fixed_now",
    [
        datetime.datetime(2026, 10, 31, 23, 59, 59),
        datetime.datetime(2026, 11, 1, 0, 0, 0),
        datetime.datetime(2026, 12, 31, 23, 59, 59),
        datetime.datetime(2027, 1, 1, 0, 0, 0),
    ],
)
@pytest.mark.asyncio
async def test_batch_api_imports_crlf_text(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fixed_now: datetime.datetime
) -> None:
    """CRLF改行の一括登録テキストも取り込み、保存内容の改行をLFへ正規化し、日付の無い名前を投入日の年月へ保存する。

    APIは時刻を引数で受け取らないため、一括取り込みの既存の`now`引数へ固定時刻を渡す。
    月末・年末の前後で、保存先の年月が投入日に従うことを確かめる。
    """
    _patch_batch_repo_operations(monkeypatch)
    original = wi_operations.awi_batch.add_batch_entries

    def add_batch_entries_at_fixed_now(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        kwargs["now"] = fixed_now
        return original(*args, **kwargs)

    monkeypatch.setattr(wi_operations.awi_batch, "add_batch_entries", add_batch_entries_at_fixed_now)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    text = _BATCH_TEXT.replace("\n", "\r\n")
    response = await app.test_client().post("/api/entries/batch", json={"text": text})

    assert response.status_code == 201
    assert (await response.get_json())["filenames"] == ["keep.md"]
    saved_path = entry_path(tmp_path, "keep.md", fallback_date=fixed_now.date())
    assert saved_path.parent == tmp_path / f"{fixed_now:%Y}" / f"{fixed_now:%m}"
    assert saved_path.read_bytes() == (
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n".encode()
    )


@pytest.mark.parametrize(
    ("body", "comment", "message"),
    [
        (
            "本文\n\n## ユーザーコメント\n\nコメント\n\n## 後続見出し\n\n後続本文\n",
            "更新",
            "ユーザーコメント節の後ろに別のH2見出しがあります",
        ),
        (
            "本文\n\n## ユーザーコメント\n\n一つ目\n\n## ユーザーコメント\n\n二つ目\n",
            "更新",
            "ユーザーコメント節が複数あります",
        ),
        ("本文\n", "## コメント内見出し", "ユーザーコメントにコードフェンス外のH2見出しを含められません"),
        ("本文\n", "```markdown\n## コメント内見出し\n```", ""),
    ],
)
def test_user_comment_pure_update_rejects_invalid_structure_without_mutation(
    body: str,
    comment: str,
    message: str,
) -> None:
    """不正な節・コメントは判別可能なエラーとなり、保存対象を変更しない。"""
    original = _session_review_awi(body)

    if message:
        with pytest.raises(user_comment.UserCommentError, match=re.escape(message)):
            user_comment.update_user_comment(original, comment)
        assert original == _session_review_awi(body)
    else:
        updated = user_comment.update_user_comment(original, comment)
        assert user_comment.extract_user_comment(updated) == comment


@pytest.mark.asyncio
async def test_user_comment_api_returns_edit_conflict_without_losing_latest_content(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """期待本文と現行本文が異なる場合は409として最新本文を保持する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    original = _session_review_awi("取得時本文\n").replace("---\n", "---\nstate: inbox\n", 1)
    latest = _session_review_awi("外部更新後の本文\n").replace("---\n", "---\nstate: inbox\n", 1)
    path = entry_path(tmp_path, "awi.md")
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    detail = await (await app.test_client().get("/api/entries/awi.md")).get_json()
    path.write_text(latest, encoding="utf-8")

    response = await app.test_client().post(
        "/api/entries/user-comment",
        json={
            "state": "inbox",
            "filename": "awi.md",
            "comment": "古い基準のコメント",
            "expected_content": detail["entry"]["content"],
        },
    )

    assert response.status_code == 409
    assert await response.get_json() == {
        "code": "edit_conflict",
        "error": "編集中に他プロセスが対象を変更しました",
    }
    assert path.read_text(encoding="utf-8") == latest
