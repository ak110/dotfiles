"""実ホストの生産済み標本を再配送し、公開hookの最終案内・遮断まで確かめる。

原記録のファイル・行・版はfixtures/read_delivery_host_samples.json.gzのsourceに保存する。
responseとrecordは実記録から未改変で抽出した値であり、試験側でホスト出力を再構成しない。
圧縮は標本の容量を減らし、過去の原文を現行資料として文章校正する誤検知を避けるためである。
"""

import gzip
import json
import pathlib

import pytest

from agent_toolkit._common import read_triggers, session_state
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SAMPLES = pathlib.Path(__file__).with_name("fixtures") / "read_delivery_host_samples.json.gz"


@pytest.mark.parametrize(
    "name",
    [
        "claude_read",
        "claude_offset_limit",
        "claude_token_cap",
        "claude_bash",
        "claude_persisted",
        "claude_failure",
        "claude_none",
    ],
)
def test_claude_produced_response_reaches_guidance_and_write(tmp_path, monkeypatch, name):
    sample = json.loads(gzip.decompress(_SAMPLES.read_bytes()).decode("utf-8"))[name]
    response = sample["response"]
    all_samples = json.loads(gzip.decompress(_SAMPLES.read_bytes()).decode("utf-8"))
    visible = response.get("file", {}).get("content", response.get("stdout", "")) if isinstance(response, dict) else ""
    acquired = name in {"claude_read", "claude_offset_limit", "claude_token_cap", "claude_bash"}
    body = visible if acquired else all_samples["claude_bash"]["response"]["stdout"]
    target = tmp_path / "received.md"
    target.write_text(body, encoding="utf-8")
    remainder = tmp_path / "including-unreceived.md"
    remainder.write_text(body + "\n未配送の残りの基準\n", encoding="utf-8")
    rows = [read_triggers.ReadTrigger(target, 1, "操作", ("tool:Write",), (target,))]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    payload = {"session_id": "sample-claude", "cwd": str(tmp_path)}
    write = {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "成果"}}

    def invoke(event, value):
        result = _run_hook_in_process(
            "posttooluse" if event.startswith("Post") else "pretooluse", {**value, "hook_event_name": event}
        )
        assert result.returncode == 0 and not result.stderr
        return result.stdout

    assert str(target) in invoke("PreToolUse", write)
    assert '"permissionDecision": "deny"' in invoke("PreToolUse", write)
    invoke(
        "PostToolUseFailure" if name == "claude_failure" else "PostToolUse",
        {
            **payload,
            "tool_name": "Read" if "file" in (response or {}) else "Bash",
            "tool_input": {"file_path": str(target)}
            if isinstance(response, dict) and "file" in response
            else {"command": "cat -- captured-source.md"},
            "tool_response": response,
        },
    )
    final = invoke("PreToolUse", write)
    assert ('"permissionDecision": "deny"' not in final) is acquired
    records = session_state.read_state("sample-claude").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(target), "main") is acquired
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(remainder), "main")
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(target), "other")
    invoke("PreToolUse", {**write, "agent_id": "other"})
    assert '"permissionDecision": "deny"' in invoke("PreToolUse", {**write, "agent_id": "other"})
    rows[:] = [read_triggers.ReadTrigger(remainder, 1, "操作", ("tool:Write",), (remainder,))]
    invoke("PreToolUse", write)
    assert '"permissionDecision": "deny"' in invoke("PreToolUse", write)


@pytest.mark.parametrize("name", ["codex_two_layer_cut", "codex_nonzero"])
def test_codex_produced_outer_record_reaches_incremental_pre_hook(tmp_path, monkeypatch, name):
    sample = json.loads(gzip.decompress(_SAMPLES.read_bytes()).decode("utf-8"))[name]
    producer = sample["producer_outputs"][0]
    # 生産側の全出力と配送後の標本を別の比較元にする。先頭の短い段落は両方へ実際に含まれる。
    paragraph = next(line for line in producer.splitlines() if len(line) > 50) + "\n"
    visible = tmp_path / "visible.md"
    visible.write_text(paragraph, encoding="utf-8")
    full = tmp_path / "full.md"
    full.write_text(producer, encoding="utf-8")
    rows = [read_triggers.ReadTrigger(visible, 1, "操作", ("tool:Write",), (visible, full))]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("", encoding="utf-8")
    payload = {"session_id": "sample-codex", "turn_id": "turn", "cwd": str(tmp_path), "transcript_path": str(rollout)}
    write = {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "成果"}}
    initial = _run_hook_in_process("pretooluse", write)
    assert initial.returncode == 0 and not initial.stderr
    inner = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "cat -- captured-source.md"},
            "tool_response": producer,
        },
    )
    assert inner.returncode == 0 and not inner.stderr
    before = session_state.read_state("sample-codex").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert not read_delivery.target_ready(before, read_triggers.ReadTarget(visible), "main")
    # 実rolloutの外側出力レコードを、書込側のJSON Linesとしてそのまま保存する。
    with rollout.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(sample["record"], ensure_ascii=False) + "\n")
    final = _run_hook_in_process("pretooluse", write)
    assert final.returncode == 0 and not final.stderr
    records = session_state.read_state("sample-codex").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(visible), "main")
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(full), "main") is (name == "codex_nonzero")
    assert ('"permissionDecision": "deny"' in final.stdout) is (name == "codex_two_layer_cut")
    offsets = session_state.read_state("sample-codex")["document_read_offsets"]
    assert next(iter(offsets.values())) == rollout.stat().st_size
    other = {**write, "agent_id": "other"}
    _run_hook_in_process("pretooluse", other)
    denied = _run_hook_in_process("pretooluse", other)
    assert denied.returncode == 0 and not denied.stderr
    assert '"permissionDecision": "deny"' in denied.stdout
