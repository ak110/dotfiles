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
        ("Bash", f"sed -n -e 1p {_SAVED}", True),
        ("Bash", f"sed -n -e1p {_SAVED}", True),
        ("Bash", f"sed -ne1p {_SAVED}", True),
        ("Bash", f"sed -ne 1p {_SAVED}", True),
        ("Bash", f"sed -nfexpr.sed {_SAVED}", True),
        ("Bash", f"sed -n -f expr.sed {_SAVED}", True),
        ("Bash", f"sed -n -fexpr.sed {_SAVED}", True),
        ("Bash", f"sed -n --expression=1p -- {_SAVED}", True),
        ("Bash", f"sed -n --file expr.sed {_SAVED}", True),
        ("Bash", f"sed -n --file=expr.sed {_SAVED}", True),
        ("Bash", f"sed -n -e1p -e 2p -- {_SAVED}", True),
        ("Bash", f"sed -n -f {_SAVED} repo.md", False),
        ("Bash", f"sed -n -f{_SAVED} repo.md", False),
        ("Bash", f"sed -nf{_SAVED} repo.md", False),
        ("Bash", f"sed -n --file={_SAVED} repo.md", False),
        ("Bash", f"sed -n -e{_SAVED} repo.md", False),
        ("Bash", f"cat {_SAVED} | sed -n -e1p", True),
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


def test_shell_parse_scope_reuses_bounded_immutable_syntax(monkeypatch: pytest.MonkeyPatch) -> None:
    """再入・別処理・例外終了でも寿命を保ち、呼出側の変更と未知構文を混同しない。"""
    parsed: list[str] = []
    original = execution.shell_segments.extract_execution_segments

    def counted(command, **kwargs):
        parsed.append(command)
        return original(command, **kwargs)

    monkeypatch.setattr(execution.shell_segments, "extract_execution_segments", counted)
    command = "cat role.subagent.md; printf '%s' marker"
    expected = execution.shell_simple_commands(command)
    parsed.clear()
    with execution.shell_parse_scope():
        first = execution.shell_simple_commands(command)
        first.clear()
        with execution.shell_parse_scope():
            assert execution.shell_simple_commands(command) == expected
        assert parsed == [command]
        for number in range(128):
            execution.shell_simple_commands(f"echo {number}")
        assert execution.shell_simple_commands(command) == expected
        assert parsed.count(command) == 2
        malformed = "cat 'unclosed"
        assert execution.shell_simple_commands(malformed) == [execution.SimpleCommand((), malformed, False)]
        assert execution.shell_simple_commands(malformed) == [execution.SimpleCommand((), malformed, False)]
    with pytest.raises(RuntimeError), execution.shell_parse_scope():
        assert execution.shell_simple_commands(command) == expected
        raise RuntimeError("処理中断")
    with execution.shell_parse_scope():
        assert execution.shell_simple_commands(command) == expected
    assert parsed.count(command) == 4


@pytest.mark.parametrize(
    ("argv", "visible"),
    [
        (["bash", "-co", "pipefail", "printf marker"], True),
        (["bash", "-cO", "extglob", "printf marker"], True),
        (["bash", "--rcfile", "session_review_evidence.py", "-c", "printf marker"], True),
        (["bash", "-c", "printf marker", "session_review_evidence.py"], True),
        (["bash", "-c", "printf marker", "data;session_review_evidence.py"], True),
        (["bash", "-c", "python session_review_evidence.py marker"], False),
        (["bash", "-c", "printf marker; python session_review_evidence.py"], False),
        (["bash", "session_review_evidence.py", "marker"], False),
        ([r"C:\tools\bash", "-c", "python session_review_evidence.py marker"], False),
        ([r"C:\tools\bash", "session_review_evidence.py", "marker"], False),
        ([r"C:\tools\sh", "-c", "python session_review_evidence.py marker"], False),
        ([r"C:\tools\sh", "session_review_evidence.py", "marker"], False),
        ([r"C:\tools\zsh", "-c", "python session_review_evidence.py marker"], False),
        ([r"C:\tools\zsh", "session_review_evidence.py", "marker"], False),
    ],
)
def test_shell_execution_roles_reach_public_queries(tmp_path, capsys, argv, visible) -> None:
    """本文だけを照会し、位置引数・rcfile名で実際の警告を除外しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "id": "call",
                        "status": "completed",
                        "command": argv,
                        "exit_code": 0,
                        "stdout": "warning: probe-visible",
                    },
                },
            }
        ],
    )
    assert evidence.main([str(transcript), "--tool-calls", "--input-regex", "marker"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    calls = [row for row in rows if row.get("kind") == "tool-call"]
    # --tool-callsは自己呼出しも監査できる。警告・本文走査だけが自己生成出力を除く。
    assert len(calls) == 1
    if visible:
        assert calls[0]["text"] == "printf marker"
    for query in (["--warn"], ["--grep", "probe-visible"]):
        assert evidence.main([str(transcript), *query]) == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert any("probe-visible" in row.get("text", "") for row in rows) is visible


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize(
    ("command", "visible"),
    [
        ("bash -c 'printf marker'; python session_review_evidence.py --warn", False),
        ("bash -c 'printf marker' && python session_review_evidence.py --warn", False),
        ("bash -c 'printf marker' || python session_review_evidence.py --warn", False),
        ("bash -c 'printf marker' | python session_review_evidence.py --warn", False),
        ("bash -c 'printf marker' & python session_review_evidence.py --warn", False),
        ("bash -c 'printf marker'\npython session_review_evidence.py --warn", False),
        ("(printf marker; python session_review_evidence.py --warn)", False),
        ("bash -c 'printf marker' 'data;session_review_evidence.py'", True),
        ('bash -c \'printf "%s" "data;session_review_evidence.py"\'', True),
        ("printf '%s' 'bash -c x; python session_review_evidence.py'", True),
        ("printf '%s' 'data;session_review_evidence.py'; bash -c 'printf marker'", True),
    ],
)
def test_compound_shell_self_calls_keep_quoted_data_visible(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str, command: str, visible: bool
) -> None:
    """外側の実自己呼出しを除き、本文・位置引数の引用データは保持する。"""
    output = "warning: probe-visible"
    if runtime == "claude":
        entries = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": command}}],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call", "content": output}]},
            },
        ]
    else:
        entries = [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "id": "call",
                        "status": "completed",
                        "command": command,
                        "exit_code": 0,
                        "stdout": output,
                    },
                },
            }
        ]
    transcript = _write_transcript(tmp_path, entries)
    assert evidence.main([str(transcript), "--tool-calls"]) == 0
    calls = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["text"] for row in calls if row.get("kind") == "tool-call"] == [command]
    for query in (["--warn"], ["--grep", "probe-visible"]):
        assert evidence.main([str(transcript), *query]) == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert any("probe-visible" in row.get("text", "") for row in rows) is visible
