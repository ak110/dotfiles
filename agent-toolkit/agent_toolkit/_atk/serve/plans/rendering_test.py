# pylint: disable=protected-access

import asyncio
import json
import pathlib

import pytest

from agent_toolkit._atk.serve import plans
from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve.plans import local_scan as plans_local_scan
from agent_toolkit._atk.serve.plans import remote as plans_remote
from agent_toolkit._atk.serve.plans import views as plans_views
from agent_toolkit._plan import creation_times as plan_creation_times
from agent_toolkit._plan import viewer_files as plan_viewer_files
from agent_toolkit._testing import plan_fixture
from agent_toolkit._testing.serve_plans_support import _context, _failed_ssh, _plan


def test_symlinked_root_is_deduplicated_by_identity(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "plans"
    root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)

    normalized = plan_viewer_files.normalize_root_specs(
        (
            plans.RootSpec(source_id=plans.NEW_SOURCE_ID, path=root, portable_path="a"),
            plans.RootSpec(source_id="other", path=link, portable_path="b"),
        )
    )

    assert [spec.source_id for spec in normalized] == [plans.NEW_SOURCE_ID]


def test_cleanup_removes_only_temporaries(index_path: pathlib.Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("{}", encoding="utf-8")
    digest = "0" * 64
    keep = index_path.parent / f"{digest}.json"
    keep.write_text("{}", encoding="utf-8")
    current_temporary = index_path.with_name(f"{index_path.name}.123.tmp")
    current_temporary.write_text("{}", encoding="utf-8")
    legacy_temporary = index_path.parent / f".{digest}.json.123.456.tmp"
    legacy_temporary.write_text("{}", encoding="utf-8")

    plan_creation_times.cleanup_creation_time_temporaries()

    assert index_path.exists()
    assert keep.exists()
    assert not current_temporary.exists()
    assert not legacy_temporary.exists()


@pytest.mark.asyncio
async def test_attached_links_are_omitted_when_other_pages_are_absent(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md")
    context = _context(root)

    assert await plans_views.plan_links_html(context, "local-host", "", "p.md") == ""


def test_non_directory_root_is_reported_as_a_warning(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    del index_path
    root = tmp_path / "plans"
    root.write_text("x", encoding="utf-8")

    entries, warning = plans_local_scan.scan_files(root, "local-host")

    assert not entries
    assert warning == "rootがディレクトリではありません"


@pytest.mark.asyncio
async def test_local_watcher_broadcasts_on_local_change(
    tmp_path: pathlib.Path,
    index_path: pathlib.Path,
) -> None:
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)

    context.state.debounce_sec = 0.01
    queue = await plans.subscribe(context.state)

    plans.start_local_watchers(context)
    try:
        _plan(root, "p.md")
        payload = await asyncio.wait_for(queue.get(), timeout=10.0)
    finally:
        plans.stop_local_watchers(context)

    assert json.loads(payload)["type"] == "refresh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"helper not found\n", "helper not found"),
        (b"  \n ", "標準エラー出力はありません"),
        (b"\xff\xfe helper failed", "helper failed"),
    ],
    ids=["message", "empty", "undecodable"],
)
async def test_remote_read_failure_reports_stderr(monkeypatch: pytest.MonkeyPatch, stderr: bytes, expected: str) -> None:
    """リモート実行が非0で終了した場合、終了コードと失敗元の標準エラー出力を例外本文へ引き継ぐ。"""
    monkeypatch.setattr(_atk_serve_remote, "run_ssh", _failed_ssh(3, stderr))

    with pytest.raises(plans_remote.RemoteHelperError) as error:
        await plans_remote.fetch_remote_file("remote-host", "p.md", plans_remote.default_ssh_runner, None)

    assert "終了コード3" in str(error.value)
    assert expected in str(error.value)


@pytest.mark.asyncio
async def test_awi_plan_renders_metadata_and_body(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "plans"
    root.mkdir()
    path = root / "01-0000_plan.md"
    content = plan_fixture.awi_plan(repo=tmp_path, related_wi=plan_fixture.WI_FILES)
    path.write_text(content, encoding="utf-8")
    context = _context(root)

    html = await plans_views.render_file_html(context, context.hostname, context.roots[0].source_id, path.name)

    assert "<dt>関連WI</dt>" in html
    assert plan_fixture.WI_FILES[0][0] in html
    assert "反映内容と反映先" in html
    assert "<pre>" in html


@pytest.mark.asyncio
async def test_wi_list_plan_renders_wi_body_link_and_missing_entry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = tmp_path / "notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    present = "20261101-000000-001.md"
    missing = "20261101-000000-002.md"
    _plan(notes, f"processing/{present}", "---\ntype: awi\n---\n# 対象WIの要求\n\n対象の本文を表示する。\n")
    root = tmp_path / "plans"
    content = plan_fixture.wi_list_plan(repo=tmp_path, related_wi=((present, ""), (missing, "")))
    path = _plan(root, "01-0000_plan.md", content)
    context = _context(root)

    html = await plans_views.render_file_html(context, context.hostname, context.roots[0].source_id, path.name)

    assert "対象の本文を表示する。" in html
    assert f'<a href="./?filename={present}">{present}</a>' in html
    assert f"{missing}</a></h2><p>このWIの本文を取得できない。" in html
