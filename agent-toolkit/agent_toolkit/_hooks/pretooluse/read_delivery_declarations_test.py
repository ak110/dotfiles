"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import subprocess

import pytest

from agent_toolkit._common import plugin_roots
from agent_toolkit._common.plugin_roots import PluginRootError
from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger, parse_tables
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.read_delivery import (
    _declaration_recovery_operation,
)
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _assert_acquired,
    _guidance,
    _guided_without_acquisition,
    _local_plugin_root,
    _memory,
    _notice_body,
    _rows,
    _write_operation,
)

__all__ = [
    "_assert_acquired",
    "_guidance",
    "_guided_without_acquisition",
    "_local_plugin_root",
    "_memory",
    "_notice_body",
    "_rows",
    "_write_operation",
]


@pytest.mark.parametrize(
    "kind,operation,document",
    [
        ("未分類", "search", "reference.md"),
        ("操作", "unknown", "reference.md"),
        ("判断", "", "reference.md"),
        ("操作", "search", "absent.md"),
    ],
)
def test_invalid_declaration_is_rejected(tmp_path, kind, operation, document):
    (tmp_path / "reference.md").write_text("# 資料\n", encoding="utf-8")
    source = tmp_path / "SKILL.md"
    source.write_text(
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
        f"| 検索 | `{document}` | {kind} | `{operation}` |\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        parse_tables(source, tmp_path, strict=True)


def test_two_column_table_is_a_judgment_declaration_in_strict_mode(tmp_path):
    """契機と配送操作の列が無い表は、資料を選んで読む判断の宣言として受理する。"""
    (tmp_path / "reference.md").write_text("# 資料\n", encoding="utf-8")
    source = tmp_path / "SKILL.md"
    source.write_text("| 時点または条件 | 全文読む資料 |\n| --- | --- |\n| 検索 | `reference.md` |\n", encoding="utf-8")
    rows = parse_tables(source, tmp_path, strict=True)
    assert [(row.kind, row.derived_read, row.documents) for row in rows] == [("判断", True, (tmp_path / "reference.md",))]


@pytest.mark.parametrize(
    ("tool", "inputs", "allowed"),
    [
        ("Read", {"file_path": "/source/criteria.md"}, True),
        ("Glob", {"pattern": "*.md"}, True),
        ("Grep", {"pattern": "全文読む資料"}, True),
        ("Bash", {"command": "cat -- /source/criteria.md"}, True),
        ("Bash", {"command": "sed -n '1,20p' -- /source/criteria.md"}, True),
        ("Bash", {"command": "sed -n '1,20w /source/out.md' /source/criteria.md"}, False),
        ("Bash", {"command": "codex plugin list --json"}, True),
        ("Bash", {"command": "atk agents logs --session-id observed"}, True),
        ("Bash", {"command": "cat -- /source/criteria.md > /source/result.md"}, False),
        ("Bash", {"command": "cat -- /source/criteria.md; rm -- /source/other.md"}, False),
        ("Bash", {"command": "cat -- $(touch /source/other.md)"}, False),
        ("Bash", {"command": "python3 -c 'print(1)'"}, False),
        ("Bash", {"command": "atk agents start --prompt proposed"}, False),
        ("Write", {"file_path": "/source/draft.md", "content": "変更"}, False),
    ],
)
def test_unresolved_declarations_keep_recovery_reads_and_block_changes(monkeypatch, tool, inputs, allowed):
    def unresolved(*_args, **_kwargs):
        raise PluginRootError("Codexのagent-basics導入先が一意でない: 0件")

    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    monkeypatch.setattr(read_delivery.delegated_session, "handoff_path", lambda _env: None)
    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    result = read_delivery.deliver({"cwd": "/source"}, tool, inputs, "test")
    assert result.block is None
    assert (
        _declaration_recovery_operation(tool, inputs, PluginRootError("Codexのagent-basics導入先が一意でない: 0件")) is allowed
    )
    if allowed:
        assert result.notices


def test_unresolved_declarations_keep_confirmation_warning_only(monkeypatch):
    def unresolved(*_args, **_kwargs):
        raise PluginRootError("Codexのagent-basics導入先が一意でない: 0件")

    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    monkeypatch.setattr(read_delivery.delegated_session, "handoff_path", lambda _env: None)
    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    result = read_delivery.deliver({}, "request_user_input", {}, "test")
    assert result.block is None
    guidance = _notice_body(result)
    assert "読込表を取得できない" in guidance
    assert result.notices


@pytest.mark.parametrize(
    ("command", "marketplaces", "error", "allowed"),
    [
        ("codex plugin add agent-basics@owner --json", ["owner"], "missing", True),
        ("codex plugin add agent-basics@other --json", ["owner"], "missing", False),
        ("codex plugin add other@owner --json", ["owner"], "missing", False),
        ("codex plugin add agent-basics@owner --json", [], "missing", False),
        ("codex plugin add agent-basics@owner --json", ["owner", "other"], "missing", False),
        ("codex plugin add agent-basics@owner --json", ["owner"], "syntax", False),
        ("codex plugin add agent-basics@owner --json > output", ["owner"], "missing", False),
        ("codex plugin add agent-basics@owner --json; echo ok", ["owner"], "missing", False),
        ("codex plugin add agent-basics@owner --json", ["owner"], "timeout", False),
    ],
)
def test_dependency_recovery_is_limited_to_the_existing_toolkit_marketplace(
    monkeypatch, memory, command, marketplaces, error, allowed
):
    def unresolved(*_args, **_kwargs):
        if error == "syntax":
            raise ValueError("読込表の列数が一致しない")
        raise PluginRootError("Codexのagent-basics導入先が一意でない: 0件")

    calls: list[list[str]] = []
    installed = [
        {"name": "agent-toolkit", "installed": True, "enabled": True, "marketplaceName": name} for name in marketplaces
    ]
    cli = codex_plugin_cli(installed, marketplaces=[*marketplaces, "unrelated"], calls=calls)

    def plugin_list(argv, **kwargs):
        if error == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        return cli(argv, **kwargs)

    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", plugin_list)
    result = read_delivery.deliver({}, "Bash", {"command": command}, "test")
    assert result.block is None
    assert result.notices
    assert all(argv[1:3] == ["plugin", "marketplace"] or "--marketplace" in argv for argv in calls)
    reason = (
        ValueError("読込表の列数が一致しない")
        if error == "syntax"
        else PluginRootError("Codexのagent-basics導入先が一意でない: 0件")
    )
    assert _declaration_recovery_operation("Bash", {"command": command}, reason) is allowed
    assert memory == {}


@pytest.mark.parametrize("kind", ["own", "other", "mixed"])
def test_missing_dependency_keeps_the_own_handoff_only_exemption(tmp_path, monkeypatch, memory, kind):
    own = tmp_path / "own-handoff.md"
    other = tmp_path / "other.md"
    calls = []

    def unresolved(*_args, **_kwargs):
        calls.append(True)
        raise PluginRootError("Codexのagent-basics導入先が一意でない: 0件")

    monkeypatch.setattr(read_delivery.delegated_session, "handoff_path", lambda _env: str(own))
    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    tool, inputs = "Write", {"file_path": str(own if kind == "own" else other), "content": "到達点"}
    if kind == "mixed":
        patch = f"*** Begin Patch\n*** Add File: {own}\n+到達点\n*** Add File: {other}\n+変更\n*** End Patch"
        tool, inputs = "apply_patch", {"command": patch}
    result = read_delivery.deliver({}, tool, inputs, "test")
    assert result.block is None
    assert bool(_guidance(result)) is (kind != "own")
    assert bool(calls) is (kind != "own")
    assert memory == {}


@pytest.mark.parametrize("failure", ["root", "io", "missing", "unicode", "root-text-only"])
@pytest.mark.parametrize(
    ("tool", "inputs"),
    [
        ("Grep", {"pattern": "x"}),
        ("Glob", {"pattern": "*.md"}),
        ("Read", {"file_path": "/source/criteria.md"}),
        ("mcp__agents_server__list", {}),
        ("Bash", {"command": "cat -- /source/criteria.md"}),
        ("Bash", {"command": "sed -n '1,20p' -- /source/criteria.md"}),
        ("Bash", {"command": "codex plugin list --json"}),
    ],
)
def test_missing_skill_recovery_requires_a_root_resolution_error(monkeypatch, memory, failure, tool, inputs):
    def unresolved(*_args, **_kwargs):
        if failure == "root":
            raise PluginRootError("codexのagent-basics導入先が一意でない: 0件")
        if failure == "io":
            raise OSError("資料を取得できない")
        if failure == "unicode":
            raise UnicodeError("資料をUTF-8で取得できない")
        if failure == "root-text-only":
            raise ValueError("codexのagent-basics導入先が一意でない: 0件")
        raise ValueError("資料が存在しない")

    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    result = read_delivery.deliver({"cwd": "/source"}, tool, inputs, "test")
    assert result.block is None
    assert result.notices
    assert memory == {}


@pytest.mark.parametrize("failure", ["root", "io", "missing"])
@pytest.mark.parametrize("operation", ["question", "own-handoff", "mixed-write", "other-write"])
def test_missing_skill_recovery_keeps_confirmation_and_handoff_boundaries(tmp_path, monkeypatch, memory, failure, operation):
    own, other = tmp_path / "handoff.md", tmp_path / "other.md"
    calls = []

    def unresolved(*_args, **_kwargs):
        calls.append(True)
        if failure == "root":
            raise PluginRootError("codexのagent-basics導入先が一意でない: 0件")
        if failure == "io":
            raise OSError("資料を取得できない")
        raise ValueError("資料が存在しない")

    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    monkeypatch.setattr(read_delivery.delegated_session, "handoff_path", lambda _env: str(own))
    tool, inputs = "Write", {"file_path": str(own if operation == "own-handoff" else other), "content": "記録"}
    if operation == "question":
        tool, inputs = "request_user_input", {}
    elif operation == "mixed-write":
        tool = "apply_patch"
        inputs = {"command": f"*** Begin Patch\n*** Add File: {own}\n+記録\n*** Add File: {other}\n+本文\n*** End Patch"}
    result = read_delivery.deliver({}, tool, inputs, "test")
    assert result.block is None
    assert bool(_guidance(result)) is (operation != "own-handoff")
    assert bool(calls) is (operation != "own-handoff")
    assert memory == {}


def test_missing_skill_read_failure_preserves_existing_actor_and_role_state(tmp_path, monkeypatch, memory):
    acquired = tmp_path / "acquired.md"
    acquired.write_text("# 取得した資料\n本文\n", encoding="utf-8")
    records = {
        read_delivery.document_key(acquired): ["main", "other"],
        "read-trigger-role:" + str(tmp_path / "exec.subagent.md"): ["main"],
    }
    memory[rules_context.OPERATION_SKILL_READY_KEY] = records
    original = json.loads(json.dumps(memory))

    def unresolved(*_args, **_kwargs):
        raise OSError("資料を取得できない")

    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    result = read_delivery.deliver({}, "Grep", {"pattern": "x"}, "test")
    assert "資料を取得できない" in _notice_body(result)
    assert memory == original


@pytest.mark.parametrize("operation", ["search", "install"])
def test_missing_skill_real_root_error_keeps_the_dependency_repair_route(tmp_path, monkeypatch, memory, operation):
    calls: list[list[str]] = []
    installed = [{"name": "agent-toolkit", "installed": True, "enabled": True, "marketplaceName": "fixture-market"}]
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", codex_plugin_cli(installed, calls=calls))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    root = tmp_path / "toolkit"

    def unresolved(*_args, **_kwargs):
        return read_delivery.read_triggers.resolve_plugin_root("agent-basics", root, "codex")

    with pytest.raises(PluginRootError) as observed:
        unresolved()
    assert isinstance(observed.value, ValueError)
    assert str(observed.value) == "codexのagent-basics導入先が一意でない: 0件。codex plugin list --jsonを確認する"
    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    tool, inputs = "Grep", {"pattern": "x"}
    if operation == "install":
        tool, inputs = "Bash", {"command": "codex plugin add agent-basics@fixture-market --json"}
    result = read_delivery.deliver({}, tool, inputs, "test")
    assert result.block is None and result.notices
    assert ["codex", "plugin", "list", "--json", "--marketplace", "fixture-market"] in calls
    assert memory == {}


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_unemitted_delivery_cannot_block_the_first_public_write(tmp_path, monkeypatch, codex):
    """破棄した試作通知では初回案内を消さず、公開出力した後は未取得でも再掲しない。"""
    path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    operation = _write_operation(tmp_path, payload)
    for _ in range(2):
        trial = read_delivery.deliver(payload, "Write", operation["tool_input"], "test")
        assert trial.notices and trial.block is None
    _guided_without_acquisition(operation, path)
    acquired = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
            "tool_response": path.read_text(encoding="utf-8"),
        },
    )
    assert acquired.returncode == 0
    _assert_acquired(payload, path, True)
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and not final.stdout and not final.stderr


@pytest.mark.parametrize("old_heading_remains", [False, True])
@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_changed_declaration_reconnects_current_heading_before_rewrite(tmp_path, monkeypatch, codex, old_heading_remains):
    """同じ資料で必要節が変わったら改めて案内し、取得範囲も現行見出しで判定する。"""
    source = tmp_path / "source.md"
    reference = tmp_path / "reference.md"
    reference.write_text("# 資料\n## 旧見出し\n以前の基準\n", encoding="utf-8")

    def declaration(heading):
        return (
            "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
            f"| 選択時 | `reference.md#{heading}` | 操作 | `tool:Write` |\n"
        )

    source.write_text(declaration("旧見出し"), encoding="utf-8")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: parse_tables(source, tmp_path))
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    operation = _write_operation(tmp_path, payload)
    selected = _run_hook_in_process("pretooluse", operation)
    assert selected.returncode == 0 and "旧見出し" in selected.stdout
    body = "# 資料\n" + ("## 旧見出し\n以前の基準\n" if old_heading_remains else "") + "## 新見出し\n現在の基準\n"
    reference.write_text(body, encoding="utf-8")
    source.write_text(declaration("新見出し"), encoding="utf-8")
    operation = _write_operation(tmp_path, payload)
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and not first.stderr
    assert first.stdout, "現行宣言の資料案内が無い"
    context = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "新見出し" in context and "旧見出し" not in context
    repeated = _run_hook_in_process("pretooluse", operation)
    assert repeated.returncode == 0 and not repeated.stdout and not repeated.stderr
    _assert_acquired(payload, ReadTarget(reference, "新見出し"), False)
    acquired = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(reference)},
            "tool_response": body,
        },
    )
    assert acquired.returncode == 0
    _assert_acquired(payload, ReadTarget(reference, "新見出し"), True)
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and not final.stdout and not final.stderr


@pytest.mark.usefixtures("memory")
def test_codex_unsupported_webfetch_does_not_create_condition_candidates(tmp_path, monkeypatch):
    row = ReadTrigger(
        tmp_path / "source.md",
        1,
        "判断",
        ("tool:Do",),
        (),
        selector="external-pytilpack-index",
        condition="対象モジュールを選ぶ時",
    )
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "test",
            "cwd": str(tmp_path),
            "turn_id": "turn",
            "tool_name": "Do",
            "tool_input": {},
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert "WebFetch" not in result.stdout and "読込条件の候補" not in result.stdout


@pytest.mark.parametrize("removed", [False, True])
@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_obsolete_declaration_cannot_block_another_phase(tmp_path, monkeypatch, removed, codex):
    """宣言の削除や操作変更後は、過去の案内を別工程の遮断に使わない。"""
    reference = tmp_path / "reference.md"
    reference.write_text("# 基準\n本文\n", encoding="utf-8")
    source = tmp_path / "source.md"
    rows = [ReadTrigger(source, 1, "操作", ("tool:Write",), (reference,))]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, reference)
    rows[:] = [] if removed else [ReadTrigger(source, 2, "操作", ("tool:Do",), (reference,))]
    result = _run_hook_in_process("pretooluse", operation)
    assert result.returncode == 0 and not result.stderr
    assert str(reference) not in result.stdout and '"permissionDecision": "deny"' not in result.stdout


@pytest.mark.parametrize("marker", ["truncated output", "Output too large", "truncatedByTokenCap"])
@pytest.mark.parametrize("method", ["Read", "Bash", "Skill"])
@pytest.mark.usefixtures("memory")
def test_marker_words_in_document_body_do_not_block_read(tmp_path, monkeypatch, method, marker):
    """省略を思わせる語句を本文に含む資料も、省略の無い取得なら読了になり再書込を許可する。"""
    if method == "Skill":
        root = tmp_path / "plugin"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text('{"name":"example-plugin"}', encoding="utf-8")
        path = root / "skills/example/SKILL.md"
        path.parent.mkdir(parents=True)
        rows = [ReadTrigger(path, 1, "操作", ("tool:Write",), (path,))]
        monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
        monkeypatch.setattr(read_delivery.read_triggers, "reference_method_targets", lambda *_args, **_kwargs: ())
    else:
        path = _rows(tmp_path, monkeypatch, "tool:Write", count=1)[0]
    path.write_text(f"# 資料\n\n本文中の語句: {marker}\n", encoding="utf-8")
    payload = {"session_id": "test", "cwd": str(tmp_path)}
    operation = _write_operation(tmp_path, payload)
    _guided_without_acquisition(operation, path)
    body = path.read_text(encoding="utf-8")
    if method == "Read":
        acquisition = {"tool_name": "Read", "tool_input": {"file_path": str(path)}, "tool_response": body}
    elif method == "Bash":
        acquisition = {
            "tool_name": "Bash",
            "tool_input": {"command": f"cat -- {path}"},
            "tool_response": {"stdout": body, "exit_code": 0},
        }
    else:
        acquisition = {
            "tool_name": "Skill",
            "tool_input": {"skill": "example-plugin:example"},
            "tool_response": f"Launching skill: example-plugin:example\n{body}",
        }
    observed = _run_hook_in_process("posttooluse", {**payload, "hook_event_name": "PostToolUse", **acquisition})
    assert observed.returncode == 0
    _assert_acquired(payload, path, True, invocation=method == "Skill")
    final = _run_hook_in_process("pretooluse", operation)
    assert final.returncode == 0 and str(path) not in final.stdout
