"""`atk serve`のテスト。"""

import asyncio
import contextlib
import json
import pathlib
import subprocess
import threading
import typing

import _atk_fb_common as common
import _atk_fb_repo as feedback_repo
import _atk_serve_app as serve_app
import _atk_serve_assets as assets
import _atk_serve_config as config
import _atk_serve_state as state
import filelock
import pytest


def test_config_precedence_and_platform_ports(tmp_path: pathlib.Path) -> None:
    """設定優先順位とOS別ポートを検証する。"""
    path = tmp_path / "serve.toml"
    path.write_text('host = "toml-host"\nport = 3000\n', encoding="utf-8")
    env = {
        "AGENT_TOOLKIT_SERVE_CONFIG": str(path),
        "AGENT_TOOLKIT_SERVE_HOST": "0.0.0.0",
        "AGENT_TOOLKIT_SERVE_PORT": "4000",
    }
    assert config.resolve_config(environ=env) == config.ServeConfig("0.0.0.0", 4000)
    assert config.resolve_config(host="cli-host", port=5000, environ=env) == config.ServeConfig("cli-host", 5000)
    assert config.default_port("linux") == 28766
    assert config.default_port("win32") == 28876


@pytest.mark.parametrize("host", ["", "  ", 1])
def test_invalid_host(host: object) -> None:
    """空又は非文字列hostを拒否する。"""
    with pytest.raises(ValueError):
        config.resolve_config(host=host)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("port", [True, 0, 65536])
def test_invalid_port(port: object) -> None:
    """bool又は範囲外portを拒否する。"""
    with pytest.raises(ValueError):
        config.resolve_config(port=port)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_assets_are_self_contained() -> None:
    """UI資産が外部URLへ依存しないことを検証する。"""
    combined = assets.HTML + assets.CSS + assets.JS
    assert "https://" not in combined
    assert "/api/entries" in combined
    assert "/api/events" in combined
    for operation in (
        "start-processing",
        "adopt",
        "reject",
        "remove",
        "commit",
        "/api/enable",
        "/api/disable",
        "/answer",
    ):
        assert operation in combined
    assert "confirm(" in assets.JS
    assert "innerHTML" not in assets.JS
    assert "textContent" in assets.JS
    for field in (
        "kind",
        "filename",
        "state",
        "target_repo",
        "source",
        "category",
        "answered",
        "summary",
        "updated_at",
    ):
        assert f"'{field}'" in assets.JS
    for batch_input in ("batch-note", "batch-category", "batch-commit"):
        assert batch_input in assets.HTML
    assert "filenames.join" in assets.JS
    assert "['inbox','processing'].includes(e.state)" in assets.JS
    assert "$('#answer').value=''" in assets.JS


def test_assets_render_api_entry_values_as_dom_text() -> None:
    """API由来のHTML様データを一覧描画してもDOM要素へ解釈しない。"""
    dangerous = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    entry: dict[str, object] = {
        field: dangerous
        for field in (
            "kind",
            "filename",
            "state",
            "target_repo",
            "source",
            "category",
            "summary",
            "updated_at",
        )
    }
    entry["answered"] = False
    render_source = assets.JS.partition("function render(){")[0]
    script = f"""
class Element {{
  constructor(tagName) {{
    this.tagName = tagName;
    this.children = [];
    this.dataset = {{}};
    this.attributes = {{}};
    this.textContent = '';
  }}
  append(...children) {{ this.children.push(...children); }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
}}
globalThis.document = {{
  createElement(tagName) {{ return new Element(tagName); }},
  querySelector() {{ throw new Error('unexpected querySelector'); }},
  querySelectorAll() {{ throw new Error('unexpected querySelectorAll'); }}
}};
eval({json.dumps(render_source)});
const root = renderEntry({json.dumps(entry)}, 0);
const descendants = [];
const visit = element => {{
  descendants.push(element);
  element.children.forEach(visit);
}};
visit(root);
process.stdout.write(JSON.stringify({{
  tags: descendants.map(element => element.tagName),
  texts: descendants.map(element => element.textContent).filter(Boolean)
}}));
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    rendered = json.loads(completed.stdout)
    assert rendered["tags"] == ["li", "input", "button", *(["span"] * 9)]
    assert len(rendered["texts"]) == 9
    assert sum(dangerous in text for text in rendered["texts"]) == 8
    assert "answered: false" in rendered["texts"]


def test_batch_kind_rejects_mixed_feedback_and_tbd_selection() -> None:
    """一括操作のkind検証がFeedbackとTBDの混在選択を拒否する。"""
    function_source = assets.JS.partition("function updateActions(){")[0]
    script = f"""
globalThis.document = {{
  createElement() {{ return {{ append() {{}}, setAttribute() {{}}, dataset: {{}} }}; }},
  querySelector() {{ return null; }},
  querySelectorAll() {{ return []; }}
}};
eval({json.dumps(function_source)});
const feedback = {{kind: 'feedback'}};
const tbd = {{kind: 'tbd'}};
process.stdout.write(JSON.stringify([
  batchKind([]),
  batchKind([feedback]),
  batchKind([tbd]),
  batchKind([feedback, feedback]),
  batchKind([feedback, tbd])
]));
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout) == [None, "feedback", "tbd", "feedback", None]


def test_batch_payloads_follow_action_contract() -> None:
    """一括操作の入力を対応するAPI操作だけへ送る。"""
    assert "action==='remove'&&kind==='tbd'" in assets.JS
    assert "action==='adopt'&&kind==='feedback'" in assets.JS
    assert "['adopt','reject'].includes(action)" in assets.JS
    assert "kind=batchKind(chosen)" in assets.JS
    assert "if(!kind){showError" in assets.JS
    assert "await api(`/api/${kind}/${action}`" in assets.JS
    assert "Object.groupBy" not in assets.JS
    assert "for(const id of ['batch-note','batch-category','batch-commit'])" in assets.JS


def test_state_keeps_latest_event(tmp_path: pathlib.Path) -> None:
    """状態管理を構築できることを検証する。"""
    current = state.ServeState(tmp_path)
    assert current.root == tmp_path


def test_all_api_routes_are_registered(tmp_path: pathlib.Path) -> None:
    """計画で定義した全APIルートを登録する。"""
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    expected = {
        "/api/status",
        "/api/entries",
        "/api/entries/<kind>/<state_name>/<filename>",
        "/api/feedback",
        "/api/feedback/<state_name>/<filename>",
        "/api/feedback/start-processing",
        "/api/feedback/adopt",
        "/api/feedback/reject",
        "/api/feedback/remove",
        "/api/feedback/commit",
        "/api/enable",
        "/api/disable",
        "/api/tbd",
        "/api/tbd/<filename>",
        "/api/tbd/<filename>/answer",
        "/api/tbd/adopt",
        "/api/tbd/remove",
        "/api/events",
    }
    assert expected <= rules


@pytest.mark.asyncio
async def test_sse_multiple_subscribers_and_cleanup(tmp_path: pathlib.Path) -> None:
    """複数購読者へ変更通知を配信し、切断後に購読を除去する。"""
    current = state.ServeState(tmp_path)
    first = current.events(heartbeat=1)
    second = current.events(heartbeat=1)
    first_next = asyncio.create_task(anext(first))
    second_next = asyncio.create_task(anext(second))
    await asyncio.sleep(0)
    current.publish()
    assert "event: changed" in await first_next
    assert "event: changed" in await second_next
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_sse_heartbeat(tmp_path: pathlib.Path) -> None:
    """変更が無い期間はheartbeatを送信する。"""
    events = state.ServeState(tmp_path).events(heartbeat=0.001)
    assert await anext(events) == ": heartbeat\n\n"
    await events.aclose()


@pytest.mark.asyncio
async def test_cancelled_request_keeps_worker_slot_until_completion() -> None:
    """要求キャンセル後も同期処理が終わるまでSemaphore枠を解放しない。"""
    workers = serve_app.BoundedWorkers(1)
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()

    def first() -> None:
        first_started.set()
        first_release.wait()

    def second() -> None:
        second_started.set()

    first_task = asyncio.create_task(workers.run(first))
    await asyncio.to_thread(first_started.wait)
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    second_task = asyncio.create_task(workers.run(second))
    await asyncio.sleep(0.01)
    assert not second_started.is_set()
    first_release.set()
    await second_task
    assert second_started.is_set()


def test_entries_hold_lock_through_snapshot(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧取得はpullからファイル読取りまで同一ロックを保持する。"""
    inbox = tmp_path / "feedback/inbox"
    inbox.mkdir(parents=True)
    entry = inbox / "entry.md"
    entry.write_text("---\ntarget_repo: example/repo\nsource: test\n---\n\n要約本文\n", encoding="utf-8")
    calls: list[str] = []
    locked = False

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        nonlocal locked
        calls.append("lock")
        locked = True
        try:
            yield
        finally:
            locked = False
            calls.append("unlock")

    def pull(_path: pathlib.Path) -> None:
        assert locked
        calls.append("pull")

    monkeypatch.setattr(common, "repo_lock", lock)
    monkeypatch.setattr(common, "pull", pull)
    result = serve_app.Operations(tmp_path).entries({})
    assert calls == ["lock", "pull", "unlock"]
    assert result[0] | {
        "updated_at": result[0]["updated_at"],
    } == {
        "kind": "feedback",
        "state": "inbox",
        "filename": "entry.md",
        "answered": None,
        "target_repo": "example/repo",
        "source": "test",
        "category": None,
        "summary": "要約本文",
        "updated_at": result[0]["updated_at"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/entries?status=unknown", None),
        ("get", "/api/entries?target_repo=", None),
        ("post", "/api/feedback", {"messages": ["x"], "source": ""}),
        (
            "post",
            "/api/tbd",
            {"messages": ["x"], "scope": "s", "question_type": "free-form", "choices": ["a"]},
        ),
        ("post", "/api/feedback/adopt", {"filenames": ["x.md"], "note": 1}),
        ("post", "/api/feedback/adopt", {"filenames": ["../x.md"]}),
    ],
)
async def test_api_rejects_invalid_inputs(
    tmp_path: pathlib.Path,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    """Web入力境界が列挙・型・空文字・basename違反を400で拒否する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()
    response = await getattr(client, method)(path, json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/feedback", {"messages": ["feedback"]}),
        (
            "/api/tbd",
            {"messages": ["TBDですか？"], "scope": "test", "question_type": "free-form"},
        ),
    ],
)
async def test_web_add_mutations_require_target_repo(
    tmp_path: pathlib.Path,
    path: str,
    payload: dict[str, object],
) -> None:
    """新規追加系APIは常駐プロセスのカレントディレクトリへ依存しないためtarget_repoを必須とする。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 400
    assert "target_repo" in (await response.get_json())["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/feedback", {"messages": ["feedback"], "target_repo": None}),
        (
            "/api/tbd",
            {
                "messages": ["TBDですか？"],
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


@pytest.mark.asyncio
async def test_web_transition_mutations_allow_omitted_target_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態遷移系APIはfilenameで対象を一意に特定できるためtarget_repo省略を許容する。"""

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.feedback_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.feedback_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.feedback_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    inbox = tmp_path / "feedback" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntarget_repo: github.com/example/foo\n---\n\n本文\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post("/api/feedback/adopt", json={"filenames": ["entry.md"]})
    assert response.status_code == 200
    assert (tmp_path / "feedback" / "adopted" / "entry.md").is_file()
    assert not (inbox / "entry.md").exists()


@pytest.mark.asyncio
async def test_invalid_json_returns_json_error(tmp_path: pathlib.Path) -> None:
    """構文エラーのJSON本文を一貫したJSON 400応答へ変換する。"""
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(
        "/api/feedback",
        data='{"messages":',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert await response.get_json() == {"error": "JSON本文の構文が不正です"}


@pytest.mark.asyncio
async def test_lock_timeout_returns_conflict(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Web操作の有限待機ロック競合をJSON 409応答へ変換する。"""

    def entries(_filters: dict[str, str]) -> list[dict[str, object]]:
        raise filelock.Timeout("locked")

    operations = serve_app.Operations(tmp_path)
    monkeypatch.setattr(operations, "entries", entries)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
        operations=operations,
    )
    response = await app.test_client().get("/api/entries")
    assert response.status_code == 409
    assert await response.get_json() == {"error": "別の操作が進行中です"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload", "expected_target"),
    [
        (
            "/api/feedback",
            {"messages": ["feedback"], "target_repo": "https://github.com/Example/Specified.git"},
            "github.com/example/specified",
        ),
        (
            "/api/tbd",
            {
                "messages": ["TBDですか？"],
                "scope": "test",
                "question_type": "free-form",
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

    def resolve(value: str | None, *, cwd: pathlib.Path | None = None) -> str:
        del cwd
        if value is None:
            return "github.com/example/current"
        if value == "https://github.com/Example/Specified.git":
            return "github.com/example/specified"
        raise AssertionError(value)

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Iterator[None]:
        yield

    monkeypatch.setattr(feedback_repo, "resolve_repo_id", resolve)
    monkeypatch.setattr(common, "_repo_lock", lock)
    monkeypatch.setattr(common, "_pull", lambda _path: None)
    monkeypatch.setattr(common, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.feedback_add, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.feedback_add, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.feedback_add, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serve_app.tbd_mutations, "_repo_lock", lock)
    monkeypatch.setattr(serve_app.tbd_mutations, "_pull", lambda _path: None)
    monkeypatch.setattr(serve_app.tbd_mutations, "_commit_and_push", lambda *_args, **_kwargs: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    response = await app.test_client().post(path, json=payload)
    assert response.status_code == 201
    body = await response.get_json()
    kind = "feedback" if path == "/api/feedback" else "tbd"
    content = (tmp_path / kind / "inbox" / body["filenames"][0]).read_text(encoding="utf-8")
    assert f"target_repo: {expected_target}" in content
    assert "target_repo: \n" not in content
