"""公開hookの取得例から実本文の配送まで、二層予算と未確定対象の契約を確かめる。"""

import argparse
import json
import re
import shlex
import subprocess

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing import session_evidence_support
from agent_toolkit._testing.document_delivery import prepare_state, run_hook


@pytest.fixture(name="state")
def _state(monkeypatch):
    state: dict = {}

    def read(_session, **_kwargs):
        return state

    def update(_session, mutate, **_kwargs):
        mutate(state)

    prepare_state(monkeypatch, read, update)
    return state


def _generated_requests(context, host_environ):
    """生成したJSの実行結果から引数を取り出し、pragmaとの予算対応を検査する。

    toolsとtextはCodexの公開契約に沿う代役であり、シェル実行とhook配送は呼出側で実施する。
    """
    blocks = re.findall(r"```javascript\n(.*?)\n```", context, re.DOTALL)
    assert blocks
    program = (
        "const tools = {exec_command: async request => "
        "({request, output: '本文', exit_code: 7, session_id: 31, diagnostic: '診断'})};\n"
        "const text = value => process.stdout.write(JSON.stringify(value) + '\\n');\n"
        + "\n".join("{\n" + block + "\n}" for block in blocks)
    )
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=program,
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    results = [json.loads(line) for line in result.stdout.splitlines()]
    for value in results:
        assert {key: value[key] for key in ("output", "exit_code", "session_id", "diagnostic")} == {
            "output": "本文",
            "exit_code": 7,
            "session_id": 31,
            "diagnostic": "診断",
        }
    requests = [value["request"] for value in results]
    assert len(requests) == len(blocks)
    for block, request in zip(blocks, requests, strict=True):
        outer = json.loads(block.splitlines()[0].removeprefix("// @exec: "))["max_output_tokens"]
        assert 0 < request["max_output_tokens"] <= outer <= 20_000
    return requests


@pytest.mark.parametrize("scenario", ["short", "japanese", "sections", "multiple", "one-line"])
def test_public_hook_guidance_preserves_selected_body_and_budgets(tmp_path, monkeypatch, state, host_environ, scenario):
    """短文・不均等な85KB・非連続節・複数資料・超長行を、公開hookと実取得で検査する。"""
    document = tmp_path / "file's $name.md"
    sections = ["必須A", "必須B"] if scenario == "sections" else [""]
    if scenario == "sections":
        first, last = "## 必須A\n第一の固有基準\n", "## 必須B\n第二の固有基準\n"
        body = "# 資料\n" + first + "## 対象外\n不要本文\n" + last + "## 後続\n不要末尾\n"
        expected = (first + last).encode("utf-8")
    elif scenario == "japanese":
        body = '# 資料\n短文"\\\n' + "長" * 14000 + "\r\n" + "末" * 14000
        expected = body.encode("utf-8")
    elif scenario == "one-line":
        body = "# 資料\n" + "超" * 30000 + "\n## 末尾\n取得可能な本文\n"
        expected = "# 資料\n## 末尾\n取得可能な本文\n".encode()
    else:
        body = "# 資料\n短い固有基準\n"
        expected = body.encode("utf-8")
    document.write_bytes(body.encode("utf-8"))
    paths = [document]
    expected_by_path = {str(document): expected}
    if scenario == "multiple":
        other = tmp_path / "other.md"
        other.write_text("# 別資料\n" + "別の基準\n" * 6000, encoding="utf-8")
        paths.append(other)
        expected_by_path[str(other)] = other.read_bytes()
    rows = [
        read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Write",), (path,), (heading,))
        for path in paths
        for heading in sections
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    payload = {"session_id": "acquisition-budget", "turn_id": "turn", "cwd": str(tmp_path)}
    guided = run_hook(
        "pretooluse",
        {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "本文"}},
    )
    assert guided.returncode == 0 and not guided.stderr
    specific = json.loads(guided.stdout)["hookSpecificOutput"]
    assert specific.get("permissionDecision") != "deny"
    context = specific["additionalContext"]
    if scenario == "one-line":
        assert "行範囲では収まらない" in context and f"{document}:2" in context
    received: dict[str, bytes] = {}
    for request in _generated_requests(context, host_environ):
        inputs = {"command": request["cmd"]}
        before = run_hook("pretooluse", {**payload, "tool_name": "Bash", "tool_input": inputs})
        assert before.returncode == 0 and not before.stderr
        produced = subprocess.run(shlex.split(request["cmd"]), capture_output=True, timeout=10, check=False)
        assert produced.returncode == 0 and not produced.stderr
        response = {"stdout": produced.stdout.decode("utf-8"), "stderr": "", "exit_code": produced.returncode}
        displayed_size = len(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        assert request["max_output_tokens"] >= (displayed_size + 2) // 3
        path = shlex.split(request["cmd"])[-1]
        received[path] = received.get(path, b"") + produced.stdout
        observed = run_hook(
            "posttooluse",
            {**payload, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": inputs, "tool_response": response},
        )
        assert observed.returncode == 0 and not observed.stderr
    assert received == expected_by_path
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    for path in paths:
        for heading in sections:
            ready = read_delivery.target_ready(records, read_triggers.ReadTarget(path, heading), "main")
            assert ready is (scenario != "one-line")


@pytest.mark.parametrize("scenario", ["brace", "brace-shadow", "generated", "quoted", "literal", "normal"])
def test_public_hook_uncertain_shell_targets_continue_and_observe(tmp_path, monkeypatch, state, host_environ, scenario):
    """未展開・前段生成を実行前に実在資料とせず、通常読取と実配送の観測を保つ。"""
    first, second = tmp_path / "journal.stdout", tmp_path / "journal.stderr"
    first.write_text("固有の標準出力\n", encoding="utf-8")
    second.write_text("固有の標準エラー\n", encoding="utf-8")
    if scenario in {"brace", "brace-shadow"}:
        command = "cat -- " + shlex.quote(str(tmp_path)) + "/journal.{stdout,stderr}"
        expected = first.read_bytes() + second.read_bytes()
        targets = [first, second]
    elif scenario == "generated":
        first = tmp_path / "generated.md"
        command = f"printf '生成した固有本文\\n' > {shlex.quote(str(first))}; cat -- {shlex.quote(str(first))}"
        expected = "生成した固有本文\n".encode()
        targets = [first]
    else:
        if scenario in {"quoted", "literal"}:
            first = tmp_path / ("literal.[1].{stdout,stderr}" if scenario == "quoted" else "literal.{word}")
            first.write_text("引用した固有本文\n", encoding="utf-8")
        command = "cat -- " + (
            shlex.quote(str(tmp_path)) + "/literal.{word}" if scenario == "literal" else shlex.quote(str(first))
        )
        expected, targets = first.read_bytes(), [first]
    rule = tmp_path / "rule.md"
    rule.write_text("# 基準\n読取に必要な基準\n", encoding="utf-8")
    operand = tmp_path / "journal.{stdout,stderr}" if scenario in {"brace", "brace-shadow"} else first
    if scenario == "brace-shadow":
        operand.write_bytes(expected)
    rows = [read_triggers.ReadTrigger(rule, 1, "判断", (f"read:{operand}",), (operand,), ("",))]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    payload = {"session_id": "uncertain-shell", "turn_id": "turn", "cwd": str(tmp_path)}
    inputs = {"command": command}
    before = run_hook("pretooluse", {**payload, "tool_name": "Bash", "tool_input": inputs})
    assert before.returncode == 0 and not before.stderr
    assert "想定外エラー" not in before.stdout and "存在しない" not in before.stdout
    assert '"permissionDecision": "deny"' not in before.stdout
    produced = subprocess.run(["/bin/bash", "-c", command], env=host_environ(), capture_output=True, timeout=10, check=False)
    assert produced.returncode == 0 and not produced.stderr and produced.stdout == expected
    observed = run_hook(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": inputs,
            "tool_response": {"stdout": produced.stdout.decode("utf-8"), "stderr": "", "exit_code": produced.returncode},
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    assert "想定外エラー" not in observed.stdout
    if scenario == "brace-shadow":
        assert (
            document_reads.acquisition_state(
                state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(operand), "main"
            )
            == "missing"
        )
    # braceの実展開を静的に復元して読了とせず、確定した通常読取で実配送を確認する。
    for path in targets:
        normal = {"command": "cat -- " + shlex.quote(str(path))}
        received = run_hook(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": normal,
                "tool_response": {"stdout": path.read_text(encoding="utf-8"), "stderr": "", "exit_code": 0},
            },
        )
        assert received.returncode == 0 and not received.stderr
        assert (
            document_reads.acquisition_state(
                state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(path), "main"
            )
            != "missing"
        )


def test_public_hook_continuation_records_reason_and_preserves_independent_protection(tmp_path, monkeypatch, state, capsys):
    """支援案内の適用判断から元書込へ進み、通常取得と独立保護を同じ操作列で保つ。"""
    rule = tmp_path / "python-rule.md"
    rule.write_text("# Python基準\nPythonコードの編集に使う。\n", encoding="utf-8")
    rows = [read_triggers.ReadTrigger(rule, 1, "判断", ("tool:Write",), (rule,), ("",), condition="Pythonコードを編集する前")]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    payload = {"session_id": "reasoned-continuation", "cwd": str(tmp_path)}
    target = tmp_path / "note.txt"
    inputs = {"file_path": str(target), "content": "保存する本文\n"}
    guided = run_hook("pretooluse", {**payload, "tool_name": "Write", "tool_input": inputs})
    assert guided.returncode == 0 and not guided.stderr
    assert str(rule) in guided.stdout
    reason = "本文をnote.txtへ保存する操作で、Pythonコードの編集は含まない。Python基準の適用条件は成立しない。"
    record = {"notice": json.loads(guided.stdout), "purpose": "本文保存", "reason": reason}
    for _ in range(2):
        continued = run_hook("pretooluse", {**payload, "tool_name": "Write", "tool_input": inputs})
        assert continued.returncode == 0 and not continued.stderr
        output = json.loads(continued.stdout) if continued.stdout else {}
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    target.write_text(inputs["content"], encoding="utf-8")
    record["result"] = target.read_text(encoding="utf-8")
    saved = tmp_path / "continuation.json"
    saved.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    restored = json.loads(saved.read_text(encoding="utf-8"))
    assert restored["notice"] == json.loads(guided.stdout) and restored["reason"] == reason
    assert restored["result"] == "保存する本文\n"
    transcript = tmp_path / "continuation.jsonl"
    session_evidence_support.write_jsonl(
        transcript,
        [
            session_evidence_support.timestamped_entry(
                "2026-11-01T00:00:00Z", record["notice"]["hookSpecificOutput"]["additionalContext"]
            ),
            session_evidence_support.assistant_text(reason),
            session_evidence_support.claude_call("2026-11-01T00:00:01Z", "write-note", "Write", inputs),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "write-note", "content": record["result"]}],
                },
            },
        ],
    )
    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="session-review-evidence",
                script_args=[
                    str(transcript),
                    "--detail",
                    "claude:continuation:1",
                    "--detail",
                    "claude:continuation:2",
                    "--detail",
                    "claude:continuation:4",
                ],
            )
        )
        == 0
    )
    evidence = session_evidence_support.read_jsonl(capsys, raw=True)
    assert any(item.get("line") == 1 and str(rule) in item.get("text", "") for item in evidence)
    assert any(item.get("line") == 2 and item.get("text") == reason for item in evidence)
    assert any(
        item.get("line") == 4 and item.get("tool") == "write-note" and item.get("text") == "保存する本文" for item in evidence
    )
    observed = run_hook(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(rule)},
            "tool_response": {"file": {"content": rule.read_text(encoding="utf-8"), "startLine": 1}},
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    assert (
        document_reads.acquisition_state(
            state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(rule), "main"
        )
        != "missing"
    )
    protected = run_hook("pretooluse", {**payload, "tool_name": "Bash", "tool_input": {"command": "pkill -f worker"}})
    assert protected.returncode == 0 and not protected.stderr
    assert json.loads(protected.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
