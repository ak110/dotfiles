"""agent-toolkit/skills/writing-standards/scripts/check_colloquial.py のテスト。

CLI スクリプトの動作確認。subprocess 経由で起動し exit code・stderr を検証する。
辞書ファイルから動的にサンプルを生成するため、テスト本体には口語表現を直接書かない。
"""

import pathlib
import re
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "check_colloquial.py"
_DENY_PATH = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "_colloquial_words.txt"
_ALLOW_PATH = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "_colloquial_words_allow.txt"


def _expand(pattern_str: str) -> str:
    return re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], pattern_str)


@pytest.fixture(name="deny_substring")
def _deny_substring() -> str:
    """辞書ファイルからdeny検出に当たる最短サンプルを動的生成する。"""
    deny_patterns: list[re.Pattern[str]] = []
    for line in _DENY_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            deny_patterns.append(re.compile(stripped))
    for line in _ALLOW_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sample = _expand(stripped)
        for dp in deny_patterns:
            m = dp.search(sample)
            if m:
                return m.group(0)
    pytest.skip("no overlap between denylist and allowlist; cannot generate test sample")
    return ""  # unreachable


def _run(*paths: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_detects_violation(tmp_path: pathlib.Path, deny_substring: str):
    target = tmp_path / "doc.md"
    target.write_text(f"# 表題\n\n概要は{deny_substring}該当する。\n", encoding="utf-8")
    result = _run(target)
    assert result.returncode == 1
    assert "colloquial" in result.stderr
    assert ":3:" in result.stderr


def test_clean_file_passes(tmp_path: pathlib.Path):
    target = tmp_path / "clean.md"
    target.write_text("# header\n\nplain ASCII content here.\n", encoding="utf-8")
    result = _run(target)
    assert result.returncode == 0
    assert result.stderr == ""


def test_missing_file_silently_skipped(tmp_path: pathlib.Path):
    result = _run(tmp_path / "nope.md")
    assert result.returncode == 0


def test_multiple_files_aggregates(tmp_path: pathlib.Path, deny_substring: str):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text(f"概要は{deny_substring}該当する。\n", encoding="utf-8")
    b.write_text("clean.\n", encoding="utf-8")
    result = _run(a, b)
    assert result.returncode == 1
    assert str(a) in result.stderr
    assert str(b) not in result.stderr  # b は検出無し


def test_directory_recurses(tmp_path: pathlib.Path, deny_substring: str):
    """ディレクトリを渡すと再帰的に対象拡張子のファイルを走査する。"""
    sub = tmp_path / "docs"
    sub.mkdir()
    target = sub / "note.md"
    other = sub / "other.txt"
    target.write_text(f"概要は{deny_substring}該当する。\n", encoding="utf-8")
    other.write_text(f"説明は{deny_substring}記載する。\n", encoding="utf-8")
    # 対象外拡張子は無視される
    skipped = sub / "ignore.bin"
    skipped.write_text(f"{deny_substring}\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert str(target) in result.stderr
    assert str(other) in result.stderr
    assert str(skipped) not in result.stderr


def test_directory_excludes_known_dirs(tmp_path: pathlib.Path, deny_substring: str):
    """`.git`等の既知の除外ディレクトリ配下はスキャン対象外。"""
    for excluded in (".git", ".venv", "node_modules", "__pycache__"):
        d = tmp_path / excluded
        d.mkdir()
        (d / "x.md").write_text(f"{deny_substring}\n", encoding="utf-8")
    target = tmp_path / "kept.md"
    target.write_text(f"概要は{deny_substring}該当する。\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert str(target) in result.stderr
    for excluded in (".git", ".venv", "node_modules", "__pycache__"):
        assert excluded not in result.stderr


def test_dictionary_files_are_skipped():
    """辞書ファイル本体を直接渡しても検査されず exit 0（自己誘発検出を避ける）。"""
    result = _run(_DENY_PATH, _ALLOW_PATH)
    assert result.returncode == 0
    assert result.stderr == ""
