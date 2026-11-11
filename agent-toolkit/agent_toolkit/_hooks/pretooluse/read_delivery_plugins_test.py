"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import hashlib
import json
import pathlib

import pytest

from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger, parse_tables
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.read_delivery import (
    _project_skill_rows,
)
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.git_repository import init_repository, run_git
from agent_toolkit._testing.read_delivery_support import (
    _TABLE_HEADER,
    _assert_acquired,
    _basics_root,
    _guidance,
    _local_plugin_root,
    _memory,
    _notice_body,
    _project_declarations,
    _project_skill_files,
    _rows,
    _two_plugin_roots,
    _write_operation,
)

__all__ = [
    "_TABLE_HEADER",
    "_assert_acquired",
    "_basics_root",
    "_guidance",
    "_local_plugin_root",
    "_memory",
    "_notice_body",
    "_project_declarations",
    "_project_skill_files",
    "_rows",
    "_two_plugin_roots",
    "_write_operation",
]


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("linked", [False, True])
def test_resolved_project_skill_delivers_startup_and_later_conditions(tmp_path, monkeypatch, memory, codex, shell, linked):
    skill = tmp_path / "other-repo/.claude/skills/language/SKILL.md"
    skill.parent.mkdir(parents=True)
    initial = skill.parent / "selection.md"
    initial.write_text("# 選択基準\n言語を選ぶ初動基準\n", encoding="utf-8")
    detail = skill.parent / "detail.md"
    detail.write_text("# 言語詳細\n## 必要節\n型の基準本文\n## 別用途\n無関係な別用途本文\n", encoding="utf-8")
    skill.write_text(
        "# 言語スキル\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 最初の判断前 | `selection.md` | 起動時 | `skill:language` |\n"
        "| Python編集前 | `detail.md`の「必要節」 | 操作 | `path:*.py` |\n",
        encoding="utf-8",
    )
    requested = skill
    if linked:
        links = tmp_path / "other-repo/.agents/skills"
        links.mkdir(parents=True)
        (links / "language").symlink_to(skill.parent, target_is_directory=True)
        requested = links / "language/SKILL.md"
    fallback = ReadTrigger(
        tmp_path / "awi-body.md",
        1,
        "操作",
        ("read:*/.claude/skills/*/SKILL.md", "read:*/.agents/skills/*/SKILL.md"),
        (),
        selector="project-skill",
    )
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [fallback])
    payload = {"cwd": str(tmp_path / "other-repo"), **({"turn_id": "turn"} if codex else {})}
    tool, inputs = ("Bash", {"command": f"cat -- {requested}"}) if shell else ("Read", {"file_path": str(requested)})
    first = read_delivery.deliver(payload, tool, inputs, "test")
    assert not _guidance(first)
    first = read_delivery.deliver(payload, "Inspect", {}, "test")
    assert str(initial) in _notice_body(first)
    assert any(str(skill) in key for key in memory[rules_context.OPERATION_SKILL_READY_KEY])
    assert "言語を選ぶ初動基準" not in _notice_body(first)
    observe_received({**payload, "tool_response": initial.read_text()}, "Read", {"file_path": str(initial)}, "test")
    assert not _guidance(read_delivery.deliver(payload, tool, inputs, "test"))
    observe_received({**payload, "tool_response": skill.read_text()}, "Read", {"file_path": str(skill)}, "test")
    assert not _guidance(read_delivery.deliver(payload, tool, inputs, "test"))
    if not codex:
        assert not read_delivery.target_ready(
            memory[rules_context.OPERATION_SKILL_READY_KEY], ReadTarget(skill), "main", invocation=True
        )
        observe_received({**payload, "tool_response": "起動成功"}, "Skill", {"skill": "language"}, "test")
        assert read_delivery.target_ready(
            memory[rules_context.OPERATION_SKILL_READY_KEY], ReadTarget(skill), "main", invocation=True
        )
    assert read_delivery.target_ready(memory[rules_context.OPERATION_SKILL_READY_KEY], ReadTarget(skill), "main")
    operation = {"file_path": str(tmp_path / "other-repo/code.py"), "content": "value = 1\n"}
    selected = read_delivery.deliver(payload, "Write", operation, "test")
    assert str(detail) in _notice_body(selected)
    assert "型の基準本文" not in _notice_body(selected)
    assert "無関係な別用途本文" not in _notice_body(selected)
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    rules_context.reset_main_operation_skill_ready("test")
    assert str(detail) in _notice_body(read_delivery.deliver(payload, "Write", operation, "test"))


@pytest.mark.usefixtures("memory")
def test_missing_scope_does_not_become_ready_from_a_whole_file_read(tmp_path, monkeypatch):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    source = tmp_path / "SKILL.md"
    rows = [ReadTrigger(source, 1, "操作", ("tool:Do",), (path,), ("存在しない節",))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    observe_received({"tool_response": path.read_text(encoding="utf-8")}, "Read", {"file_path": str(path)}, "test")
    decision = read_delivery.deliver({}, "Do", {}, "test")
    assert "見出しを一意に解決できない" in _notice_body(decision)
    assert "必須基準0" not in _notice_body(decision)
    recovery = read_delivery.deliver({}, "Write", {"file_path": str(source), "content": "宣言を修正する"}, "test")
    assert recovery.block is None
    rows[:] = [ReadTrigger(source, 1, "操作", ("tool:Do",), (path,), ("資料0",))]
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.usefixtures("memory")
@pytest.mark.parametrize("owner", ["agent-basics/skills/writing-standards", "agent-toolkit/skills/delegation"])
def test_artifact_standards_use_the_author_declaration(monkeypatch, tmp_path, owner):
    source = tmp_path / "agent-basics/skills/review-standards/SKILL.md"
    author = tmp_path / owner / "SKILL.md"
    reference = tmp_path / "python.md"
    reference.write_text("# 作成規範\n## 対象節\n対象言語の基準\n", encoding="utf-8")
    author.parent.mkdir(parents=True)
    author.write_text("# 作成側の基準\n", encoding="utf-8")
    row = ReadTrigger(source, 1, "判断", ("skill:review-standards",), (), selector="artifact-standards")
    declared = ReadTrigger(author, 1, "操作", ("path:*.py",), (reference,), ("対象節",))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row, declared])
    # 旧来の拡張子別の全文対応表が残っていれば`references/python.md`の全文が加わるよう、名前で解決先を分ける。
    monkeypatch.setattr(
        read_delivery.read_triggers,
        "resolve_document",
        lambda spec, *_args, **_kwargs: reference if spec.endswith("references/python.md") else author,
    )
    targets = read_delivery.dynamic_targets(
        row, {"cwd": str(tmp_path)}, "Write", {"file_path": str(tmp_path / "code.py"), "content": "value = 1"}, {}, "main"
    )
    assert ReadTarget(author) in targets
    assert ReadTarget(reference, "対象節") in targets
    assert ReadTarget(reference) not in targets
    read_targets = read_delivery.dynamic_targets(
        row, {"cwd": str(tmp_path)}, "Read", {"file_path": str(tmp_path / "code.py")}, {}, "main"
    )
    assert ReadTarget(reference, "対象節") not in read_targets


def test_same_skill_name_keeps_plugin_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", tmp_path / "agent-toolkit")
    monkeypatch.setattr(read_delivery, "sibling_plugin_root", lambda name, _root: tmp_path / name)
    rows = []
    for plugin in ("agent-toolkit", "agent-basics"):
        root = tmp_path / plugin
        manifest = root / ".claude-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": plugin}), encoding="utf-8")
        source = root / "skills/sample/SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("# 基準\n", encoding="utf-8")
        rows.append(ReadTrigger(source, 1, "起動時", ("skill:sample",), (source,)))
    assert read_delivery.row_matches(rows[0], "Skill", {"skill": ":".join(("agent-toolkit", "sample"))}, str(tmp_path))
    assert not read_delivery.row_matches(rows[1], "Skill", {"skill": ":".join(("agent-toolkit", "sample"))}, str(tmp_path))
    assert read_delivery.row_matches(rows[1], "Skill", {"skill": "agent-basics:sample"}, str(tmp_path))
    assert not read_delivery.row_matches(rows[0], "Skill", {"skill": "agent-basics:sample"}, str(tmp_path))


def test_reference_to_another_root_does_not_activate_its_whole_skill_tree(tmp_path, monkeypatch):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    project = tmp_path / ".claude/skills/local/SKILL.md"
    project.parent.mkdir(parents=True)
    selected = foreign / "skills/search/SKILL.md"
    project.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        f"| 選択時 | `{selected}` | 判断 | `read:*/search/SKILL.md` |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(read_delivery.read_triggers, "document_sources", lambda *_args: [project])
    rows = read_delivery.declarations(str(tmp_path), host="codex")
    assert [row.source for row in rows] == [project]
    assert tuple(target.path for target in rows[0].targets) == (selected,)
    assert all(not row.source.is_relative_to(foreign) for row in rows)
    assert active != foreign


@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("agy", [False, True])
def test_same_named_plugin_read_uses_the_selected_physical_root(tmp_path, monkeypatch, shell, agy):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    source = foreign / "skills/search/SKILL.md"
    row = parse_tables(source, active)[0]
    path = active / "skills/search/SKILL.md"
    if agy:
        for root in (active, foreign):
            (root / "plugin.json").write_text((root / ".claude-plugin/plugin.json").read_text(), encoding="utf-8")
        path = path.parent / "references/antigravity-full-instructions.md"
        path.parent.mkdir()
        path.write_text(source.read_text(), encoding="utf-8")
    tool, inputs = ("Bash", {"command": f"cat -- {path}"}) if shell else ("Read", {"file_path": str(path)})
    assert not read_delivery.row_matches(row, tool, inputs, str(tmp_path))
    selected = source.parent / "references/antigravity-full-instructions.md" if agy else source
    if agy:
        selected.parent.mkdir()
        selected.write_text(source.read_text(), encoding="utf-8")
    inputs = {"command": f"cat -- {selected}"} if shell else {"file_path": str(selected)}
    assert read_delivery.row_matches(row, tool, inputs, str(tmp_path))
    assert not read_delivery.row_matches(row, "Skill", {"skill": f"agent-toolkit:{source.parent.name}"}, str(tmp_path))


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_explicit_foreign_skill_keeps_startup_and_followup_declarations(tmp_path, monkeypatch, codex):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(read_delivery.read_triggers, "document_sources", lambda *_args: [])
    skill = foreign / "skills/search/SKILL.md"
    initial, later = skill.parent / "initial.md", skill.parent / "later.md"
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    inputs = {"file_path": str(skill)}
    first = read_delivery.deliver(payload, "Read", inputs, "test")
    assert not _guidance(first)
    assert str(initial) in _notice_body(read_delivery.deliver(payload, "Inspect", {}, "test"))
    observe_received({**payload, "tool_response": initial.read_text()}, "Read", {"file_path": str(initial)}, "test")
    assert _guidance(read_delivery.deliver(payload, "Read", inputs, "test")) is None
    if codex:
        observe_received({**payload, "tool_response": skill.read_text()}, "Read", inputs, "test")
    else:
        observe_received({**payload, "tool_response": skill.read_text()}, "Skill", {"skill": "agent-basics:search"}, "test")
    selected = read_delivery.deliver(payload, "Do", {}, "test")
    if not codex:
        # 同名pluginのSkill起動は、指定した別実体を取得した根拠にはならない。
        assert not _guidance(selected)
        return
    assert str(later) in _notice_body(selected)
    assert str(active) not in _notice_body(selected)


@pytest.mark.parametrize("change", ["path", "operation", "removed"])
@pytest.mark.parametrize("actor", ["main", "child"])
@pytest.mark.usefixtures("memory")
def test_selected_foreign_declaration_uses_current_source_and_actor(tmp_path, monkeypatch, change, actor):
    """明示選択した外部pluginの現行宣言だけを、選択した主体の後続操作へ再接続する。"""
    _active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    monkeypatch.setattr(read_delivery.read_triggers, "document_sources", lambda *_args: [])
    source = foreign / "skills/search/SKILL.md"
    initial = source.parent / "initial.md"
    replacement = source.parent / "replacement.md"
    replacement.write_text("# 現行資料\n現在の本文\n", encoding="utf-8")
    payload = {"session_id": "test", "cwd": str(tmp_path), "turn_id": "turn"}
    selected = _run_hook_in_process("pretooluse", {**payload, "tool_name": "Read", "tool_input": {"file_path": str(source)}})
    assert selected.returncode == 0
    if change == "removed":
        source.write_text("# 選択したskill\n", encoding="utf-8")
    else:
        source.write_text(
            "# 選択したskill\n| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
            + (
                "| 初動 | `replacement.md` | 起動時 | `skill:search` |\n"
                if change == "path"
                else "| 別操作 | `initial.md` | 操作 | `tool:Other` |\n"
            ),
            encoding="utf-8",
        )
    operation = _write_operation(tmp_path, {**payload, **({"agent_id": actor} if actor != "main" else {})})
    first = _run_hook_in_process("pretooluse", operation)
    assert first.returncode == 0 and not first.stderr
    assert str(initial) not in first.stdout
    if change != "path" or actor != "main":
        assert str(replacement) not in first.stdout and '"permissionDecision": "deny"' not in first.stdout
        return
    assert str(replacement) in first.stdout
    repeated = _run_hook_in_process("pretooluse", operation)
    assert repeated.returncode == 0 and not repeated.stdout and not repeated.stderr
    _assert_acquired(payload, replacement, False)
    for path in (initial, replacement):
        result = _run_hook_in_process(
            "posttooluse",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": str(path)},
                "tool_response": path.read_text(encoding="utf-8"),
            },
        )
        assert result.returncode == 0
        rewritten = _run_hook_in_process("pretooluse", operation)
        assert rewritten.returncode == 0 and not rewritten.stdout and not rewritten.stderr
        if path == initial:
            _assert_acquired(payload, replacement, False)
        else:
            _assert_acquired(payload, replacement, True)


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.usefixtures("memory")
def test_packaged_skill_read_delivers_startup_material_before_its_own_pending_read(tmp_path, monkeypatch, codex, shell):
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-toolkit", "version": "2.224.0"}), encoding="utf-8"
    )
    skill = root / "skills/search/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 選択したskill\n", encoding="utf-8")
    initial = skill.parent / "initial.md"
    initial.write_text("# 初動\n判断前の本文\n", encoding="utf-8")
    rows = [
        ReadTrigger(skill, 1, "起動時", ("skill:search",), (initial,)),
        ReadTrigger(skill, 2, "判断", ("read:*/skills/search/SKILL.md",), (skill,), selector="same-document"),
    ]
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    tool, inputs = ("Bash", {"command": f"cat -- {skill}"}) if shell else ("Read", {"file_path": str(skill)})

    first = read_delivery.deliver(payload, tool, inputs, "test")
    assert not _guidance(first)
    assert str(initial) in _notice_body(read_delivery.deliver(payload, "Inspect", {}, "test"))
    observe_received({**payload, "tool_response": initial.read_text()}, "Read", {"file_path": str(initial)}, "test")
    assert _guidance(read_delivery.deliver(payload, tool, inputs, "test")) is None


@pytest.mark.parametrize("case", ["full", "section", "actor", "changed", "data"])
def test_only_current_full_actor_proof_activates_plugin_declarations(tmp_path, monkeypatch, case):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    path = foreign / "skills/search/SKILL.md"
    if case == "data":
        path = tmp_path / "task-input.md"
        path.write_text((foreign / "skills/search/SKILL.md").read_text(), encoding="utf-8")
    key = read_delivery.document_key(path)
    if case == "section":
        key += "#heading:選択したskill"
    records = {key: ["other" if case == "actor" else "main"]}
    if case == "changed":
        path.write_text(path.read_text() + "変更後\n", encoding="utf-8")
    rows = _project_skill_rows(records, "main")
    assert bool(rows) is (case == "full")
    assert active != foreign


@pytest.mark.usefixtures("memory")
def test_public_hook_records_the_worktree_project_skill_invoked_in_a_nested_worktree(tmp_path, monkeypatch):
    """主checkoutと配下のlinked worktreeに同名のプロジェクトスキルがあっても、worktree側の起動を読了として記録する。

    Claude Codeはlinked worktreeのrootで探索を止め、worktree側の`SKILL.md`を読み込む（2026年10月29日のセッション記録の
    `Base directory for this skill:`行で確認）。
    """

    main = init_repository(tmp_path / "repo", files=_project_skill_files("proj-skill"), commit_message="初期")
    worktree = main / ".claude/worktrees/lane"
    run_git(main, "worktree", "add", "--quiet", "--detach", str(worktree))
    _project_declarations(monkeypatch, read_delivery.plugin_resources.plugin_root().resolve())
    payload = {"cwd": str(worktree)}
    before = _notice_body(read_delivery.deliver(payload, "Do", {}, "test"))
    assert "`Skill`で`proj-skill`を起動せよ" in before
    assert str((worktree / ".claude/skills/proj-skill/SKILL.md").resolve()) not in before
    assert str((main / ".claude/skills/proj-skill/SKILL.md").resolve()) not in before
    observe_received(
        {**payload, "tool_response": {"success": True, "commandName": "proj-skill"}}, "Skill", {"skill": "proj-skill"}, "test"
    )
    assert _guidance(read_delivery.deliver(payload, "Do", {}, "test")) is None


@pytest.mark.usefixtures("memory")
def test_public_hook_prefers_the_user_skill_over_a_same_named_project_skill(tmp_path, monkeypatch):
    """同名のユーザースキルとプロジェクトスキルでは、Claude Codeの優先順位どおりユーザースキルの起動として記録する。"""

    project = init_repository(tmp_path / "repo", files=_project_skill_files("proj-skill"), commit_message="初期")
    user = pathlib.Path.home() / ".claude/skills/proj-skill/SKILL.md"
    user.parent.mkdir(parents=True)
    user.write_text("# ユーザースキル\n\nユーザー本文\n", encoding="utf-8")
    caller = pathlib.Path.home() / ".claude/skills/user-caller/SKILL.md"
    caller.parent.mkdir(parents=True)
    caller.write_text(
        f"# 呼出側\n\n{_TABLE_HEADER}| 操作の前 | `../proj-skill/SKILL.md` | 操作 | `tool:Judge` |\n", encoding="utf-8"
    )
    _project_declarations(monkeypatch, read_delivery.plugin_resources.plugin_root().resolve())
    payload = {"cwd": str(project)}
    observe_received(
        {**payload, "tool_response": {"success": True, "commandName": "proj-skill"}}, "Skill", {"skill": "proj-skill"}, "test"
    )
    assert _guidance(read_delivery.deliver(payload, "Judge", {}, "test")) is None
    project_skill = (project / ".claude/skills/proj-skill/SKILL.md").resolve()
    assert str(project_skill) in _notice_body(read_delivery.deliver(payload, "Do", {}, "test"))
    _assert_acquired(payload, project_skill, False)


@pytest.mark.parametrize("same_content", [True, False])
@pytest.mark.usefixtures("memory")
def test_worktree_copy_of_a_distributed_skill_is_released_by_skill_or_read(tmp_path, monkeypatch, same_content):
    """起動中の配布物とworktree内の写しが同じ名前のスキルを持つとき、写しも取得済みへ到達できる。

    Skill起動は起動中の配布物だけを読み込むため、同名の写しが候補に含まれても実体を1件に決めて記録する。
    写しは内容の一致にかかわらず、その実体に対するReadで取得済みにする。
    """
    toolkit = tmp_path / "dist/agent-toolkit"
    (toolkit / ".claude-plugin").mkdir(parents=True)
    (toolkit / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    active = _basics_root(tmp_path / "dist/agent-basics", "# 検証\n\n配布物の本文\n")
    copy = _basics_root(
        tmp_path / "worktree/agent-basics", "# 検証\n\n配布物の本文\n" if same_content else "# 検証\n\n作業中の本文\n"
    )
    monkeypatch.setattr(read_delivery.plugin_resources, "plugin_root", lambda: toolkit)
    rows = [ReadTrigger(tmp_path / "caller.md", 1, "操作", ("tool:Write",), (active, copy))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    monkeypatch.setattr(read_delivery.read_triggers, "reference_method_targets", lambda *_args, **_kwargs: ())
    payload = {"session_id": "test", "cwd": str(tmp_path / "worktree")}
    operation = {"file_path": str(tmp_path / "out.md"), "content": "本文"}
    first = read_delivery.deliver(payload, "Write", operation, "test")
    notice = _notice_body(first)
    assert notice.count("`Skill`で`agent-basics:check-execution`を起動せよ") == 1
    assert str(active) not in notice
    assert f"Readで`{copy}`を取得せよ" in notice
    assert notice.count(str(copy)) == 1
    notice_module.record_delivery("\n".join(first.notices))
    assert not _guidance(read_delivery.deliver(payload, "Write", operation, "test"))
    _assert_acquired(payload, active, False, invocation=True)
    _assert_acquired(payload, copy, False)
    observe_received({**payload, "tool_response": "起動成功"}, "Skill", {"skill": "agent-basics:check-execution"}, "test")
    assert not _guidance(read_delivery.deliver(payload, "Write", operation, "test"))
    _assert_acquired(payload, active, True, invocation=True)
    _assert_acquired(payload, copy, same_content)
    observe_received({**payload, "tool_response": copy.read_text(encoding="utf-8")}, "Read", {"file_path": str(copy)}, "test")
    decision = read_delivery.deliver(payload, "Write", operation, "test")
    assert decision.block is None
    assert not _guidance(decision)
    _assert_acquired(payload, copy, True)


@pytest.mark.parametrize("agent", [False, True])
def test_acquisition_failure_and_compact_guidance_read_a_worktree_copy(tmp_path, monkeypatch, agent):
    """取得失敗と圧縮後の案内も、`Skill`起動では読み込まれない写しへReadを案内する。"""
    toolkit = tmp_path / "dist/agent-toolkit"
    (toolkit / ".claude-plugin").mkdir(parents=True)
    (toolkit / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    active = _basics_root(tmp_path / "dist/agent-basics", "# 検証\n\n配布物の本文\n")
    copy = _basics_root(tmp_path / "worktree/agent-basics", "# 検証\n\n作業中の本文\n")
    monkeypatch.setattr(read_delivery.plugin_resources, "plugin_root", lambda: toolkit)
    for path, loadable in ((active, True), (copy, False)):
        notice = rules_context.acquisition_failure(ReadTarget(path, "不存在"), ValueError("範囲不明"), shell=False, agent=agent)
        assert ("`Skill`で`agent-basics:check-execution`" in notice) is loadable
        assert (f"`{path}`をReadで全文読め" in notice) is (not loadable or agent)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        compact = rules_context._compact_recovery_context("claude", 10000, [(path, "", digest, False)])  # pylint: disable=protected-access
        if loadable:
            assert compact is None
        else:
            assert compact is not None
            assert "`Skill`で`agent-basics:check-execution`" not in compact
            assert f"Readで`{path}`を全文読め" in compact
