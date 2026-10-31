"""加工候補の公開bundleで、実行位置・文字列データ・保存出力の渡し方を対照する。"""

import json
import pathlib

import pytest
import session_evidence_execution as execution
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript

_SAVED = "/cache/atk-output-example/output.txt"


@pytest.mark.parametrize(
    ("tool", "command", "expected"),
    [
        ("Bash", f"F={_SAVED}; jq . $F", True),
        ("Bash", f"x=$(F={_SAVED}); jq . $F", False),
        ("Bash", f"F={_SAVED}; x=$(echo unchanged); jq . $F", True),
        ("Bash", "T=/cache/managed-temp/s; python3 $T/a.py", True),
        ("Bash", f"sed -n -e a -e {_SAVED} repo.md", False),
        ("Bash", f"sed -n --expression=a --expression={_SAVED} repo.md", False),
        ("Bash", f"cat {_SAVED}; sed -n 1,20p repo.md", False),
        ("Bash", f"printf sed; cat {_SAVED}", False),
        ("Bash", f"cat {_SAVED} | sed -n 1,20p repo.md", False),
        ("Bash", "printf '%s' 'python3 -c x; node -e x'", False),
        ("Bash", f"sed -n 1,20p {_SAVED}", True),
        ("Bash", f"sed -n 1,20p < {_SAVED}", True),
        ("Bash", f"cat {_SAVED} | sed -n 1,20p", True),
        ("Bash", f"cat {_SAVED} | head -n 20 | jq '.'", True),
        ("Bash", f"cat {_SAVED} | jq --arg label value .", True),
        ("Bash", f"cat {_SAVED} | jq --argjson count 1 .", True),
        ("Bash", f"cat {_SAVED} | jq --arg label value . repo.json", False),
        ("Bash", f"cat {_SAVED} | jq --argjson count 1 . repo.json", False),
        ("Bash", f"cat {_SAVED} | jq --slurpfile values repo.json .", True),
        ("Bash", "uv run /cache/managed-temp/session-x/processing.py", True),
        ("local_shell", ["bash", "-lc", "python3 -c 'print(1)'"], True),
        ("Bash", "uv run --frozen python -c 'print(1)'", True),
        ("Bash", "python3 - <<'PY'\nprint(1)\nPY", True),
        ("Bash", "node -e 'console.log(1)'", True),
        (
            "exec",
            f"await tools.exec_command({{cmd: 'cat {_SAVED}'}}); await tools.exec_command({{cmd: 'sed -n 1,20p repo.md'}});",
            False,
        ),
        ("exec", 'await tools.apply_patch("python3 -c x; node -e x");', False),
        ("exec", "await tools.apply_patch(\"tools.exec_command({cmd: 'python -c x'})\");", False),
        ("exec", "await tools.exec_command({cmd: \"python3 -c 'print(1)'\"});", True),
        ("exec", f"await tools.apply_patch('sed -n 1,20p {_SAVED}');", False),
    ],
)
def test_bundle_classifies_executed_processing_only(tmp_path: pathlib.Path, capsys, tool, command, expected) -> None:
    """同じ語形を入力の実行位置と文字列位置へ置き、元の呼び出し単位を保持する。"""
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call", "name": tool, "input": {"command": command}}],
        },
    }
    if tool == "exec":
        entry = {
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "call", "input": command},
        }
    elif tool == "local_shell":
        entry = {
            "type": "response_item",
            "payload": {"type": "function_call", "name": tool, "call_id": "call", "arguments": json.dumps({"cmd": command})},
        }
    transcript = _write_transcript(tmp_path, [entry])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    processing = [row for row in candidates if row.get("candidate_kind") == "adhoc-processing"]
    assert bool(processing) is expected
    if expected:
        assert processing[0]["calls"][0]["line"] == 1
    assert evidence.main([str(transcript), "--tool-calls"]) == 0
    calls = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    displayed = " ".join(command) if isinstance(command, list) else command
    assert [call["text"] for call in calls if call["kind"] == "tool-call"] == [displayed]


@pytest.mark.parametrize(
    "argument",
    [
        "dynamicArgs",
        '{cmd: "cat role.subagent.md" + suffix}',
        "{cmd: `cat ${path}`}",
        '{cmd: "cat role.subagent.md", ...overrides}',
        '{cmd: "cat role.subagent.md", cmd: dynamic}',
    ],
)
def test_dynamic_command_is_retained_without_evaluation(argument: str) -> None:
    """式と上書きのある入力を、静的な文字列の先頭へ切り詰めない。"""
    cell = f"await tools.exec_command({argument});"
    assert not execution.command_inputs("functions.exec", cell)
    observations = execution.command_input_observations("functions.exec", cell)
    assert len(observations) == 1
    assert observations[0]["command"] is None and observations[0]["reason"]
    original_input = observations[0]["input"]
    assert original_input is not None
    assert argument in original_input
