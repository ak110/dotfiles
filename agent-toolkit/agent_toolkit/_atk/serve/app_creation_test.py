"""serveのappに関するcreationの試験。"""

# pylint: disable=protected-access

import contextlib
import datetime
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state
from agent_toolkit._atk.wi import repo as awi_repo
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.serve_app_support import (
    _SINGLE_SHOW_TEXT,
    _fixed_path,
    _patch_single_add_operations,
    _saved_files,
    _serve_app,
)
from agent_toolkit._testing.serve_support import _BATCH_TEXT, _patch_batch_repo_operations, _run_node_ui


@pytest.mark.asyncio
async def test_add_api_rejects_missing_type(tmp_path: pathlib.Path) -> None:
    """`type`欠落は必須キー不足として400を返す。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/entries",
        json={"messages": ["x"], "target_repo": "example/repo"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_add_choice_requires_two_options(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Webの通常投入は共通件数検証で拒否し、成功時は保存値を全件読み戻せる。"""
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    client = _serve_app(tmp_path).test_client()
    payload = {
        "type": "uwi",
        "target_repo": "github.com/example/repo",
        "messages": ["どの案ですか？"],
        "question_type": "choice",
        "choices": ["A"],
    }
    rejected = await client.post("/api/entries", json=payload)
    assert rejected.status_code == 400
    assert "2件以上" in (await rejected.get_json())["error"]
    assert not list(tmp_path.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    accepted = await client.post("/api/entries", json={**payload, "choices": ["A", "B,C"]})
    assert accepted.status_code == 201
    filename = (await accepted.get_json())["filenames"][0]
    detail = await (await client.get(f"/api/entries/inbox/{filename}")).get_json()
    assert detail["entry"]["choices"] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_batch_api_input_error_returns_reason_and_next_action(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一括登録の形式エラーは、`error`へ理由だけを保ち、入力を直す操作を`next_action`で返す。"""
    _patch_single_add_operations(monkeypatch)
    invalid = "### broken.md [inbox]\n---\ntype: awi\n---\n\n対象リポジトリの無い本文\n"

    response = await _serve_app(tmp_path).test_client().post("/api/entries/batch", json={"text": invalid})

    assert response.status_code == 400
    body = await response.get_json()
    assert set(body) == {"error", "next_action"}
    assert "broken.md" in body["error"]
    assert "次の操作" not in body["error"]
    assert "target_repo" in body["next_action"]
    assert body["next_action"].endswith("再投入する")
    assert _saved_files(tmp_path) == {}


@pytest.mark.asyncio
async def test_entries_api_period_limits_by_filename_creation_time(tmp_path: pathlib.Path) -> None:
    """一覧APIの`period`はファイル名の作成日時で限定し、省略時と`all`は全期間、不正値は400を返す。

    更新時刻で限定すると、Gitの取得や同期で古いWIの更新時刻が新しくなり期間の内側へ戻る。
    """
    inbox = _fixed_path(tmp_path, "entry.md").parent
    inbox.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now()
    names = {days: f"{now - datetime.timedelta(days=days):%Y%m%d-%H%M%S}-001.md" for days in (1, 20, 40, 70)}
    for name in [*names.values(), "unnamed.md"]:
        _fixed_path(tmp_path, name).write_text(
            "---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n", encoding="utf-8"
        )
    # 更新時刻はすべて現在とし、更新時刻が期間の判定を変えないことを確かめる。
    client = _serve_app(tmp_path).test_client()

    async def filenames(query: str) -> list[str]:
        response = await client.get(f"/api/entries?status=inbox{query}")
        assert response.status_code == 200
        return [item["filename"] for item in (await response.get_json())["entries"]]

    every = sorted([*names.values(), "unnamed.md"], reverse=True)
    assert await filenames("") == every
    assert await filenames("&period=all") == every
    assert await filenames("&period=2w") == sorted([names[1], "unnamed.md"], reverse=True)
    assert await filenames("&period=4w") == sorted([names[1], names[20], "unnamed.md"], reverse=True)
    assert await filenames("&period=8w") == sorted([names[1], names[20], names[40], "unnamed.md"], reverse=True)
    invalid = await client.get("/api/entries?period=1w")
    assert invalid.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/entries", {"type": "awi", "messages": ["awi"]}),
        (
            "/api/entries",
            {"type": "uwi", "messages": ["UWIですか？"], "scope": "test", "question_type": "yes-no"},
        ),
    ],
)
async def test_web_add_mutations_require_target_repo(
    tmp_path: pathlib.Path,
    path: str,
    payload: dict[str, object],
) -> None:
    """新規追加系APIは常駐プロセスのカレントディレクトリを参照しないためtarget_repoを必須とする。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("question_type", [None, "free-form"])
async def test_create_uwi_rejects_missing_or_free_form_question_type(
    tmp_path: pathlib.Path,
    question_type: str | None,
) -> None:
    """新規追加APIは回答形式の省略とfree-formを入力エラーとし、UWIを保存しない。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    payload: dict[str, object] = {
        "type": "uwi",
        "messages": ["どちらの案を採用しますか？"],
        "target_repo": "github.com/example/repo",
    }
    if question_type is not None:
        payload["question_type"] = question_type

    response = await app.test_client().post("/api/entries", json=payload)

    assert response.status_code == 400
    assert "choice" in (await response.get_json())["error"]
    assert not list(tmp_path.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload", "expected_target"),
    [
        (
            "/api/entries",
            {
                "type": "awi",
                "messages": ["---\nsource: add-awi\n---\n\nawi"],
                "target_repo": "https://github.com/Example/Specified.git",
            },
            "github.com/example/specified",
        ),
        (
            "/api/entries",
            {
                "type": "uwi",
                "messages": ["---\nsource: add-awi\n---\n\nUWIですか？"],
                "scope": "test",
                "question_type": "yes-no",
                "target_repo": "https://github.com/Example/Specified.git",
            },
            "github.com/example/specified",
        ),
    ],
)
async def test_add_api_resolves_target_repo_into_frontmatter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, object],
    expected_target: str,
) -> None:
    """追加APIがCLIと同じ解決契約のtarget_repoをfrontmatterへ保存する。"""
    monkeypatch.setenv("AI_AGENT", "1")

    def resolve(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
        del cwd
        if value is None:
            return "github.com/example/current"
        if value == "https://github.com/Example/Specified.git":
            return "github.com/example/specified"
        raise AssertionError(value)

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(awi_repo, "resolve_repo_id", resolve)
    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 201
    body = await response.get_json()
    content = entry_path(tmp_path, body["filenames"][0]).read_text(encoding="utf-8")
    assert f"target_repo: {expected_target}" in content
    assert "target_repo: \n" not in content
    assert "source:" not in content


@pytest.mark.asyncio
async def test_batch_api_imports_entries(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一括登録APIが保存名・対応・警告を返し、原文保持で保存する。"""
    _patch_batch_repo_operations(monkeypatch)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().post("/api/entries/batch", json={"text": _BATCH_TEXT})

    assert response.status_code == 201
    assert await response.get_json() == {
        "filenames": ["keep.md"],
        "mapping": {"keep.md": "keep.md"},
        "skipped": [],
        "warnings": [],
    }
    assert entry_path(tmp_path, "keep.md").read_text(encoding="utf-8") == (
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n取り込む本文\n"
    )


def test_normal_creation_omits_empty_target_repo_from_payload() -> None:
    """対象リポジトリ欄が空の通常追加では、target_repoを送らずサーバー検証へ委ねる。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-content'].value = '---\\ntarget_repo: example/repo\\n---\\n\\n本文';
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries')) {
    return {ok: true, status: 201, statusText: 'Created', json: async () => ({filenames: ['new.md']})};
  }
  if (url.endsWith('/api/repos?status=all')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries'));
process.stdout.write(JSON.stringify({body: JSON.parse(call.options.body)}));
"""
    )
    assert result == {
        "body": {
            "type": "awi",
            "messages": ["---\ntarget_repo: example/repo\n---\n\n本文"],
            "raw_text": "---\ntarget_repo: example/repo\n---\n\n本文",
        }
    }


def test_normal_creation_reports_auto_batch_import_and_ignored_fields() -> None:
    """種別uwiのまま送った本文をサーバーが一括登録として取り込んだ場合、その旨と使わなかった入力欄を通知する。"""
    result = _run_node_ui(
        """
elements['create-dialog'].open = true;
dialogStack.push('create-dialog');
elements['create-kind'].value = 'uwi';
updateCreateFields();
elements['create-content'].value = '  show形式テキスト  ';
elements['create-target'].value = 'example/repo';
fetchHandler = async (url) => {
  if (url.endsWith('/api/entries')) {
    return {
      ok: true, status: 201, statusText: 'Created',
      json: async () => ({
        batch: true, filenames: ['new.md'], mapping: {'old.md': 'new.md'}, skipped: [],
        warnings: ['依存先が不在'], ignored_fields: ['target_repo', 'question_type']
      })
    };
  }
  if (url.endsWith('/api/repos?status=all')) {
    return {ok: true, status: 200, statusText: 'OK', json: async () => ({repos: []})};
  }
  return {ok: true, status: 200, statusText: 'OK', json: async () => ({entries: [], warnings: []})};
};
await createEntry({preventDefault() {}});
const call = fetchCalls.find(item => item.url.endsWith('/api/entries'));
process.stdout.write(JSON.stringify({body: JSON.parse(call.options.body), toast: elements['toast'].textContent}));
"""
    )
    assert result["body"]["raw_text"] == "  show形式テキスト  "
    assert result["body"]["messages"] == ["show形式テキスト"]
    assert result["toast"] == (
        "［WI / node-test / /notes / 項目登録］show形式の本文のため一括登録として取り込みました。"
        "1件を取り込みました。改名: old.md -> new.md"
        " 使わなかった入力欄: target-repo、回答形式 警告: 依存先が不在"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
@pytest.mark.parametrize("text", [_BATCH_TEXT, _SINGLE_SHOW_TEXT], ids=["show-all", "show-single"])
async def test_single_entry_api_switches_show_format_to_batch_import(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_type: str,
    text: str,
) -> None:
    """種別awi・uwiのままshow形式の本文を送ると、単件保存せず一括登録と同じ結果で取り込む。

    ブラウザーは単件の本文をtrimして送るため、未trimの`raw_text`で取り込まないと最終行の末尾空白が失われる。
    """
    _patch_single_add_operations(monkeypatch)
    batch_root = tmp_path / "batch"
    single_root = tmp_path / "single"
    batch_response = await _serve_app(batch_root).test_client().post("/api/entries/batch", json={"text": text})
    payload: dict[str, object] = {"type": entry_type, "messages": [text.strip()], "raw_text": text}
    if entry_type == "uwi":
        payload["question_type"] = "yes-no"

    response = await _serve_app(single_root).test_client().post("/api/entries", json=payload)

    assert batch_response.status_code == 201
    assert response.status_code == 201
    assert await response.get_json() == {**(await batch_response.get_json()), "batch": True, "ignored_fields": []}
    assert _saved_files(single_root) == _saved_files(batch_root)
    assert list(_saved_files(single_root)) == ["keep.md"]


@pytest.mark.asyncio
async def test_single_entry_api_reports_ignored_single_fields(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自動切替した登録は、入力されていた単件用の項目を使わなかったことを応答で示す。"""
    _patch_single_add_operations(monkeypatch)

    response = (
        await _serve_app(tmp_path)
        .test_client()
        .post(
            "/api/entries",
            json={
                "type": "uwi",
                "messages": [_BATCH_TEXT.strip()],
                "raw_text": _BATCH_TEXT,
                "target_repo": "github.com/example/foo",
                "scope": "範囲",
                "question_type": "choice",
                "choices": ["はい", "いいえ"],
            },
        )
    )

    assert response.status_code == 201
    body = await response.get_json()
    assert body["batch"] is True
    assert body["ignored_fields"] == ["target_repo", "scope", "question_type", "choices"]
    assert list(_saved_files(tmp_path)) == ["keep.md"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("---\ntarget_repo: github.com/Example/Repo\n---\n\n通常の本文\n", id="frontmatter"),
        pytest.param(
            "---\ntarget_repo: github.com/Example/Repo\n---\n\n# 書式の例\n\n"
            "### 20260101-000000-001.md\n---\ntype: awi\n---\n\n引用した例\n",
            id="quoted-example",
        ),
        pytest.param("# 見出し\n\n本文だけの依頼\n", id="plain"),
    ],
)
async def test_single_entry_api_keeps_non_show_text_single(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> None:
    """show形式の構造に該当しない本文は、`raw_text`があっても単件で保存する。"""
    _patch_single_add_operations(monkeypatch)
    payload: dict[str, object] = {"type": "awi", "messages": [text.strip()], "raw_text": text}
    if not text.startswith("---"):
        payload["target_repo"] = "github.com/Example/Repo"

    response = await _serve_app(tmp_path).test_client().post("/api/entries", json=payload)

    assert response.status_code == 201
    body = await response.get_json()
    assert set(body) == {"filenames"}
    saved = _saved_files(tmp_path)
    assert list(saved) == body["filenames"]
    assert text.strip().splitlines()[-1] in saved[body["filenames"][0]]


@pytest.mark.asyncio
async def test_single_entry_api_rejects_invalid_show_format_without_saving(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """show形式の構造に該当しエントリ検証に失敗する本文は、一括登録と同じエラーで拒否し何も保存しない。"""
    _patch_single_add_operations(monkeypatch)
    invalid = "### broken.md [inbox]\n---\ntype: awi\n---\n\n対象リポジトリの無い本文\n"
    batch_response = await _serve_app(tmp_path / "batch").test_client().post("/api/entries/batch", json={"text": invalid})

    response = (
        await _serve_app(tmp_path / "single")
        .test_client()
        .post(
            "/api/entries",
            json={"type": "awi", "messages": [invalid.strip()], "raw_text": invalid, "target_repo": "github.com/Example/Repo"},
        )
    )

    assert batch_response.status_code == 400
    assert response.status_code == 400
    assert await response.get_json() == await batch_response.get_json()
    assert _saved_files(tmp_path / "single") == {}
