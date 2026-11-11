"""配送本文を独立の比較元に、公開hookの受領証拠・更新案内・継続許可を検収する。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import document_reads, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.read_delivery_receipts_support import (
    _assert_acquisition,
    _assert_allowed_without_guidance,
    _produce,
    _scenario,
)

__all__ = ["_assert_acquisition", "_assert_allowed_without_guidance", "_produce", "_scenario"]


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("syntax", ["generated", "for", "variable", "pipe", "failed-tail", "stderr"])
def test_generated_read_output_records_current_body(scenario, host_environ, host, syntax):
    """同じ実本文を返す構文変形は、生成した取得操作と同じ後続結果になる。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "output.md"), "content": "成果"}
    guide = invoke(host, "PreToolUse", "Write", write)
    assert str(path) in guide
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))
    step = read_delivery.acquisition_step(read_triggers.ReadTarget(path), "codex")
    command = step.split("`", 2)[1]
    if syntax == "for":
        command = f'for f in {shlex.quote(str(path))}; do cmp -s "$f" "$f"; done && {command}'
    elif syntax == "variable":
        command = f'f={shlex.quote(str(path))}; s=1; e=999; sed -n "${{s}},${{e}}p" -- "$f"'
    elif syntax == "pipe":
        command += " | cat"
    elif syntax == "failed-tail":
        command += " && false"
    elif syntax == "stderr":
        command += " >&2"
    invoke(host, "PreToolUse", "Bash", {"command": command})
    executed = subprocess.run(
        ["/bin/bash", "-c", command],
        cwd=path.parent,
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    body = executed.stderr if syntax == "stderr" else executed.stdout
    assert body == path.read_text(encoding="utf-8")
    assert executed.returncode == (1 if syntax == "failed-tail" else 0)
    response = {"stdout": executed.stdout, "stderr": executed.stderr, "exit_code": executed.returncode}
    invoke(host, "PostToolUseFailure" if executed.returncode else "PostToolUse", "Bash", {"command": command}, response)
    if host == "codex":
        assert not read_delivery.target_ready(
            state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(path), "main"
        )
        delivered(json.dumps({"output": body, "exit_code": executed.returncode}, ensure_ascii=False))
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))
    assert read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_nontext_read_prepares_no_text_receipt(scenario, host):
    state, _rows, path, _rollout, invoke, _delivered = scenario
    binary = path.with_name("binary.dat")
    binary.write_bytes(b"\x89PNG\r\n\xff\x00")
    result = invoke(host, "PreToolUse", "Read", {"file_path": str(binary)})
    assert "想定外のエラーが起きた" not in result and '"permissionDecision": "deny"' not in result
    assert not state.get("document_read_candidates")


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_executed_empty_sed_range_keeps_document_unacquired(scenario, host_environ, host):
    """終了0でも存在しない行範囲の実出力は取得の証拠にならない。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    command = f"sed -n '999999p' -- {shlex.quote(str(path))}"
    invoke(host, "PreToolUse", "Bash", {"command": command})
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and not result.stdout and not result.stderr
    invoke(host, "PostToolUse", "Bash", {"command": command}, {"stdout": result.stdout})
    if host == "codex":
        delivered(json.dumps({"output": result.stdout}))
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
    )


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("shape", ["partial", "truncated", "persisted", "empty", "different"])
def test_partial_delivery_only_acquires_visible_scope(scenario, host_environ, host, shape):
    state, rows, path, _rollout, invoke, delivered = scenario
    target = read_triggers.ReadTarget(path, "必要節")
    missing = read_triggers.ReadTarget(path, "別節")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,), ("必要節",)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    body = target.body()
    visible = body if shape in {"partial", "truncated"} else "" if shape in {"persisted", "empty"} else "別の本文\n"
    if host == "codex":
        response = visible
        if shape == "truncated":
            response = f"Warning: truncated output (original token count: 100)\n{visible}…90 tokens truncated…\n末尾\n"
        invoke(host, "PostToolUse", "Bash", {"command": f"cat -- {shlex.quote(str(path))}"}, path.read_text(encoding="utf-8"))
        delivered(response)
    else:
        # Claude Code 2.1.317のReadとBash退避の実記録に対応するフィールド。
        response = {
            "file": {
                "content": visible,
                "startLine": 3,
                "numLines": 3,
                "totalLines": 8,
                "truncatedByTokenCap": shape == "truncated",
            }
        }
        if shape == "persisted":
            response = {"stdout": "", "persistedOutputPath": str(path.parent / "saved.txt")}
        if shape == "empty":
            response = None
        invoke(host, "PostToolUse", "Read", {"file_path": str(path)}, response)
    following = invoke(host, "PreToolUse", "Write", write)
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_delivery.target_ready(records, target, "main") is (shape in {"partial", "truncated"})
    assert not read_delivery.target_ready(records, missing, "main")
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(path), "main")
    _assert_allowed_without_guidance(following)
    if shape == "persisted":
        saved = path.parent / "saved.txt"
        saved.write_text(body, encoding="utf-8")
        command, response = _produce(read_triggers.ReadTarget(saved), host_environ)
        invoke(host, "PreToolUse", "Bash", {"command": command})
        invoke(host, "PostToolUse", "Bash", {"command": command}, response)
        if host == "codex":
            delivered(response["stdout"])
        _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))
        _assert_acquisition(state, target, True)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_received_version_and_new_scope_stay_distinct(scenario, host_environ, host):
    state, rows, path, _rollout, invoke, delivered = scenario
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    target = read_triggers.ReadTarget(path, "必要節")
    row = read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,), ("必要節",))
    rows.append(row)
    invoke(host, "PreToolUse", "Write", write)
    command, response = _produce(target, host_environ)
    old = response["stdout"]
    invoke(host, "PostToolUse", "Bash", {"command": command}, response)
    if host == "codex":
        delivered(old)
    invoke(host, "PreToolUse", "Write", write)
    path.write_text(path.read_text(encoding="utf-8").replace("固有の別本文", "他プロセスによる他節更新"), encoding="utf-8")
    assert str(path) not in invoke(host, "PreToolUse", "Write", write)
    path.write_text(path.read_text(encoding="utf-8").replace("固有の必要本文", "更新された必要本文"), encoding="utf-8")
    update = invoke(host, "PreToolUse", "Write", write)
    assert "取得済み資料の更新あり" in update and '"permissionDecision": "deny"' not in update
    assert str(path) not in invoke(host, "PreToolUse", "Write", write)
    assert document_reads.acquisition_state(state[rules_context.OPERATION_SKILL_READY_KEY], target, "main") == "updated"
    rows[:] = [read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,), ("別節",))]
    assert "未取得" in invoke(host, "PreToolUse", "Write", write)
    _assert_acquisition(state, read_triggers.ReadTarget(path, "別節"), False)
    _assert_allowed_without_guidance(invoke(host, "PreToolUse", "Write", write))


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_generated_makefile_read_preserves_unchanged_target(scenario, host_environ, host):
    """全文に含まれる非連続targetも、他targetの更新後に取得済みを保つ。"""
    _state, rows, path, _rollout, invoke, delivered = scenario
    makefile = path.with_name("Makefile")
    makefile.write_text(
        "VALUE = x\n\nbuild: prep\n\techo build\n\nother:\n\techo other\n\nprep:\n\techo prep\n", encoding="utf-8"
    )
    target = read_triggers.ReadTarget(makefile, "make:build")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (makefile,), (target.heading,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    command, response = _produce(target, host_environ)
    invoke(host, "PostToolUse", "Bash", {"command": command}, response)
    if host == "codex":
        delivered(response["stdout"])
    invoke(host, "PreToolUse", "Write", write)
    makefile.write_text(makefile.read_text(encoding="utf-8").replace("echo other", "echo changed"), encoding="utf-8")
    assert str(makefile) not in invoke(host, "PreToolUse", "Write", write)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_read_before_selection_and_copy_reuse(scenario, host_environ, host):
    state, rows, path, _rollout, invoke, delivered = scenario
    copy = path.parent / "copy.md"
    copy.write_bytes(path.read_bytes())
    invoke(host, "PreToolUse", "Read", {"file_path": str(copy)})
    command, response = _produce(read_triggers.ReadTarget(copy), host_environ)
    invoke(host, "PostToolUse", "Bash", {"command": command}, response)
    if host == "codex":
        delivered(response["stdout"])
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    assert str(path) not in invoke(host, "PreToolUse", "Write", {"file_path": str(path.parent / "out.md"), "content": "成果"})
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "other"
    )


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_received_version_is_not_replaced_between_read_and_observation(scenario, host_environ, host):
    state, rows, path, _rollout, invoke, delivered = scenario
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,), ("必要節",)))
    invoke(host, "PreToolUse", "Read", {"file_path": str(path)})
    command, response = _produce(read_triggers.ReadTarget(path), host_environ)
    old = response["stdout"]
    path.write_text(old.replace("固有の必要本文", "外部で更新した必要本文"), encoding="utf-8")
    invoke(host, "PostToolUse", "Bash", {"command": command}, response)
    if host == "codex":
        delivered(old)
    text = invoke(host, "PreToolUse", "Write", {"file_path": str(path.parent / "out.md"), "content": "成果"})
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert document_reads.acquisition_state(records, read_triggers.ReadTarget(path, "必要節"), "main") == "updated"
    assert '"permissionDecision": "deny"' not in text
    assert "main" not in records.get(read_delivery.document_key(path), [])


@pytest.mark.parametrize("changed", ["manifest", "elsewhere", "required", "new-section"])
def test_worktree_versions_share_proven_logical_document(scenario, host_environ, changed):
    state, rows, path, _rollout, invoke, _delivered = scenario
    relative = "plugin/skills/example/references/rules.md"
    repo = git_repository.init_repository(
        path.parent / "repo",
        files={
            relative: path.read_text(encoding="utf-8"),
            "plugin/.claude-plugin/plugin.json": '{"name":"example-plugin","version":"1.0.0"}',
        },
        commit_message="試験の基準",
    )
    worktree = path.parent / "worktree"
    git_repository.run_git(repo, "worktree", "add", "--detach", str(worktree))
    source, target = repo / relative, worktree / relative
    invoke("claude", "PreToolUse", "Read", {"file_path": str(source)})
    command, response = _produce(read_triggers.ReadTarget(source), host_environ)
    invoke("claude", "PostToolUse", "Bash", {"command": command}, response)
    manifest = target.parents[3] / ".claude-plugin/plugin.json"
    manifest.write_text('{"name":"example-plugin","version":"2.0.0"}', encoding="utf-8")
    heading = "追加節" if changed == "new-section" else "必要節"
    if changed == "elsewhere":
        target.write_text(target.read_text(encoding="utf-8").replace("固有の別本文", "別版の他節"), encoding="utf-8")
    elif changed == "required":
        target.write_text(target.read_text(encoding="utf-8").replace("固有の必要本文", "別版の必要節"), encoding="utf-8")
    elif changed == "new-section":
        target.write_text(target.read_text(encoding="utf-8") + "\n## 追加節\n新しい基準\n", encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (target,), (heading,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    result = invoke("claude", "PreToolUse", "Write", write)
    status = document_reads.acquisition_state(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(target, heading), "main"
    )
    assert status == ("current" if changed == "manifest" else "updated")
    assert ("取得済み資料の更新あり" in result) is (changed != "manifest")
    if changed == "manifest":
        assert str(target) not in result
    elif changed == "new-section":
        assert str(target) in result and "追加節" in result
        _assert_acquisition(state, read_triggers.ReadTarget(target, heading), False)
        _assert_allowed_without_guidance(invoke("claude", "PreToolUse", "Write", write))
    git_repository.run_git(repo, "worktree", "remove", "--force", str(worktree))
