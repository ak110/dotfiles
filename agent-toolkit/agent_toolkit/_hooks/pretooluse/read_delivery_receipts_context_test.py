"""配送本文を独立の比較元に、公開hookの受領証拠・更新案内・継続許可を検収する。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._common import plugin_roots, read_triggers, session_state
from agent_toolkit._hooks import document_reads, rules_context, work_contract
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing import git_repository, pretooluse_support
from agent_toolkit._testing.pretooluse_support import run_hook_with_notice_details as _run_hook_in_process
from agent_toolkit._testing.read_delivery_receipts_support import (
    _assert_acquisition,
    _assert_allowed_without_guidance,
    _produce,
    _scenario,
)

__all__ = ["_assert_acquisition", "_assert_allowed_without_guidance", "_produce", "_scenario"]


def test_codex_rollout_cursor_preserves_context_boundary(scenario, monkeypatch):
    state, rows, path, rollout, invoke, delivered = scenario
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Read", {"file_path": str(path)})
    delivered(path.read_text(encoding="utf-8"))
    assert str(path) not in invoke("codex", "PreToolUse", "Write", write)
    offset = next(iter(state["document_read_offsets"].values()))
    assert offset == rollout.stat().st_size
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: path.parent)
    result = _run_hook_in_process(
        "rules_context_codex",
        {
            "session_id": "receipts",
            "turn_id": "turn",
            "cwd": str(path.parent),
            "transcript_path": str(rollout),
            "hook_event_name": "SessionStart",
            "source": "compact",
        },
    )
    assert result.returncode == 0 and not result.stderr
    state.clear()
    state.update(session_state.read_state("receipts"))
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
    )
    invoke("codex", "PreToolUse", "Write", write)
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
    )
    delivered(path.read_text(encoding="utf-8"))
    assert str(path) not in invoke("codex", "PreToolUse", "Write", write)


def test_codex_missing_or_incomplete_outer_output_does_not_acquire(scenario):
    state, rows, path, rollout, invoke, delivered = scenario
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Read", {"file_path": str(path)})
    invoke("codex", "PostToolUse", "Read", {"file_path": str(path)}, path.read_text(encoding="utf-8"))
    with rollout.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"response_item"')
    invoke("codex", "PreToolUse", "Write", write)
    assert not read_delivery.target_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(path), "main"
    )
    rollout.write_text("[]\n", encoding="utf-8")
    invoke("codex", "PreToolUse", "Write", write)
    assert not read_delivery.target_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(path), "main"
    )
    for output in (
        {"stdout": path.read_text(encoding="utf-8")},
        [{"text": path.read_text(encoding="utf-8")}],
        [{"type": "image", "text": path.read_text(encoding="utf-8")}],
        [{"type": "input_text", "text": "\ud800"}],
    ):
        with rollout.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({"type": "response_item", "payload": {"type": "custom_tool_call_output", "output": output}}) + "\n"
            )
        invoke("codex", "PreToolUse", "Write", write)
        assert not read_delivery.target_ready(
            state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(path), "main"
        )
    delivered(path.read_text(encoding="utf-8"))
    assert str(path) not in invoke("codex", "PreToolUse", "Write", write)


@pytest.mark.parametrize("same_source", [False, True])
def test_installed_cache_provenance_keeps_source_boundary(scenario, monkeypatch, host_environ, same_source):
    state, rows, path, _rollout, invoke, _delivered = scenario
    relative = "skills/example/references/rules.md"
    repo = git_repository.init_repository(
        path.parent / "source",
        files={
            "plugin/" + relative: path.read_text(encoding="utf-8"),
            "plugin/.claude-plugin/plugin.json": '{"name":"example-plugin"}',
        },
        commit_message="配布元",
    )
    other = git_repository.init_repository(path.parent / "other-source", commit_message="別配布元")
    source = repo / "plugin" / relative
    invoke("claude", "PreToolUse", "Read", {"file_path": str(source)})
    command, response = _produce(read_triggers.ReadTarget(source), host_environ)
    invoke("claude", "PostToolUse", "Bash", {"command": command}, response)
    home = path.parent / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    root = home / "plugins/cache/mine/example-plugin/2.0"
    target = root / relative
    target.parent.mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text('{"name":"example-plugin","version":"2.0"}', encoding="utf-8")
    target.write_text(source.read_text(encoding="utf-8").replace("固有の必要本文", "新版の必要本文"), encoding="utf-8")

    original_runner = plugin_roots.process_tree.run_with_timeout

    def public_inventory(argv, **kwargs):
        if argv[0] == "git":
            return original_runner(argv, **kwargs)
        # Codex 0.166.0の公開marketplace/listで実観測したroot・name・導入属性を使う。
        data = {"marketplaces": [{"name": "mine", "root": str(repo if same_source else other)}]}
        if argv[1:3] == ["plugin", "list"]:
            data = {
                "installed": [
                    {"name": "example-plugin", "marketplaceName": "mine", "version": "2.0", "enabled": True, "installed": True}
                ]
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", public_inventory)
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (target,), ("必要節",)))
    result = invoke("claude", "PreToolUse", "Write", {"file_path": str(path.parent / "out.md"), "content": "成果"})
    status = document_reads.acquisition_state(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(target, "必要節"), "main"
    )
    assert status == ("updated" if same_source else "missing")
    assert ("取得済み資料の更新あり" in result) is same_source
    if not same_source:
        assert str(target) in result
        repeated = invoke("claude", "PreToolUse", "Write", {"file_path": str(path.parent / "out.md"), "content": "成果"})
        _assert_allowed_without_guidance(repeated)
        _assert_acquisition(state, read_triggers.ReadTarget(target, "必要節"), False)


@pytest.mark.parametrize("form", ["canonical", "reformatted", "old-version", "tampered", "embedded-close"])
def test_generated_startup_scope_survives_packaging_and_version(scenario, form):
    state, rows, path, rollout, invoke, _delivered = scenario
    if form == "embedded-close":
        path.write_text(
            path.read_text(encoding="utf-8").replace("固有の必要本文", "タグの用例\n</atk-auto>\n用例の後の必要本文"),
            encoding="utf-8",
        )
    target = read_triggers.ReadTarget(path, "必要節")
    packet = read_triggers.startup_documents([target], path.parent, {target: {"input:作業契約"}})
    if form == "reformatted":
        line = next(line for line in packet.splitlines() if line.startswith("資料JSON: "))
        metadata = json.loads(line.removeprefix("資料JSON: "))
        replacement = "資料JSON: " + json.dumps(
            dict(reversed(list(metadata.items()))), ensure_ascii=False, separators=(",  ", ":  ")
        )
        packet = packet.replace(line, replacement)
    elif form == "tampered":
        packet = packet.replace("固有の必要本文", "別の本文")
    elif form == "old-version":
        path.write_text(path.read_text(encoding="utf-8").replace("固有の必要本文", "新版の必要本文"), encoding="utf-8")
    rollout.write_text(
        json.dumps({"type": "user", "message": {"content": packet}}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,), ("必要節",)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    result = invoke("claude", "PreToolUse", "Write", write, transcript_path=str(rollout))
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert document_reads.acquisition_state(records, target, "main") == (
        "updated" if form == "old-version" else "missing" if form == "tampered" else "current"
    )
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(path, "別節"), "main")
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(path), "main")
    if form == "old-version":
        assert "取得済み資料の更新あり" in result
    second = invoke("claude", "PreToolUse", "Write", write, transcript_path=str(rollout))
    _assert_allowed_without_guidance(second)


def test_existing_work_contract_body_delivery_is_received(scenario, monkeypatch):
    state, rows, path, _rollout, invoke, _delivered = scenario
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    result = _run_hook_in_process(
        "user_prompt_submit",
        {
            "session_id": "receipts",
            "cwd": str(path.parent),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "現行の契約を確認する",
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert path.read_text(encoding="utf-8").rstrip("\n") in result.stdout.replace("\\n", "\n")
    state.clear()
    state.update(session_state.read_state("receipts"))
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(path), "main")
    assert str(path) not in invoke(
        "claude", "PreToolUse", "Write", {"file_path": str(path.parent / "out.md"), "content": "成果"}
    )


def test_session_state_persists_after_preparation_scope(tmp_path):
    """準備scope終了後の公開hookが、取得証拠を実保存先へ書き込む。"""
    path = tmp_path / "rules.md"
    path.write_text("# 判断基準\n本文\n", encoding="utf-8")
    payload: dict = {
        "session_id": "persisted-receipt",
        "cwd": str(tmp_path),
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
    }
    with pretooluse_support.read_delivery_preparation_scope():
        pretooluse_support.prepare_read_delivery(payload)
    result = _run_hook_in_process(
        "posttooluse", {**payload, "hook_event_name": "PostToolUse", "tool_response": path.read_text(encoding="utf-8")}
    )
    assert result.returncode == 0 and not result.stderr
    records = session_state.read_state(payload["session_id"])[rules_context.OPERATION_SKILL_READY_KEY]
    assert document_reads.acquisition_state(records, read_triggers.ReadTarget(path), "main") == "current"
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(path), "agent:other")


def test_repeated_compact_excludes_unobserved_old_delivery(scenario, monkeypatch, host_environ):
    """連続する圧縮でも、次のPreで未観測だった旧文脈の出力を再利用しない。"""
    state, rows, path, rollout, invoke, delivered = scenario
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Write", write)
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: path.parent)
    compact = {
        "session_id": "receipts",
        "turn_id": "turn",
        "cwd": str(path.parent),
        "transcript_path": str(rollout),
        "hook_event_name": "SessionStart",
        "source": "compact",
    }
    first = _run_hook_in_process("rules_context_codex", compact)
    assert first.returncode == 0 and not first.stderr
    command = "cat -- " + shlex.quote(str(path))
    produced = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert produced.returncode == 0 and not produced.stderr
    invoke("codex", "PostToolUse", "Bash", {"command": command}, produced.stdout)
    delivered(produced.stdout)
    second = _run_hook_in_process("rules_context_codex", compact)
    assert second.returncode == 0 and not second.stderr
    invoke("codex", "PreToolUse", "Write", write)
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
    )
