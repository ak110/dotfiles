"""serveのstateに関するmutationsの試験。"""

# pylint: disable=protected-access

import contextlib
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing import wi_entry_files as wi_files
from agent_toolkit._testing.serve_support import _disable_wi_git, _patch_comment_edit_dependencies, _run_node_ui


@pytest.mark.asyncio
async def test_add_api_rejects_uwi_only_scope_on_awi(tmp_path: pathlib.Path) -> None:
    """UWI専用の`scope`をAWIへ指定すると拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        json={"type": "awi", "messages": ["x"], "target_repo": "example/repo", "scope": "s"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_edit_and_answer_apis_detect_external_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得後の外部更新を409で保護し、最新値と従来形式の更新を許可する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)

    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    awi_path = inbox / "awi.md"
    initial_awi = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n取得時本文\n"
    awi_path.write_text(initial_awi, encoding="utf-8")
    uwi_path = inbox / "question.md"
    initial_uwi = (
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
    )
    uwi_path.write_text(initial_uwi, encoding="utf-8")

    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    awi_detail = await (await client.get("/api/entries/inbox/awi.md")).get_json()
    external_awi = initial_awi.replace("取得時本文", "外部更新後の本文")
    awi_path.write_text(external_awi, encoding="utf-8")
    conflict = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": "ユーザーの本文", "expected_content": awi_detail["entry"]["content"]},
    )
    assert conflict.status_code == 409
    assert (await conflict.get_json())["code"] == "edit_conflict"
    assert awi_path.read_text(encoding="utf-8") == external_awi

    latest_content = external_awi.replace("外部更新後の本文", "最新基準で更新")
    latest = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": latest_content, "expected_content": external_awi},
    )
    assert latest.status_code == 200
    assert awi_path.read_text(encoding="utf-8") == latest_content
    legacy_content = latest_content.replace("最新基準で更新", "従来形式で更新")
    legacy = await client.put(
        "/api/entries/inbox/awi.md",
        json={"content": legacy_content},
    )
    assert legacy.status_code == 200
    assert awi_path.read_text(encoding="utf-8") == legacy_content

    uwi_detail = await (await client.get("/api/entries/inbox/question.md")).get_json()
    external_uwi = initial_uwi.replace("質問？", "外部更新後の質問？")
    uwi_path.write_text(external_uwi, encoding="utf-8")
    answer_conflict = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "古い基準からの回答",
            "expected_content": uwi_detail["entry"]["content"],
        },
    )
    assert answer_conflict.status_code == 409
    assert (await answer_conflict.get_json())["code"] == "edit_conflict"
    assert uwi_path.read_text(encoding="utf-8") == external_uwi

    latest_answer = await client.post(
        "/api/entries/answer",
        json={
            "filename": "question.md",
            "answer": "最新基準からの回答",
            "expected_content": external_uwi,
        },
    )
    assert latest_answer.status_code == 200
    assert uwi_path.read_text(encoding="utf-8").endswith("最新基準からの回答\n")
    legacy_answer = await client.post(
        "/api/entries/answer",
        json={"filename": "question.md", "answer": "従来形式からの回答"},
    )
    assert legacy_answer.status_code == 200
    assert uwi_path.read_text(encoding="utf-8").endswith("従来形式からの回答\n")


@pytest.mark.asyncio
async def test_web_transition_mutations_allow_omitted_target_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態遷移系APIはfilenameで対象を一意に特定できるためtarget_repo省略を許容する。"""

    _disable_wi_git(monkeypatch)
    path = wi_files.write_awi(tmp_path, "entry.md", target_repo="github.com/example/foo")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post("/api/entries/adopt", json={"filenames": ["entry.md"]})
    assert response.status_code == 200
    assert path.is_file()
    assert wi_entries.entry_state_of(path) == "adopted"


@pytest.mark.asyncio
async def test_add_api_accepts_omitted_target_repo_with_frontmatter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文frontmatterにtarget_repoがあれば対象リポジトリ指定を省略できる。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post(
        "/api/entries",
        json={"type": "awi", "messages": ["---\ntarget_repo: github.com/Example/Repo\n---\n\n本文"]},
    )

    assert response.status_code == 201
    body = await response.get_json()
    content = wi_files.entry_path(tmp_path, body["filenames"][0]).read_text(encoding="utf-8")
    assert "target_repo: github.com/example/repo" in content
    assert "source:" not in content


def test_batch_creation_sends_raw_text_and_hides_frontmatter_driven_fields() -> None:
    """一括登録では生テキストを送り、対象リポジトリ欄と投入元欄を隠す。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-kind'].value = 'batch';
updateCreateFields();
const hiddenRepoFields = elements['create-repo-fields'].hidden;
const contentLabel = elements['create-content-label'].textContent;
elements['create-content'].value = '  show形式テキスト  ';
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries/batch')) {
    return {
      ok: true, status: 201, statusText: 'Created',
      json: async () => ({filenames: ['new.md'], mapping: {'old.md': 'new.md'}, warnings: ['依存先が不在']})
    };
  }
  if (url.endsWith('/api/repos?status=all')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries/batch'));
process.stdout.write(JSON.stringify({
  hiddenRepoFields,
  contentLabel,
  body: JSON.parse(call.options.body),
  toast: elements['toast'].textContent,
  detailOpen: elements['detail-dialog'].open
}));
"""
    )
    assert result == {
        "hiddenRepoFields": True,
        "contentLabel": "show形式テキスト（必須）",
        "body": {"text": "  show形式テキスト  "},
        "toast": "［WI / node-test / /notes / 項目登録］1件を取り込みました。改名: old.md -> new.md 警告: 依存先が不在",
        "detailOpen": False,
    }


@pytest.mark.asyncio
async def test_remove_api_accepts_terminal_states_and_preserves_protected_states(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """終了状態とholdは削除し、processingの保護を維持する。"""
    _patch_comment_edit_dependencies(monkeypatch)
    contents: dict[str, str] = {}
    for state_name in ("adopted", "rejected", "hold", "processing"):
        path = wi_files.write_awi(tmp_path, f"{state_name}.md", state=state_name)
        contents[state_name] = path.read_text(encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    for state_name in ("adopted", "rejected", "hold"):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [f"{state_name}.md"],
                "state": state_name,
                "expected_content": contents[state_name],
                "force": False,
            },
        )
        assert response.status_code == 200
        assert not wi_files.entry_path(tmp_path, f"{state_name}.md").exists()

    for state_name in ("processing",):
        response = await client.post(
            "/api/entries/remove",
            json={
                "filenames": [f"{state_name}.md"],
                "state": state_name,
                "expected_content": contents[state_name],
                "force": False,
            },
        )
        assert response.status_code == 400
        assert wi_files.entry_path(tmp_path, f"{state_name}.md").is_file()
