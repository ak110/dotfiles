"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._common.read_triggers import ReadTrigger
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _assert_acquired,
    _guided_without_acquisition,
    _local_plugin_root,
    _memory,
    _observe_bash_result,
    _rows,
    _write_operation,
)

__all__ = [
    "_assert_acquired",
    "_guided_without_acquisition",
    "_local_plugin_root",
    "_memory",
    "_observe_bash_result",
    "_rows",
    "_write_operation",
]


@pytest.mark.parametrize("subject", ["main", "child", "codex"])
@pytest.mark.parametrize("separator", ["; ", " && ", "\n"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.usefixtures("memory")
def test_mixed_cat_sed_public_acquisition_preserves_actor_and_hash(tmp_path, monkeypatch, subject, separator, reverse):
    """配送された本文で解除し、他主体と未受信の更新を区別する。"""
    paths = _rows(tmp_path, monkeypatch, "path:*.py", count=2)
    actor = {"agent_id": "child"} if subject == "child" else {"turn_id": "turn"} if subject == "codex" else {}
    operation = {
        "session_id": "mixed",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "code.py"), "content": "value = 1\n"},
        **actor,
    }
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and all(str(path) in first.stdout for path in paths)
    commands = [f"cat -- {paths[0]}", f"sed -n '1,$p' -- {paths[1]}"]
    bodies = [path.read_text() for path in paths]
    if reverse:
        commands.reverse()
        bodies.reverse()
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **operation,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": separator.join(commands)},
            "tool_response": {"stdout": "".join(bodies), "exit_code": 0},
        },
    )
    assert observed.returncode == 0
    final = _run_hook_in_process("pretooluse", operation)
    acquired = True
    assert final.returncode == 0 and all((str(path) not in final.stdout) is acquired for path in paths)
    for path in paths:
        _assert_acquired(operation, path, True)
    other = {**operation, "agent_id": "other"}
    pending = _run_hook_in_process("pretooluse", other)
    assert pending.returncode == 0 and all(str(path) in pending.stdout for path in paths)
    for path in paths:
        _assert_acquired(other, path, False)
    paths[0].write_text("# 更新した資料\n更新後の本文\n", encoding="utf-8")
    changed = _run_hook_in_process("pretooluse", operation)
    assert changed.returncode == 0 and str(paths[0]) in changed.stdout
    assert (str(paths[1]) not in changed.stdout) is acquired
    _assert_acquired(operation, paths[0], False)
    _assert_acquired(operation, paths[1], True)


@pytest.mark.parametrize("subject", ["main", "child", "codex"])
@pytest.mark.parametrize("joined", [False, True])
@pytest.mark.usefixtures("memory")
def test_cat_only_preserves_full_body_acquisition_with_surrounding_output(tmp_path, monkeypatch, subject, joined):
    """sedがない取得では、結果内の全文を受理する従来契約を維持する。"""
    paths = _rows(tmp_path, monkeypatch, "path:*.py", count=2 if joined else 1)
    actor = {"agent_id": "child"} if subject == "child" else {"turn_id": "turn"} if subject == "codex" else {}
    operation = {
        "session_id": "cat-only",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "code.py"), "content": "value = 1\n"},
        **actor,
    }
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and all(str(path) in first.stdout for path in paths)
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **operation,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": " && ".join(f"cat -- {path}" for path in paths)},
            "tool_response": {
                "stdout": "結果の前置き\n" + "".join(path.read_text() for path in paths) + "結果の後記\n",
                "exit_code": 0,
            },
        },
    )
    assert observed.returncode == 0
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and all(str(path) not in final.stdout for path in paths)
    for path in paths:
        _assert_acquired(operation, path, True)


@pytest.mark.parametrize("subject", ["main", "child", "codex"])
@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.usefixtures("memory")
def test_mixed_skill_body_does_not_replace_main_skill_invocation(tmp_path, monkeypatch, subject, history, reverse):
    """メインは混在Bash取得後もSkill起動を要し、参照資料と他主体の通常取得を妨げない。"""
    root = tmp_path / "plugin"
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"example-plugin"}', encoding="utf-8")
    skill = root / "skills/example/SKILL.md"
    reference = skill.parent / "references/rule.md"
    reference.parent.mkdir(parents=True)
    skill.write_text("# スキル固有本文\n", encoding="utf-8")
    reference.write_text("# 参照資料\n参照の基準\n", encoding="utf-8")
    row = ReadTrigger(skill, 1, "操作", ("path:*.py",), (skill, reference))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(read_delivery.read_triggers, "reference_method_targets", lambda *_args, **_kwargs: ())
    actor = {"agent_id": "child"} if subject == "child" else {"turn_id": "turn"} if subject == "codex" else {}
    operation = {
        "session_id": "mixed-skill",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "code.py"), "content": "value = 1\n"},
        **actor,
    }
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and str(reference) in first.stdout
    assert (str(skill) in first.stdout) is (subject != "main")
    paths = [reference, skill] if reverse else [skill, reference]
    command = f"cat -- {paths[0]} && sed -n '1,$p' -- {paths[1]}"
    response = {"stdout": "".join(path.read_text() for path in paths), "exit_code": 0}
    if history:
        entries = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "mixed-skill", "name": "Bash", "input": {"command": command}},
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "mixed-skill", "content": json.dumps(response)},
                    ]
                },
            },
        ]
        if subject == "child":
            for entry in entries:
                entry["agentId"] = "child"
        log = tmp_path / "mixed-skill.jsonl"
        log.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
        operation["transcript_path"] = str(log)
    else:
        observed = _run_hook_in_process(
            "posttooluse",
            {
                **operation,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": response,
            },
        )
        assert observed.returncode == 0
    final = _run_hook_in_process("pretooluse", operation)
    if history:
        assert final.returncode == 0 and not final.stdout and not final.stderr
        _assert_acquired(operation, reference, False)
        _assert_acquired(operation, skill, False)
        observed = _run_hook_in_process(
            "posttooluse",
            {
                **operation,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": response,
            },
        )
        assert observed.returncode == 0
        final = _run_hook_in_process("pretooluse", operation)
    _assert_acquired(operation, reference, True)
    _assert_acquired(operation, skill, True)
    if subject == "main":
        assert final.returncode == 0 and not final.stdout and not final.stderr
        _assert_acquired(operation, skill, False, invocation=True)
        invoked = _run_hook_in_process(
            "posttooluse",
            {
                **operation,
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "example-plugin:example"},
                "tool_response": "起動成功",
            },
        )
        assert invoked.returncode == 0
        _assert_acquired(operation, skill, True, invocation=True)
        final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and str(skill) not in final.stdout and str(reference) not in final.stdout


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("sed", ["sed", "/bin/sed", "/usr/bin/sed"])
@pytest.mark.usefixtures("memory")
def test_public_compound_reads_before_diagnostics_record_received_ranges(tmp_path, monkeypatch, host, sed, host_environ):
    """独立診断を含む実出力から、受信した資料の必要範囲だけを取得済みとする。

    Codex 0.166.0の実記録で観測したcat3資料とsed、続くsed2件・wc・if/findの構成を隔離する。
    """
    paths = _rows(tmp_path, monkeypatch, "tool:Write", count=5)
    paths[3].write_text("".join(f"基準{i}\n" for i in range(1, 161)), encoding="utf-8")
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, paths[4])
    quoted = [shlex.quote(str(path)) for path in paths]
    commands = [
        f"cat -- {' '.join(quoted[:3])}; {sed} -n '1,85p' -- {quoted[3]}",
        f"{sed} -n '86,$p' -- {quoted[3]}; {sed} -n '1,80p' -- {quoted[4]}; "
        f"wc -lc -- {' '.join(quoted[:3])}; if test -d {shlex.quote(str(tmp_path))}; "
        f"then find {shlex.quote(str(tmp_path))} -maxdepth 1 -type f; fi",
    ]
    for index, command in enumerate(commands):
        actual = subprocess.run(
            ["bash", "-c", command],
            cwd=tmp_path,
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        observed = _observe_bash_result(payload, command, actual, host)
        assert observed.returncode == 0
        final = _run_hook_in_process("pretooluse", operation)
        assert final.returncode == 0 and not final.stdout and not final.stderr
        for item, path in enumerate(paths):
            _assert_acquired(payload, path, index != 0 or item < 3)
    command = " && ".join(f"cat -- {path}" for path in quoted)
    actual = subprocess.run(
        ["bash", "-c", command], cwd=tmp_path, env=host_environ(), capture_output=True, text=True, encoding="utf-8", check=True
    )
    assert _observe_bash_result(payload, command, actual, host).returncode == 0
    assert not _run_hook_in_process("pretooluse", operation).stdout
    for path in paths:
        _assert_acquired(payload, path, True)


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("separator", ["; ", "\n"])
@pytest.mark.usefixtures("memory")
def test_public_short_circuit_hidden_by_later_success_keeps_document_unacquired(
    tmp_path, monkeypatch, host, separator, host_environ
):
    """終了0でも、JSON条件の失敗で実行しなかった資料読取を取得証拠にしない。"""
    path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    condition = tmp_path / "condition.json"
    condition.write_text('{"flag": false}\n', encoding="utf-8")
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, path)
    command = f"jq -e '.flag' {shlex.quote(str(condition))} && cat -- {shlex.quote(str(path))}{separator}true"
    actual = subprocess.run(
        ["bash", "-c", command], cwd=tmp_path, env=host_environ(), capture_output=True, text=True, encoding="utf-8", check=True
    )
    assert actual.stdout == "false\n" and not actual.stderr
    observed = _observe_bash_result(payload, command, actual, host)
    assert observed.returncode == 0
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and not final.stdout and not final.stderr
    _assert_acquired(payload, path, False)
