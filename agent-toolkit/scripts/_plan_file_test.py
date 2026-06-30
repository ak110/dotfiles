"""_plan_file.pyのstrip_background_text_blocksの挙動を検証する。"""

import _plan_file


def test_strips_text_block_inside_background() -> None:
    content = "# Title\n\n## 背景\n\n```text\nuser feedback line 1\nline 2\n```\n\n## 対応方針\n\nbody\n"
    result = _plan_file.strip_background_text_blocks(content)
    assert "user feedback line 1" not in result
    assert "## 背景" in result
    assert "```text\n```" in result
    assert "## 対応方針" in result


def test_h2_inside_text_block_does_not_terminate_section() -> None:
    """`## 背景`配下のtextコードブロック内の`## F1`等で範囲が早期終了しない。"""
    content = (
        "# Title\n\n"
        "## 背景\n\n"
        "```text\n## F1 feedback heading inside block\n## F2 another\nraw quote\n```\n\n"
        "## 対応方針\n\n"
        "body\n"
    )
    result = _plan_file.strip_background_text_blocks(content)
    assert "## F1 feedback heading" not in result
    assert "raw quote" not in result
    assert "## 背景" in result
    assert "## 対応方針" in result


def test_background_section_absent_returns_input() -> None:
    content = "# Title\n\n## 対応方針\n\nbody\n"
    assert _plan_file.strip_background_text_blocks(content) == content


def test_background_section_at_end_of_file() -> None:
    content = "# Title\n\n## 背景\n\n```text\ntrailing material\n```\n"
    result = _plan_file.strip_background_text_blocks(content)
    assert "trailing material" not in result
    assert "## 背景" in result
    assert "```text\n```" in result


def test_non_text_fence_in_background_is_stripped() -> None:
    """言語指定がtext以外のフェンス（python・sh等）でも内容を除去する。"""
    content = "# Title\n\n## 背景\n\n```python\nprint('secret')\n```\n\n## 対応方針\n\nbody\n"
    result = _plan_file.strip_background_text_blocks(content)
    assert "print('secret')" not in result
    assert "## 対応方針" in result


def test_unclosed_fence_in_background() -> None:
    """`## 背景`配下で閉じフェンスが無いまま末尾へ達した場合、フェンス内行は除去される。"""
    content = "# Title\n\n## 背景\n\n```text\norphan line 1\norphan line 2\n"
    result = _plan_file.strip_background_text_blocks(content)
    assert "orphan line 1" not in result
    assert "orphan line 2" not in result
    assert "## 背景" in result


def test_long_backtick_fence_in_background() -> None:
    """4文字以上のバックティックフェンスでも閉じ判定が正しく機能する。"""
    content = "# Title\n\n## 背景\n\n````text\ninner ```inline``` lines\n## XX inside\n````\n\n## 対応方針\n\nbody\n"
    result = _plan_file.strip_background_text_blocks(content)
    assert "inner" not in result
    assert "## XX inside" not in result
    assert "## 対応方針" in result


def test_tilde_fence_in_background() -> None:
    """波線フェンスでもフェンス内の`## XX`を次セクションと誤検出しない。"""
    content = "# Title\n\n## 背景\n\n~~~text\n## fake heading\nquote line\n~~~\n\n## 対応方針\n\nbody\n"
    result = _plan_file.strip_background_text_blocks(content)
    assert "## fake heading" not in result
    assert "quote line" not in result
    assert "## 対応方針" in result


def test_compute_prelint_hashes_returns_pair() -> None:
    content = "# Title\n\n## 背景\n\n```text\nfoo\n```\n"
    full_sha, stripped_sha = _plan_file.compute_prelint_hashes(content)
    assert isinstance(full_sha, str) and len(full_sha) == 64
    assert isinstance(stripped_sha, str) and len(stripped_sha) == 64
    # stripped_shaは背景フェンス内容除去後のハッシュなので異なる
    assert full_sha != stripped_sha
