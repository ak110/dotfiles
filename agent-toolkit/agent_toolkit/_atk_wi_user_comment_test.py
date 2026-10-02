"""ユーザーコメント節の改行保持契約を検証する。"""

import pytest

from agent_toolkit._atk.wi import user_comment


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("has_heading", [False, True])
def test_split_returns_substrings_of_input(newline: str, has_heading: bool) -> None:
    """予約見出しの有無と改行形式を問わず、分割結果から入力を復元できる。"""
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
    """空のコメントは予約節だけを削除し、frontmatterと前の本文を元の改行のまま保つ。"""
    kept = newline.join(
        ["---", "type: awi", "source: session-review", "---", "", "本文", "", "```markdown", "## コード内見出し", "```", ""]
    )
    original = kept + newline.join(["", "## ユーザーコメント", "", "旧コメント", ""])

    updated = user_comment.update_user_comment(original, comment)

    assert updated == kept
    assert user_comment.extract_user_comment(updated) is None
    # 節が無い本文への空の保存は、本文を変えずに成功する。
    assert user_comment.update_user_comment(updated, comment) == updated


def test_empty_comment_keeps_structure_errors() -> None:
    """空のコメントでも、削除対象を一意に定められない本文は変更せずに拒否する。"""
    original = "---\ntype: awi\n---\n\n本文\n\n## ユーザーコメント\n\nA\n\n## 後続\n\nB\n"

    with pytest.raises(user_comment.UserCommentError, match="後ろに別のH2見出し"):
        user_comment.update_user_comment(original, "")


def test_extract_and_has_reserved_heading_accept_crlf() -> None:
    """CRLF本文の予約見出しを検出し、抽出結果をLFへ正規化する。"""
    text = "---\r\ntype: awi\r\n---\r\n\r\n本文\r\n\r\n## ユーザーコメント\r\n\r\n1行目\r\n2行目\r\n"

    assert user_comment.has_reserved_heading(text)
    assert user_comment.extract_user_comment(text) == "1行目\n2行目"


def test_crlf_frontmatter_value_is_not_misidentified_as_reserved_heading() -> None:
    """frontmatter値中の同名行を本文の予約見出しと誤認しない。"""
    text = '---\r\ntitle: "複数行\r\n## ユーザーコメント"\r\ntype: awi\r\n---\r\n\r\n本文\r\n'

    assert not user_comment.has_reserved_heading(text)
    assert user_comment.split_before_user_comment(text) == (text, "")
