"""`atk serve`の計画ファイル画面の処理のテスト。"""

# pylint: disable=protected-access

import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import plans
from agent_toolkit._atk.serve.plans import local_scan as plans_local_scan
from agent_toolkit._atk.serve.plans import remote as plans_remote
from agent_toolkit._atk.serve.plans import rendering as plans_rendering
from agent_toolkit._atk.serve.plans import views as plans_views
from agent_toolkit._plan import viewer_files as plan_viewer_files
from agent_toolkit._testing.serve_plans_support import _context, _FakeWatcher, _plan, _read_payload, _runner_returning


def test_distinct_roots_are_kept_separately(tmp_path: pathlib.Path) -> None:
    """別実体のrootは同名ファイルを持っていても別々に保持する。"""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    normalized = plan_viewer_files.normalize_root_specs(
        (
            plans.RootSpec(source_id=plans.NEW_SOURCE_ID, path=first, portable_path="a"),
            plans.RootSpec(source_id=plans.LEGACY_SOURCE_ID, path=second, portable_path="b"),
        )
    )

    assert [spec.source_id for spec in normalized] == [plans.NEW_SOURCE_ID, plans.LEGACY_SOURCE_ID]


def test_raw_html_is_escaped(tmp_path: pathlib.Path) -> None:
    """本文中の生HTMLを実行させず、文字として表示する。"""
    del tmp_path
    html = plans_rendering.markdown_to_html('<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n')

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "onerror" not in html or "&lt;img" in html


def test_fenced_code_is_highlighted_by_language() -> None:
    """言語指定のあるフェンスはハイライトし、未知言語は素通しで描画する。"""
    highlighted = plans_rendering.markdown_to_html("```python\nx = 1\n```\n")
    plain = plans_rendering.markdown_to_html("```nosuchlanguage\nx = 1\n```\n")

    assert 'class="codehilite language-python"' in highlighted
    assert "codehilite" not in plain
    assert "<pre>" in plain


@pytest.mark.asyncio
async def test_attached_plan_navigation_is_symmetric(tmp_path: pathlib.Path, index_path: pathlib.Path) -> None:
    """メインと付属計画のどちらを表示中でも、実在する全ページへのリンクを返す。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    for rel in ("p.md", "p.detail.md", "p.bugs.md"):
        _plan(root, rel)
    context = _context(root)

    for current in ("p.md", "p.detail.md", "p.bugs.md"):
        html = await plans_views.plan_links_html(context, "local-host", "", current)
        assert 'class="detail-link"' in html
        others = {rel for rel in ("p.md", "p.detail.md", "p.bugs.md") if rel != current}
        for other in others:
            assert f'data-plan-path="{other}"' in html
        # 現在表示中のページはリンクにせず、ラベルだけを置く。
        assert f'data-plan-path="{current}"' not in html


def test_search_matches_full_text_case_insensitively(tmp_path: pathlib.Path) -> None:
    """本文検索は大文字小文字を区別しない部分一致とする。"""
    root = tmp_path / "plans"
    root.mkdir()
    _plan(root, "p.md", "Hello World")

    assert plans_local_scan.search_files(root, "hello") == {"p.md"}
    assert plans_local_scan.search_files(root, "missing") == set()


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("plan.md", True),
        ("dir/plan.md", True),
        ("plan.exec-review.tsv", True),
        ("../plan.md", False),
        ("dir/../../plan.md", False),
        ("/abs/plan.md", False),
        ("dir\\plan.md", False),
        ("plan.txt", False),
        ("", False),
    ],
)
def test_remote_relative_path_is_validated_before_ssh(rel: str, expected: bool) -> None:
    """上位ディレクトリ参照と対象外の接尾辞は、SSH呼び出しの前に拒否する。"""
    assert plans_remote.is_safe_remote_relpath(rel) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [None, RuntimeError("切断"), {"ok": False, "error": "permission denied"}],
    ids=["disconnected", "rpc-raises", "rpc-error"],
)
async def test_remote_file_falls_back_to_single_ssh(response: typing.Any, caplog: pytest.LogCaptureFixture) -> None:
    """RPCが未接続・失敗・エラー応答の場合は単発SSHへ切り替える。"""
    runner, calls = _runner_returning(_read_payload("fallback"))
    watcher = None if response is None else _FakeWatcher(connected=True, response=response)

    text = await plans_remote.fetch_remote_file("remote-host", "p.md", runner, typing.cast(typing.Any, watcher))

    assert text == "fallback"
    assert [call[0:2] for call in calls] == [("remote-host", "read")]
    assert len([record for record in caplog.records if record.levelname == "WARNING"]) == (0 if response is None else 1)
