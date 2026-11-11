"""serveのstateに関するdetailの試験。"""

# pylint: disable=protected-access

import json
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._testing.serve_support import _write_detail_entry


def test_detail_returns_existing_uwi_answer(tmp_path: pathlib.Path) -> None:
    """回答済みUWIの詳細は既存回答を編集用に返す。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問本文\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n既存回答\n2行目\n",
    )

    detail = wi_operations.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["answer"] == "既存回答\n2行目"


@pytest.mark.asyncio
async def test_detail_api_round_trips_frontmatter_as_strict_json(tmp_path: pathlib.Path) -> None:
    """詳細HTTP APIは非有限浮動小数点を含むfrontmatterも標準JSONとして返す。"""
    _write_detail_entry(
        tmp_path,
        "---\ntype: awi\nqueue_schedule:\n  nan: .nan\n  positive: .inf\n---\n\n本文\n",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries/inbox/entry.md")

    assert response.status_code == 200

    def reject_constant(_value: str) -> typing.NoReturn:
        raise ValueError("非標準JSON定数です")

    payload = json.loads(await response.get_data(), parse_constant=reject_constant)
    assert payload["entry"]["frontmatter_entries"] == [
        {"key": {"type": "str", "value": "type"}, "value": "awi"},
        {
            "key": {"type": "str", "value": "queue_schedule"},
            "value": {"nan": "NaN", "positive": "Infinity"},
        },
    ]


def test_detail_falls_back_on_broken_frontmatter(tmp_path: pathlib.Path) -> None:
    """frontmatterの解析に失敗した場合は本文全体の整形結果を返す。"""
    _write_detail_entry(tmp_path, "---\nkey: [unclosed\n---\n\n本文\n")
    rendered = typing.cast(str, wi_operations.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "本文" in rendered
    # 表へ振り分けず本文全体をMarkdownとして整形するため、開始区切りが水平線として残る。
    assert '<table class="frontmatter">' not in rendered
    assert "<hr" in rendered


def test_detail_disables_only_bare_address_links(tmp_path: pathlib.Path) -> None:
    """詳細表示は裸アドレスをリンク化せず、明示リンクとGFM拡張を維持する。"""
    _write_detail_entry(
        tmp_path,
        "https://example.com www.example.com user@example.com\n\n"
        "[明示リンク](https://example.net) <https://example.org>\n\n"
        "~~取消~~\n\n| 列 |\n| --- |\n| 値 |\n",
    )

    rendered = typing.cast(str, wi_operations.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])

    assert '<a href="https://example.com">' not in rendered
    assert '<a href="http://www.example.com">' not in rendered
    assert '<a href="mailto:user@example.com">' not in rendered
    assert '<a href="https://example.net">明示リンク</a>' in rendered
    assert '<a href="https://example.org">https://example.org</a>' in rendered
    assert "<s>取消</s>" in rendered
    assert "<table>" in rendered


def test_operations_active_includes_hold_entries_of_both_types(tmp_path: pathlib.Path) -> None:
    """一覧APIのactive状態はhold配下のawiとUWIをいずれも含める。"""
    hold = tmp_path / "hold"
    hold.mkdir()
    (hold / "held.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n保留中\n",
        encoding="utf-8",
    )
    (hold / "held-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n保留中UWI\n",
        encoding="utf-8",
    )

    entries, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"status": "active"})

    assert not warnings
    assert sorted(str(item["filename"]) for item in entries) == ["held-uwi.md", "held.md"]
    assert {item["state"] for item in entries} == {"hold"}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("本文途中の固有語", ["entry.md"]),
        ("日本語", ["entry.md"]),
        ("EXAMPLE/REPO", ["entry.md"]),
        ("entry.md", ["entry.md"]),
        ("日本語  EXAMPLE/REPO ", ["entry.md"]),
        ("entry.md other/repo", []),
    ],
)
def test_operations_query_searches_full_markdown_and_metadata(tmp_path: pathlib.Path, query: str, expected: list[str]) -> None:
    """`q`は半角スペース区切りで各語を複数フィールドへAND検索する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "entry.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n先頭\n\n日本語と本文途中の固有語\n",
        encoding="utf-8",
    )
    (inbox / "other.md").write_text(
        "---\ntype: awi\ntarget_repo: other/repo\n---\n\n別本文\n",
        encoding="utf-8",
    )

    result, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"q": query})
    assert not warnings

    assert [item["filename"] for item in result] == expected


def test_operations_sort_entries_by_filename_across_kinds(tmp_path: pathlib.Path) -> None:
    """一覧は種別と回答状況にかかわらずファイル名降順で返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # ファイル名の昇順でファイルを作成
    (inbox / "a-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "z-answered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "m-answered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n回答済み\n",
        encoding="utf-8",
    )
    (inbox / "a-unanswered-uwi.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n## 質問\n\n質問\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "d-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (inbox / "z-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )

    operations = wi_operations.Operations(tmp_path)
    result, warnings = operations.entries_with_warnings({})
    assert not warnings
    filenames = [item["filename"] for item in result]

    assert filenames == [
        "z-awi.md",
        "z-answered-uwi.md",
        "m-answered-uwi.md",
        "d-awi.md",
        "a-unanswered-uwi.md",
        "a-awi.md",
    ]
    # 種別の確認
    assert [item["kind"] for item in result] == ["awi", "uwi", "uwi", "awi", "uwi", "awi"]
