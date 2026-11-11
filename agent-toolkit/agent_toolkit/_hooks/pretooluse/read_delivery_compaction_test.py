"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json

import pytest

from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _assert_acquired,
    _copies,
    _guidance,
    _guided_without_acquisition,
    _local_plugin_root,
    _memory,
    _notice_body,
    _write_operation,
)

__all__ = [
    "_assert_acquired",
    "_copies",
    "_guidance",
    "_guided_without_acquisition",
    "_local_plugin_root",
    "_memory",
    "_notice_body",
    "_write_operation",
]


@pytest.mark.parametrize(
    ("case", "ready"),
    [("same", True), ("different", False), ("other-actor", False), ("partial", False), ("split-copies", False)],
)
@pytest.mark.usefixtures("memory")
def test_public_hook_reuses_received_content_across_paths(tmp_path, monkeypatch, case, ready):
    """同じ要求本文だけを再利用し、別本文・別主体・不完全な本文を区別する。"""
    checkout, worktree = _copies(tmp_path, monkeypatch, same=case != "different")
    lines = worktree.read_text(encoding="utf-8").splitlines(keepends=True)
    payload = {"agent_id": "child"} if case == "other-actor" else {}

    def read(path, start, count):
        text = "".join(lines[start - 1 : start - 1 + count])
        response = {"file": {"content": text, "startLine": start, "numLines": count, "totalLines": len(lines)}}
        observe_received({**payload, "tool_response": response}, "Read", {"file_path": str(path)}, "test")

    if case == "partial":
        read(worktree, 1, 2)
    elif case == "split-copies":
        read(worktree, 1, 2)
        read(checkout, 3, len(lines) - 2)
    else:
        read(worktree, 1, len(lines))
    assert (_guidance(read_delivery.deliver({}, "Do", {}, "test")) is None) is ready


@pytest.mark.usefixtures("memory")
def test_unrecorded_copy_history_keeps_the_selected_path_unread(tmp_path, monkeypatch):
    """内容とパスの対応を特定できない過去の履歴は、現行本文の取得済み状態へ変換しない。"""
    _checkout, worktree = _copies(tmp_path, monkeypatch)
    entries = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "read", "name": "Read", "input": {"file_path": str(worktree)}}]},
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "read", "content": worktree.read_text()}]},
        },
    ]
    log = tmp_path / "history.jsonl"
    log.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    assert _guidance(read_delivery.deliver({"transcript_path": str(log)}, "Do", {}, "test")) is not None


def test_compaction_recovery_lists_a_selected_document_read_through_a_same_content_copy(tmp_path, monkeypatch, memory):
    """会話圧縮時は、選択済み資料を同じ内容の別の場所で読んだ場合も、選択済み資料のパスを再取得の候補に挙げる。"""
    checkout, worktree = _copies(tmp_path, monkeypatch)
    observe_received({"tool_response": worktree.read_text()}, "Read", {"file_path": str(worktree)}, "test")
    memory[rules_context.OPERATION_SKILL_READY_KEY][
        "selected-read-target:" + json.dumps({"path": str(checkout), "heading": ""}, ensure_ascii=False, separators=(",", ":"))
    ] = ["main"]
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    recovery = rules_context.reset_main_operation_skill_ready("test", retain_for_compact=True)
    assert [path for path, _heading, _fingerprint, _reference in recovery] == [checkout.resolve()]
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))


def test_compaction_reguides_only_current_documents_without_blocking(tmp_path, monkeypatch, memory):
    """圧縮後は現在必要な資料を一度案内し、未取得の再書込も許可する。"""
    earlier = tmp_path / "earlier.md"
    later = tmp_path / "later.md"
    earlier.write_text("# 圧縮前の資料\n\n圧縮前の基準\n", encoding="utf-8")
    later.write_text("# 圧縮後の資料\n\n圧縮後の基準\n", encoding="utf-8")
    rows = [
        ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Do",), (earlier,)),
        ReadTrigger(tmp_path / "SKILL.md", 2, "操作", ("tool:Write",), (later,)),
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    payload = {"cwd": str(tmp_path)}
    write = {"file_path": str(tmp_path / "out.md"), "content": "本文"}

    def delivered(tool, inputs):
        decision = read_delivery.deliver(payload, tool, inputs, "test")
        notice_module.record_delivery("\n".join(decision.notices))
        return decision

    assert str(earlier) in _notice_body(delivered("Do", {}))
    observe_received({**payload, "tool_response": later.read_text()}, "Read", {"file_path": str(later)}, "test")
    assert not _guidance(delivered("Write", write))
    _assert_acquired(payload, earlier, False)
    _assert_acquired(payload, later, True)

    rules_context.reset_main_operation_skill_ready("test", retain_for_compact=True)
    # 圧縮前の候補は操作ごとに再掲せず、現在の操作が選んだ資料だけを案内する。
    first = _notice_body(delivered("Write", write))
    assert str(later) in first and str(earlier) not in first
    assert not _guidance(read_delivery.deliver(payload, "Write", write, "test"))
    _assert_acquired(payload, later, False)
    observe_received({**payload, "tool_response": later.read_text()}, "Read", {"file_path": str(later)}, "test")
    _assert_acquired(payload, later, True)
    for _ in range(2):
        decision = read_delivery.deliver(payload, "Write", write, "test")
        assert decision.block is None and not decision.notices
    assert str(earlier) in _notice_body(delivered("Do", {}))
    assert not _guidance(delivered("Write", write))
    assert not _guidance(delivered("Do", {}))
    _assert_acquired(payload, earlier, False)


@pytest.mark.parametrize(
    "scenario",
    ["no-compaction", "compaction-then-change", "change-then-compaction", "compaction-only", "compaction-then-edit"],
)
def test_selection_that_no_longer_matches_the_document_is_not_reguided(tmp_path, monkeypatch, memory, scenario):
    """選択後に見出しが消えた資料と、圧縮後に変わった資料を、宣言が外れた後の操作ごとに再掲しない。"""
    doc = tmp_path / "parent.md"
    doc.write_text("# 親\n\n## 生成\n\n生成の本文\n\n## 起動\n\n起動の本文\n", encoding="utf-8")
    rows = [ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Do",), (doc,), ("生成",))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    payload = {"cwd": str(tmp_path)}
    assert str(doc) in _notice_body(read_delivery.deliver(payload, "Do", {}, "test"))
    observe_received({**payload, "tool_response": doc.read_text()}, "Read", {"file_path": str(doc)}, "test")
    # 節「生成」を別の資料へ移した後の本文。compaction-then-editだけは節を残して本文を変える。
    changed = (
        "# 親\n\n## 生成\n\n改訂した生成の本文\n\n## 起動\n\n起動の本文\n"
        if scenario == "compaction-then-edit"
        else "# 親\n\n## 起動\n\n起動の本文\n"
    )
    if scenario == "change-then-compaction":
        doc.write_text(changed, encoding="utf-8")
    if scenario != "no-compaction":
        rules_context.reset_main_operation_skill_ready("test", retain_for_compact=True)
    if scenario in {"no-compaction", "compaction-then-change", "compaction-then-edit"}:
        doc.write_text(changed, encoding="utf-8")
    rows.clear()
    for _ in range(2):
        assert str(doc) not in "\n".join(read_delivery.deliver(payload, "Bash", {"command": "git status"}, "test").notices)
    observe_received({**payload, "tool_response": doc.read_text()}, "Read", {"file_path": str(doc)}, "test")
    assert not read_delivery.deliver(payload, "Bash", {"command": "git status"}, "test").notices


@pytest.mark.parametrize("codex", [False, True])
def test_public_guidance_distinguishes_new_scope_target_and_compaction(tmp_path, monkeypatch, memory, codex):
    """実配送済みでも新しい必要節・別資料・圧縮後の現在資料を案内し、取得証拠を分離する。"""
    path = tmp_path / "criteria.md"
    path.write_text("# 基準\n## 初動\n初動本文\n## 詳細\n詳細本文\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("# 別資料\n別の基準\n", encoding="utf-8")
    source = tmp_path / "source.md"
    rows = [ReadTrigger(source, 1, "操作", ("tool:Write",), (path,), ("初動",))]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    monkeypatch.setattr(rules_context, "read_state", lambda _session: memory)
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, path)
    rows[:] = [ReadTrigger(source, 1, "操作", ("tool:Write",), (path,), ("詳細",))]
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and not first.stderr and "詳細" in first.stdout and "初動" not in first.stdout
    assert not _run_hook_in_process("pretooluse", operation).stdout
    observe_received(
        {**payload, "tool_response": "## 初動\n初動本文\n"},
        "Read",
        {"file_path": str(path), "offset": 2, "limit": 2},
        "test",
    )
    # 宣言が名指ししなくなった節の読取は、名指しされた節と資料全体のどちらの取得にも数えない。
    _assert_acquired(payload, ReadTarget(path, "詳細"), False)
    _assert_acquired(payload, path, False)
    assert not _run_hook_in_process("pretooluse", operation).stdout
    rows[:] = [ReadTrigger(source, 1, "操作", ("tool:Write",), (other,))]
    _guided_without_acquisition(operation, other)
    compact = _run_hook_in_process("rules_context", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
    assert compact.returncode == 0 and not compact.stderr
    restored = _run_hook_in_process("pretooluse", operation)
    assert restored.returncode == 0 and not restored.stderr
    assert str(other) in restored.stdout and str(path) not in restored.stdout
    assert not _run_hook_in_process("pretooluse", operation).stdout
    _assert_acquired(payload, other, False)
