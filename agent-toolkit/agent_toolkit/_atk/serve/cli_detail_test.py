"""serveのcliに関するdetailの試験。"""

# pylint: disable=protected-access

import contextlib
import json
import math
import pathlib
import subprocess
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.serve_support import _write_detail_entry, _write_repo_entry


def test_detail_preserves_frontmatter_as_strict_json_values(tmp_path: pathlib.Path) -> None:
    """詳細APIはfrontmatterの入れ子値とYAML固有型を厳格JSON互換値へ変換する。"""
    _write_detail_entry(
        tmp_path,
        "---\n"
        "type: awi\n"
        "target_repo: example/repo\n"
        "source: test\n"
        "binary_value: !!binary |\n"
        "  SGVsbG8=\n"
        "set_value: !!set\n"
        "  alpha: null\n"
        "  beta: null\n"
        "ordered: !!omap\n"
        "  - first: 1\n"
        "  - second: 2\n"
        "nested:\n"
        "  label: value\n"
        "  values:\n"
        "    - one\n"
        "    - two\n"
        "queue_schedule:\n"
        "  timestamp: 2026-08-20T12:34:56Z\n"
        "  day: 2026-08-20\n"
        "  nan: .nan\n"
        "  positive: .inf\n"
        "  negative: -.inf\n"
        "---\n\n本文\n",
    )

    detail = wi_operations.Operations(tmp_path).detail("inbox", "entry.md")
    entries = typing.cast(list[dict[str, typing.Any]], detail["frontmatter_entries"])
    frontmatter = {item["key"]["value"]: item["value"] for item in entries}

    json.dumps(detail, ensure_ascii=False, allow_nan=False)
    assert frontmatter["binary_value"] == "SGVsbG8="
    assert frontmatter["set_value"] == ["alpha", "beta"]
    assert frontmatter["ordered"] == [["first", "1"], ["second", "2"]]
    assert frontmatter["nested"] == {"label": "value", "values": ["one", "two"]}
    assert frontmatter["queue_schedule"] == {
        "timestamp": "2026-08-20T12:34:56+00:00",
        "day": "2026-08-20",
        "nan": "NaN",
        "positive": "Infinity",
        "negative": "-Infinity",
    }


def test_detail_with_frontmatter_only_returns_empty_body_html(tmp_path: pathlib.Path) -> None:
    """frontmatterだけの詳細は本文用HTMLを空文字列として返す。"""
    _write_detail_entry(tmp_path, "---\ntype: awi\ntarget_repo: example/repo\n---\n")

    detail = wi_operations.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["body_html"] == ""
    assert '<table class="frontmatter">' in typing.cast(str, detail["content_html"])


@pytest.mark.parametrize(
    ("question_source", "expected_type", "expected_choices"),
    [
        ("question_type: choice\nchoices: 最初, 2番目\n", "choice", ["最初", "2番目"]),
        ("question_type: choice\nchoices:\n  - 最初\n  - 2番目\n", "choice", ["最初", "2番目"]),
        ("question_type: yes-no\nchoices: 無視する\n", "yes-no", []),
        ("question_type: broken\nchoices: [最初, '']\n", "free-form", []),
        ("question_type:\n  - choice\nchoices: [最初, 2番目]\n", "free-form", []),
        ("question_type:\n  nested: choice\nchoices: [最初, 2番目]\n", "free-form", []),
    ],
)
def test_detail_returns_body_only_and_normalized_question_metadata(
    tmp_path: pathlib.Path,
    question_source: str,
    expected_type: str,
    expected_choices: list[str],
) -> None:
    """詳細API用データはfrontmatterを除いた本文と正規化済みの回答形式を返す。"""
    _write_detail_entry(
        tmp_path,
        f"---\ntype: uwi\ntarget_repo: example/repo\n{question_source}---\n\n## 質問\n\n質問本文\n",
    )

    detail = wi_operations.Operations(tmp_path).detail("inbox", "entry.md")

    assert detail["question_type"] == expected_type
    assert detail["choices"] == expected_choices
    body_html = typing.cast(str, detail["body_html"])
    assert '<table class="frontmatter">' not in body_html
    assert "target_repo" not in body_html
    assert "質問本文" in body_html


def test_operations_source_empty_filter_returns_items_with_missing_or_empty_source(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`source_empty=true`は空の投入元だけを返し、値あり・非文字列の項目を除外する。"""
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "no-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n投入元なし\n",
        encoding="utf-8",
    )
    (inbox / "empty-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: \n---\n\n空投入元\n",
        encoding="utf-8",
    )
    (inbox / "whitespace-source.md").write_text(
        '---\ntype: awi\ntarget_repo: example/repo\nsource: "   "\n---\n\n空白投入元\n',
        encoding="utf-8",
    )
    (inbox / "with-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: web\n---\n\n投入元あり\n",
        encoding="utf-8",
    )
    (inbox / "list-source.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: []\n---\n\nリスト形式の投入元\n",
        encoding="utf-8",
    )
    result, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"source_empty": "true"})
    assert not warnings
    filenames = [typing.cast(str, item["filename"]) for item in result]
    filenames.sort()
    assert filenames == ["empty-source.md", "no-source.md", "whitespace-source.md"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 100, 101])
async def test_entries_api_paginates_at_one_hundred_entries(tmp_path: pathlib.Path, count: int) -> None:
    """一覧APIは明示ページだけを100件単位で返し、総数境界と空一覧を正規化する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for index in range(count):
        (inbox / f"entry-{index:03d}.md").write_text(
            "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
            encoding="utf-8",
        )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )

    response = await app.test_client().get("/api/entries?status=inbox&page=1")

    assert response.status_code == 200
    payload = await response.get_json()
    expected_page_count = max(1, math.ceil(count / 100))
    assert len(payload["entries"]) == min(count, 100)
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 100,
        "page_count": expected_page_count,
        "total_count": count,
    }
    if count == 101:
        second = await app.test_client().get("/api/entries?status=inbox&page=2")
        second_payload = await second.get_json()
        assert len(second_payload["entries"]) == 1
        assert second_payload["pagination"]["page"] == 2


@pytest.mark.asyncio
async def test_entries_api_filters_awi_plan_type_and_keeps_warnings_full_scan(tmp_path: pathlib.Path) -> None:
    """awiの通常型・計画型を独立条件で限定し、ページ警告は全走査分を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "normal.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n通常\n",
        encoding="utf-8",
    )
    (inbox / "plan.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nplan_file: /tmp/plan.md\n---\n\n計画\n",
        encoding="utf-8",
    )
    (inbox / "question.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\n---\n\n質問\n",
        encoding="utf-8",
    )
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        state.ServeState(tmp_path),
    )
    client = app.test_client()

    normal = await client.get("/api/entries?status=inbox&type=awi&plan=normal&page=1")
    planned = await client.get("/api/entries?status=inbox&type=all&plan=plan&page=1")

    assert [item["filename"] for item in (await normal.get_json())["entries"]] == ["normal.md"]
    assert [item["filename"] for item in (await planned.get_json())["entries"]] == ["plan.md"]
    assert (await normal.get_json())["entries"][0]["plan"] is False
    assert (await planned.get_json())["entries"][0]["plan"] is True


def test_repository_readers_canonicalize_legacy_paths_once_per_operation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧フィルターと候補収集は旧パス形を統合し、解決不能値の原値を保持する。"""
    local_repo = tmp_path / "target-repo"
    local_repo.mkdir()
    _write_repo_entry(tmp_path, "inbox", "current.md", "github.com/example/repo")
    _write_repo_entry(tmp_path, "inbox", "legacy-a.md", str(local_repo))
    _write_repo_entry(tmp_path, "inbox", "legacy-b.md", str(local_repo))
    _write_repo_entry(tmp_path, "inbox", "other.md", "github.com/example/other")
    _write_repo_entry(tmp_path, "inbox", "missing.md", str(tmp_path / "missing"))
    _write_repo_entry(tmp_path, "inbox", "unresolved.md", "example/repo")
    resolved_paths: list[str] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        resolved_paths.append(cmd[2])
        return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/example/repo.git\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    operations = wi_operations.Operations(tmp_path)

    url_entries, warnings = operations.entries_with_warnings({"target_repo": "github.com/example/repo"})
    assert [item["filename"] for item in url_entries] == ["legacy-b.md", "legacy-a.md", "current.md"]
    assert not warnings
    assert resolved_paths == [str(local_repo)]

    resolved_paths.clear()
    path_entries, warnings = operations.entries_with_warnings({"target_repo": str(local_repo)})
    assert [item["filename"] for item in path_entries] == ["legacy-b.md", "legacy-a.md", "current.md"]
    assert not warnings
    assert resolved_paths == [str(local_repo)]

    resolved_paths.clear()
    assert operations.target_repos() == [
        str(tmp_path / "missing"),
        "example/repo",
        "github.com/example/other",
        "github.com/example/repo",
    ]
    assert resolved_paths == [str(local_repo)]

    unresolved_entries, warnings = operations.entries_with_warnings({"target_repo": "example/repo"})
    assert [item["filename"] for item in unresolved_entries] == ["unresolved.md"]
    assert not warnings

    unfiltered_entries, warnings = operations.entries_with_warnings({})
    assert not warnings
    assert next(item for item in unfiltered_entries if item["filename"] == "missing.md")["target_repo"] == str(
        tmp_path / "missing"
    )
