"""配送本文を独立の比較元に、公開hookの取得・更新・遮断まで検収する。"""

import importlib
import json
import shlex
import subprocess

import pytest

from agent_toolkit._common import plugin_roots, read_triggers, session_state
from agent_toolkit._hooks import document_reads, notice, rules_context, work_contract
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


def _produce(target, host_environ):
    """案内が生成した取得コマンドを実行し、未加工のstdout・stderrを返す。"""
    command = read_delivery.acquisition_step(target, "codex").split("`", 2)[1]
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    return command, {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}


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

    def delivered(body, *, destination=None):
        # Codex 0.166.0のrolloutで観測した外側output配列と、exec_commandのJSON文字列。
        entry = {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "outer",
                "output": [{"type": "input_text", "text": "Script completed\nOutput:\n"}, {"type": "input_text", "text": body}],
            },
        }
        with (destination or rollout).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return state, rows, path, rollout, invoke, delivered


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("syntax", ["generated", "for", "variable", "pipe", "failed-tail", "stderr"])
def test_generated_read_output_releases_public_write(scenario, host_environ, host, syntax):
    """同じ実本文を返す構文変形は、生成した取得操作と同じ後続結果になる。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "output.md"), "content": "成果"}
    guide = invoke(host, "PreToolUse", "Write", write)
    assert str(path) in guide
    assert '"permissionDecision": "deny"' in invoke(host, "PreToolUse", "Write", write)
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
    assert str(path) not in invoke(host, "PreToolUse", "Write", write)
    assert read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_nontext_read_prepares_no_text_receipt(scenario, host):
    state, _rows, path, _rollout, invoke, _delivered = scenario
    binary = path.with_name("binary.dat")
    binary.write_bytes(b"\x89PNG\r\n\xff\x00")
    result = invoke(host, "PreToolUse", "Read", {"file_path": str(binary)})
    assert "想定外エラー" not in result and '"permissionDecision": "deny"' not in result
    assert not state.get("document_read_candidates")


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_executed_empty_sed_range_keeps_write_blocked(scenario, host_environ, host):
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
    assert '"permissionDecision": "deny"' in invoke(host, "PreToolUse", "Write", write)
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
    assert ('"permissionDecision": "deny"' in following) is (shape not in {"partial", "truncated"})
    if shape == "persisted":
        saved = path.parent / "saved.txt"
        saved.write_text(body, encoding="utf-8")
        command, response = _produce(read_triggers.ReadTarget(saved), host_environ)
        invoke(host, "PreToolUse", "Bash", {"command": command})
        invoke(host, "PostToolUse", "Bash", {"command": command}, response)
        if host == "codex":
            delivered(response["stdout"])
        assert str(path) not in invoke(host, "PreToolUse", "Write", write)


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
    assert '"permissionDecision": "deny"' in invoke(host, "PreToolUse", "Write", write)


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
    assert status == ("updated" if changed == "required" else "missing" if changed == "new-section" else "current")
    assert ("取得済み資料の更新あり" in result) is (changed == "required")
    if changed in {"manifest", "elsewhere"}:
        assert str(target) not in result
    elif changed == "new-section":
        assert str(target) in result and "追加節" in result
        assert '"permissionDecision": "deny"' in invoke("claude", "PreToolUse", "Write", write)
    git_repository.run_git(repo, "worktree", "remove", "--force", str(worktree))


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

    def public_inventory(argv, **_kwargs):
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
        denied = invoke("claude", "PreToolUse", "Write", {"file_path": str(path.parent / "out.md"), "content": "成果"})
        assert '"permissionDecision": "deny"' in denied


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
    assert ('"permissionDecision": "deny"' in second) is (form == "tampered")


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


def test_session_state_bindings_survive_receipt_scenarios():
    """先行scenarioの終了後も公開hookの状態取得が実保存先へ接続する。"""
    dispatch = importlib.import_module("agent_toolkit._hooks.pretooluse.dispatch")

    assert dispatch.read_state is session_state.read_state
    assert dispatch.update_state is session_state.update_state


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


@pytest.mark.parametrize("host,reader", [("claude", "Read"), ("claude", "Bash"), ("codex", "Bash")])
@pytest.mark.parametrize(
    "mode", ["complete", "missing", "other-agent", "version", "overlap", "reverse", "omitted", "repeat", "blank-boundary"]
)
def test_split_delivery_controls_public_write(scenario, host_environ, host, reader, mode):
    """実回収した同じ版の全範囲だけが、案内済みの再書込を解除する。"""
    _state, rows, path, _rollout, invoke, delivered = scenario
    if mode == "repeat":
        path.write_text("同じ行\n" * 4, encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    assert '"permissionDecision": "deny"' in invoke(host, "PreToolUse", "Write", write)
    intervals = [(1, 5), (4, None)] if mode == "overlap" else [(1, 3), (4, None)]
    if mode == "repeat":
        intervals = [(1, 2), (1, 2)]
    if mode == "blank-boundary":
        intervals = [(1, 2), (3, None)]
    if mode == "reverse":
        intervals.reverse()
    received = []
    for index, (start, end) in enumerate(intervals):
        if index and mode == "missing":
            break
        if index and mode == "version":
            path.write_text(path.read_text(encoding="utf-8").replace("固有の必要本文", "新版の必要本文"), encoding="utf-8")
        actor = {"agent_id": "other"} if index and mode == "other-agent" else {}
        child = path.with_name("child.jsonl")
        if actor and host == "codex":
            actor["agent_transcript_path"] = str(child)
        command = f"sed -n '{start},{end if end is not None else '$'}p' -- {shlex.quote(str(path))}"
        inputs: dict[str, object] = (
            {"file_path": str(path), "offset": start, **({"limit": end - start + 1} if end else {})}
            if reader == "Read"
            else {"command": command}
        )
        invoke(host, "PreToolUse", reader, inputs, **actor)
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
        received.append(produced.stdout)
        visible = produced.stdout
        if index and mode == "omitted":
            visible = "".join(visible.splitlines(keepends=True)[-1:])
        response = (
            {"type": "text", "file": {"content": visible, "startLine": start, "truncatedByTokenCap": mode == "omitted"}}
            if reader == "Read"
            else {"stdout": visible, "exit_code": 0}
        )
        invoke(host, "PostToolUse", reader, inputs, response if host == "claude" else produced.stdout, **actor)
        if host == "codex":
            delivered(visible, destination=child if actor else None)
    final = invoke(host, "PreToolUse", "Write", write)
    complete = mode in {"complete", "overlap", "reverse", "blank-boundary"}
    assert (
        read_delivery.target_ready(_state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
        is complete
    )
    if mode == "version":
        # 本文が変わった現行版の初回案内はwarn。その案内後の再書込を検収する。
        assert "未取得" in final
        final = invoke(host, "PreToolUse", "Write", write)
    assert ('"permissionDecision": "deny"' not in final) is complete
    if mode == "complete":
        assert "".join(received) == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("host,reader", [("claude", "Read"), ("claude", "Bash"), ("codex", "Bash")])
@pytest.mark.parametrize("identical", [False, True])
@pytest.mark.parametrize(
    "mode", ["complete", "reverse", "overlap", "duplicate", "missing", "wrong", "omitted", "unknown", "recover"]
)
def test_repeated_lines_keep_producer_positions(scenario, host_environ, host, reader, identical, mode):
    """同じ本文の異なる要求位置を区別し、実回収した全行だけで再書込を解除する。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    body = "繰り返す本文\n" * 4 if identical else "# 基準\n第一行\n繰り返す本文\n繰り返す本文\n"
    path.write_text(body, encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    assert '"permissionDecision": "deny"' in invoke(host, "PreToolUse", "Write", write)
    intervals = [(1, 2), (3, 4)] if identical else [(1, 3), (4, 4)]
    if mode == "reverse":
        intervals.reverse()
    elif mode == "overlap":
        intervals = [(1, 3), (3, 4)]
    elif mode == "duplicate":
        intervals[1] = intervals[0]
    elif mode == "missing":
        intervals = intervals[:1]
    elif mode == "recover":
        intervals.append(intervals[0])
    received = []
    for index, (start, end) in enumerate(intervals):
        command = f"sed -n '{start},{end}p' -- {shlex.quote(str(path))}"
        if mode == "unknown" and index:
            command = f"awk 'NR>={start} && NR<={end}' {shlex.quote(str(path))}"
        inputs: dict[str, object] = (
            {"file_path": str(path), "offset": start, "limit": end - start + 1} if reader == "Read" else {"command": command}
        )
        if reader == "Read" and mode == "unknown" and index:
            inputs["offset"] = 1
        invoke(host, "PreToolUse", reader, inputs)
        actual = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert actual.returncode == 0 and not actual.stderr
        received.append(actual.stdout)
        visible = actual.stdout
        if mode == "wrong" and index:
            visible = "別の本文\n"
        if mode == "omitted" and index or mode == "recover" and index == 0:
            visible = ""
        response = (
            {"type": "text", "file": {"content": visible, "startLine": start}}
            if reader == "Read"
            else {"stdout": visible, "exit_code": 0}
        )
        invoke(host, "PostToolUse", reader, inputs, response if host == "claude" else actual.stdout)
        if host == "codex":
            delivered(visible)
    result = invoke(host, "PreToolUse", "Write", write)
    complete = mode in {"complete", "reverse", "overlap", "recover"}
    assert (
        read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
        is complete
    )
    assert ('"permissionDecision": "deny"' not in result) is complete
    if mode in {"complete", "reverse"}:
        assert sorted("".join(received).splitlines()) == sorted(body.splitlines())


@pytest.mark.parametrize("identical", [False, True])
@pytest.mark.parametrize("both", [False, True])
def test_codex_positioned_results_follow_outer_delivery(scenario, host_environ, identical, both):
    """要求位置が異なる同文も実配送の数で確認し、片方の未配送は再取得で回復する。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text("同じ本文\n" * 4 if identical else "第一行\n第二行\n第三行\n第四行\n", encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Write", write)
    assert '"permissionDecision": "deny"' in invoke("codex", "PreToolUse", "Write", write)
    commands = [f"sed -n '{span}p' -- {shlex.quote(str(path))}" for span in ("1,2", "3,4")]

    def read(command):
        inputs = {"command": command}
        invoke("codex", "PreToolUse", "Bash", inputs)
        actual = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert actual.returncode == 0 and not actual.stderr
        invoke("codex", "PostToolUse", "Bash", inputs, actual.stdout)
        return actual.stdout

    outputs = [read(command) for command in commands]
    assert "".join(outputs) == path.read_text(encoding="utf-8")
    delivered(json.dumps({"results": [{"output": body} for body in outputs]}) if both else outputs[0])
    if both:
        assert not invoke("codex", "PreToolUse", "Write", write)
        assert read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
        )
        return
    assert '"permissionDecision": "deny"' in invoke("codex", "PreToolUse", "Write", write)
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main"
    )
    for command, body in zip(commands, outputs, strict=True):
        actual = read(command)
        assert actual == body
        delivered(actual)
    assert not invoke("codex", "PreToolUse", "Write", write)


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("distinct", [False, True])
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_same_response_repetition_keeps_requested_coverage(scenario, host_environ, host, distinct, channel):
    """本文全体に偶然一致する反復出力も、要求位置の全被覆で判定する。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text("反復する行\n" * 4, encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke(host, "PreToolUse", "Write", write)
    assert '"permissionDecision": "deny"' in invoke(host, "PreToolUse", "Write", write)
    latter = "3,4" if distinct else "1,2"
    redirect = " >&2" if channel == "stderr" else ""
    command = "; ".join(f"sed -n '{span}p' -- {shlex.quote(str(path))}{redirect}" for span in ("1,2", latter))
    invoke(host, "PreToolUse", "Bash", {"command": command})
    actual = subprocess.run(
        ["/bin/bash", "-c", command],
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert actual.returncode == 0
    visible = getattr(actual, channel)
    assert visible == path.read_text(encoding="utf-8")
    invoke(
        host, "PostToolUse", "Bash", {"command": command}, {"stdout": actual.stdout, "stderr": actual.stderr, "exit_code": 0}
    )
    if host == "codex":
        delivered(visible)
    result = invoke(host, "PreToolUse", "Write", write)
    assert (
        read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
        is distinct
    )
    assert ('"permissionDecision": "deny"' not in result) is distinct


def test_codex_truncated_diagnostic_keeps_received_requested_span(scenario, host_environ):
    """必要な読取範囲を全受信していれば、診断出力の省略でその位置を失わない。"""
    state, rows, path, _rollout, invoke, delivered = scenario
    path.write_text("第一行\n第二行\n第三行\n第四行\n", encoding="utf-8")
    rows.append(read_triggers.ReadTrigger(path, 1, "操作", ("tool:Write",), (path,)))
    write = {"file_path": str(path.parent / "out.md"), "content": "成果"}
    invoke("codex", "PreToolUse", "Write", write)
    assert '"permissionDecision": "deny"' in invoke("codex", "PreToolUse", "Write", write)
    for start, end in ((1, 2), (3, 4)):
        command = f"sed -n '{start},{end}p' -- {shlex.quote(str(path))}"
        if start == 1:
            command += "; printf '追加の診断結果\\n'"
        invoke("codex", "PreToolUse", "Bash", {"command": command})
        actual = subprocess.run(
            ["/bin/bash", "-c", command],
            env=host_environ(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert actual.returncode == 0 and not actual.stderr
        invoke("codex", "PostToolUse", "Bash", {"command": command}, actual.stdout)
        visible = (
            actual.stdout
            if start == 3
            else "Warning: truncated output (original token count: 100)\n"
            + "".join(actual.stdout.splitlines(keepends=True)[:2])
            + "…10 tokens truncated…"
        )
        delivered(visible)
    assert not invoke("codex", "PreToolUse", "Write", write)
    assert read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(path), "main")
