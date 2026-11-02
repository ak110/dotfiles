"""役割の起動配送と、主体別の取得手段をまたぐ読込契約を確かめる。"""

import hashlib
import json
import shlex

import pytest

from agent_toolkit._common import read_triggers, session_state
from agent_toolkit._hooks import document_reads, notice, rules_context
from agent_toolkit._hooks.pretooluse import agent_checks, read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process, denial_reason


@pytest.fixture(name="state")
def _state(monkeypatch):
    state: dict = {}

    def read(_session):
        return state

    def update(_session, mutate):
        mutate(state)

    for module in (agent_checks, read_delivery, rules_context, notice, session_state):
        monkeypatch.setattr(module, "read_state", read, raising=False)
        monkeypatch.setattr(module, "update_state", update)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    notice.set_warning_session_id("")
    return state


def test_compact_recovery_first_output_survives_notice_cycle(tmp_path, monkeypatch, state):
    """実候補1件の予算見積りが、同じ公開SessionStartの初回配送を消費しない。"""
    document = tmp_path / "compact-reference.md"
    document.write_text("# 圧縮候補\n再取得する固有基準\n", encoding="utf-8")
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        rules_context, "reset_main_operation_skill_ready", lambda *_args, **_kwargs: [(document, "", digest, False)]
    )
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    result = _run_hook_in_process(
        "rules_context",
        {"session_id": "compact-first", "cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "compact"},
    )
    assert result.returncode == 0 and not result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context and str(document) in context
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
    for response in ({"stdout": body, "exit_code": 1},):
        read_delivery.observe({**payload, "tool_response": response}, tool, inputs, "test")
        assert not document_reads.content_ready(
            state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), document_reads.content_fingerprint(skill), "main"
        )
    read_delivery.observe({**payload, "tool_response": body[:12]}, tool, inputs, "test")
    ready = document_reads.content_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), document_reads.content_fingerprint(skill), "main"
    )
    assert ready
    following = read_delivery.deliver(payload, "Do", {}, "test")
    assert bool(following.notices) is (host == "claude" and not allows_read)
    if host == "claude" and not allows_read:
        read_delivery.observe({**payload, "tool_response": "スキル起動済み"}, "Skill", {"skill": "local"}, "test")
        assert document_reads.content_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], document_reads.content_fingerprint(skill), "main"
        )


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("reading", ["Read", "Bash"])
@pytest.mark.parametrize("writing", ["Write", "Bash"])
def test_project_skill_cwd_boundary_releases_same_write(tmp_path, monkeypatch, state, inside, linked, reading, writing):
    """会話から起動できる実体だけSkillを要求し、範囲外は通常取得で再書込へ戻る。"""
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
    assert str(skill) in first.stdout
    assert ("`Skill`で`local`" in first.stdout) is inside
    if not inside:
        assert "Readで" in first.stdout and "cat --" in first.stdout
    assert str(skill) in denial_reason(_run_hook_in_process("pretooluse", request))
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
    assert ('"permissionDecision": "deny"' in after.stdout) is inside
    if not inside:
        assert str(skill) not in after.stdout
        monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
        monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
        monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
        compact = _run_hook_in_process("rules_context", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
        assert compact.returncode == 0 and not compact.stderr
        assert str(skill) in compact.stdout and "`Skill`で`local`" not in compact.stdout
        assert "Readで" in compact.stdout and "cat --" in compact.stdout
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
        assert str(skill) in denial_reason(_run_hook_in_process("pretooluse", request))
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
def test_role_resumes_finding_save_after_current_acquisition(tmp_path, monkeypatch, state, role_name, select_current):
    """レビュー・公開担当の指摘保存は、現在の資料だけを再取得すれば同じ入力で再開できる。"""
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
                "tool_response": body.splitlines(keepends=True)[0] if partial else body,
            },
        )
        assert result.returncode == 0 and not result.stderr

    launched = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Launch", "tool_input": {}})
    assert launched.returncode == 0 and all(str(path) in launched.stdout for path in (delegation, waiting))
    for path in (delegation, waiting):
        read(path)
    compact = _run_hook_in_process("rules_context_codex", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
    assert compact.returncode == 0
    assert all(str(path) in compact.stdout for path in (delegation, waiting))
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
            reason = denial_reason(_run_hook_in_process("pretooluse", operation))
            assert str(needed) in reason
            assert all(str(path) not in reason for path in (delegation, waiting, unrelated))
        read(needed)
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
    """共有SKILLの履歴を再接続しても他役割の必読資料を復帰させない。"""
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
        for path in (planning, integration):
            assert (str(path) in result.stdout) is (path == required)
        assert f"`Skill`で`{skill_name}`" not in result.stdout
        assert (str(skill) in result.stdout) is (history == "existing")
        repeated = _run_hook_in_process("pretooluse", {**payload, "tool_name": tool, "tool_input": inputs})
        assert repeated.returncode == 0 and not repeated.stderr
        assert '"permissionDecision": "deny"' not in repeated.stdout
    acquire("Skill", {"skill": skill_name}, "起動済み")
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
            assert "main" not in records.get("read-guidance:" + read_delivery.document_key(path), [])


@pytest.mark.usefixtures("state")
def test_small_codex_section_guidance_has_capacity_and_both_budgets(tmp_path, monkeypatch):
    """48KiB未満でも指定節のバイト数を示し、取得範囲と内外の予算へ接続する。"""
    document = tmp_path / "reference.md"
    document.write_text("# 資料\n\n## 必須\n基準a\n\n## その他\n別の基準\n", encoding="utf-8")
    target = read_triggers.ReadTarget(document, "必須")
    assert len(target.body().encode("utf-8")) == 19
    row = read_triggers.ReadTrigger(document, 1, "操作", ("tool:Write",), (document,), ("必須",))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "small-capacity",
            "turn_id": "turn",
            "cwd": str(tmp_path),
            "tool_name": "Write",
            "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "本文"},
        },
    )
    assert result.returncode == 0 and not result.stderr
    step = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "対象19バイト、予算目安7トークン" in step
    assert "sed -n '3,5p' --" in step
    assert "内側exec_commandのmax_output_tokens" in step
    assert "外側functions.exec" in step
    assert "前提・例外" in step
    whole = read_delivery._merged_steps(  # pylint: disable=protected-access
        [target, read_triggers.ReadTarget(document)], {}, "codex"
    )
    assert len(whole) == 1 and "cat --" in whole[0]


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    "command,success",
    [
        ("cat -- reference.md", True),
        ("wc -l reference.md && cat -- reference.md", True),
        ("cd nested && ls && wc -l ../reference.md && sed -n '1,$p' -- ../reference.md", True),
        ("cat -- reference.md | cut -c1-3", False),
        ("cat -- reference.md > copy.md", False),
        ("cat -- reference.md; ls", False),
        ("wc -l missing && cat -- reference.md; ls", False),
        ("cat -- reference.md && printf done; ls", False),
    ],
)
def test_hook_chain_acquisition_releases_same_write(tmp_path, monkeypatch, state, host, command, success):
    """規範の単独・&&読取でだけ、案内済み資料の書込遮断を解除する。"""
    reference = tmp_path / "reference.md"
    reference.write_text("# 基準\n必要な内容\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Write",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "chain", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    writing = {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "本文"}}
    first = _run_hook_in_process("pretooluse", writing)
    assert first.returncode == 0 and str(reference) in first.stdout
    assert str(reference) in denial_reason(_run_hook_in_process("pretooluse", writing))
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": "基準", "exit_code": 0},
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(reference), "main") is success
    after = _run_hook_in_process("pretooluse", writing)
    assert after.returncode == 0 and not after.stderr
    assert ('"permissionDecision": "deny"' in after.stdout) is not success
    if success:
        assert str(reference) not in after.stdout


@pytest.mark.parametrize("failure", ["PostToolUseFailure", "exit_code", "is_error"])
def test_failed_hook_chain_does_not_acquire(tmp_path, monkeypatch, state, failure):
    reference = tmp_path / "reference.md"
    reference.write_text("# 基準\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(reference, 1, "操作", ("tool:Write",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    response = {"stdout": "基準", **({failure: 1} if failure != "PostToolUseFailure" else {})}
    result = _run_hook_in_process(
        "posttooluse",
        {
            "session_id": "failed-chain",
            "cwd": str(tmp_path),
            "hook_event_name": "PostToolUseFailure" if failure == "PostToolUseFailure" else "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "wc -l reference.md && cat -- reference.md"},
            "tool_response": response,
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert not read_delivery.target_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(reference), "main"
    )


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("own", [False, True])
def test_piped_foreign_role_warning_keeps_acquisition_boundary(tmp_path, monkeypatch, state, host, own):
    """相対パス・複数パイプ読取では、他担当の手順書だけへ警告する。"""
    root = tmp_path / "plugin"
    share = root / "share"
    share.mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    role = share / "worker.subagent.md"
    outside = tmp_path / "worker.subagent.md"
    for path in (role, outside):
        path.write_text("# 担当手順\n本文\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [])
    if own:
        state[rules_context.OPERATION_SKILL_READY_KEY] = {"read-trigger-role:" + str(role): ["main"]}
    payload = {"session_id": "pipe-role", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    inputs = {
        "command": "cd plugin && sed -n '1,2p' -- share/worker.subagent.md | cut -c1-300; "
        "cat -- ../worker.subagent.md | cut -c1-300"
    }
    before = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Bash", "tool_input": inputs})
    assert before.returncode == 0 and not before.stderr
    assert ("他の担当の手順書" in before.stdout) is not own
    if not own:
        assert str(role) in before.stdout
        assert str(outside) not in before.stdout
    after = _run_hook_in_process(
        "posttooluse",
        {**payload, "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": inputs, "tool_response": "本文"},
    )
    assert after.returncode == 0 and not after.stderr
    assert not read_delivery.target_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(role), "main"
    )


@pytest.mark.parametrize("tool", ["Read", "Bash"])
@pytest.mark.parametrize("order", ["reference-first", "invocation-first", "simultaneous"])
def test_named_skill_reference_and_invocation_keep_separate_evidence(tmp_path, monkeypatch, state, tool, order):
    """名指し参照を読んだ後も、後続・同時の起動要求にはSkillの成功を要する。"""
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/process-wi/SKILL.md"
    reference = skill.parent / "references/reference.md"
    reference.parent.mkdir(parents=True)
    skill.write_text("---\nname: target\n---\n# 対象\n固有本文\n", encoding="utf-8")
    reference.write_text("# 参考資料\n本文\n", encoding="utf-8")
    source = root / "share/source.md"
    source.parent.mkdir()
    source.write_text("# 配送元\n", encoding="utf-8")
    rows = [
        read_triggers.ReadTrigger(reference, 1, "判断", ("read:*/reference.md",), (skill,)),
        read_triggers.ReadTrigger(source, 1, "操作", ("skill:write-plan",), (skill,)),
    ]
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    payload = {"session_id": "skill-reference", "cwd": str(tmp_path)}

    def invoke(event, name, inputs, response=None):
        result = _run_hook_in_process(
            "posttooluse" if event == "PostToolUse" else "pretooluse",
            {
                **payload,
                "hook_event_name": event,
                "tool_name": name,
                "tool_input": inputs,
                "tool_response": response,
            },
        )
        assert result.returncode == 0 and not result.stderr
        return result.stdout

    def select_invocation():
        invoke("PreToolUse", "Skill", {"skill": "agent-toolkit:write-plan"})

    def acquire_skill():
        invoke("PostToolUse", "Skill", {"skill": "agent-toolkit:process-wi"}, "起動済み")

    write = {"file_path": str(tmp_path / "output.md"), "content": "本文"}
    if order == "invocation-first":
        select_invocation()
        acquire_skill()
    invoke("PreToolUse", "Read", {"file_path": str(reference)})
    invoke("PostToolUse", "Read", {"file_path": str(reference)}, reference.read_text(encoding="utf-8"))
    if order == "simultaneous":
        select_invocation()
    guidance = invoke("PreToolUse", "Write", write)
    if order == "invocation-first":
        assert str(skill) not in guidance
        return
    if order == "reference-first":
        assert "Readで" in guidance and "`Skill`で`agent-toolkit:process-wi`" not in guidance
    else:
        assert "`Skill`で`agent-toolkit:process-wi`" in guidance
    inputs = {"file_path": str(skill)} if tool == "Read" else {"command": "cat -- " + shlex.quote(str(skill))}
    invoke("PreToolUse", tool, inputs)
    invoke("PostToolUse", tool, inputs, skill.read_text(encoding="utf-8"))
    following = invoke("PreToolUse", "Write", write)
    if order == "reference-first":
        assert str(skill) not in following and '"permissionDecision": "deny"' not in following
        select_invocation()
        following = invoke("PreToolUse", "Write", write)
    assert "`Skill`で`agent-toolkit:process-wi`" in following
    acquire_skill()
    assert str(skill) not in invoke("PreToolUse", "Write", write)
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "other", invocation=True)
    skill.write_text(skill.read_text(encoding="utf-8") + "変更\n", encoding="utf-8")
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "main", invocation=True)


@pytest.mark.parametrize("tool", ["Edit", "MultiEdit", "Write"])
def test_hook_self_edit_twice_preserves_acquired_body(tmp_path, monkeypatch, state, tool):
    """Read後の自己編集成功を経由し、2回目の編集前に再取得や遮断を求めない。"""
    document = tmp_path / "reference.md"
    document.write_text("# 基準\n本文0\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:" + tool,), (document,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "self-edit", "cwd": str(tmp_path)}
    acquired = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(document)},
            "tool_response": document.read_text(encoding="utf-8"),
        },
    )
    assert acquired.returncode == 0 and not acquired.stderr
    for index in range(2):
        before = document.read_text(encoding="utf-8")
        after = before.replace(f"本文{index}", f"本文{index + 1}")
        edit = {"old_string": f"本文{index}", "new_string": f"本文{index + 1}"}
        inputs = {
            "file_path": str(document),
            **({"content": after} if tool == "Write" else {"edits": [edit]} if tool == "MultiEdit" else edit),
        }
        request = {**payload, "tool_name": tool, "tool_input": inputs}
        allowed = _run_hook_in_process("pretooluse", request)
        assert allowed.returncode == 0 and not allowed.stderr
        assert str(document) not in allowed.stdout and '"permissionDecision": "deny"' not in allowed.stdout
        document.write_text(after, encoding="utf-8")
        # Edit/Writeの型はClaude Code 2.1.316の出力スキーマに基づく。
        # MultiEditは同じ前全文を渡す境界の検証であり、現行ホストの起動対応を保証しない。
        observed = _run_hook_in_process(
            "posttooluse",
            {
                **request,
                "hook_event_name": "PostToolUse",
                "tool_response": {"filePath": str(document), "originalFile": before, "userModified": False},
            },
        )
        assert observed.returncode == 0 and not observed.stderr
    assert read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(document), "main"
    )


@pytest.mark.parametrize(
    "change", ["unread", "other-agent", "bash", "external", "failed", "missing-original", "user-modified", "staged"]
)
def test_hook_unknown_edits_require_reacquisition(tmp_path, monkeypatch, state, change):
    """取得・主体・編集成功・前後本文の対応が欠ける変更は継承しない。"""
    document = tmp_path / "reference.md"
    before = "# 基準\n前本文\n"
    document.write_text(before, encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Edit",), (document,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "unknown-edit", "cwd": str(tmp_path)}
    if change != "unread":
        acquired = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": str(document)},
                "tool_response": before,
            },
        )
        assert acquired.returncode == 0 and not acquired.stderr
    after = before.replace("前本文", "後本文")
    document.write_text(after + ("外部変更\n" if change == "external" else ""), encoding="utf-8")
    response = {
        "filePath": str(document),
        "originalFile": None if change == "missing-original" else before,
        "userModified": change == "user-modified",
        "staged": change == "staged",
    }
    result = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            **({"agent_id": "other"} if change == "other-agent" else {}),
            "hook_event_name": "PostToolUseFailure" if change == "failed" else "PostToolUse",
            "tool_name": "Bash" if change == "bash" else "Edit",
            "tool_input": {"command": "sed -i 's/前本文/後本文/' reference.md"}
            if change == "bash"
            else {"file_path": str(document), "old_string": "前本文", "new_string": "後本文"},
            "tool_response": response,
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert not read_delivery.target_ready(
        state.get(rules_context.OPERATION_SKILL_READY_KEY, {}), read_triggers.ReadTarget(document), "main"
    )
    request = {
        **payload,
        "tool_name": "Edit",
        "tool_input": {"file_path": str(document), "old_string": "後本文", "new_string": "次本文"},
    }
    guided = _run_hook_in_process("pretooluse", request)
    assert guided.returncode == 0 and str(document) in guided.stdout
    assert str(document) in denial_reason(_run_hook_in_process("pretooluse", request))


def test_hook_invoked_skill_self_edit_twice_keeps_invocation(tmp_path, monkeypatch, state):
    """起動済み本文の自己編集では起動証拠も継承し、参照取得とは区別する。"""
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/process-wi/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: target\n---\n# 基準\n本文0\n", encoding="utf-8")
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Edit",), (skill,))
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "skill-self-edit", "cwd": str(tmp_path)}
    acquired = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "agent-toolkit:process-wi"},
            "tool_response": "起動済み",
        },
    )
    assert acquired.returncode == 0 and not acquired.stderr
    for index in range(2):
        before = skill.read_text(encoding="utf-8")
        inputs = {"file_path": str(skill), "old_string": f"本文{index}", "new_string": f"本文{index + 1}"}
        request = {**payload, "tool_name": "Edit", "tool_input": inputs}
        allowed = _run_hook_in_process("pretooluse", request)
        assert allowed.returncode == 0 and not allowed.stderr
        assert str(skill) not in allowed.stdout and '"permissionDecision": "deny"' not in allowed.stdout
        skill.write_text(before.replace(inputs["old_string"], inputs["new_string"]), encoding="utf-8")
        observed = _run_hook_in_process(
            "posttooluse",
            {
                **request,
                "hook_event_name": "PostToolUse",
                "tool_response": {"filePath": str(skill), "originalFile": before, "userModified": False},
            },
        )
        assert observed.returncode == 0 and not observed.stderr
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "main", invocation=True)


@pytest.mark.parametrize("purpose", ["reference", "invocation", "both"])
@pytest.mark.usefixtures("state")
def test_public_compact_preserves_skill_acquisition_purpose(tmp_path, monkeypatch, purpose):
    """Read→compact→書込の案内でも、名指し参照と起動要求の用途を保つ。"""
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    skill = root / "skills/process-wi/SKILL.md"
    reference = skill.parent / "references/reference.md"
    reference.parent.mkdir(parents=True)
    skill.write_text("---\nname: process-wi\n---\n# 基準\n本文\n", encoding="utf-8")
    reference.write_text("# 参照元\n本文\n", encoding="utf-8")
    named = read_triggers.ReadTrigger(reference, 1, "操作", ("read:*/reference.md", "tool:Write"), (skill,))
    invocation = read_triggers.ReadTrigger(root / "source.md", 1, "操作", ("skill:write-plan", "tool:Write"), (skill,))
    rows = [named] if purpose == "reference" else [invocation] if purpose == "invocation" else [named, invocation]
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    payload = {"session_id": "compact-purpose", "cwd": str(tmp_path)}
    write = {"file_path": str(tmp_path / "output.md"), "content": "本文"}

    def invoke(event, tool, inputs, response=None):
        result = _run_hook_in_process(
            "posttooluse" if event == "PostToolUse" else "pretooluse",
            {**payload, "hook_event_name": event, "tool_name": tool, "tool_input": inputs, "tool_response": response},
        )
        assert result.returncode == 0 and not result.stderr
        return result.stdout

    if purpose != "invocation":
        invoke("PreToolUse", "Read", {"file_path": str(reference)})
        invoke("PostToolUse", "Read", {"file_path": str(reference)}, reference.read_text(encoding="utf-8"))
    invoke("PreToolUse", "Write", write)
    if purpose == "reference":
        invoke("PostToolUse", "Read", {"file_path": str(skill)}, skill.read_text(encoding="utf-8"))
    else:
        invoke("PostToolUse", "Skill", {"skill": "agent-toolkit:process-wi"}, "起動済み")
    compact = _run_hook_in_process("rules_context", {**payload, "hook_event_name": "SessionStart", "source": "compact"})
    assert compact.returncode == 0 and not compact.stderr
    context = json.loads(compact.stdout)["hookSpecificOutput"]["additionalContext"]
    skill_step = "`Skill`で`agent-toolkit:process-wi`"
    assert (skill_step in context) is (purpose != "reference")
    assert ("Readで`" + str(skill) + "`" in context) is (purpose == "reference")
    failure = rules_context.acquisition_failure(
        read_triggers.ReadTarget(skill, "欠落"), ValueError("見出し不明"), shell=False, reference=purpose == "reference"
    )
    assert (skill_step in failure) is (purpose != "reference")
    assert str(skill) in invoke("PreToolUse", "Write", write)
    invoke("PostToolUse", "Read", {"file_path": str(skill)}, skill.read_text(encoding="utf-8"))
    following = invoke("PreToolUse", "Write", write)
    if purpose == "reference":
        assert str(skill) not in following and '"permissionDecision": "deny"' not in following
    else:
        assert skill_step in following
        invoke("PostToolUse", "Skill", {"skill": "agent-toolkit:process-wi"}, "起動済み")
        assert str(skill) not in invoke("PreToolUse", "Write", write)
