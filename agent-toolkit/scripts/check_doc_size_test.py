"""check_doc_size.pyのユニットテスト。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
import check_doc_size  # noqa: E402


def _write(tmp_path: pathlib.Path, name: str, line_count: int) -> pathlib.Path:
    path = tmp_path / name
    path.write_text("x\n" * line_count, encoding="utf-8")
    return path


def test_at_limit_passes(tmp_path):
    path = _write(tmp_path, "at_limit.md", check_doc_size.LIMIT)
    assert check_doc_size.main([str(path)]) == 0


def test_over_limit_fails(tmp_path, capsys):
    path = _write(tmp_path, "over.md", check_doc_size.LIMIT + 1)
    assert check_doc_size.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert "違反" in captured.err


def test_short_passes(tmp_path):
    path = _write(tmp_path, "short.md", 1)
    assert check_doc_size.main([str(path)]) == 0


def test_missing_file_fails(tmp_path):
    missing = tmp_path / "missing.md"
    assert check_doc_size.main([str(missing)]) == 1
