"""委譲先の記録解決を公開CLIと一時的な会話・登録簿で検証する。"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import typing

import pytest
import session_evidence_ancestors as ancestors
import session_review_evidence as evidence

from agent_toolkit._atk.serve import session_watch
from agent_toolkit._common import session_launchers, state_paths
from agent_toolkit._testing.session_evidence_support import read_jsonl, write_jsonl


def _claude_record(root: pathlib.Path, identity: str, child: str | None = None) -> pathlib.Path:
    # 既存session_evidence_records_test.pyと同じClaude Codeのtool_use/tool_result形式。
    entries: list[dict] = [{"type": "user", "message": {"role": "user", "content": f"{identity}の発話"}}]
    if child:
        entries.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "start", "name": "mcp__agents_server__start", "input": {}}],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "start", "content": json.dumps({"session_id": child})}
                        ],
                    },
                    "toolUseResult": {"session_id": child},
                },
            ]
        )
    path = root / "projects" / "repo" / f"{identity}.jsonl"
    write_jsonl(path, entries)
    return path


@pytest.fixture(name="record_roots")
def _record_roots(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "child")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    return claude, codex


@pytest.mark.parametrize("codex_child", [False, True])
@pytest.mark.parametrize("stale_registry", [False, True])
def test_delegation_record_selects_parent_and_root_after_clear(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    codex_child: bool,
    stale_registry: bool,
) -> None:
    """二段の委譲と古い登録簿でも起動結果を優先し、直接指定と同じrecordを返す。"""
    claude, codex = record_roots
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "old" if stale_registry else "root")
    child_id = "11111111-1111-4111-8111-111111111111" if codex_child else "child"
    root = _claude_record(claude, "root", "parent")
    parent = _claude_record(claude, "parent", child_id)
    _claude_record(claude, "old")
    if codex_child:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION")
        monkeypatch.setenv("CODEX_THREAD_ID", child_id)
        write_jsonl(
            codex / "sessions" / "2026" / "10" / "18" / f"rollout-2026-10-18T00-00-00-{child_id}.jsonl",
            [
                {"type": "session_meta", "payload": {"id": child_id}},
            ],
        )
    else:
        _claude_record(claude, "child")
    directory = session_launchers.registry_directory(state_paths.state_dir())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{child_id}.json").write_text(
        json.dumps({"launcher_session_id": "old" if stale_registry else "parent"}), encoding="utf-8"
    )
    (directory / "parent.json").write_text(json.dumps({"launcher_session_id": "root"}), encoding="utf-8")
    for scope, path in (("parent", parent), ("root", root)):
        assert evidence.main(["--delegation-record", scope, "--user-events"]) == 0
        resolved = read_jsonl(capsys, raw=True)
        assert any(item["kind"] == "record-provenance" and item.get("role") == "main" for item in resolved)
        assert evidence.main(["--transcript", str(path), "--user-events"]) == 0
        assert [item for item in resolved if item["kind"] != "record-provenance"] == read_jsonl(capsys, raw=True)
    assert evidence.main(["--delegation-record", "parent", "--tool-calls"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert any(item.get("record") == "claude:parent" and item["kind"] == "tool-call" for item in events)


def test_codex_launcher_resolves_without_registry(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexメインの起動結果だけでClaudeの委譲先から会話を解決する。"""
    claude, codex = record_roots
    _claude_record(claude, "child")
    path = codex / "sessions" / "2026" / "10" / "18" / "rollout-root.jsonl"
    write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "root"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "元の要求"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "start",
                    "arguments": "{}",
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "start", "output": json.dumps({"session_id": "child"})},
            },
        ],
    )
    assert evidence.main(["--delegation-record", "root", "--user-events"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert any(item.get("record") == "codex:root" and item.get("text") == "元の要求" for item in events)


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("codex_child", [False, True])
def test_delegation_record_retains_no_display_text(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    registered: bool,
    codex_child: bool,
) -> None:
    """登録簿経路と64件の全探索で親子を保ち、保持する走査結果を本文長から独立させる。"""
    claude, codex = record_roots
    child_id = "11111111-1111-4111-8111-111111111111" if codex_child else "child"
    root = _claude_record(claude, "root", "parent")
    parent = _claude_record(claude, "parent", child_id)
    if codex_child:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION")
        monkeypatch.setenv("CODEX_THREAD_ID", child_id)
        child = codex / "sessions/2026/10/18" / f"rollout-2026-10-18-{child_id}.jsonl"
        write_jsonl(
            child,
            [
                {"type": "session_meta", "payload": {"id": child_id}},
                {"type": "response_item", "payload": {"role": "user", "content": "子の発話"}},
            ],
        )
    else:
        child = _claude_record(claude, "child")
    paths = [root, parent, child]
    for number in range(61):
        if number % 2:
            path = codex / "sessions/2026/10/18" / f"rollout-filler-{number:03d}.jsonl"
            write_jsonl(
                path,
                [
                    {"type": "session_meta", "payload": {"id": f"filler-{number:03d}"}},
                    {"type": "response_item", "payload": {"role": "user", "content": "発話"}},
                ],
            )
        else:
            path = _claude_record(claude, f"filler-{number:03d}")
        paths.append(path)
    if registered:
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root")
        directory = session_launchers.registry_directory(state_paths.state_dir())
        directory.mkdir(parents=True, exist_ok=True)
        for identity, launcher in ((child_id, "parent"), ("parent", "root")):
            (directory / f"{identity}.json").write_text(json.dumps({"launcher_session_id": launcher}), encoding="utf-8")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    scans: list[session_watch.RecordScan] = []
    original = session_watch.scan_record

    def observe_scan(*args: typing.Any, **kwargs: typing.Any) -> session_watch.RecordScan:
        scan = original(*args, **kwargs)
        scans.append(scan)
        return scan

    monkeypatch.setattr(session_watch, "scan_record", observe_scan)
    retained: list[int] = []
    for length in (65536, 262144):
        for path in paths:
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            record = records[1] if path.is_relative_to(codex) else records[0]
            if path.is_relative_to(codex):
                record["payload"]["content"] = "選外" + "x" * length
            else:
                record["message"]["content"] = "選外" + "x" * length
            write_jsonl(path, records)
        scans.clear()
        assert ancestors.delegation_record("parent") == parent
        assert ancestors.delegation_record("root") == root
        assert len(scans) == (6 if registered else 130)
        serialized = json.dumps(
            [dataclasses.asdict(scan) | {"delegated_ids": sorted(scan.delegated_ids)} for scan in scans], ensure_ascii=False
        )
        assert "選外" not in serialized
        assert "x" * 65536 not in serialized
        retained.append(len(serialized))
    assert retained[0] == retained[1]


def test_start_results_override_cyclic_registry(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """登録簿の連鎖が循環しても、起動結果が示す一意な親子を優先する。"""
    claude, _ = record_roots
    _claude_record(claude, "root", "parent")
    _claude_record(claude, "parent", "child")
    _claude_record(claude, "child")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root")
    directory = session_launchers.registry_directory(state_paths.state_dir())
    directory.mkdir(parents=True, exist_ok=True)
    for child, parent in (("child", "parent"), ("parent", "child")):
        (directory / f"{child}.json").write_text(json.dumps({"launcher_session_id": parent}), encoding="utf-8")
    assert evidence.main(["--delegation-record", "root", "--user-events"]) == 0
    assert any(item.get("text") == "rootの発話" for item in read_jsonl(capsys, raw=True))


@pytest.mark.parametrize("failure", ["missing-self", "missing-parent", "unverified", "cycle", "ambiguous"])
def test_delegation_record_rejects_unresolved_relationship(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """出所を確定できない関係から発話を返さず、明示指定の再実行へ接続する。"""
    claude, _ = record_roots
    if failure != "missing-self":
        _claude_record(claude, "child", "parent" if failure == "cycle" else None)
    if failure in {"cycle", "ambiguous"}:
        _claude_record(claude, "parent", "child")
    if failure == "ambiguous":
        _claude_record(claude, "other", "child")
    if failure == "unverified":
        _claude_record(claude, "parent")
        directory = session_launchers.registry_directory(state_paths.state_dir())
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "child.json").write_text(json.dumps({"launcher_session_id": "parent"}), encoding="utf-8")
    assert evidence.main(["--delegation-record", "root", "--user-events"]) == 2
    events = read_jsonl(capsys, raw=True)
    assert len(events) == 1 and events[0]["kind"] == "error"
    assert "--claude-session-id" in events[0]["next_action"]


@pytest.mark.parametrize("owner", [None, "mcp-private-identity", "missing-owner"])
def test_owner_session_failure_keeps_identity_private(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    owner: str | None,
) -> None:
    """所有会話が解決不能な場合も環境の識別子を出力しない。"""
    del record_roots
    if owner is None:
        monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    else:
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", owner)
    assert evidence.main(["--owner-session", "--user-events"]) == 2
    output = capsys.readouterr().out
    assert owner is None or owner not in output
    assert "--transcript" in output


def test_owner_session_matches_explicit_record(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """所有ルートから得るユーザー発話のrecordとlineを直接指定とそろえる。"""
    claude, _ = record_roots
    path = _claude_record(claude, "root")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root")
    assert evidence.main(["--owner-session", "--user-events"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert evidence.main(["--transcript", str(path), "--user-events"]) == 0
    assert [item for item in events if item["kind"] != "record-provenance"] == read_jsonl(capsys, raw=True)


def test_owner_session_rejects_multiple_records(
    record_roots: tuple[pathlib.Path, pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じ識別子の記録を複数projectに持つ場合は先頭を選ばない。"""
    claude, _ = record_roots
    path = _claude_record(claude, "duplicate")
    other = claude / "projects" / "another" / path.name
    other.parent.mkdir(parents=True)
    other.write_bytes(path.read_bytes())
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "duplicate")
    assert evidence.main(["--owner-session", "--user-events"]) == 2
    events = read_jsonl(capsys, raw=True)
    assert len(events) == 1 and events[0]["kind"] == "error"
    assert "duplicate" not in events[0]["text"]
    assert str(path) in events[0]["next_action"] and str(other) in events[0]["next_action"]


@pytest.mark.parametrize("source", [["--owner-session"], ["--delegation-record", "root"]])
def test_implicit_source_rejects_another_source(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    source: list[str],
) -> None:
    """指定した対象記録と委譲関係からの選択を混ぜて別の会話を読むことを防ぐ。"""
    assert evidence.main([*source, "--transcript", str(tmp_path / "record.jsonl"), "--user-events"]) == 2
    assert read_jsonl(capsys, raw=True)[0]["kind"] == "error"
