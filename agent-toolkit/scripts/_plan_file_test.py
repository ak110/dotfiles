"""_plan_file.pyのstrip_background_text_blocks等の挙動を検証する。"""

import hashlib
import pathlib

import _plan_file
import pytest


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


def test_strip_diff_markers_unified_diff_block_stripped() -> None:
    """先頭2行以内に`@@`を含むブロックはフェンスマーカー行と行頭`+`・`-`が除去される。"""
    content = "# Title\n\n## 変更内容\n\n```text\n@@ -1,2 +1,2 @@\n-old line\n+new line\n context\n```\n\n## 実行方法\n"
    result = _plan_file.strip_diff_markers_in_changes_blocks(content)
    assert "```" not in result.split("## 変更内容", 1)[1].split("## 実行方法", 1)[0]
    assert "-old line" not in result
    assert "+new line" not in result
    assert "old line" in result
    assert "new line" in result
    assert " context" in result


def test_strip_diff_markers_dash_marker_block_stripped() -> None:
    """先頭2行以内に`---`・`+++`を含むブロックも加工対象になる。"""
    content = "# Title\n\n## 変更内容\n\n```text\n--- a/file.py\n+++ b/file.py\n-removed\n+added\n```\n"
    result = _plan_file.strip_diff_markers_in_changes_blocks(content)
    assert "-removed" not in result
    assert "+added" not in result
    assert "removed" in result
    assert "added" in result


def test_strip_diff_markers_non_diff_language_block_unchanged() -> None:
    """`python`・`sh`等の言語指定を持つブロックは変更しない。"""
    content = "# Title\n\n## 変更内容\n\n```python\ndef f():\n    return 1\n```\n"
    result = _plan_file.strip_diff_markers_in_changes_blocks(content)
    assert result == content


def test_strip_diff_markers_text_block_with_list_marker_unchanged() -> None:
    """言語指定`text`かつ`+`・`-`をリスト記法として使うブロックは`@@`等が無いため変更しない。"""
    content = "# Title\n\n## 変更内容\n\n```text\n- item one\n+ item two\n```\n"
    result = _plan_file.strip_diff_markers_in_changes_blocks(content)
    assert result == content


def test_strip_diff_markers_multiple_blocks_individually_judged() -> None:
    """複数コードブロック混在時、diffブロックのみ加工し非diffブロックは維持する。"""
    content = "# Title\n\n## 変更内容\n\n```python\ndef f():\n    return 1\n```\n\n```text\n@@ -1 +1 @@\n-old\n+new\n```\n"
    result = _plan_file.strip_diff_markers_in_changes_blocks(content)
    assert "```python\ndef f():\n    return 1\n```" in result
    assert "-old" not in result
    assert "+new" not in result
    assert "old" in result
    assert "new" in result


def test_strip_diff_markers_empty_block_unchanged() -> None:
    """空のコードブロックは非diff扱いとなり変更しない。"""
    content = "# Title\n\n## 変更内容\n\n```text\n```\n"
    result = _plan_file.strip_diff_markers_in_changes_blocks(content)
    assert result == content


def test_strip_diff_markers_unclosed_fence_unchanged() -> None:
    """閉じフェンスが無いブロックは変更しない。"""
    content = "# Title\n\n## 変更内容\n\n```text\n@@ -1 +1 @@\n-old\n"
    result = _plan_file.strip_diff_markers_in_changes_blocks(content)
    assert result == content


def test_strip_diff_markers_absent_section_returns_input() -> None:
    """`## 変更内容`が無い場合は元の内容をそのまま返す。"""
    content = "# Title\n\n## 対応方針\n\n```text\n@@ -1 +1 @@\n-old\n```\n"
    assert _plan_file.strip_diff_markers_in_changes_blocks(content) == content


def test_compute_prelint_hashes_matches_pipeline_output_for_diff_blocks() -> None:
    """diff表記を含む計画本文の`stripped_sha`が、scratchpad加工後テキストのハッシュと整合する。"""
    content = "# Title\n\n## 背景\n\n```text\nuser feedback\n```\n\n## 変更内容\n\n```text\n@@ -1 +1 @@\n-old\n+new\n```\n"
    _, stripped_sha = _plan_file.compute_prelint_hashes(content)
    pipeline_output = _plan_file.strip_diff_markers_in_changes_blocks(_plan_file.strip_background_text_blocks(content))
    assert stripped_sha == hashlib.sha256(pipeline_output.encode("utf-8")).hexdigest()


@pytest.fixture
def _plans_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """`~/.claude/plans/`を`tmp_path`配下に振り替える。"""
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return plans


def test_is_plan_file_normal_md_returns_true(_plans_home: pathlib.Path) -> None:
    """`~/.claude/plans/`直下の`.md`は計画ファイル判定される。"""
    plan = _plans_home / "sample.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(plan)) is True


def test_is_plan_file_review_md_excluded(_plans_home: pathlib.Path) -> None:
    """`.review.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.review.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(path)) is False


def test_is_plan_file_codex_log_excluded(_plans_home: pathlib.Path) -> None:
    """`.codex.log`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.codex.log"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(path)) is False


def test_is_plan_file_workaround_check_excluded(_plans_home: pathlib.Path) -> None:
    """`-workaround-check.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample-workaround-check.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(path)) is False
