"""読込表の解析結果を、本文が変わらない間だけhookの起動をまたいで再利用する契約を確認する。"""

import os
import pathlib
import time
import types

import pytest

from agent_toolkit._common import read_triggers, read_triggers_cache


def _table(material: str, operation: str = "tool:Write") -> str:
    return (
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        f"| 必要な操作 | `{material}` | 操作 | `{operation}` |\n"
    )


@pytest.fixture(name="parsed")
def _parsed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[pathlib.Path]:
    """保存先を隔離し、実際に本文を解析した文書を記録する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    calls: list[pathlib.Path] = []
    original = read_triggers.parse_deferred_text

    def counted(source, *args, **kwargs):
        calls.append(source)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(read_triggers, "parse_deferred_text", counted)
    return calls


def _sources(directory: pathlib.Path, count: int) -> list[pathlib.Path]:
    directory.mkdir(parents=True, exist_ok=True)
    sources = []
    for index in range(count):
        source = directory / f"doc{index}.md"
        source.write_text(_table(f"target{index}.md"), encoding="utf-8")
        # 呼出元と同じく実体のパスで渡す（Windowsの短い名前の一時ディレクトリなど）。
        sources.append(source.resolve())
    return sources


def _parse(sources: list[pathlib.Path], root: pathlib.Path) -> list[tuple[pathlib.Path, list]]:
    with read_triggers.resolution_scope():
        return read_triggers_cache.parse_deferred(sources, root, host="claude", project=str(root))


def test_unchanged_documents_are_not_parsed_again_regardless_of_count(
    tmp_path: pathlib.Path, parsed: list[pathlib.Path]
) -> None:
    """2回目以降のhookでは、文書数を2倍にしても本文が変わらない文書を解析しない。"""
    for count in (20, 40):
        sources = _sources(tmp_path / f"docs{count}", count)
        first = _parse(sources, tmp_path)
        parsed.clear()
        second = _parse(sources, tmp_path)
        assert not parsed
        assert second == first


def test_rewritten_document_is_parsed_with_new_content(tmp_path: pathlib.Path, parsed: list[pathlib.Path]) -> None:
    """書き換えた直後の文書は新しい本文で判定し、他の文書は再利用する。"""
    sources = _sources(tmp_path / "docs", 3)
    _parse(sources, tmp_path)
    sources[1].write_text(_table("changed.md", "tool:Edit"), encoding="utf-8")
    parsed.clear()
    result = dict(_parse(sources, tmp_path))
    assert parsed == [sources[1].resolve()]
    assert [row.operations for row in result[sources[1].resolve()]] == [("tool:Edit",)]
    assert [row.material for row in result[sources[1].resolve()]] == ["`changed.md`"]


def test_excluded_rows_report_the_same_issue_when_reused(tmp_path: pathlib.Path, parsed: list[pathlib.Path]) -> None:
    """ユーザー側の宣言から除いた行の診断は、再利用した要求でも同じく記録する。"""
    skill = tmp_path / ".claude/skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_table("missing.md", "unregistered"), encoding="utf-8")
    issues = []
    for _ in range(2):
        with read_triggers.resolution_scope():
            rows = read_triggers_cache.parse_deferred([skill.resolve()], tmp_path, host="claude", project=str(tmp_path))
            issues.append(read_triggers.declaration_issues())
        assert rows == [(skill.resolve(), [])]
    assert len(parsed) == 1
    assert issues[0] == issues[1]
    assert issues[0]


def _settle(sources: list[pathlib.Path], seconds_ago: int) -> None:
    """文書の最後の書込から、時刻の刻み（ext3は1秒）より十分に時間が過ぎた状態にする。"""
    settled = (time.time_ns() // 1_000_000_000 - seconds_ago) * 1_000_000_000
    for source in sources:
        os.utime(source, ns=(settled, settled))


def test_unchanged_documents_are_not_read_again_regardless_of_count(
    tmp_path: pathlib.Path, parsed: list[pathlib.Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """2回目以降のhookでは、文書数を2倍にしても変わらない文書の本文を読まない。"""
    del parsed
    read: list[pathlib.Path] = []
    original = pathlib.Path.read_bytes

    def counting_read_bytes(self: pathlib.Path) -> bytes:
        read.append(self)
        return original(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting_read_bytes)
    for count in (20, 40):
        sources = _sources(tmp_path / f"docs{count}", count)
        _settle(sources, 60)
        first = _parse(sources, tmp_path)
        read.clear()
        assert _parse(sources, tmp_path) == first
        assert not [path for path in read if path in sources]


def test_document_rewritten_within_the_same_timestamp_tick_is_parsed_with_new_content(
    tmp_path: pathlib.Path, parsed: list[pathlib.Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """更新時刻の刻みが粗いファイルシステムで、直前のhookと同じ刻みの中に同じ大きさで書き換えた文書を新しい本文で判定する。"""
    del parsed
    tick = time.time_ns() // 1_000_000_000 * 1_000_000_000

    def coarse_stat(path):
        status = os.stat(path)
        return types.SimpleNamespace(st_ino=status.st_ino, st_size=status.st_size, st_mtime_ns=tick, st_ctime_ns=tick)

    monkeypatch.setattr(read_triggers_cache, "os", types.SimpleNamespace(stat=coarse_stat))
    sources = _sources(tmp_path / "docs", 2)
    _parse(sources, tmp_path)
    before = sources[0].stat().st_size
    sources[0].write_text(_table("target9.md"), encoding="utf-8")
    assert sources[0].stat().st_size == before
    result = dict(_parse(sources, tmp_path))
    assert [row.material for row in result[sources[0]]] == ["`target9.md`"]
