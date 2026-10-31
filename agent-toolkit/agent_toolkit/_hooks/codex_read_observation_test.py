"""公開Post hookを通して、Codexのnative読取の成功だけを読了として観測し、本文を配送しないことを確かめる。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import codex_read_observation as observation
from agent_toolkit._hooks import plugin_resources, posttooluse, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery


def _payload(tmp_path, command, body, *, exit_code=0):
    records = [
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "turn",
                "thread_id": "session",
                "item": {
                    "type": "CommandExecution",
                    "id": "exec-id",
                    "status": "completed" if not exit_code else "failed",
                    "exit_code": exit_code,
                    "cwd": tmp_path.as_uri(),
                    "command": ["/bin/bash", "-lc", command],
                },
            },
        },
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return {
        "session_id": "session",
        "turn_id": "turn",
        "tool_use_id": "exec-id",
        "hook_event_name": "PostToolUse",
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": body,
    }


def _cut(body, tokens=100):
    return (
        f"Warning: truncated output (original token count: {tokens})\nTotal output lines: 20\n\n"
        f"{body[:12]}…90 tokens truncated…{body[-12:]}"
    )


def _additional_context(capsys) -> str:
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"].get("additionalContext", "") if out.strip() else ""


@pytest.mark.parametrize("kind", ["cat", "sed", "head", "tail", "jq", "chain"])
@pytest.mark.parametrize("truncated", [False, True])
def test_public_post_hook_never_delivers_document_body(tmp_path, capsys, kind, truncated):
    """成功した標準読取でも、省略の有無にかかわらず公開Post hookは資料本文を追加コンテキストへ載せない。

    2026年10月30日に省略補償として再導入された本文配送の再発を、公開経路の出力で検出する。
    """
    text = "".join(f"資料{i:03d}: 配送してはならない本文\n" for i in range(40))
    path = tmp_path / "資料 space.md"
    path.write_text(text, encoding="utf-8")
    operand = shlex.quote(str(path))
    command, expected = f"cat -- {operand}", text
    if kind == "sed":
        command, expected = f"sed -n '2,$p' -- {operand}", "".join(text.splitlines(keepends=True)[1:])
    elif kind == "head":
        command, expected = f"head -n 3 -- {operand}", "".join(text.splitlines(keepends=True)[:3])
    elif kind == "tail":
        command, expected = f"tail -n3 -- {operand}", "".join(text.splitlines(keepends=True)[-3:])
    elif kind == "jq":
        path.write_text(json.dumps({"body": text}), encoding="utf-8")
        command, expected = f"jq -r '.body' {operand}", text + "\n"
    elif kind == "chain":
        command, expected = f"cat -- {operand}; cat -- {operand}", text * 2
    payload = _payload(tmp_path, command, _cut(expected) if truncated else expected)
    assert posttooluse.main(json.dumps(payload)) == 0
    output = _additional_context(capsys)
    for line in expected.splitlines()[:3]:
        assert line not in output
    if truncated:
        assert observation.TRUNCATED_NOTICE in output
    else:
        assert output == ""


def test_full_read_is_observed_and_truncated_read_is_not(tmp_path):
    """終了0で省略の無いnative本文だけを読了観測へ渡し、省略時は分割読取だけを案内する。"""
    path = tmp_path / "note"
    path.write_text("元の全量\n" * 30, encoding="utf-8")
    full = observation.collect(_payload(tmp_path, f"cat {path}", path.read_text()))
    assert full == observation.Observation("", path.read_text())
    cut = observation.collect(_payload(tmp_path, f"cat {path}", _cut(path.read_text())))
    assert cut == observation.Observation(observation.TRUNCATED_NOTICE, None)


@pytest.mark.parametrize("mismatch", ["exit", "id", "turn", "session", "command", "missing"])
def test_failed_or_unidentified_execution_is_not_observed(tmp_path, mismatch):
    """本文が読取に見えても、同じ元実行の成功を確認できなければ読了の根拠にしない。"""
    path = tmp_path / "note"
    path.write_text("資料の本文\n" * 30, encoding="utf-8")
    payload = _payload(tmp_path, f"cat {path}", path.read_text(), exit_code=1 if mismatch == "exit" else 0)
    if mismatch in {"id", "turn", "session"}:
        payload[{"id": "tool_use_id", "turn": "turn_id", "session": "session_id"}[mismatch]] = "別の操作"
    elif mismatch == "command":
        payload["tool_input"]["command"] += " "
    elif mismatch == "missing":
        pathlib.Path(payload["transcript_path"]).write_text("", encoding="utf-8")
    assert observation.collect(payload) is None


@pytest.mark.parametrize(
    "command",
    [
        "cat file > out",
        "cat file | head",
        "cat $(echo file)",
        "tail -f file",
        "sed -n '1w out' file",
        "jq env file",
        "jq 'input' file",
        "jq '.body' file; touch out",
    ],
)
def test_side_effects_and_external_jq_inputs_are_not_read_plans(command):
    assert not observation.read_plan(command)


@pytest.mark.parametrize("value", [None, [], {"payload": None}, {"type": "event_msg", "payload": {"item": None}}])
def test_malformed_transcript_is_not_observed(tmp_path, value):
    """壊れた外部記録を成功の根拠にせず、hookの他の処理を維持する。"""
    path = tmp_path / "note"
    path.write_text("本文")
    payload = _payload(tmp_path, f"cat {path}", "本文")
    pathlib.Path(payload["transcript_path"]).write_text(json.dumps(value) + "\n")
    assert observation.collect(payload) is None


@pytest.mark.parametrize("exit_code", [0, 1, None])
@pytest.mark.parametrize("truncated", [False, True])
def test_document_read_state_follows_successful_full_native_read(tmp_path, capsys, exit_code, truncated):
    """終了0で省略の無いnative読取だけを読了にし、失敗・未確認・省略は読了にしない。"""
    path = plugin_resources.plugin_root().parent / "agent-basics/skills/bugfix/references/root-cause-analysis.md"
    body = path.read_text(encoding="utf-8")
    payload = _payload(tmp_path, f"cat -- {path}", _cut(body) if truncated else body, exit_code=exit_code)
    if exit_code is None:
        pathlib.Path(payload["transcript_path"]).write_text("", encoding="utf-8")
    assert posttooluse.main(json.dumps(payload)) == 0
    capsys.readouterr()
    recorded = session_state.read_state("session").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    target = read_delivery.read_triggers.ReadTarget(path)
    assert read_delivery.target_ready(recorded, target, "main") == (exit_code == 0 and not truncated)


@pytest.mark.parametrize("command", ["rg -n 対象 -- .", "git diff"])
def test_truncated_non_read_output_gets_recovery_notice(tmp_path, capsys, command):
    """標準読取以外の出力が省略された場合も、担当内で回収する次の操作を示す。"""
    payload = _payload(tmp_path, command, _cut("検索結果の本文" * 40))
    assert posttooluse.main(json.dumps(payload)) == 0
    output = _additional_context(capsys)
    assert observation.TRUNCATED_OUTPUT_NOTICE in output
    assert "続行できない理由にしない" in output


def test_untruncated_non_read_output_has_no_notice(tmp_path, capsys):
    payload = _payload(tmp_path, "git status --short", " M file\n")
    assert posttooluse.main(json.dumps(payload)) == 0
    assert observation.TRUNCATED_OUTPUT_NOTICE not in _additional_context(capsys)
