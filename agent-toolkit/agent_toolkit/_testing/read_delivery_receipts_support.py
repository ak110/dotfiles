"""配送本文を独立の比較元に、公開hookの受領証拠・更新案内・継続許可を検収する。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._common import read_triggers, session_state
from agent_toolkit._hooks import document_reads, notice, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import run_hook_with_notice_details as _run_hook_in_process


def _assert_allowed_without_guidance(text):
    """invokeで確認したhook正常終了に加え、denyと反復案内がないことを確かめる。"""
    output = json.loads(text).get("hookSpecificOutput", {}) if text else {}
    assert output.get("permissionDecision") != "deny"
    assert not output.get("additionalContext")


def _assert_acquisition(state, target, expected):
    """操作の許可と独立に、現行本文・要求範囲・主体の受領証拠を照会する。"""
    target = target if isinstance(target, read_triggers.ReadTarget) else read_triggers.ReadTarget(target)
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert (document_reads.acquisition_state(records, target, "main") == "current") is expected


def _read_output(command, host_environ):
    """ホスト環境で取得コマンドを実行し、正常な標準出力を返す。"""
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    return result


def _produce(target, host_environ):
    """案内が生成した取得コマンドを実行し、未加工のstdout・stderrを返す。"""
    command = read_delivery.acquisition_step(target, "codex").split("`", 2)[1]
    result = _read_output(command, host_environ)
    return command, {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}


def _deliver_sed(path, span, invoke, delivered, host_environ):
    command = f"sed -n '{span}p' -- {shlex.quote(str(path))}"
    invoke("codex", "PreToolUse", "Bash", {"command": command})
    result = _read_output(command, host_environ)
    invoke("codex", "PostToolUse", "Bash", {"command": command}, result.stdout)
    delivered(json.dumps({"output": result.stdout}))


@pytest.fixture(name="scenario")
def _scenario(tmp_path, monkeypatch):
    state: dict = {}
    rows: list[read_triggers.ReadTrigger] = []

    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    notice.set_warning_session_id("")
    path = tmp_path / "reference space[1].md"
    path.write_text("# 基準\n\n## 必要節\n固有の必要本文\n\n## 別節\n固有の別本文\n", encoding="utf-8")
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("", encoding="utf-8")

    def invoke(host, event, tool, inputs, response=None, **extra):
        payload = {
            "session_id": "receipts",
            "cwd": str(tmp_path),
            "hook_event_name": event,
            "tool_name": tool,
            "tool_input": inputs,
            "tool_response": response,
            **extra,
        }
        if host == "codex":
            payload.update(turn_id="turn", transcript_path=str(rollout))
        result = _run_hook_in_process("posttooluse" if event.startswith("Post") else "pretooluse", payload)
        assert result.returncode == 0 and not result.stderr
        state.clear()
        state.update(session_state.read_state("receipts"))
        return result.stdout

    def delivered(body, *, destination=None, blocks=None):
        # Codex 0.166.0のrolloutで観測した外側output配列と、exec_commandのJSON文字列。
        entry = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "outer",
                "output": [
                    {"type": "input_text", "text": "Script completed\nOutput:\n"},
                    *[{"type": "input_text", "text": text} for text in (blocks if blocks is not None else [body])],
                ],
            },
        }
        with (destination or rollout).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return state, rows, path, rollout, invoke, delivered
