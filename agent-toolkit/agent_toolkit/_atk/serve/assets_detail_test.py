"""serveのassetsに関するdetailの試験。"""

# pylint: disable=protected-access

import html
import pathlib
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import frontmatter as wi_frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi as wi_uwi
from agent_toolkit._testing.serve_support import _write_detail_entry, _write_repo_entry


def test_operations_reads_local_entries_and_detail_without_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧と詳細はGit同期を開始せずローカル内容を返す。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    entry = inbox / "entry.md"
    entry.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: test\n---\n\n要約本文\n",
        encoding="utf-8",
    )

    def unexpected(*_args: object, **_kwargs: object) -> typing.NoReturn:
        raise AssertionError("読取り処理がGit同期を開始しました")

    monkeypatch.setattr(_wi_sync, "repo_lock", unexpected)
    monkeypatch.setattr(_wi_sync, "pull", unexpected)
    operations = wi_operations.Operations(tmp_path)
    result, warnings = operations.entries_with_warnings({})
    assert not warnings
    assert result[0] | {
        "updated_at": result[0]["updated_at"],
    } == {
        "kind": "awi",
        "state": "inbox",
        "filename": "entry.md",
        "answered": None,
        "needs_verify": False,
        "blocked": False,
        "cooldown_until": None,
        "plan": False,
        "important": False,
        "target_repo": "example/repo",
        "source": "test",
        "summary": "要約本文",
        "updated_at": result[0]["updated_at"],
    }
    detail = operations.detail("inbox", "entry.md")
    content = detail["content"]
    assert isinstance(content, str)
    assert content.endswith("要約本文\n")


def test_detail_renders_frontmatter_as_table(tmp_path: pathlib.Path) -> None:
    """frontmatterは入れ子の長い値も表として描画し、区切り行由来の見出しを生成しない。"""
    _write_detail_entry(
        tmp_path,
        "---\ntarget_repo: github.com/ak110/dotfiles\ntype: awi\n"
        "plan_file: /tmp/plan.md\ndepends_on:\n  - predecessor.md\n---\n\n本文です。\n",
    )
    rendered = typing.cast(str, wi_operations.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<table" in rendered
    assert "target_repo" in rendered
    assert "plan_file" in rendered
    assert "predecessor.md" in rendered
    assert "<hr" not in rendered
    assert "<h2" not in rendered
    assert "<p>本文です。</p>" in rendered


def test_detail_escapes_frontmatter_values(tmp_path: pathlib.Path) -> None:
    """frontmatterの値に含まれるHTML特殊文字をエスケープする。"""
    _write_detail_entry(tmp_path, '---\ntype: awi\nnote: "<script>alert(1)</script>"\n---\n\n本文\n')
    rendered = typing.cast(str, wi_operations.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


@pytest.mark.parametrize("question_type", ["post-approval", "free-form", "choice"])
@pytest.mark.parametrize("answer", ["", "問題がある\n是正内容を確認してください。"])
def test_uwi_preview_hides_answer_separator_and_preserves_raw_content(
    tmp_path: pathlib.Path, question_type: str, answer: str
) -> None:
    """各質問形式の両プレビューは区切りだけを除き、回答と安全な本文表示・原文を保つ。"""
    content = (
        f"---\ntype: uwi\nquestion_type: {question_type}\nchoices: [選択肢]\n---\n\n"
        "## 質問\n\n質問本文\n\n<!-- 一般のコメント -->\n\n<script>alert(1)</script>\n\n"
        f"## 回答\n\n{wi_uwi.ANSWER_MARKER}\n{answer}\n"
    )
    _write_detail_entry(tmp_path, content)
    detail = wi_operations.Operations(tmp_path).detail("inbox", "entry.md")
    assert detail["content"] == content
    assert detail["answer"] == (answer or None)
    for key in ("content_html", "body_html"):
        rendered = typing.cast(str, detail[key])
        assert "ユーザーはこの行以降に回答を追記する" not in rendered
        assert "質問本文" in rendered
        assert html.escape("<!-- 一般のコメント -->") in rendered
        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered
        if answer:
            assert answer in rendered


@pytest.mark.parametrize("kind", ["uwi", "awi"])
def test_preview_preserves_marker_examples_before_answer_separator(tmp_path: pathlib.Path, kind: str) -> None:
    """UWIの最後の保存区切り以外の記法例と、AWIの本文は表示から失わない。"""
    marker = wi_uwi.ANSWER_MARKER
    content = f"---\ntype: {kind}\n---\n\n`{marker}`\n\n```markdown\n{marker}\n```\n\n## 回答\n\n{marker}\n"
    _write_detail_entry(tmp_path, content)
    detail = wi_operations.Operations(tmp_path).detail("inbox", "entry.md")
    assert detail["content"] == content
    for key in ("content_html", "body_html"):
        rendered = typing.cast(str, detail[key])
        assert rendered.count(html.escape(marker)) == (2 if kind == "uwi" else 3)
        assert "<code>" in rendered
        assert '<code class="language-markdown">' in rendered


def test_detail_with_empty_frontmatter_renders_body_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """空のfrontmatterは空表を生成せず、分離後の本文だけを整形する。"""
    _write_detail_entry(tmp_path, "---\n---\n\n本文\n")
    monkeypatch.setattr(_wi_entries, "entry_type_from_metadata", lambda *_args: "awi")
    rendered = typing.cast(str, wi_operations.Operations(tmp_path).detail("inbox", "entry.md")["content_html"])
    assert '<table class="frontmatter">' not in rendered
    # 分離自体は成立するため、開始区切りが水平線として残らない。
    assert "<hr" not in rendered
    assert "<p>本文</p>" in rendered


def test_operations_sort_entries_by_filename_across_states_and_render_markdown(tmp_path: pathlib.Path) -> None:
    """一覧を状態横断のファイル名順で返し、詳細本文を安全なHTMLへ整形する。"""
    inbox = tmp_path / "inbox"
    processing = tmp_path / "processing"
    inbox.mkdir()
    processing.mkdir()
    (inbox / "z-last.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n",
        encoding="utf-8",
    )
    (processing / "a-first.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n"
        "# 見出し\n\n- 項目\n\n```python\nprint('x')\n```\n\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )

    operations = wi_operations.Operations(tmp_path)
    entries, warnings = operations.entries_with_warnings({"status": "active"})
    assert not warnings
    assert [item["filename"] for item in entries] == ["z-last.md", "a-first.md"]
    detail = operations.detail("processing", "a-first.md")
    rendered = typing.cast(str, detail["content_html"])
    assert "<h1>見出し</h1>" in rendered
    assert "<li>項目</li>" in rendered
    assert '<code class="language-python">' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_operations_parses_each_scanned_entry_once_before_query_filter(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文検索で除外するエントリも含め、走査したMarkdownを1件1回だけ解析する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    for filename, body in (("match.md", "検索対象"), ("other.md", "別の本文")):
        (inbox / filename).write_text(
            f"---\ntype: awi\ntarget_repo: example/repo\n---\n\n{body}\n",
            encoding="utf-8",
        )
    original_parse = wi_frontmatter.parse_frontmatter
    parse_calls = 0

    def counting_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(text)

    monkeypatch.setattr(wi_frontmatter, "parse_frontmatter", counting_parse)

    result, warnings = wi_operations.Operations(tmp_path).entries_with_warnings({"q": "検索対象"})

    assert not warnings
    assert [item["filename"] for item in result] == ["match.md"]
    assert parse_calls == 2


@pytest.mark.asyncio
async def test_api_repos_returns_target_repos(tmp_path: pathlib.Path) -> None:
    """`GET /api/repos`は一覧と同じ状態指定で対象リポジトリの一覧を返す。"""
    _write_repo_entry(tmp_path, "inbox", "a.md", "github.com/x/alpha")
    _write_repo_entry(tmp_path, "adopted", "b.md", "github.com/x/adopted")
    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state)
    response = await app.test_client().get("/api/repos")
    assert response.status_code == 200
    assert await response.get_json() == {"repos": ["github.com/x/alpha"]}
    adopted_response = await app.test_client().get("/api/repos?status=adopted")
    assert adopted_response.status_code == 200
    assert await adopted_response.get_json() == {"repos": ["github.com/x/adopted"]}
    invalid_response = await app.test_client().get("/api/repos?status=unknown")
    assert invalid_response.status_code == 400
