# pylint: disable=protected-access

import asyncio
import base64
import json
import pathlib
import types
import typing

import pytest
import quart

from agent_toolkit._atk.serve import plans, shell_routes
from agent_toolkit._atk.serve.plans import local_scan as plans_local_scan
from agent_toolkit._atk.serve.plans import rendering as plans_rendering
from agent_toolkit._atk.serve.plans import roots as plans_roots
from agent_toolkit._atk.serve.plans import views
from agent_toolkit._atk.serve.plans.remote import RemoteWatcher
from agent_toolkit._plan import creation_times as plan_creation_times
from agent_toolkit._plan import viewer_files as plan_viewer_files
from agent_toolkit._testing.serve_plans_support import _context, _plan, _read_payload, _runner_returning


@pytest.mark.asyncio
async def test_remote_missing_attachments_do_not_warn_or_fallback_but_explicit_read_does(
    tmp_path: pathlib.Path, index_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    del index_path
    runner, calls = _runner_returning(_read_payload("代替取得"))
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
    existing = {"p.md": "# 本文", "p.bugs.md": "# バグ"}

    async def request(_op: str, args: dict[str, typing.Any]) -> dict[str, typing.Any]:
        path = base64.b64decode(args["path"]).decode("utf-8")
        if path in existing:
            return {"ok": True, **_read_payload(existing[path])}
        return {"ok": False, "error_type": "not_found", "error": f"FileNotFoundError: {path}"}

    context.state.remote_watchers["remote-host"] = typing.cast(
        RemoteWatcher, types.SimpleNamespace(is_connected=lambda: True, request=request)
    )
    app = quart.Quart(__name__)
    shell_routes.register_plan_routes(app, context)
    client = app.test_client()
    caplog.set_level("WARNING")
    for _ in range(2):
        response = await client.get("/api/plans/file", query_string={"host": "remote-host", "path": "p.md"})
        assert response.status_code == 200
        html = await response.get_data(as_text=True)
        assert 'data-plan-path="p.bugs.md"' in html
        assert html.count("data-plan-path") == 1
        assert "本文" in html
    assert not caplog.records
    assert not calls

    response = await client.get("/api/plans/raw", query_string={"host": "remote-host", "path": "p.exec-review.tsv"})
    assert response.status_code == 404
    assert len(caplog.records) == 1
    assert "リモートファイル取得失敗" in caplog.records[0].message
    assert not calls

    existing.pop("p.md")
    caplog.clear()
    response = await client.get("/api/plans/file", query_string={"host": "remote-host", "path": "p.bugs.md"})
    assert response.status_code == 200
    assert "data-plan-path" not in await response.get_data(as_text=True)
    assert not caplog.records
    assert not calls


def test_absent_root_keeps_the_recorded_creation_times(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "plan.md", mtime=1_000.0)
    plans_local_scan.list_files(root, "local-host")
    stored = json.loads(index_path.read_text(encoding="utf-8"))
    (root / "plan.md").unlink()
    root.rmdir()

    entries, warning = plans_local_scan.scan_files(root, "local-host")

    assert not entries
    assert warning is None
    assert json.loads(index_path.read_text(encoding="utf-8")) == stored


def test_unchanged_rescan_does_not_rewrite_creation_time_index(
    tmp_path: pathlib.Path, index_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "plan.md", mtime=1_000.0)
    plans_local_scan.list_files(root, "local-host")
    stored = index_path.read_text(encoding="utf-8")
    writes: list[object] = []
    original = plan_creation_times._write_index

    def recording(index: typing.Any) -> typing.Any:
        writes.append(index)
        return original(index)

    monkeypatch.setattr(plan_creation_times, "_write_index", recording)

    plans_local_scan.list_files(root, "local-host")
    assert not writes
    assert index_path.read_text(encoding="utf-8") == stored

    _plan(root, "added.md", mtime=2_000.0)
    plans_local_scan.list_files(root, "local-host")
    assert len(writes) == 1


def test_scan_follows_only_file_links_that_stay_inside_root(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    del index_path
    root = tmp_path / "plans"
    (root / "sub").mkdir(parents=True)
    (root / ".hidden").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _plan(root / "sub", "plan.md", mtime=1_000.0)
    _plan(root / ".hidden", "secret.md", mtime=1_000.0)
    _plan(outside, "foreign.md", mtime=1_000.0)
    (root / "inner-link.md").symlink_to(root / "sub" / "plan.md")
    (root / "outer-link.md").symlink_to(outside / "foreign.md")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)

    entries, warning = plans_local_scan.scan_files(root, "local-host")

    assert warning is None
    assert sorted(entry.path for entry in entries) == ["inner-link.md", "sub/plan.md"]


def test_mermaid_fence_is_rendered_as_escaped_diagram() -> None:
    html = plans_rendering.markdown_to_html("```mermaid\ngraph TD;\n  A[<b>x</b>] --> B;\n```\n")

    assert 'class="diagram diagram-mermaid"' in html
    assert 'class="diagram-output mermaid-output"' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_pygments_css_keeps_token_rules_without_base_rule() -> None:
    css = plans.read_pygments_css()

    assert ".codehilite .k" in css
    assert not any(line.strip().startswith(".codehilite {") for line in css.splitlines())


@pytest.mark.asyncio
async def test_plan_links_checks_independent_attachments_concurrently(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """付属計画候補は一つずつ待たず、同時に存在確認を開始する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)
    started: list[str] = []
    all_started = asyncio.Event()

    async def wait_for_other_candidates(
        _context_value: plans.PlansContext,
        _host: str,
        _source_id: str,
        plan_rel: str,
    ) -> bool:
        started.append(plan_rel)
        if len(started) == 4:
            all_started.set()
        await all_started.wait()
        return True

    monkeypatch.setattr(views, "_plan_exists", wait_for_other_candidates)

    html = await asyncio.wait_for(views.plan_links_html(context, "local-host", plans.NEW_SOURCE_ID, "a.md"), 1)

    assert len(started) == 4
    assert html.count("data-plan-path") == 4


@pytest.mark.parametrize("rel", ["../outside.md", "a/../../outside.md", "/etc/passwd.md"])
def test_resolve_under_root_rejects_traversal(tmp_path: pathlib.Path, rel: str) -> None:
    root = tmp_path / "plans"
    root.mkdir()
    _plan(tmp_path, "outside.md")

    assert plan_viewer_files.resolve_under_root(root, rel) is None


def test_synchronized_root_keeps_only_the_oldest_host(tmp_path: pathlib.Path) -> None:
    """ホスト間で同期されるrootの同名ファイルは、作成日時が最も古いホストの1件へ絞る。"""
    del tmp_path

    def entry(host: str, source_id: str, path: str, ctime: float) -> plans_roots.FileEntry:
        return plans_roots.FileEntry(
            host=host,
            path=path,
            name=path,
            mtime="",
            ctime="",
            mtime_epoch=ctime,
            ctime_epoch=ctime,
            source_id=source_id,
        )

    merged = views._oldest_host_per_file(
        [
            entry("a", plans.NEW_SOURCE_ID, "p.md", 200.0),
            entry("b", plans.NEW_SOURCE_ID, "p.md", 100.0),
            entry("a", plans.LEGACY_SOURCE_ID, "q.md", 200.0),
            entry("b", plans.LEGACY_SOURCE_ID, "q.md", 100.0),
        ]
    )

    assert sorted((item.host, item.source_id) for item in merged) == [
        ("a", plans.LEGACY_SOURCE_ID),
        ("b", plans.LEGACY_SOURCE_ID),
        ("b", plans.NEW_SOURCE_ID),
    ]


@pytest.mark.asyncio
async def test_remote_path_outside_the_root_is_rejected_before_ssh(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    runner, calls = _runner_returning(_read_payload("body"))
    context = _context(root, remote_hosts=["remote-host"], ssh_runner=runner)

    with pytest.raises(plans.PlanFileError) as error:
        await plans.resolve_text(context, "remote-host", "", "../secret.md")

    assert error.value.status == 400
    assert not calls


@pytest.mark.asyncio
async def test_render_file_html_keys_cache_by_text_digest(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)
    rendered_texts: list[str] = []
    original = plans_rendering.markdown_to_html

    def counting_markdown_to_html(text: str, renderer: typing.Any = None) -> str:
        rendered_texts.append(text)
        return original(text, renderer)

    monkeypatch.setattr(views, "markdown_to_html", counting_markdown_to_html)
    source_id = context.roots[0].source_id
    _plan(root, "plan.md", "# 変更前\n", mtime=3_000.0)
    before = await plans.render_file_html(context, "local-host", source_id, "plan.md")

    _plan(root, "plan.md", "# 変更後\n", mtime=3_000.0)
    after = await plans.render_file_html(context, "local-host", source_id, "plan.md")
    again = await plans.render_file_html(context, "local-host", source_id, "plan.md")

    assert "変更前" in before
    assert "変更後" in after
    assert "変更前" not in after
    assert again == after
    assert rendered_texts == ["# 変更前\n", "# 変更後\n"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["schedule", "start", "none"])
async def test_local_watch_acquisition_and_release_are_owned(
    tmp_path: pathlib.Path, index_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)

    observer: typing.Any = types.SimpleNamespace(ident=None, stopped=0, joined=0)

    def schedule(*_args: typing.Any, **_kwargs: typing.Any) -> None:
        if failure == "schedule":
            raise OSError("登録失敗")

    def start() -> None:
        if failure == "start":
            raise OSError("開始失敗")
        observer.ident = 1

    def stop() -> None:
        observer.stopped += 1

    def join() -> None:
        assert observer.ident is not None
        observer.joined += 1

    observer.schedule, observer.start, observer.stop, observer.join = schedule, start, stop, join
    monkeypatch.setattr(views.watchdog.observers, "Observer", lambda: observer)
    await views.start_local_watchers_async(context)

    assert (context.state.local_observer is observer) is (failure == "none")
    await views.stop_local_watchers_async(context)
    assert context.state.local_observer is None
    assert observer.stopped == 1
    assert observer.joined == (1 if failure == "none" else 0)


@pytest.mark.asyncio
async def test_remote_watch_cleanup_collects_all_failures(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """終了済みの監視失敗を全件受領し、参照を回収してからまとめて返す。"""
    del index_path
    context = _context(tmp_path)

    async def fail(error: Exception) -> None:
        raise error

    context.state.remote_tasks.extend(
        (asyncio.create_task(fail(ValueError("一件目"))), asyncio.create_task(fail(OSError("二件目"))))
    )
    await asyncio.gather(*context.state.remote_tasks, return_exceptions=True)
    with pytest.raises(BaseExceptionGroup) as failed:
        await views.stop_remote_watchers(context)
    assert [type(error) for error in failed.value.exceptions] == [ValueError, OSError]
    assert not context.state.remote_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["file", "search"])
async def test_remote_plan_timeout_is_not_a_confirmed_empty_result(tmp_path: pathlib.Path, route: str) -> None:
    fail = True

    async def runner(_host: str, operation: str, _args: list[str]) -> str:
        if fail:
            raise TimeoutError("SSH timeout")
        if operation == "read":
            return json.dumps({"error_type": "not_found"})
        return json.dumps({"paths": []})

    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
    app = quart.Quart(__name__)
    shell_routes.register_plan_routes(app, context)
    client = app.test_client()
    query = {"host": "remote-host", "path": "p.md"} if route == "file" else {"q": "keyword"}
    response = await client.get(f"/api/plans/{route}", query_string=query)
    if route == "file":
        assert response.status_code == 503
    else:
        assert response.status_code == 200
        assert await response.get_json() == {"entries": [], "failures": {"remote-host": 503}}
    fail = False
    response = await client.get(f"/api/plans/{route}", query_string=query)
    if route == "file":
        assert response.status_code == 404
    else:
        assert response.status_code == 200
        assert await response.get_json() == []


@pytest.mark.asyncio
async def test_remote_search_partial_results_and_targeted_recovery(tmp_path: pathlib.Path) -> None:
    (tmp_path / "local.md").write_text("needle", encoding="utf-8")
    calls: list[str] = []
    broken = True

    async def runner(host: str, _operation: str, _args: list[str]) -> str:
        calls.append(host)
        if host == "offline" and broken:
            raise TimeoutError
        return json.dumps({"paths": [f"{host}.md"]})

    hosts = ["healthy", "offline"]
    context = _context(tmp_path, remote_hosts=hosts, ssh_runner=runner)
    for host in hosts:
        entry = plans_roots.FileEntry(host, f"{host}.md", f"{host}.md", "", "", 0, 0)
        context.state.remote_files[host] = [entry]
    app = quart.Quart(__name__)
    shell_routes.register_plan_routes(app, context)
    client = app.test_client()
    answer = await (await client.get("/api/plans/search", query_string={"q": "needle"})).get_json()
    assert {entry["path"] for entry in answer["entries"]} == {"local.md", "healthy.md"}
    assert answer["failures"] == {"offline": 503}
    assert set(calls) == set(hosts)
    calls.clear()
    broken = False
    answer = await (await client.get("/api/plans/search", query_string={"q": "needle", "host": "offline"})).get_json()
    assert [entry["path"] for entry in answer["entries"]] == ["offline.md"]
    assert answer["failures"] == {}
    assert calls == ["offline"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rpc", [False, True])
@pytest.mark.parametrize(
    ("kind", "status"),
    [("permission_denied", 403), ("not_found", 404), ("timeout", 503)],
)
async def test_remote_read_error_type_reaches_api(tmp_path: pathlib.Path, rpc: bool, kind: str, status: int) -> None:
    calls = 0
    error = {"ok": False, "error_type": kind}

    async def runner(*_args: typing.Any) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(error)

    async def request(*_args: typing.Any) -> dict[str, typing.Any]:
        return error

    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
    if rpc:
        context.state.remote_watchers["remote-host"] = typing.cast(
            RemoteWatcher, types.SimpleNamespace(is_connected=lambda: True, request=request)
        )
    app = quart.Quart(__name__)
    shell_routes.register_plan_routes(app, context)
    client = app.test_client()
    response = await client.get("/api/plans/raw", query_string={"host": "remote-host", "path": "p.md"})
    assert response.status_code == status
    assert calls == (0 if rpc else 1)


@pytest.mark.parametrize("deleted", ["file", "directory"])
def test_search_keeps_real_matches_when_a_candidate_disappears(monkeypatch, tmp_path, deleted):
    root = tmp_path
    (root / "wanted.md").write_text("match", encoding="utf-8")
    (root / "vanished.md").write_text("match", encoding="utf-8")
    original = types.SimpleNamespace(read=pathlib.Path.read_text, walk=pathlib.Path.walk)

    def read(path, **kwargs):
        if deleted == "file" and path.name == "vanished.md":
            raise FileNotFoundError("after stat")
        return original.read(path, **kwargs)

    def walk_with_race(path, *, on_error=None, **kwargs):
        if deleted == "directory" and path == root and on_error is not None:
            on_error(FileNotFoundError("deleted child"))
        yield from original.walk(path, on_error=on_error, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read)
    monkeypatch.setattr(pathlib.Path, "walk", walk_with_race)
    result = plan_viewer_files.search_root(root, "match", strict_io=True)
    assert "wanted.md" in result
    if deleted == "file":
        assert "vanished.md" not in result
