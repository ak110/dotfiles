"""役割の起動配送と、主体別の取得手段をまたぐ読込契約を確かめる。"""

import json
import shlex

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import document_reads, notice, rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_acquisition_support import (
    _assert_continues_without_guidance,
    _state,
)

__all__ = ["_assert_continues_without_guidance", "_state"]


def test_compact_recovery_first_output_survives_notice_cycle(tmp_path, monkeypatch, state):
    """圧縮後の工程選択案内を、公開SessionStartの初回配送で保持する。"""
    document = tmp_path / "compact-reference.md"
    document.write_text("# 圧縮候補\n再取得する固有基準\n", encoding="utf-8")
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    result = _run_hook_in_process(
        "rules_context",
        {"session_id": "compact-first", "cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "compact"},
    )
    assert result.returncode == 0 and not result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "会話圧縮後の復元" in context
    assert str(document) not in context
    assert state.get("notice_fingerprints")


def test_unchecked_condition_keeps_notice_suppressed_until_actual_resolution(tmp_path, monkeypatch, state):
    """同scopeの非該当操作は抑止を保ち、公開全文取得後の再成立でだけ再配送する。"""
    document = tmp_path / "reference.md"
    document.write_text("# 必須資料\n取得する固有基準\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("bash:printf json",), (document,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "unchecked", "cwd": str(tmp_path), "tool_name": "Bash"}

    def invoke(suffix):
        command = "printf json" if suffix == "json" else "cat -- " + shlex.quote(str(tmp_path / "unrelated.txt"))
        result = _run_hook_in_process("pretooluse", {**payload, "tool_input": {"command": command}})
        assert result.returncode == 0 and not result.stderr
        return result.stdout

    assert str(document) in invoke("json")
    fingerprints = dict(state["notice_fingerprints"])
    assert str(document) not in invoke("txt")
    assert state["notice_fingerprints"] == fingerprints
    assert str(document) not in invoke("json")
    assert not document_reads.content_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), document_reads.content_fingerprint(document), "main"
    )
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(document)},
            "tool_response": document.read_text(encoding="utf-8"),
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    assert document_reads.content_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], document_reads.content_fingerprint(document), "main"
    )
    assert str(document) not in invoke("json")
    # 別操作で本文を更新したため、同じ宣言の資料が再び未取得となる。
    document.write_text("# 必須資料\n更新された固有基準\n", encoding="utf-8")
    assert str(document) in invoke("json")


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    "frontmatter,allows_read",
    [
        ("disable-model-invocation: true", True),
        ("disable-model-invocation: false", False),
        ("allow_implicit_invocation: false", False),
    ],
)
@pytest.mark.parametrize("tool", ["Read", "Bash"])
def test_skill_acquisition_respects_model_invocation(tmp_path, monkeypatch, state, host, frontmatter, allows_read, tool):
    """全文だけを取得済みにし、通常のClaudeスキルはSkill起動を要する。"""
    skill = tmp_path / ".claude/skills/local/SKILL.md"
    skill.parent.mkdir(parents=True)
    body = f"---\nname: local\n{frontmatter}\n---\n# 基準\n固有の本文\n"
    skill.write_text(body, encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Do",), (skill,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    inputs = {"file_path": str(skill)} if tool == "Read" else {"command": "cat -- " + shlex.quote(str(skill))}
    first = read_delivery.deliver(payload, "Do", {}, "test")
    assert first.notices and not first.block
    if host == "claude":
        assert ("`Skill`で`local`" in "".join(first.notices)) is not allows_read
    for response in ({"stdout": "読取失敗", "exit_code": 1},):
        observe_received({**payload, "tool_response": response}, tool, inputs, "test")
        assert not document_reads.content_ready(
            state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), document_reads.content_fingerprint(skill), "main"
        )
    observe_received({**payload, "tool_response": body[:12]}, tool, inputs, "test")
    ready = document_reads.content_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), document_reads.content_fingerprint(skill), "main"
    )
    assert not ready
    observe_received({**payload, "tool_response": body}, tool, inputs, "test")
    following = read_delivery.deliver(payload, "Do", {}, "test")
    assert bool(following.notices) is (host == "claude" and not allows_read)
    if host == "claude" and not allows_read:
        observe_received({**payload, "tool_response": "スキル起動済み"}, "Skill", {"skill": "local"}, "test")
        assert document_reads.content_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], document_reads.content_fingerprint(skill), "main"
        )


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("reading", ["Read", "Bash"])
@pytest.mark.parametrize("writing", ["Write", "Bash"])
def test_project_skill_cwd_boundary_keeps_acquisition_and_invocation_distinct(
    tmp_path, monkeypatch, state, inside, linked, reading, writing
):
    """会話から起動できる実体だけSkillを案内し、通常取得と起動の証拠を分ける。"""
    project = tmp_path / "project"
    skill = project / ".claude/skills/local/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: local\n---\n# 基準\n固有の本文\n", encoding="utf-8")
    if linked:
        physical = tmp_path / "physical/skills/local"
        physical.parent.mkdir(parents=True)
        skill.parent.rename(physical)
        skill.parent.symlink_to(physical, target_is_directory=True)
        skill = skill.resolve()
    cwd = project if inside else tmp_path / "outside"
    cwd.mkdir(exist_ok=True)
    row = read_triggers.ReadTrigger(project / "source.md", 1, "操作", ("write:*",), (skill,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "project-cwd", "cwd": str(cwd)}
    output = project / "out.md"
    request = {
        **payload,
        "tool_name": writing,
        "tool_input": {"file_path": str(output), "content": "本文"}
        if writing == "Write"
        else {"command": f"printf '本文' > {shlex.quote(str(output))}"},
    }
    first = _run_hook_in_process("pretooluse", request)
    assert first.returncode == 0 and not first.stderr
    assert (str(skill) in first.stdout) is not inside
    assert ("`Skill`で`local`" in first.stdout) is inside
    if not inside:
        assert "Readで" in first.stdout
    _assert_continues_without_guidance(_run_hook_in_process("pretooluse", request))
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main"
    )
    inputs = {"file_path": str(skill)} if reading == "Read" else {"command": "cat -- " + shlex.quote(str(skill))}
    acquired = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": reading,
            "tool_input": inputs,
            "tool_response": {"stdout": skill.read_text(encoding="utf-8"), "exit_code": 0}
            if reading == "Bash"
            else skill.read_text(encoding="utf-8"),
        },
    )
    assert acquired.returncode == 0 and not acquired.stderr
    after = _run_hook_in_process("pretooluse", request)
    assert after.returncode == 0 and not after.stderr
    assert read_delivery.target_ready(state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main")
    _assert_continues_without_guidance(after)
    if inside:
        assert not read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main", invocation=True
        )
    if not inside:
        assert str(skill) not in after.stdout
        monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
        monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
        monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
        compact = _run_hook_in_process("rules_context", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
        assert compact.returncode == 0 and not compact.stderr
        assert str(skill) not in compact.stdout and "`Skill`で`local`" not in compact.stdout
        assert "工程を定めるスキルと役割文書" in compact.stdout
        failed = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": reading,
                "tool_input": inputs,
                "tool_response": "失敗",
            },
        )
        assert failed.returncode == 0 and not failed.stderr
        guided = _run_hook_in_process("pretooluse", request)
        assert guided.returncode == 0 and str(skill) in guided.stdout
        _assert_continues_without_guidance(_run_hook_in_process("pretooluse", request))
        assert not read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main"
        )
        acquired = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_name": reading,
                "tool_input": inputs,
                "tool_response": skill.read_text(encoding="utf-8"),
            },
        )
        assert acquired.returncode == 0 and not acquired.stderr
        resumed = _run_hook_in_process("pretooluse", request)
        assert resumed.returncode == 0 and not resumed.stderr
        assert str(skill) not in resumed.stdout and '"permissionDecision": "deny"' not in resumed.stdout


@pytest.mark.parametrize("tampered", [False, True])
def test_startup_delivery_and_observation_share_targets(tmp_path, monkeypatch, state, tampered):
    """実際に生成した起動本文の資料を観測し、改変した本文は読了にしない。"""
    root = tmp_path / "plugin"
    role = root / "share/example.subagent.md"
    role.parent.mkdir(parents=True)
    reference = role.parent / "reference.md"
    reference.write_text("# 起動基準\n起動時に必要な固有本文\n", encoding="utf-8")
    role.write_text(
        "# 役割\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n| 起動時 | `reference.md` | 起動時 | |\n",
        encoding="utf-8",
    )
    context = read_triggers.startup_context(role, root, {})
    if tampered:
        context = context.replace("起動時に必要な固有本文", "改変された本文")
    prompt = f"次の文書の手順を実行せよ（出所: {role}）。\n{context}"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: read_triggers.parse_tables(role, root))
    read_delivery.deliver({"transcript_path": str(transcript)}, "Do", {}, "test")
    assert (
        document_reads.content_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], document_reads.content_fingerprint(reference), "main"
        )
        is not tampered
    )


def test_unconfirmed_conditions_and_foreign_roles_cannot_block(tmp_path, monkeypatch, state):
    """自然文の判断候補と他役割を、必読案内済みの集合から分ける。"""
    reference = tmp_path / "reference.md"
    reference.write_text("# 条件付き資料\n基準\n", encoding="utf-8")
    rows = [
        read_triggers.ReadTrigger(
            tmp_path / "source.md", 1, "判断", ("write:*",), (reference,), condition="独立したレビューを省く時"
        ),
        read_triggers.ReadTrigger(tmp_path / "other.subagent.md", 2, "操作", ("write:*",), (reference,)),
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    for _ in range(2):
        decision = read_delivery.deliver({}, "Write", {"file_path": str(tmp_path / "output.py"), "content": "x=1"}, "test")
        notice.record_delivery("".join(decision.notices))
        assert not decision.block
    recorded = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert not any(key.startswith("read-guidance:") for key in recorded)
    assert not document_reads.content_ready(recorded, document_reads.content_fingerprint(reference), "main")


@pytest.mark.parametrize("role_name", ["exec-review.subagent.md", "session-termination.subagent.md"])
@pytest.mark.parametrize("select_current", [False, True])
def test_role_finding_save_keeps_current_acquisition_evidence(tmp_path, monkeypatch, state, role_name, select_current):
    """レビュー・公開担当の指摘保存は継続を許可し、現在の資料の取得証拠を独立に確認する。"""
    root = tmp_path / "agent-toolkit"
    share = root / "share"
    share.mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    needed, delegation, waiting, unrelated = (
        share / name for name in ("finding.md", "delegation.md", "waiting.md", "unrelated.md")
    )
    for path in (needed, delegation, waiting, unrelated):
        path.write_text(f"# {path.stem}\n固有の基準\n", encoding="utf-8")
    role = share / role_name
    other = share / ("session-termination.subagent.md" if role_name == "exec-review.subagent.md" else "exec-review.subagent.md")
    header = "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
    role.write_text(
        "# 担当\n"
        + header
        + "| 委譲・待機 | `delegation.md`、`waiting.md` | 操作 | `tool:Launch` |\n"
        + f"| 指摘保存 | `finding.md` | 操作 | `{'path:*.json' if select_current else 'tool:Inspect'}` |\n"
        + "| 独立文脈レビューを省く時 | `unrelated.md` | 判断 | `path:*.json` |\n",
        encoding="utf-8",
    )
    other.write_text("# 他担当\n" + header + "| 保存 | `unrelated.md` | 操作 | `path:*.json` |\n", encoding="utf-8")
    transcript = tmp_path / "transcript.jsonl"
    prompt = f"次の文書の手順を実行せよ（出所: {role}）。\n指摘をJSONへ保存する。"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(
        read_delivery,
        "declarations",
        lambda *_args, **_kwargs: [row for source in (role, other) for row in read_triggers.parse_tables(source, root)],
    )
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    payload = {"session_id": "test", "cwd": str(tmp_path), "turn_id": "turn", "transcript_path": str(transcript)}

    def read(path, *, partial=False, failed=False):
        body = path.read_text(encoding="utf-8")
        result = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUseFailure" if failed else "PostToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": str(path), **({"limit": 1} if partial else {})},
                "tool_response": "読取失敗" if failed else body.splitlines(keepends=True)[0] if partial else body,
            },
        )
        assert result.returncode == 0 and not result.stderr

    launched = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Launch", "tool_input": {}})
    assert launched.returncode == 0 and all(str(path) in launched.stdout for path in (delegation, waiting))
    for path in (delegation, waiting):
        read(path)
    compact = _run_hook_in_process("rules_context_codex", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
    assert compact.returncode == 0
    assert all(str(path) not in compact.stdout for path in (delegation, waiting))
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert all("main" in records[rules_context.carried_read_target_key(path)] for path in (delegation, waiting))
    target = tmp_path / "findings.json"
    body = json.dumps({"findings": [{"reason": "確認した指摘"}]}, ensure_ascii=False)
    operation = {
        **payload,
        "tool_name": "apply_patch",
        "tool_input": {"command": f"*** Begin Patch\n*** Add File: {target}\n+{body}\n*** End Patch\n"},
    }
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and not first.stderr
    assert all(str(path) not in first.stdout for path in (delegation, waiting))
    if select_current:
        assert str(needed) in first.stdout
        assert not target.exists()
        for acquisition in (None, "failed"):
            if acquisition:
                read(needed, failed=True)
            _assert_continues_without_guidance(_run_hook_in_process("pretooluse", operation))
            assert (
                document_reads.acquisition_state(
                    state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(needed), "main"
                )
                == "missing"
            )
        read(needed)
        assert read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(needed), "main"
        )
    for _ in range(2):
        result = _run_hook_in_process("pretooluse", operation)
        assert result.returncode == 0 and not result.stderr
        assert '"permissionDecision": "deny"' not in result.stdout
        assert all(str(path) not in result.stdout for path in (delegation, waiting, needed))
    # 模擬ホストは許可された同じ保存入力をここで反映する。
    target.write_text(body, encoding="utf-8")
    assert json.loads(target.read_text(encoding="utf-8"))["findings"] == [{"reason": "確認した指摘"}]


@pytest.mark.parametrize("role_name", [None, "exec.subagent.md", "lane-integration.subagent.md"])
@pytest.mark.parametrize("history", ["new", "existing", "compact"])
def test_selected_shared_skill_keeps_current_role_boundary(tmp_path, monkeypatch, state, role_name, history):
    """取得時の候補通知を保留し、役割・工程の成立と実受領・起動の証拠を分ける。"""
    root = tmp_path / "agent-toolkit"
    share = root / "share"
    share.mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/shared/SKILL.md"
    skill_name = f"{root.name}:{skill.parent.name}"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: shared\n---\n# 共通の基準\n本文\n", encoding="utf-8")
    planning, integration = share / "planning.md", share / "integration.md"
    for path in (planning, integration):
        path.write_text(f"# {path.stem}\n役割の固有基準\n", encoding="utf-8")
    base, execution_role, integration_role = (
        share / name for name in ("base.md", "exec.subagent.md", "lane-integration.subagent.md")
    )
    header = "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
    operation = "read:agent-toolkit:*/skills/shared/SKILL.md"
    base.write_text("# 共通\n" + header + f"| 選択 | `../skills/shared/SKILL.md` | 判断 | `{operation}` |\n", encoding="utf-8")
    for source, target in ((execution_role, planning), (integration_role, integration)):
        source.write_text(
            "# 役割\n" + header + f"| 工程資料の選択 | `../skills/shared/SKILL.md`、`{target.name}` | 判断 | `{operation}` |\n",
            encoding="utf-8",
        )
    rows = [row for source in (base, execution_role, integration_role) for row in read_triggers.parse_tables(source, root)]
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    # ユーザー発話の集計本文は対象外とし、公開SessionStartの取得初期化を検証する。
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    prompt = (
        f"次の文書の手順を実行せよ（出所: {share / role_name}）。\n担当工程を実施する。"
        if role_name
        else "all-reposの統括。レーンを担当しない。"
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n", encoding="utf-8")
    payload = {"session_id": "test", "cwd": str(tmp_path), "transcript_path": str(transcript)}
    state["agents_server_cwd_by_session"] = {"child": str(tmp_path)}

    def acquire(tool, inputs, response):
        before = _run_hook_in_process("pretooluse", {**payload, "tool_name": tool, "tool_input": inputs})
        assert before.returncode == 0 and not before.stderr
        if tool == "Read" and inputs.get("file_path") == str(skill):
            assert "読込条件の候補" not in before.stdout
        after = _run_hook_in_process(
            "posttooluse",
            {**payload, "hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": inputs, "tool_response": response},
        )
        assert after.returncode == 0 and not after.stderr

    if history == "existing":
        selected = {
            "path": str(skill),
            "heading": "",
            "declarations": [
                {"source": str(row.source), "operations": list(row.operations), "kind": row.kind, "selector": row.selector}
                for row in rows
            ],
        }
        state[rules_context.OPERATION_SKILL_READY_KEY] = {"selected-read-target:" + json.dumps(selected): ["main"]}
    else:
        acquire("Read", {"file_path": str(skill)}, skill.read_text(encoding="utf-8"))
        if history == "compact":
            acquire("Skill", {"skill": skill_name}, "起動済み")
            compact = _run_hook_in_process("rules_context", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
            assert compact.returncode == 0 and not compact.stderr
            acquire("Read", {"file_path": str(skill)}, skill.read_text(encoding="utf-8"))
    required = planning if role_name == execution_role.name else integration if role_name == integration_role.name else None
    operations = (
        ("mcp__plugin_agent-toolkit_agents_server__send_message", {"session_id": "child", "prompt": "観測用入力"}),
        ("Bash", {"command": "git grep -n -F sample -- example.md"}),
        ("mcp__plugin_agent-toolkit_agents_server__show", {"session_id": "child"}),
    )
    for tool, inputs in operations:
        result = _run_hook_in_process("pretooluse", {**payload, "tool_name": tool, "tool_input": inputs})
        assert result.returncode == 0 and not result.stderr
        assert '"permissionDecision": "deny"' not in result.stdout
        assert all(str(path) not in result.stdout for path in (planning, integration))
        assert f"`Skill`で`{skill_name}`" not in result.stdout
        assert str(skill) not in result.stdout
        repeated = _run_hook_in_process("pretooluse", {**payload, "tool_name": tool, "tool_input": inputs})
        assert repeated.returncode == 0 and not repeated.stderr
        assert '"permissionDecision": "deny"' not in repeated.stdout
        assert all(str(path) not in repeated.stdout for path in (planning, integration, skill))
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    for path in (planning, integration):
        assert document_reads.acquisition_state(records, read_triggers.ReadTarget(path), "main") == "missing"
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "main") is (history != "existing")
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "main", invocation=True)
    acquire("Skill", {"skill": skill_name}, "起動済み")
    assert read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main", invocation=True
    )
    if required:
        acquire("Read", {"file_path": str(required)}, required.read_text(encoding="utf-8"))
    for tool, inputs in operations:
        for _ in range(2):
            result = _run_hook_in_process("pretooluse", {**payload, "tool_name": tool, "tool_input": inputs})
            assert result.returncode == 0 and not result.stderr
            assert all(str(path) not in result.stdout for path in (planning, integration, skill))
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    for path in (planning, integration):
        ready = document_reads.content_ready(records, document_reads.content_fingerprint(path), "main")
        assert ready is (path == required)
        if path != required:
            assert "main" not in records.get(
                read_delivery.read_guidance_key(read_triggers.ReadTarget(path), invocation=False), []
            )
