"""ワークアイテム一覧のプロセス内索引のテスト。"""

import contextlib
import datetime
import os
import pathlib
import time
import types
import typing

import pytest

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, entry_index
from agent_toolkit._atk.serve import state as serve_state


def _write_entry(root: pathlib.Path, state: str, filename: str, body: str) -> pathlib.Path:
    directory = root / state
    directory.mkdir(exist_ok=True)
    path = directory / filename
    path.write_text(f"---\ntype: awi\ntarget_repo: example/{filename}\n---\n\n{body}\n", encoding="utf-8")
    return path


def _age(path: pathlib.Path) -> None:
    """更新時刻を信用判定の幅より十分前へ移す。"""
    past_ns = time.time_ns() - 10_000_000_000
    os.utime(path, ns=(past_ns, past_ns))


def test_index_reuses_unchanged_files_without_reading_or_parsing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """更新から2秒より後に解析した同じ無効化キーのファイルは、2回目の走査で読み直さず解析もしない。"""
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
    _age(_write_entry(tmp_path, "inbox", "entry.md", "本文"))
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


def test_index_uses_nanosecond_mtime_and_size_as_invalidation_key(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """信用済みの解析結果でも、秒単位の更新日時が同じままナノ秒値が変われば本文を更新する。"""
    path = _write_entry(tmp_path, "inbox", "entry.md", "変更前")
    original_stat = pathlib.Path.stat
    original_scandir = os.scandir
    file_stat = path.stat()
    # 信用判定で読み直さないよう、報告する更新時刻を信用判定の幅より前へ置く。
    reported_mtime_ns = (time.time_ns() - 10_000_000_000) // 1_000_000_000 * 1_000_000_000 + 1

    def reported_stat(*_args: typing.Any, **_kwargs: typing.Any) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            st_mtime_ns=reported_mtime_ns,
            st_mtime=reported_mtime_ns / 1_000_000_000,
            st_size=file_stat.st_size,
        )

    def path_stat(candidate: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if candidate == path:
            return reported_stat()
        return original_stat(candidate, *args, **kwargs)

    @contextlib.contextmanager
    def scandir(directory: pathlib.Path) -> typing.Iterator[list[types.SimpleNamespace]]:
        with original_scandir(directory) as entries:
            yield [types.SimpleNamespace(name=entry.name, path=entry.path, stat=reported_stat) for entry in entries]

    monkeypatch.setattr(pathlib.Path, "stat", path_stat)
    monkeypatch.setattr(os, "scandir", scandir)
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
    """対象ファイルの`stat`の報告値を、書き換え後も同じ更新時刻と書き換え前のサイズへ固定する。"""
    original_stat = pathlib.Path.stat
    original_scandir = os.scandir
    size = path.stat().st_size

    def reported_stat(*_args: typing.Any, **_kwargs: typing.Any) -> types.SimpleNamespace:
        return types.SimpleNamespace(st_mtime_ns=mtime_ns, st_mtime=mtime_ns / 1_000_000_000, st_size=size)

    def path_stat(candidate: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if candidate == path:
            return reported_stat()
        return original_stat(candidate, *args, **kwargs)

    @contextlib.contextmanager
    def scandir(directory: pathlib.Path) -> typing.Iterator[list[typing.Any]]:
        with original_scandir(directory) as entries:
            yield [
                types.SimpleNamespace(name=entry.name, path=entry.path, stat=reported_stat)
                if pathlib.Path(entry.path) == path
                else entry
                for entry in entries
            ]

    monkeypatch.setattr(pathlib.Path, "stat", path_stat)
    monkeypatch.setattr(os, "scandir", scandir)


def test_index_rereads_same_size_rewrite_within_mtime_precision(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """時刻精度の範囲内で更新時刻とサイズが変わらない書き換えも、次の走査で新しい本文を返す。"""
    path = _write_entry(tmp_path, "inbox", "entry.md", "VALUE-A")
    # 秒単位の精度のファイルシステムで、書き換え前後に同じ値が報告される状況を再現する。
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
    """更新から2秒以内の走査は読み直し、2秒経過後に読み直した結果は以降の走査で再利用する。"""
    path = _write_entry(tmp_path, "inbox", "entry.md", "本文")
    mtime_ns = path.stat().st_mtime_ns
    now_ns = mtime_ns + 1_000_000_000

    def time_ns() -> int:
        return now_ns

    monkeypatch.setattr(entry_index, "time", types.SimpleNamespace(time_ns=time_ns))
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
    """一覧・検索・対象リポジトリ候補のAPIは、時刻精度の範囲内の同サイズの書き換え後に新しい値を返す。"""
    path = tmp_path / "inbox" / "entry.md"
    path.parent.mkdir()
    path.write_text("---\ntype: awi\ntarget_repo: example/aaaa\n---\n\nBEFORE\n", encoding="utf-8")
    _report_fixed_stat(monkeypatch, path, time.time_ns() // 1_000_000_000 * 1_000_000_000)
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), serve_state.ServeState(tmp_path))
    client = app.test_client()
    first_entries = await (await client.get("/api/entries?status=inbox&q=before")).get_json()
    first_repos = await (await client.get("/api/repos?status=inbox")).get_json()
    assert [item["target_repo"] for item in first_entries["entries"]] == ["example/aaaa"]
    assert first_repos["repos"] == ["example/aaaa"]
    path.write_text("---\ntype: awi\ntarget_repo: example/bbbb\n---\n\nAFTER!\n", encoding="utf-8")

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
    """一覧の状態をactiveとadoptedで交互に切り替えても、走査していない状態の索引を保持して再利用する。"""
    _age(_write_entry(tmp_path, "inbox", "active.md", "本文"))
    _age(_write_entry(tmp_path, "adopted", "done.md", "本文"))
    operations = serve_app.Operations(tmp_path)
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
    """直近7日の終端項目の判定は、変更の無いファイルの処理日時を再解析せず、境界より十分前に更新したファイルを解析しない。"""
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
    operations = serve_app.Operations(tmp_path)
    heading_calls: list[str] = []
    original_headings = serve_app.top_level_atx_headings

    def counting_headings(text: str, level: int) -> typing.Any:
        heading_calls.append(text)
        return original_headings(text, level)

    monkeypatch.setattr(serve_app, "top_level_atx_headings", counting_headings)

    assert operations.target_repos("active") == ["example/recent.md"]
    assert operations.target_repos("active") == ["example/recent.md"]
    assert len(heading_calls) == 1
