"""ユーザーコメント節の改行保持契約を検証する。"""

import pytest

from agent_toolkit._atk.wi import user_comment


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("has_heading", [False, True])
def test_split_returns_substrings_of_input(newline: str, has_heading: bool) -> None:
    text = newline.join(["---", "type: awi", "---", "", "本文"])
    if has_heading:
        text += newline.join([newline, "## ユーザーコメント", "", "記入"])

    before, comment = user_comment.split_before_user_comment(text)

    assert before + comment == text
    assert bool(comment) is has_heading


def test_update_user_comment_keeps_preserved_part_bytes() -> None:
    """既存のCRLF部分を保持し、新しいコメント節だけをLFで組み立てる。"""
    original = "---\r\ntype: awi\r\n---\r\n\r\n本文\r\n\r\n## ユーザーコメント\r\n\r\n旧コメント\r\n"

    updated = user_comment.update_user_comment(original, "新コメント")

    assert updated == "---\r\ntype: awi\r\n---\r\n\r\n本文\r\n\r\n## ユーザーコメント\n\n新コメント\n"


@pytest.mark.parametrize("comment", ["", " \n\n"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_empty_comment_removes_section_and_keeps_other_parts(comment: str, newline: str) -> None:
    kept = newline.join(
        ["---", "type: awi", "source: session-review", "---", "", "本文", "", "```markdown", "## コード内見出し", "```", ""]
    )
    original = kept + newline.join(["", "## ユーザーコメント", "", "旧コメント", ""])

    updated = user_comment.update_user_comment(original, comment)

    assert updated == kept
    assert user_comment.extract_user_comment(updated) is None

    assert user_comment.update_user_comment(updated, comment) == updated


def test_empty_comment_keeps_structure_errors() -> None:
    original = "---\ntype: awi\n---\n\n本文\n\n## ユーザーコメント\n\nA\n\n## 後続\n\nB\n"

    with pytest.raises(user_comment.UserCommentError, match="後ろに別のH2見出し"):
        user_comment.update_user_comment(original, "")


def test_extract_and_has_reserved_heading_accept_crlf() -> None:
    """CRLF本文の予約見出しを検出し、抽出結果をLFへ正規化する。"""
    text = "---\r\ntype: awi\r\n---\r\n\r\n本文\r\n\r\n## ユーザーコメント\r\n\r\n1行目\r\n2行目\r\n"

    assert user_comment.has_reserved_heading(text)
    assert user_comment.extract_user_comment(text) == "1行目\n2行目"


def test_crlf_frontmatter_value_is_not_misidentified_as_reserved_heading() -> None:
    text = '---\r\ntitle: "複数行\r\n## ユーザーコメント"\r\ntype: awi\r\n---\r\n\r\n本文\r\n'

    assert not user_comment.has_reserved_heading(text)
    assert user_comment.split_before_user_comment(text) == (text, "")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("## ユーザー指摘の逐語引用\n\n> 指摘A\n>\n> 指摘B  \n", True, id="blockquote"),
        pytest.param("## ユーザー指摘の逐語引用\n\n指摘A\n\n指摘B\n\n## 次の節\n", True, id="plain"),
        pytest.param("## ユーザー指摘の逐語引用\n\n> 前置き\n>\n> 指摘A\n>\n> 指摘B\n> 後書き\n", True, id="surrounded"),
        pytest.param("## ユーザー指摘の逐語引用\n\n> 指摘A\n> 指摘B\n", False, id="paragraph-merged"),
        pytest.param("## ユーザー指摘の逐語引用\n\n> 指摘A\n> 別の行\n> 指摘B\n", False, id="interrupted"),
        pytest.param("## 反映内容と反映先\n\n指摘A\n\n指摘B\n", False, id="other-section"),
        pytest.param(
            "## ユーザー指摘の逐語引用\n\n```text\n## 反映内容と反映先\n```\n\n指摘A\n\n指摘B\n", True, id="fenced-heading"
        ),
    ],
)
def test_quotes_user_comment_requires_contiguous_lines_in_quote_section(body: str, expected: bool) -> None:
    text = f"---\ntype: awi\n---\n\n本文\n\n{body}"

    assert user_comment.quotes_user_comment(text, "\n指摘A\n\n指摘B\n") is expected


@pytest.mark.parametrize(
    ("right", "expected"),
    [("指摘A  \r\n\r\n指摘B\r\n\r\n", True), ("指摘A\n指摘B", False), ("指摘A", False), ("指摘A\n\n指摘B\n\n追記", False)],
)
def test_same_user_comment_ignores_only_trailing_spaces_and_outer_blank_lines(right: str, expected: bool) -> None:
    assert user_comment.same_user_comment("\n指摘A\n\n指摘B", right) is expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("本文\n\n## ユーザーコメント\n\n指摘\n", True),
        ("本文\n\n## ユーザーコメント\n\n", False),
        ("本文\n\n```text\n## ユーザーコメント\n指摘\n```\n", False),
        ("本文\n\n## ユーザーコメント\n\n1\n\n## ユーザーコメント\n\n2\n", True),
    ],
)
def test_has_user_comment(body: str, expected: bool) -> None:
    assert user_comment.has_user_comment(f"---\ntype: awi\n---\n\n{body}") is expected
