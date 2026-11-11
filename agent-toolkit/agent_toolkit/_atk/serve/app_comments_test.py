"""serveのappに関するcommentsの試験。"""

# pylint: disable=protected-access

import pathlib

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.serve_app_support import _fixed_path
from agent_toolkit._testing.serve_support import _patch_comment_edit_dependencies, _session_review_awi


@pytest.mark.asyncio
async def test_user_comment_api_rejects_non_inbox_states_and_uwi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """processing・終了状態およびUWIには操作を提供しない。"""
    _patch_comment_edit_dependencies(monkeypatch)
    contents = {
        "processing": _session_review_awi("processing本文\n"),
        "adopted": _session_review_awi("adopted本文\n"),
        "rejected": _session_review_awi("rejected本文\n"),
        "uwi": _session_review_awi("UWI本文\n", entry_type="uwi"),
    }
    for state_name, content in contents.items():
        path = _fixed_path(tmp_path, f"{state_name}.md")
        content = content.replace("state: inbox", f"state: {'inbox' if state_name == 'uwi' else state_name}")
        contents[state_name] = content
        path.write_text(content, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()
    for state_name, content in contents.items():
        filename = f"{state_name}.md"
        response = await client.post(
            "/api/entries/user-comment",
            json={
                "state": "inbox" if state_name == "uwi" else state_name,
                "filename": filename,
                "comment": "追加コメント",
                "expected_content": content,
            },
        )
        assert response.status_code == 400
        actual_path = entry_path(tmp_path, filename)
        assert actual_path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_user_comment_api_empty_comment_removes_section(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空のコメントを保存するとユーザーコメント節だけが消え、節が無い状態の再保存も成功する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    kept = _session_review_awi("通常本文\n")
    original = kept + "\n## ユーザーコメント\n\n削除対象のコメント\n"
    path = inbox / "awi.md"
    path.write_text(original, encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    payload = {"state": "inbox", "filename": "awi.md", "comment": "", "expected_content": original}
    removed = await client.post("/api/entries/user-comment", json=payload)
    assert removed.status_code == 200
    assert await removed.get_json() == {"changed": True}
    assert path.read_text(encoding="utf-8") == kept
    detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    assert detail["entry"]["user_comment"] is None
    assert detail["entry"]["user_comment_editable"] is True

    again = await client.post("/api/entries/user-comment", json={**payload, "comment": " ", "expected_content": kept})
    assert again.status_code == 200
    assert await again.get_json() == {"changed": False}
    assert path.read_text(encoding="utf-8") == kept

    missing = await client.post("/api/entries/user-comment", json={**payload, "comment": None, "expected_content": kept})
    assert missing.status_code == 400
