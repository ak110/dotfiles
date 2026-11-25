import datetime
import os
import pathlib
import time
import types
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, entry_index, wi_operations
from agent_toolkit._atk.serve import state as serve_state


def _write_entry(root: pathlib.Path, state: str, filename: str, body: str) -> pathlib.Path:
    directory = root / "2026" / "10"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(f"---\nstate: {state}\ntype: awi\ntarget_repo: example/{filename}\n---\n\n{body}\n", encoding="utf-8")
    return path


def _age(path: pathlib.Path) -> None:
    past_ns = time.time_ns() - 10_000_000_000
    os.utime(path, ns=(past_ns, past_ns))


def test_index_reuses_unchanged_files_without_reading_or_parsing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _age(_write_entry(tmp_path, "inbox", "one.md", "本文1"))
    _age(_write_entry(tmp_path, "inbox", "two.md", "本文2"))
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
        "---\nstate: inbox\ntype: awi\ntarget_repo: example/changed.md\n---\n\n変更後の長い本文\n",
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
    path = _write_entry(tmp_path, "inbox", "entry.md", "本文")
    original_parse = entry_index.frontmatter.parse_frontmatter

    def moving_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        path.write_text(text.replace("state: inbox", "state: adopted"), encoding="utf-8")
        return original_parse(text)

    monkeypatch.setattr(entry_index.frontmatter, "parse_frontmatter", moving_parse)

    result, warnings = entry_index.EntryIndex(tmp_path).scan(("inbox",))

    assert not result
    assert not warnings


def test_operations_entries_and_target_repos_share_index(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _age(_write_entry(tmp_path, "inbox", "entry.md", "本文"))
    operations = wi_operations.Operations(tmp_path)
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


def test_index_uses_nanosecond_mtime_and_size_as_invalidation_key(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_entry(tmp_path, "inbox", "entry.md", "変更前")
    original_stat = pathlib.Path.stat
    file_stat = path.stat()

    reported_mtime_ns = (time.time_ns() - 10_000_000_000) // 1_000_000_000 * 1_000_000_000 + 1

    def reported_stat(*_args: typing.Any, **_kwargs: typing.Any) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            st_mode=file_stat.st_mode,
            st_mtime_ns=reported_mtime_ns,
            st_mtime=reported_mtime_ns / 1_000_000_000,
            st_size=file_stat.st_size,
            st_ctime_ns=file_stat.st_ctime_ns,
        )

    _patch_stat(monkeypatch, path, original_stat, reported_stat)
    index = entry_index.EntryIndex(tmp_path)
    first, _warnings = index.scan(("inbox",))
    replacement = first[0].text.replace("変更前", "変更後")
    assert len(replacement.encode()) == file_stat.st_size
    path.write_text(replacement, encoding="utf-8")
    reported_mtime_ns += 1

    second, warnings = index.scan(("inbox",))

    assert not warnings
    assert second[0].text.endswith("変更後\n")


def _report_fixed_stat(
    monkeypatch: pytest.MonkeyPatch,
    path: pathlib.Path,
    mtime_ns: int,
) -> None:
    original_stat = pathlib.Path.stat
    file_stat = path.stat()

    def reported_stat(*_args: typing.Any, **_kwargs: typing.Any) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            st_mode=file_stat.st_mode,
            st_mtime_ns=mtime_ns,
            st_mtime=mtime_ns / 1_000_000_000,
            st_size=file_stat.st_size,
            st_ctime_ns=file_stat.st_ctime_ns,
        )

    _patch_stat(monkeypatch, path, original_stat, reported_stat)


def _patch_stat(
    monkeypatch: pytest.MonkeyPatch,
    path: pathlib.Path,
    original_stat: typing.Callable[..., typing.Any],
    reported_stat: typing.Callable[..., typing.Any],
) -> None:
    original_lstat = os.lstat

    def path_stat(candidate: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if candidate == path:
            return reported_stat()
        return original_stat(candidate, *args, **kwargs)

    def lstat(candidate: typing.Any, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if os.fspath(candidate) == os.fspath(path):
            return reported_stat()
        return original_lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", path_stat)
    monkeypatch.setattr(os, "lstat", lstat)


def test_index_rereads_same_size_rewrite_within_mtime_precision(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_entry(tmp_path, "inbox", "entry.md", "VALUE-A")

    _report_fixed_stat(monkeypatch, path, time.time_ns() // 1_000_000_000 * 1_000_000_000)
    index = entry_index.EntryIndex(tmp_path)
    first, _warnings = index.scan(("inbox",))
    assert first[0].text.endswith("VALUE-A\n")
    path.write_text(first[0].text.replace("VALUE-A", "VALUE-B"), encoding="utf-8")

    second, warnings = index.scan(("inbox",))

    assert not warnings
    assert second[0].text.endswith("VALUE-B\n")


def test_index_trusts_parsed_result_after_mtime_precision_window(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_entry(tmp_path, "inbox", "entry.md", "本文")
    mtime_ns = path.stat().st_mtime_ns
    now_ns = mtime_ns + 1_000_000_000

    def time_ns() -> int:
        return now_ns

    monkeypatch.setattr(entry_index, "time", types.SimpleNamespace(time_ns=time_ns, monotonic=time.monotonic))
    original_read_text = pathlib.Path.read_text
    read_calls = 0

    def counting_read_text(candidate: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> str:
        nonlocal read_calls
        read_calls += 1
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", counting_read_text)
    index = entry_index.EntryIndex(tmp_path)

    def reads_during_scan() -> int:
        nonlocal read_calls
        read_calls = 0
        result, warnings = index.scan(("inbox",))
        assert [item.path.name for item in result] == ["entry.md"]
        assert not warnings
        return read_calls

    assert reads_during_scan() == 1
    assert reads_during_scan() == 1
    now_ns = mtime_ns + 2_000_000_000
    assert reads_during_scan() == 1
    assert reads_during_scan() == 0


@pytest.mark.asyncio
async def test_entries_and_repos_api_reflect_same_size_rewrite_within_mtime_precision(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "2026" / "10" / "entry.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nstate: inbox\ntype: awi\ntarget_repo: example/aaaa\n---\n\nBEFORE\n", encoding="utf-8")
    _report_fixed_stat(monkeypatch, path, time.time_ns() // 1_000_000_000 * 1_000_000_000)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), serve_state.ServeState(tmp_path))
    client = app.test_client()
    first_entries = await (await client.get("/api/entries?status=inbox&q=before")).get_json()
    first_repos = await (await client.get("/api/repos?status=inbox")).get_json()
    assert [item["target_repo"] for item in first_entries["entries"]] == ["example/aaaa"]
    assert first_repos["repos"] == ["example/aaaa"]
    path.write_text("---\nstate: inbox\ntype: awi\ntarget_repo: example/bbbb\n---\n\nAFTER!\n", encoding="utf-8")

    stale_search = await (await client.get("/api/entries?status=inbox&q=before")).get_json()
    fresh_search = await (await client.get("/api/entries?status=inbox&q=after!")).get_json()
    repos = await (await client.get("/api/repos?status=inbox")).get_json()

    assert stale_search["entries"] == []
    assert [item["target_repo"] for item in fresh_search["entries"]] == ["example/bbbb"]
    assert repos["repos"] == ["example/bbbb"]


def test_switching_states_keeps_index_of_unscanned_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _age(_write_entry(tmp_path, "inbox", "active.md", "本文"))
    _age(_write_entry(tmp_path, "adopted", "done.md", "本文"))
    operations = wi_operations.Operations(tmp_path)
    operations.entries_with_warnings({"status": "adopted"})
    operations.entries_with_warnings({"status": "active"})
    parse_calls = 0
    original_parse = entry_index.frontmatter.parse_frontmatter

    def counting_parse(text: str) -> tuple[dict[str, typing.Any], str] | None:
        nonlocal parse_calls
        parse_calls += 1
        return original_parse(text)

    monkeypatch.setattr(entry_index.frontmatter, "parse_frontmatter", counting_parse)

    adopted, _warnings = operations.entries_with_warnings({"status": "adopted"})
    active, _warnings = operations.entries_with_warnings({"status": "active"})

    assert [entry["filename"] for entry in adopted] == ["done.md"]
    assert [entry["filename"] for entry in active] == ["active.md"]
    assert parse_calls == 0


def test_target_repos_does_not_reparse_terminal_entries(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    recent = _write_entry(
        tmp_path,
        "adopted",
        "recent.md",
        f"本文\n\n## 処理結果\n\n- 処理日時: {(now - datetime.timedelta(days=1)).isoformat()}\n",
    )
    _age(recent)
    old = _write_entry(
        tmp_path,
        "rejected",
        "old.md",
        f"本文\n\n## 処理結果\n\n- 処理日時: {(now - datetime.timedelta(days=30)).isoformat()}\n",
    )
    old_ns = time.time_ns() - 30 * 24 * 3600 * 1_000_000_000
    os.utime(old, ns=(old_ns, old_ns))
    operations = wi_operations.Operations(tmp_path)
    heading_calls: list[str] = []
    original_headings = wi_operations.top_level_atx_headings

    def counting_headings(text: str, level: int) -> typing.Any:
        heading_calls.append(text)
        return original_headings(text, level)

    monkeypatch.setattr(wi_operations, "top_level_atx_headings", counting_headings)

    assert operations.target_repos("active") == ["example/recent.md"]
    assert operations.target_repos("active") == ["example/recent.md"]
    assert len(heading_calls) == 1


def test_warnings_report_unknown_state_for_unreadable_fixed_entry(tmp_path: pathlib.Path) -> None:
    directory = tmp_path / "2026" / "10"
    directory.mkdir(parents=True)
    broken = directory / "broken.md"
    broken.write_bytes(b"\xff")
    result, warnings = entry_index.EntryIndex(tmp_path).scan(("inbox", "adopted"))
    assert not result
    assert warnings == [
        {
            "state": "unknown",
            "filename": "broken.md",
            "path": str(broken),
            "reason": "UTF-8として読み取れません",
            "next_action": f"{broken}を別名で複製して元の内容を保全し、内容をUTF-8として保存し直す",
        }
    ]


def test_warnings_carry_path_and_fix_action_for_invalid_state(tmp_path: pathlib.Path) -> None:
    path = _write_entry(tmp_path, "bogus", "bad-state.md", "本文")

    result, warnings = entry_index.EntryIndex(tmp_path).scan(("inbox", "adopted"))

    assert not result
    assert len(warnings) == 1
    warning = warnings[0]
    assert (warning["state"], warning["filename"], warning["path"]) == ("unknown", "bad-state.md", str(path))
    assert "stateが不正" in warning["reason"]

    assert "修正する" not in warning["reason"]
    assert str(path) in warning["next_action"]
    assert warning["next_action"].endswith("のいずれかへ修正する")


def test_warnings_carry_path_and_permission_check_for_stat_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ファイル情報を読めない項目の警告は、実際の保存先と、実在・読取権限を確認する操作を持つ。"""
    path = _write_entry(tmp_path, "inbox", "denied.md", "本文")
    original_lstat = os.lstat

    def lstat(candidate: typing.Any, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if os.fspath(candidate) == os.fspath(path):
            raise PermissionError("読取り拒否")
        return original_lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)

    result, warnings = entry_index.EntryIndex(tmp_path).scan(("inbox",))

    assert not result
    assert warnings == [
        {
            "state": "unknown",
            "filename": "denied.md",
            "path": str(path),
            "reason": "ファイル情報を読み取れません",
            "next_action": f"{path}が実在し、atk serveを実行するOSアカウントから読み取れる権限になっているかを確認する",
        }
    ]


def test_tracked_index_skips_walk_until_change_or_rescan_interval(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """変更追跡を有効にした索引は、変更通知が無い間は全件の列挙を省き、通知か一定時間の経過で列挙し直す。

    一覧・検索のたびに全件のファイル情報を取り直すと、高負荷の環境で1要求が数秒かかるためである。
    """
    _age(_write_entry(tmp_path, "inbox", "one.md", "本文1"))
    _age(_write_entry(tmp_path, "adopted", "done.md", "本文2"))
    index = entry_index.EntryIndex(tmp_path)
    index.enable_change_tracking()
    walks = 0
    original_iter = entry_index.iter_entry_paths

    def counting_iter(root: pathlib.Path) -> typing.Iterator[pathlib.Path]:
        nonlocal walks
        walks += 1
        return original_iter(root)

    monkeypatch.setattr(entry_index, "iter_entry_paths", counting_iter)
    clock = [1000.0]
    monkeypatch.setattr(entry_index.time, "monotonic", lambda: clock[0])

    inbox, _ = index.scan(("inbox",))
    adopted, _ = index.scan(("adopted",))
    assert ([item.path.name for item in inbox], [item.path.name for item in adopted], walks) == (["one.md"], ["done.md"], 1)

    _age(_write_entry(tmp_path, "inbox", "two.md", "本文3"))
    stale, _ = index.scan(("inbox",))
    index.mark_changed()
    fresh, _ = index.scan(("inbox",))
    assert ([item.path.name for item in stale], sorted(item.path.name for item in fresh), walks) == (
        ["one.md"],
        ["one.md", "two.md"],
        2,
    )

    clock[0] += entry_index._RESCAN_SECONDS + 1  # pylint: disable=protected-access
    index.scan(("inbox",))
    assert walks == 3


def test_untracked_index_walks_every_scan(tmp_path: pathlib.Path) -> None:
    _age(_write_entry(tmp_path, "inbox", "one.md", "本文1"))
    index = entry_index.EntryIndex(tmp_path)
    index.scan(("inbox",))
    _age(_write_entry(tmp_path, "inbox", "two.md", "本文2"))
    entries, _ = index.scan(("inbox",))
    assert sorted(item.path.name for item in entries) == ["one.md", "two.md"]


def test_tracked_index_rereads_only_pending_entries_after_write(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _age(_write_entry(tmp_path, "inbox", "old.md", "本文1"))
    fresh = _write_entry(tmp_path, "inbox", "new.md", "書込直後")
    index = entry_index.EntryIndex(tmp_path)
    index.enable_change_tracking()
    walks = 0
    original_iter = entry_index.iter_entry_paths

    def counting_iter(root: pathlib.Path) -> typing.Iterator[pathlib.Path]:
        nonlocal walks
        walks += 1
        return original_iter(root)

    monkeypatch.setattr(entry_index, "iter_entry_paths", counting_iter)
    index.scan(("inbox",))

    fresh.write_text(fresh.read_text(encoding="utf-8").replace("書込直後", "書換え後"), encoding="utf-8")
    entries, _ = index.scan(("inbox",))
    assert (sorted(item.path.name for item in entries), walks) == (["new.md", "old.md"], 1)
    assert "書換え後" in next(item.text for item in entries if item.path.name == "new.md")

    fresh.unlink()
    entries, _ = index.scan(("inbox",))
    assert ([item.path.name for item in entries], walks) == (["old.md"], 1)


def test_saved_index_skips_parsing_after_restart_and_reads_body_on_demand(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = tmp_path / "notes"
    cache = tmp_path / "state" / "wi-entry-index.json"
    stable = _write_entry(notes, "inbox", "stable.md", "変わらない本文")
    changed = _write_entry(notes, "inbox", "changed.md", "前の本文")
    for path in (stable, changed):
        _age(path)
    first, _ = entry_index.EntryIndex(notes, cache_path=cache).scan(("inbox",))
    assert cache.is_file()
    _write_entry(notes, "processing", "changed.md", "後の本文に変えた")
    _age(changed)
    parsed: list[str] = []
    read: list[pathlib.Path] = []
    original_parse = entry_index.frontmatter.parse_frontmatter
    original_read = pathlib.Path.read_text

    def recording_parse(text: str) -> typing.Any:
        parsed.append(text)
        return original_parse(text)

    def recording_read(self: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> str:
        if self.suffix == ".md":
            read.append(self)
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(entry_index.frontmatter, "parse_frontmatter", recording_parse)
    monkeypatch.setattr(pathlib.Path, "read_text", recording_read)
    restarted = entry_index.EntryIndex(notes, cache_path=cache)
    inbox, _ = restarted.scan(("inbox",))
    processing, _ = restarted.scan(("processing",))
    assert [entry.path for entry in inbox] == [stable]
    assert [entry.path for entry in processing] == [changed]
    assert read == [changed] and len(parsed) == 1
    assert inbox[0].metadata == first[[entry.path for entry in first].index(stable)].metadata
    assert inbox[0].body == "\n変わらない本文\n"
    assert read == [changed, stable]
    assert processing[0].body == "\n後の本文に変えた\n"


def test_body_read_after_rewrite_follows_new_frontmatter(tmp_path: pathlib.Path) -> None:
    notes = tmp_path / "notes"
    cache = tmp_path / "state" / "wi-entry-index.json"
    path = _write_entry(notes, "inbox", "entry.md", "本文")
    _age(path)
    entry_index.EntryIndex(notes, cache_path=cache).scan(("inbox",))
    restarted, _ = entry_index.EntryIndex(notes, cache_path=cache).scan(("inbox",))
    path.write_text("---\nstate: inbox\ntype: awi\nextra: frontmatterを長くした\n---\n\n書き換えた本文\n", encoding="utf-8")
    assert restarted[0].body == "\n書き換えた本文\n"


def test_preloaded_texts_let_first_search_after_restart_skip_reading_bodies(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = tmp_path / "notes"
    cache = tmp_path / "state" / "wi-entry-index.json"
    for index in range(3):
        _age(_write_entry(notes, "inbox", f"entry-{index}.md", f"本文{index} 検索語{index}"))
    wi_operations.Operations(notes, index_cache_path=cache).warm_index()
    assert cache.is_file()
    restarted = wi_operations.Operations(notes, index_cache_path=cache)
    restarted.warm_index()
    restarted.preload_entry_texts()
    read: list[pathlib.Path] = []
    original_read = pathlib.Path.read_text

    def recording_read(self: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> str:
        if self.suffix == ".md":
            read.append(self)
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", recording_read)
    found, _warnings = restarted.entries_with_warnings({"q": "検索語1"})
    assert [item["filename"] for item in found] == ["entry-1.md"]
    assert not read
