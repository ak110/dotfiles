"""serveのconfigに関するdetailの試験。"""

# pylint: disable=protected-access

import contextlib
import json
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.wi import frontmatter as wi_frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.serve_support import _write_detail_entry
from agent_toolkit._testing.wi_entry_files import entry_path


def test_detail_returns_empty_frontmatter_when_unavailable(tmp_path: pathlib.Path) -> None:
    """frontmatterが無い詳細は空の表示用一覧を返す。"""
    _write_detail_entry(tmp_path, "本文のみ\n")
    detail = wi_operations.Operations(tmp_path).detail("inbox", "entry.md")
    assert not detail["frontmatter_entries"]


@pytest.mark.asyncio
async def test_detail_api_preserves_top_level_key_types_and_order(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """詳細HTTP APIは整数形式キーを含むトップレベル項目の型と挿入順を保持する。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: awi\nz_key: z\na_key: a\n---\n\n本文\n",
    )

    original_parse = wi_frontmatter.parse_frontmatter

    def parse_with_integer_key(text: str) -> tuple[dict[typing.Any, typing.Any], str] | None:
        parsed = original_parse(text)
        if parsed is None:
            return None
        metadata, body = parsed
        enriched: dict[typing.Any, typing.Any] = {}
        for key, value in metadata.items():
            enriched[key] = value
            if key == "z_key":
                enriched[1] = "numeric"
                enriched["1"] = "textual"
        return enriched, body

    monkeypatch.setattr(wi_frontmatter, "parse_frontmatter", parse_with_integer_key)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/entry.md")

    assert response.status_code == 200
    payload = json.loads(await response.get_data())
    assert payload["entry"]["frontmatter_entries"] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {"key": {"type": "str", "value": "z_key"}, "value": "z"},
        {"key": {"type": "int", "value": 1}, "value": "numeric"},
        {"key": {"type": "str", "value": "1"}, "value": "textual"},
        {"key": {"type": "str", "value": "a_key"}, "value": "a"},
    ]


def test_render_body_renders_footnote_with_document_anchor() -> None:
    """注記記法は本文と同一文書内の参照リンクとして描画する。"""
    rendered = wi_operations._render_body("本文です[^1]。\n\n[^1]: 注記の本文\n")

    assert "注記の本文" in rendered
    assert 'href="#fn1"' in rendered
    assert 'href="%E6%B3%A8%E8%A8%98%E3%81%AE%E6%9C%AC%E6%96%87"' not in rendered


def test_operations_answered_filter_returns_only_answered_uwis(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`answered=yes`は回答済みUWIのみを返し、未回答UWI・AWIを除外する。"""
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    entry_path(tmp_path, "answered.md").write_text(
        "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    entry_path(tmp_path, "unanswered.md").write_text(
        "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    entry_path(tmp_path, "awi.md").write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\nAWI本文\n",
        encoding="utf-8",
    )
    result, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"answered": "yes"})
    assert not warnings
    assert [item["filename"] for item in result] == ["answered.md"]


@pytest.mark.asyncio
async def test_entries_api_filters_source_kind_and_preserves_raw_source_filters(tmp_path: pathlib.Path) -> None:
    """一覧APIは投入元分類を適用し、既存のraw投入元フィルターを維持する。"""
    for filename, source in [
        ("agent.md", "agent"),
        ("alert-monitor.md", "alert-monitor"),
        ("review.md", "session-review"),
        ("plan.md", "plan"),
        ("unknown.md", "web"),
        ("human.md", "human"),
        ("empty.md", None),
    ]:
        metadata = "type: awi\nstate: inbox\ntarget_repo: example/repo"
        if source is not None:
            metadata += f"\nsource: {source}"
        entry_path(tmp_path, filename).write_text(f"---\n{metadata}\n---\n\n本文\n", encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    agent = await client.get("/api/entries?status=inbox&type=awi&source_kind=agent")
    human = await client.get("/api/entries?status=inbox&type=awi&source_kind=human")
    raw = await client.get("/api/entries?status=inbox&type=awi&source=session-review")
    empty = await client.get("/api/entries?status=inbox&type=awi&source_empty=true")

    assert {item["filename"] for item in (await agent.get_json())["entries"]} == {
        "agent.md",
        "alert-monitor.md",
        "human.md",
        "plan.md",
        "review.md",
        "unknown.md",
    }
    assert {item["filename"] for item in (await human.get_json())["entries"]} == {"empty.md"}
    assert [item["filename"] for item in (await raw.get_json())["entries"]] == ["review.md"]
    assert {item["filename"] for item in (await empty.get_json())["entries"]} == {"empty.md"}


@pytest.mark.asyncio
async def test_entries_api_omits_pagination_without_explicit_page_and_clamps_final_page(
    tmp_path: pathlib.Path,
) -> None:
    """ページ省略時の既存payloadを維持し、最終ページを超える指定を末尾へ正規化する。"""
    for index in range(101):
        entry_path(tmp_path, f"entry-{index:03d}.md").write_text(
            "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n本文\n",
            encoding="utf-8",
        )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    complete = await client.get("/api/entries?status=inbox")
    clamped = await client.get("/api/entries?status=inbox&page=999")

    complete_payload = await complete.get_json()
    clamped_payload = await clamped.get_json()
    assert set(complete_payload) == {"entries", "warnings"}
    assert len(complete_payload["entries"]) == 101
    assert clamped_payload["pagination"] == {
        "page": 2,
        "page_size": 100,
        "page_count": 2,
        "total_count": 101,
    }
    assert len(clamped_payload["entries"]) == 1
    assert clamped_payload["entries"][0]["filename"] == complete_payload["entries"][-1]["filename"]


def test_entries_reports_os_error_without_treating_unknown_kind_as_warning(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OS読取り失敗だけを警告へ分離し、種別不明のMarkdownは一覧へ残す。"""
    entry_path(tmp_path, "unknown.md").write_text("---\nstate: inbox\n---\n\n種別を判定できない本文\n", encoding="utf-8")
    failed_path = entry_path(tmp_path, "os-error.md")
    failed_path.touch()
    original_read_text = pathlib.Path.read_text

    def read_text(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == failed_path:
            raise OSError("読取り失敗")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)

    entries, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"status": "inbox"})

    assert [(entry["filename"], entry["kind"]) for entry in entries] == [("unknown.md", "unknown")]
    assert warnings == [
        {
            "state": "unknown",
            "filename": "os-error.md",
            "path": str(failed_path),
            "reason": "ファイルを読み取れません",
            "next_action": (
                f"{failed_path}が実在し、atk serveを実行するOSアカウントから読み取れる権限になっているかを確認する"
            ),
        }
    ]
