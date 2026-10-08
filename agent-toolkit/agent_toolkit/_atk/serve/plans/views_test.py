"""`atk serve`の計画ファイル画面の処理のテスト。"""

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
from agent_toolkit._plan import viewer_files as plan_viewer_files
from agent_toolkit._testing.serve_plans_support import _context, _plan, _read_payload, _runner_returning


@pytest.mark.asyncio
async def test_remote_missing_attachments_do_not_warn_or_fallback_but_explicit_read_does(
    tmp_path: pathlib.Path, index_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """HTTP表示の関連リンク探索は正常な不在を無警告で省き、同じ不在の明示取得だけ404と警告にする。"""
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

    # 付属側から消えたメインを探す場合も同じ分類を使う。
    existing.pop("p.md")
    caplog.clear()
    response = await client.get("/api/plans/file", query_string={"host": "remote-host", "path": "p.bugs.md"})
    assert response.status_code == 200
    assert "data-plan-path" not in await response.get_data(as_text=True)
    assert not caplog.records
    assert not calls


def test_absent_root_keeps_the_recorded_creation_times(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """rootへ一時的に到達できない間は、記録済みの作成日時を回収しない。"""
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


def test_mermaid_fence_is_rendered_as_escaped_diagram() -> None:
    """Mermaidのフェンスは原文をエスケープした描画枠と原文表示へ変換する。"""
    html = plans_rendering.markdown_to_html("```mermaid\ngraph TD;\n  A[<b>x</b>] --> B;\n```\n")

    assert 'class="diagram diagram-mermaid"' in html
    assert 'class="diagram-output mermaid-output"' in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>x</b>" not in html


def test_pygments_css_keeps_token_rules_without_base_rule() -> None:
    """Pygmentsのスタイルは基底ルールを除き、トークン別ルールだけを返す。"""
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
    """root外を指す相対パスは解決しない。"""
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
    """危険な相対パスはリモートへ送らず、入力不正として拒否する。"""
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
    """同じ更新時刻の書き換えでも新しい本文を描画し、同じ本文では描画結果を再利用する。"""
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
