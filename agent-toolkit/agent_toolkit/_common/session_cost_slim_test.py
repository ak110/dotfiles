"""縮約記録から求めた家族の推定費用が、全行の解析と一致し、追記分だけを解析することを検証する。"""

import json
import os
import pathlib
import typing

import pytest

from agent_toolkit._common import session_cost_records, session_cost_slim
from agent_toolkit._common import session_family as family
from agent_toolkit._testing import session_tree

_MODEL = "claude-opus-5-5"


def _append(path: pathlib.Path, *records: dict[str, typing.Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _usage(message_id: str, tokens: int, *, tool_use: dict[str, typing.Any] | None = None) -> dict[str, typing.Any]:
    content: list[dict[str, typing.Any]] = [{"type": "text", "text": "応答" * 50}]
    if tool_use is not None:
        content.append(tool_use)
    return {
        "type": "assistant",
        "timestamp": "2026-09-10T00:00:05Z",
        "message": {
            "id": message_id,
            "model": _MODEL,
            "usage": {"input_tokens": tokens, "output_tokens": 10},
            "content": content,
        },
    }


def _homes(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    return {"claude_home": tmp_path / "claude", "codex_home": tmp_path / "codex", "state_root": tmp_path / "state"}


def _full(path: pathlib.Path, homes: dict[str, pathlib.Path]) -> session_cost_records.DetailCost:
    loaded = family._load_records_checked(str(path))  # pylint: disable=protected-access
    assert loaded.records is not None
    return session_cost_records.estimate_detail_family(
        "claude",
        path,
        [record.entry for record in loaded.records],
        include_descendants=True,
        claude_home=homes["claude_home"],
        codex_home=homes["codex_home"],
        state_root=homes["state_root"],
    )


def _slim(
    path: pathlib.Path, homes: dict[str, pathlib.Path], store: session_cost_slim.SlimRecordStore
) -> session_cost_records.DetailCost:
    records = store.records(str(path))
    assert records is not None
    return session_cost_records.estimate_detail_family(
        "claude",
        path,
        [record.entry for record in records],
        include_descendants=True,
        claude_home=homes["claude_home"],
        codex_home=homes["codex_home"],
        state_root=homes["state_root"],
        record_loader=store.loader,
    )


def _same(left: session_cost_records.DetailCost, right: session_cost_records.DetailCost) -> None:
    assert left.cost == right.cost
    assert left.members == right.members
    assert left.complete == right.complete


@pytest.fixture(name="tree")
def _tree(tmp_path: pathlib.Path) -> session_tree.SessionTree:
    """Claude Codeの親、サブエージェント、Codexの委譲先と使用量・累積額・タスク完了通知を持つ家族を作る。"""
    tree = session_tree.write_session_tree(tmp_path / "claude", tmp_path / "codex", tmp_path / "state")
    parent = tree.paths[session_tree.CLAUDE_PARENT_ID]
    _append(
        parent,
        _usage("msg-1", 1000),
        {"type": "attachment", "timestamp": "2026-09-10T00:00:06Z", "content": "<task-notification>無関係</task-notification>"},
        {"type": "cost-state", "timestamp": "2026-09-10T00:00:07Z", "totalCostUSD": 0.5},
        _usage("msg-2", 2000),
    )
    _append(tree.paths[session_tree.SUBAGENT_ID], _usage("sub-1", 300))
    _append(
        tree.paths[session_tree.CODEX_CHILD_ID],
        {"type": "turn_context", "payload": {"model": "gpt-5.5", "cwd": "/work", "instructions": "長い指示" * 100}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {"input_tokens": 500, "cached_input_tokens": 100, "output_tokens": 20},
                    "total_token_usage": {"total_tokens": 520},
                    "model_context_window": 400000,
                },
            },
        },
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "CommandExecution", "output": "x" * 500}}},
    )
    return tree


def test_slim_family_cost_matches_full_parse(tmp_path: pathlib.Path, tree: session_tree.SessionTree) -> None:
    """縮約記録から求めた推定費用と家族が全行の解析と一致し、使わない行と項目を保持しない。"""
    homes = _homes(tmp_path)
    parent = tree.paths[session_tree.CLAUDE_PARENT_ID]
    store = session_cost_slim.SlimRecordStore(tmp_path / "slim")
    expected = _full(parent, homes)
    assert len(expected.members) == 4
    _same(_slim(parent, homes, store), expected)
    child = store.records(str(tree.paths[session_tree.CODEX_CHILD_ID]))
    assert child is not None
    kinds = [(record.entry.get("type"), record.entry.get("payload", {}).get("type")) for record in child]
    assert ("event_msg", "item_completed") not in kinds
    turn = next(record.entry for record in child if record.entry.get("type") == "turn_context")
    assert turn["payload"] == {"model": "gpt-5.5"}


def test_appended_lines_are_parsed_incrementally(
    tmp_path: pathlib.Path, tree: session_tree.SessionTree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """記録への追記は追記分だけを解析して縮約記録へ加え、追記で増えた委譲先も家族へ加わる。"""
    homes = _homes(tmp_path)
    parent = tree.paths[session_tree.CLAUDE_PARENT_ID]
    store = session_cost_slim.SlimRecordStore(tmp_path / "slim")
    _slim(parent, homes, store)
    _append(
        parent,
        _usage(
            "msg-3", 4000, tool_use={"type": "tool_use", "id": "call-late", "name": "mcp__agents_server__start", "input": {}}
        ),
        {
            "type": "user",
            "timestamp": "2026-09-10T00:00:08Z",
            "toolUseResult": {"session_id": session_tree.CLAUDE_CHILD_ID},
            "message": {"content": [{"type": "tool_result", "tool_use_id": "call-late", "content": "起動"}]},
        },
    )
    sliced: list[bytes] = []
    original = session_cost_slim._slim_entry  # pylint: disable=protected-access

    def counting(raw: bytes, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        sliced.append(raw)
        return original(raw, *args, **kwargs)

    monkeypatch.setattr(session_cost_slim, "_slim_entry", counting)
    result = _slim(parent, homes, store)
    monkeypatch.setattr(session_cost_slim, "_slim_entry", original)
    # 縮約するのは追記した2行と、追記で家族に加わった委譲先の記録（1行）だけで、既存の記録と追記前の行は保存した縮約記録を使う。
    child_lines = tree.paths[session_tree.CLAUDE_CHILD_ID].read_bytes().splitlines()
    assert sliced == parent.read_bytes().splitlines()[-2:] + child_lines
    expected = _full(parent, homes)
    assert tree.paths[session_tree.CLAUDE_CHILD_ID] in expected.members
    _same(result, expected)


def test_saved_slim_records_are_reused_after_restart(
    tmp_path: pathlib.Path, tree: session_tree.SessionTree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """保存した縮約記録は別のプロセス（新しい保持先）でも記録を読み直さずに使う。"""
    homes = _homes(tmp_path)
    parent = tree.paths[session_tree.CLAUDE_PARENT_ID]
    expected = _slim(parent, homes, session_cost_slim.SlimRecordStore(tmp_path / "slim"))

    def unexpected(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        raise AssertionError("保存した縮約記録を使わずに記録を解析し直した")

    monkeypatch.setattr(session_cost_slim.SlimRecordStore, "_extend", unexpected)
    _same(_slim(parent, homes, session_cost_slim.SlimRecordStore(tmp_path / "slim")), expected)


def test_rewritten_record_is_rebuilt(tmp_path: pathlib.Path, tree: session_tree.SessionTree) -> None:
    """追記で説明できない書き換え（縮小、解析済み部分の変更）は、縮約記録を最初から作り直す。"""
    homes = _homes(tmp_path)
    parent = tree.paths[session_tree.CLAUDE_PARENT_ID]
    store = session_cost_slim.SlimRecordStore(tmp_path / "slim")
    _slim(parent, homes, store)
    text = parent.read_text(encoding="utf-8")
    stamp = parent.stat().st_mtime_ns
    parent.write_text(text.replace('"input_tokens": 2000', '"input_tokens": 9000'), encoding="utf-8")
    os.utime(parent, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))
    _same(_slim(parent, homes, store), _full(parent, homes))
    parent.write_text("".join(text.splitlines(keepends=True)[:3]), encoding="utf-8")
    _same(_slim(parent, homes, store), _full(parent, homes))


@pytest.mark.parametrize("tail", ['{"type": "assistant", "message"', "not json\n"])
def test_record_with_partial_or_broken_line_is_read_in_full(
    tmp_path: pathlib.Path, tree: session_tree.SessionTree, tail: str
) -> None:
    """書き込み途中の末尾や解析できない行を含む記録は縮約せず、読込は従来の全行の読込と同じ結果を返す。"""
    child = tree.paths[session_tree.CODEX_CHILD_ID]
    with child.open("a", encoding="utf-8") as stream:
        stream.write(tail)
    store = session_cost_slim.SlimRecordStore(tmp_path / "slim")
    assert store.records(str(child)) is None
    assert store.loader(str(child)) == family._load_records_checked(str(child))  # pylint: disable=protected-access


def test_prune_removes_oldest_files_over_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """保存先の縮約記録が上限を超えると、更新の古いものから消す。"""
    directory = tmp_path / "slim"
    directory.mkdir()
    for index in range(5):
        file = directory / f"{index}.json.z"
        file.write_bytes(b"x")
        os.utime(file, ns=(index * 1_000_000_000, index * 1_000_000_000))
    monkeypatch.setattr(session_cost_slim, "_MAX_FILES", 3)
    session_cost_slim.SlimRecordStore(directory).prune()
    assert sorted(path.name for path in directory.iterdir()) == ["2.json.z", "3.json.z", "4.json.z"]
