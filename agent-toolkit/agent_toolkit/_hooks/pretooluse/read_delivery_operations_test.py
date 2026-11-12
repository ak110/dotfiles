"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import os
import pathlib
import shlex
import tempfile

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.read_triggers import ReadTrigger, parse_tables
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.pretooluse_support import _additional_context
from agent_toolkit._testing.read_delivery_support import (
    _assert_acquired,
    _guidance,
    _local_plugin_root,
    _memory,
    _rows,
)

__all__ = ["_assert_acquired", "_guidance", "_local_plugin_root", "_memory", "_rows"]


@pytest.mark.parametrize(
    "filename,before",
    [
        ("sample_test.py", "def test_value():\n    assert 1 == 1\n"),
        ("sample.test.ts", "test('value', () => expect(1).toBe(1));\n"),
        ("sample.spec.tsx", "test('view', () => <div>1</div>);\n"),
        ("src/lib.rs", "#[test]\nfn value() { assert_eq!(1, 1); }\n"),
    ],
)
@pytest.mark.parametrize(
    "tool,exists", [("Write", False), ("Write", True), ("Edit", True), ("apply_patch", False), ("apply_patch", True)]
)
@pytest.mark.parametrize("acquired", [False, True])
@pytest.mark.usefixtures("memory")
def test_real_test_file_declaration_guides_both_standards_and_consumes_acquisition(
    tmp_path, monkeypatch, filename, before, tool, exists, acquired
):
    """現物宣言から試験編集の両基準を初回に案内し、取得済み本文は再要求しない。"""
    root = pathlib.Path(__file__).resolve().parents[3]
    owner = root.parent / "agent-basics/skills/writing-standards/SKILL.md"
    references = tuple(owner.parent / "references" / name for name in ("testing.md", "testing-selection.md"))
    rows = parse_tables(owner, root, strict=True)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    target = tmp_path / filename
    if exists:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(before, encoding="utf-8")
    after = before.replace("1", "2")
    if tool == "Write":
        inputs = {"file_path": str(target), "content": after}
    elif tool == "Edit":
        inputs = {"file_path": str(target), "old_string": before, "new_string": after}
    elif exists:
        removed = "".join("-" + line + "\n" for line in before.splitlines())
        added = "".join("+" + line + "\n" for line in after.splitlines())
        inputs = {"command": f"*** Begin Patch\n*** Update File: {target}\n@@\n{removed}{added}*** End Patch\n"}
    else:
        added = "".join("+" + line + "\n" for line in after.splitlines())
        inputs = {"command": f"*** Begin Patch\n*** Add File: {target}\n{added}*** End Patch\n"}
    payload = {
        "session_id": "test-file-standards",
        "cwd": str(tmp_path),
        **({"turn_id": "turn"} if tool == "apply_patch" else {}),
    }

    def acquire():
        for reference in references:
            observed = _run_hook_in_process(
                "posttooluse",
                {
                    **payload,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Read",
                    "tool_input": {"file_path": str(reference)},
                    "tool_response": reference.read_text(encoding="utf-8"),
                },
            )
            assert observed.returncode == 0 and not observed.stderr
            _assert_acquired(payload, reference, True)

    if acquired:
        # 初回Preより先に取得し、案内済み通知の抑止と取得済み判定を区別する。
        acquire()
    operation = {**payload, "tool_name": tool, "tool_input": inputs}
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and not first.stderr
    assert json.loads(first.stdout).get("hookSpecificOutput", {}).get("permissionDecision") != "deny" if first.stdout else True
    for reference in references:
        assert (str(reference) in _additional_context(first)) is (not acquired)
        _assert_acquired(payload, reference, acquired)
    if not acquired:
        acquire()
    repeated = _run_hook_in_process("pretooluse", operation)
    assert repeated.returncode == 0 and not repeated.stderr
    assert (
        json.loads(repeated.stdout).get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
        if repeated.stdout
        else True
    )
    for reference in references:
        assert str(reference) not in _additional_context(repeated)
        _assert_acquired(payload, reference, True)


@pytest.mark.parametrize("operation", ["path:*.py", "write:*.py", "existing-file-edit", "body-writing"])
@pytest.mark.usefixtures("memory")
def test_temporary_area_writes_do_not_require_artifact_reads(tmp_path, monkeypatch, operation):
    """managed-tempとClaude Codeの一時領域への書込には成果物向けの必読を課さず、再書込も遮断しない。"""
    path = _rows(tmp_path, monkeypatch, operation, count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path)}
    directories = [managed_temp.temp_root_path() / "session-0123456789abcdef"]
    if hasattr(os, "getuid"):
        # Claude Codeは一時ディレクトリ配下の`claude-<uid>`へタスク出力とscratchpadを置く（POSIXだけ）。
        directories.append(pathlib.Path(tempfile.gettempdir()) / f"claude-{os.getuid()}" / "project/session/scratchpad")
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        script = directory / "aggregate.py"
        script.write_text("print(1)\n", encoding="utf-8")
        operation_input = {"file_path": str(script), "content": "print(2)\n"}
        for _ in range(2):
            decision = read_delivery.deliver(payload, "Write", operation_input, "test")
            assert decision.block is None
            assert not _guidance(decision)
    artifact = tmp_path / "artifact.py"
    artifact.write_text("print(1)\n", encoding="utf-8")
    first = read_delivery.deliver(payload, "Write", {"file_path": str(artifact), "content": "print(2)\n"}, "test")
    assert _guidance(first)
    read_delivery.notice_module.record_delivery("\n".join(first.notices))
    assert not _guidance(read_delivery.deliver(payload, "Write", {"file_path": str(artifact), "content": "print(3)\n"}, "test"))
    _assert_acquired(payload, path, False)
    for directory in directories:
        decision = read_delivery.deliver(
            payload, "Write", {"file_path": str(directory / "aggregate.py"), "content": "print(3)\n"}, "test"
        )
        assert decision.block is None
        assert not _guidance(decision)
    if operation == "body-writing":
        # 同じ基準の実配送後は、一時領域の文章原稿でも再掲しない。
        draft = managed_temp.temp_root_path() / "session-0123456789abcdef" / "awi-body.md"
        assert not _guidance(read_delivery.deliver(payload, "Write", {"file_path": str(draft), "content": "# 目的\n"}, "test"))


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_repeated_write_is_allowed_without_fabricating_acquisition(tmp_path, monkeypatch, codex):
    """公開Pre/Postで初回案内・再編集許可と、失敗・成功による取得証拠の違いを確認する。"""
    path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    operation = {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "本文"}}
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and str(path) in first.stdout
    second = _run_hook_in_process("pretooluse", operation)
    assert second.returncode == 0 and not second.stdout and not second.stderr
    _assert_acquired(payload, path, False)
    failed = {
        **payload,
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash",
        "tool_input": {"command": f"cat -- {path}"},
        "tool_response": {"stdout": "", "exit_code": 1},
    }
    assert _run_hook_in_process("posttooluse", failed).returncode == 0
    repeated = _run_hook_in_process("pretooluse", operation)
    assert repeated.returncode == 0 and not repeated.stdout and not repeated.stderr
    _assert_acquired(payload, path, False)
    complete = {
        **payload,
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "tool_response": path.read_text(),
    }
    assert _run_hook_in_process("posttooluse", complete).returncode == 0
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and str(path) not in final.stdout
    _assert_acquired(payload, path, True)


@pytest.mark.parametrize(
    "command",
    ["$COMMAND > {out}", "> {out}", "sh -c 'printf x > {out}' name \"$ARG\""],
    ids=["unknown-command", "redirect-only", "static-shell-body"],
)
@pytest.mark.usefixtures("memory")
def test_unknown_execution_with_static_output_guides_once_and_records_full_read(tmp_path, monkeypatch, command):
    """未知の実行の静的な絶対出力先にも初回案内を出し、再編集許可と取得証拠を分けて検証する。

    実行するコマンド自体は起動せず、公開PreToolUse・PostToolUseの入力だけで確かめる。
    """
    out = tmp_path / "out.md"
    path = _rows(tmp_path, monkeypatch, f"write:{tmp_path}/*.md", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path)}
    operation = {**payload, "tool_name": "Bash", "tool_input": {"command": command.format(out=out)}}
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and str(path) in first.stdout
    repeated = _run_hook_in_process("pretooluse", operation)
    assert repeated.returncode == 0 and not repeated.stdout and not repeated.stderr
    _assert_acquired(payload, path, False)
    complete = {
        **payload,
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "tool_response": path.read_text(encoding="utf-8"),
    }
    assert _run_hook_in_process("posttooluse", complete).returncode == 0
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and str(path) not in final.stdout
    _assert_acquired(payload, path, True)


@pytest.mark.usefixtures("memory")
def test_unknown_output_path_is_not_protected_as_a_guessed_path(tmp_path, monkeypatch):
    """出力先そのものが未知の入力は、推測した実パスの保護で代用しない。"""
    path = _rows(tmp_path, monkeypatch, f"write:{tmp_path}/*.md", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path)}
    operation = {**payload, "tool_name": "Bash", "tool_input": {"command": 'printf x > "$ROOT/out.md"'}}
    result = _run_hook_in_process("pretooluse", operation)
    assert result.returncode == 0 and str(path) not in result.stdout


@pytest.mark.parametrize("tool", ["Read", "Bash", "Skill"])
@pytest.mark.usefixtures("memory")
def test_parallel_acquisition_guidance(tmp_path, monkeypatch, tool):
    """Preに並行相手が未記録でも、取得中は案内を保留して非取得操作で未取得だけを案内する。"""
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=2)
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Read", "tool:Bash", "tool:Skill", "tool:Do"), tuple(paths))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "test", "cwd": str(tmp_path)}
    inputs = (
        {"file_path": str(paths[0])}
        if tool == "Read"
        else {"command": f"cat -- {paths[0]}"}
        if tool == "Bash"
        else {"skill": "local"}
    )
    initial = _run_hook_in_process("pretooluse", {**payload, "tool_name": tool, "tool_input": inputs})
    assert initial.returncode == 0 and all(str(path) not in initial.stdout for path in paths)
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(paths[0])},
            "tool_response": paths[0].read_text(),
        },
    )
    assert observed.returncode == 0
    following = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Do", "tool_input": {}})
    assert following.returncode == 0 and str(paths[0]) not in following.stdout and str(paths[1]) in following.stdout


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("tool", ["cat", "sed"])
@pytest.mark.parametrize("result", ["full", "partial", "failed", "truncated"])
@pytest.mark.usefixtures("memory")
def test_cd_acquisition_defers_guidance_until_non_read(tmp_path, monkeypatch, codex, tool, result):
    """cd後の静的取得では全依存案内を保留し、次の操作で成功全文取得だけを除く。"""
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=2)
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Bash", "tool:Do"), tuple(paths))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "cd-acquisition", "cwd": str(tmp_path.parent), **({"turn_id": "turn"} if codex else {})}
    source = paths[0].read_text()
    body = source.splitlines(keepends=True)[0] if result == "partial" else source
    script = "1p" if result == "partial" else "1,$p"
    read = f"cat -- {paths[0].name}" if tool == "cat" else f"sed -n '{script}' -- {paths[0].name}"
    inputs = {"command": f"cd -- {shlex.quote(str(tmp_path))} && {read}"}
    initial = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Bash", "tool_input": inputs})
    assert initial.returncode == 0 and all(str(path) not in initial.stdout for path in paths)
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUseFailure" if result == "failed" else "PostToolUse",
            "tool_name": "Bash",
            "tool_input": inputs,
            "tool_response": {
                "stdout": body,
                "exit_code": int(result == "failed"),
                **({"persistedOutputPath": "/tmp/out.txt"} if result == "truncated" else {}),
            },
        },
    )
    assert observed.returncode == 0
    following = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Do", "tool_input": {}})
    if codex:
        # 外側配送を回収するObserveDeliveryで初回案内を配送済み。
        assert following.returncode == 0 and not following.stdout
        # 終了コード・退避先メタデータと独立に、この合成応答では実本文全量を配送している。
        _assert_acquired(payload, paths[0], result != "partial")
        _assert_acquired(payload, paths[1], False)
        return
    assert following.returncode == 0 and str(paths[1]) in following.stdout
    assert (str(paths[0]) not in following.stdout) is (result != "partial")


@pytest.mark.parametrize(
    "suffix",
    [
        "; touch output",
        "; printf data > output",
        "; sed -i 's/a/b/' output",
        " > output",
        " | tee output",
        " & touch output",
        "; echo $(touch output)",
    ],
)
@pytest.mark.usefixtures("memory")
def test_cd_read_with_side_effects_keeps_dependency_guidance(tmp_path, monkeypatch, suffix):
    """cd付き読取に書込や置換内の副作用を加えた操作では依存案内を保留しない。"""
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=2)
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Bash",), tuple(paths))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    command = f"cd {shlex.quote(str(tmp_path))} && cat -- {paths[0].name}{suffix}"
    result = _run_hook_in_process(
        "pretooluse",
        {"session_id": "cd-side-effect", "cwd": str(tmp_path.parent), "tool_name": "Bash", "tool_input": {"command": command}},
    )
    assert result.returncode == 0 and str(paths[1]) in result.stdout
