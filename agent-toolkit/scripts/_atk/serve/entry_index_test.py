"""ワークアイテム一覧のプロセス内索引のテスト。"""

import os
import pathlib
import typing

import pytest

from _atk.serve import app as serve_app
from _atk.serve import entry_index


def _write_entry(root: pathlib.Path, state: str, filename: str, body: str) -> pathlib.Path:
    directory = root / state
    directory.mkdir(exist_ok=True)
    path = directory / filename
    path.write_text(f"---\ntype: awi\ntarget_repo: example/{filename}\n---\n\n{body}\n", encoding="utf-8")
    return path


def test_index_reuses_unchanged_files_without_reading_or_parsing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ無効化キーを持つ2回目の走査は本文を読み直さず解析もしない。"""
    _write_entry(tmp_path, "inbox", "one.md", "本文1")
    _write_entry(tmp_path, "inbox", "two.md", "本文2")
    index = entry_index.EntryIndex(tmp_path)
    first, warnings = index.scan(("inbox",))
    assert len(first) == 2
    assert not warnings

    original_read_text = pathlib.Path.read_text
    original_parse = entry_index.frontmatter.parse_frontmatter
    read_calls = 0
    parse_calls = 0

    def counting_read_text(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> str:
        nonlocal read_calls
        read_calls += 1
        return original_read_text(path, *args, **kwargs)

    def counting_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(text)

    monkeypatch.setattr(pathlib.Path, "read_text", counting_read_text)
    monkeypatch.setattr(entry_index.frontmatter, "parse_frontmatter", counting_parse)

    second, second_warnings = index.scan(("inbox",))

    assert [item.path.name for item in second] == ["one.md", "two.md"]
    assert not second_warnings
    assert read_calls == 0
    assert parse_calls == 0


def test_index_invalidates_changed_file_and_removes_deleted_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変更した1件だけを再解析し、消えたファイルを次の結果から除く。"""
    changed = _write_entry(tmp_path, "inbox", "changed.md", "変更前")
    deleted = _write_entry(tmp_path, "inbox", "deleted.md", "削除前")
    index = entry_index.EntryIndex(tmp_path)
    index.scan(("inbox",))
    original_parse = entry_index.frontmatter.parse_frontmatter
    parse_calls = 0

    def counting_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(text)

    monkeypatch.setattr(entry_index.frontmatter, "parse_frontmatter", counting_parse)
    changed.write_text(
        "---\ntype: awi\ntarget_repo: example/changed.md\n---\n\n変更後の長い本文\n",
        encoding="utf-8",
    )
    deleted.unlink()

    result, warnings = index.scan(("inbox",))

    assert [item.path.name for item in result] == ["changed.md"]
    assert result[0].text.endswith("変更後の長い本文\n")
    assert not warnings
    assert parse_calls == 1


def test_index_omits_file_moved_during_parse(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解析中に別の状態へ移動したファイルを元の状態の結果へ含めない。"""
    path = _write_entry(tmp_path, "inbox", "entry.md", "本文")
    (tmp_path / "adopted").mkdir()
    original_parse = entry_index.frontmatter.parse_frontmatter

    def moving_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        path.rename(tmp_path / "adopted" / path.name)
        return original_parse(text)

    monkeypatch.setattr(entry_index.frontmatter, "parse_frontmatter", moving_parse)

    result, warnings = entry_index.EntryIndex(tmp_path).scan(("inbox",))

    assert not result
    assert not warnings


def test_operations_entries_and_target_repos_share_index(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一覧の直後の候補取得は同じ索引を使い、frontmatterを再解析しない。"""
    _write_entry(tmp_path, "inbox", "entry.md", "本文")
    operations = serve_app.Operations(tmp_path)
    entries, warnings = operations.entries_with_warnings({"status": "active"})
    assert len(entries) == 1
    assert not warnings
    parse_calls = 0
    original_parse = entry_index.frontmatter.parse_frontmatter

    def counting_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(text)

    monkeypatch.setattr(entry_index.frontmatter, "parse_frontmatter", counting_parse)

    assert operations.target_repos("active") == ["example/entry.md"]
    assert parse_calls == 0


def test_index_uses_nanosecond_mtime_and_size_as_invalidation_key(tmp_path: pathlib.Path) -> None:
    """秒単位の更新日時が同じでもナノ秒値の変化で本文を更新する。"""
    path = _write_entry(tmp_path, "inbox", "entry.md", "変更前")
    index = entry_index.EntryIndex(tmp_path)
    first, _warnings = index.scan(("inbox",))
    before = path.stat()
    replacement = first[0].text.replace("変更前", "変更後")
    assert len(replacement.encode()) == before.st_size
    path.write_text(replacement, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1))

    second, warnings = index.scan(("inbox",))

    assert not warnings
    assert second[0].text.endswith("変更後\n")
