"""agent-toolkit/scripts/_colloquial_check.py のテスト。

共通ロジック（load_patterns / scan_text / first_hit / mask_allowed）の検証。
テスト本体に口語表現を直接書かないよう、辞書ファイルから動的にサンプルを構築する。
"""

import pathlib
import re

import _colloquial_check
import pytest


def _expand_pattern(pattern_str: str) -> str:
    """文字クラス `[...]` を先頭文字に置換する簡易展開。

    辞書のパターン記法は文字クラスのみのため、これでマッチサンプルが得られる。
    """
    return re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], pattern_str)


def _read_patterns_text(path: pathlib.Path) -> list[str]:
    """ファイルから（コンパイル前の）正規表現文字列を順に取り出す。"""
    return [s for line in path.read_text(encoding="utf-8").splitlines() for s in [line.strip()] if s and not s.startswith("#")]


@pytest.fixture(name="deny_patterns", scope="module")
def _deny_patterns():
    return _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)


@pytest.fixture(name="allow_patterns", scope="module")
def _allow_patterns():
    return _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)


@pytest.fixture(name="overlap_sample", scope="module")
def _overlap_sample(deny_patterns) -> tuple[str, str]:
    """allowlist側のパターンを展開して、denylist側にも当たる最初のサンプルを返す。

    戻り値: `(allow_sample, deny_substring)`
    マスキング動作の検証用に、両者を共通の文字列から取り出す。
    """
    for raw in _read_patterns_text(_colloquial_check.ALLOW_PATH):
        sample = _expand_pattern(raw)
        for dp in deny_patterns:
            m = dp.search(sample)
            if m:
                return sample, m.group(0)
    pytest.skip("no allowlist pattern overlaps with denylist; cannot verify masking")
    return "", ""  # unreachable; pytest.skipで関数は終了する


class TestLoadPatterns:
    """`load_patterns` のテスト。"""

    def test_dictionaries_are_loaded(self, deny_patterns, allow_patterns):
        assert deny_patterns, "denylist 辞書が空"
        assert allow_patterns, "allowlist 辞書が空"

    def test_skips_comments_and_blanks(self, tmp_path: pathlib.Path):
        f = tmp_path / "p.txt"
        f.write_text("# header\n\n[xy]\n   \n# tail\n", encoding="utf-8")
        patterns = _colloquial_check.load_patterns(f)
        assert len(patterns) == 1
        assert patterns[0].search("x")

    def test_skips_invalid_regex(self, tmp_path: pathlib.Path):
        f = tmp_path / "p.txt"
        f.write_text("[unclosed\n", encoding="utf-8")
        assert not _colloquial_check.load_patterns(f)

    def test_missing_file_returns_empty(self, tmp_path: pathlib.Path):
        assert not _colloquial_check.load_patterns(tmp_path / "missing.txt")


class TestFirstHit:
    """`first_hit` のテスト。"""

    def test_detects_isolated_deny(self, deny_patterns, allow_patterns, overlap_sample):
        _, deny_sub = overlap_sample
        text = f"概要は{deny_sub}該当する。"
        assert _colloquial_check.first_hit(text, deny_patterns, allow_patterns)

    def test_swallowed_by_allow(self, deny_patterns, allow_patterns, overlap_sample):
        allow_sample, _ = overlap_sample
        text = f"概要は{allow_sample}該当する。"
        assert not _colloquial_check.first_hit(text, deny_patterns, allow_patterns)

    def test_clean_text(self, deny_patterns, allow_patterns):
        text = "plain ASCII content without Japanese characters.\n"
        assert not _colloquial_check.first_hit(text, deny_patterns, allow_patterns)

    def test_empty_deny_returns_false(self, allow_patterns):
        text = "全てのパターンが未登録なら検出は発生しない"
        assert not _colloquial_check.first_hit(text, [], allow_patterns)


class TestScanText:
    """`scan_text` のテスト。"""

    def test_returns_position_for_match(self, deny_patterns, allow_patterns, overlap_sample):
        _, deny_sub = overlap_sample
        text = f"line1\n本文に{deny_sub}末尾\nline3"
        hits = _colloquial_check.scan_text(text, deny_patterns, allow_patterns)
        assert hits, "検出が無い"
        line_no, col, match_str, snippet = hits[0]
        assert line_no == 2
        assert col >= 1
        assert match_str
        assert "末尾" in snippet

    def test_empty_for_clean_text(self, deny_patterns, allow_patterns):
        assert not _colloquial_check.scan_text("nothing here.\n", deny_patterns, allow_patterns)

    def test_empty_when_no_deny(self, allow_patterns):
        assert not _colloquial_check.scan_text("様々な内容の文字列", [], allow_patterns)


class TestMaskAllowed:
    """`mask_allowed` のテスト。"""

    def test_preserves_length(self, allow_patterns, overlap_sample):
        allow_sample, _ = overlap_sample
        text = f"abc{allow_sample}xyz"
        masked = _colloquial_check.mask_allowed(text, allow_patterns)
        assert len(masked) == len(text)
        assert masked != text  # 少なくとも 1 箇所はマスクされている
        assert masked.startswith("abc")
        assert masked.endswith("xyz")
