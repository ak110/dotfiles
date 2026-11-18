"""読込表の解析結果を、本文が変わらない間だけhookの起動をまたいで再利用する契約を確認する。"""

import pathlib

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
        sources.append(source)
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
            rows = read_triggers_cache.parse_deferred([skill], tmp_path, host="claude", project=str(tmp_path))
            issues.append(read_triggers.declaration_issues())
        assert rows == [(skill.resolve(), [])]
    assert len(parsed) == 1
    assert issues[0] == issues[1]
    assert issues[0]
