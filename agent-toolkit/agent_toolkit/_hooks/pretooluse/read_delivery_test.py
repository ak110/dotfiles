"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import subprocess

import pytest

from agent_toolkit._agents_server import launch_requests
from agent_toolkit._common.plugin_roots import PluginRootError
from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger, parse_tables
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.read_delivery import (
    _declaration_recovery_operation,
    _mark,
    _project_skill_rows,
    _redundant_selection,
)


def test_included_table_keeps_marker_order_and_is_counted_once(tmp_path, monkeypatch):
    def table(name):
        return (
            "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n"
            "| --- | --- | --- | --- |\n"
            f"| 取得時 | `{name}.md` | 判断 | `read:*/{name}.md` |\n"
        )

    for name in ("before", "outside", "after"):
        (tmp_path / f"{name}.md").write_text(f"# {name}\n基準\n", encoding="utf-8")
    external = tmp_path / "reading.md"
    external.write_text(table("outside"), encoding="utf-8")
    owner = tmp_path / "SKILL.md"
    owner.write_text(table("before") + "\n<!-- read-table-source: reading.md -->\n\n" + table("after"), encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", tmp_path)
    monkeypatch.setattr(read_delivery.read_triggers, "document_sources", lambda *_args: [external, owner])
    rows = read_delivery.declarations(str(tmp_path))
    assert [row.documents[0].name for row in rows] == ["before.md", "outside.md", "after.md"]


def _guidance(decision: Decision) -> str | None:
    assert decision.block is None
    return "\n".join(part for part in decision.notices if part) or None


def _notice_body(decision: Decision) -> str:
    assert decision.block is None
    message = "\n".join(part for part in decision.notices if part)
    assert message
    return message


@pytest.mark.parametrize("codex", [False, True])
def test_common_method_is_delivered_for_direct_and_pending_reads_without_starting_the_skill(
    tmp_path, monkeypatch, memory, codex
):
    root = tmp_path / "plugin"
    skill = root / "skills/example/SKILL.md"
    rule = skill.parent / "references/rule.md"
    rule.parent.mkdir(parents=True)
    skill.write_text("# 技能\n## 名指し参照の読込方法\n共通取得順\n## 別用途\n無関係な技能本文\n", encoding="utf-8")
    rule.write_text(
        "# 個別資料\n## 名指し参照の読込方法\n名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。\n",
        encoding="utf-8",
    )
    extra = skill.parent / "startup.md"
    extra.write_text("# 起動専用\n追加の起動時基準\n", encoding="utf-8")
    rows = [
        ReadTrigger(skill, 1, "起動時", ("skill:example",), (extra,)),
        ReadTrigger(rule, 1, "判断", ("tool:Judge",), (rule,)),
    ]
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    first = _notice_body(read_delivery.deliver(payload, "Read", {"file_path": str(rule)}, "test"))
    assert str(skill) in first
    assert str(extra) not in first
    assert "無関係な技能本文" not in first
    if not codex:
        assert "共通取得順" in first
    _mark("test", skill, "main", "名指し参照の読込方法")
    assert not _guidance(read_delivery.deliver(payload, "Read", {"file_path": str(rule)}, "test"))
    method_read = {"command": f"sed -n '2,3p' -- {skill}"}
    assert not _guidance(read_delivery.deliver(payload, "Bash", method_read, "test"))
    memory.clear()
    pending = _notice_body(read_delivery.deliver(payload, "Judge", {}, "test"))
    assert str(skill) in pending
    assert str(extra) not in pending
    assert "無関係な技能本文" not in pending


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_pending_documents_preserve_the_own_handoff_exemption(tmp_path, monkeypatch, codex):
    _rows(tmp_path, monkeypatch, "tool:Do", count=1)
    monkeypatch.setattr(read_delivery, "CLAUDE_BODY_BUDGET", 1)
    handoff = tmp_path / "own-handoff.md"
    monkeypatch.setattr(read_delivery.delegated_session, "handoff_path", lambda _env: str(handoff))
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    assert _guidance(read_delivery.deliver(payload, "Do", {}, "test"))
    assert not _guidance(read_delivery.deliver(payload, "Write", {"file_path": str(handoff), "content": "到達点"}, "test"))
    other = tmp_path / "draft.md"
    patch = f"*** Begin Patch\n*** Add File: {handoff}\n+到達点\n*** Add File: {other}\n+原稿\n*** End Patch"
    assert _guidance(read_delivery.deliver(payload, "apply_patch", {"command": patch}, "test")) is None


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("form", ["full", "excerpt", "legacy-reference", "compact-before-first-operation"])
def test_whole_contract_body_and_compaction_use_the_resolved_input(tmp_path, monkeypatch, memory, codex, form):
    contract = tmp_path / "explicit-contract.md"
    contract.write_text("# 現行作業契約\n保持する品質価値\n", encoding="utf-8")
    initial = launch_requests.whole_contract_reference(whole_contract_path=str(contract))
    assert "保持する品質価値" in initial
    if form == "excerpt":
        initial = initial.replace("保持する品質価値", "省略された抜粋")
    elif form == "legacy-reference":
        initial = initial.split('<atk-auto source="read-delivery"', 1)[0]
        memory[rules_context.OPERATION_SKILL_READY_KEY] = {"read-trigger-role:" + str(tmp_path / "old-role.md"): ["main"]}
    elif form == "compact-before-first-operation":
        monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
        rules_context.reset_main_operation_skill_ready("test")
    log = tmp_path / "transcript.jsonl"
    entry = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": initial}]}}
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("tool:Judge",), (), selector="input:作業契約")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    payload = {"transcript_path": str(log), **({"turn_id": "turn"} if codex else {})}
    first = read_delivery.deliver(payload, "Judge", {}, "test")
    if form == "full":
        assert _guidance(first) is None
    else:
        assert str(contract) in _notice_body(first)
    read_delivery.observe({**payload, "tool_response": contract.read_text()}, "Read", {"file_path": str(contract)}, "test")
    assert not _guidance(read_delivery.deliver(payload, "Judge", {}, "test"))
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    rules_context.reset_main_operation_skill_ready("test")
    contract.write_text("# 現行作業契約\n追加後の完成条件\n", encoding="utf-8")
    later = read_delivery.deliver(payload, "Judge", {}, "test")
    assert str(contract) in _notice_body(later)
    if not codex:
        assert "追加後の完成条件" in _notice_body(later)


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
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    tool, inputs = ("Bash", {"command": f"cat -- {requested}"}) if shell else ("Read", {"file_path": str(requested)})
    first = read_delivery.deliver(payload, tool, inputs, "test")
    assert str(initial) in _notice_body(first)
    assert any(str(skill) in key for key in memory[rules_context.OPERATION_SKILL_READY_KEY])
    if not codex:
        assert "言語を選ぶ初動基準" in _notice_body(first)
    read_delivery.observe({**payload, "tool_response": initial.read_text()}, "Read", {"file_path": str(initial)}, "test")
    assert not _guidance(read_delivery.deliver(payload, tool, inputs, "test"))
    read_delivery.observe({**payload, "tool_response": skill.read_text()}, "Read", {"file_path": str(skill)}, "test")
    assert not _guidance(read_delivery.deliver(payload, tool, inputs, "test"))
    operation = {"file_path": str(tmp_path / "other-repo/code.py"), "content": "value = 1\n"}
    selected = read_delivery.deliver(payload, "Write", operation, "test")
    assert str(detail) in _notice_body(selected)
    if not codex:
        assert "型の基準本文" in _notice_body(selected)
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
    read_delivery.observe({"tool_response": path.read_text(encoding="utf-8")}, "Read", {"file_path": str(path)}, "test")
    decision = read_delivery.deliver({}, "Do", {}, "test")
    assert "見出しを一意に解決できない" in _notice_body(decision)
    assert "必須基準0" not in _notice_body(decision)
    recovery = read_delivery.deliver({}, "Write", {"file_path": str(source), "content": "宣言を修正する"}, "test")
    assert recovery.block is None
    rows[:] = [ReadTrigger(source, 1, "操作", ("tool:Do",), (path,), ("資料0",))]
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.parametrize("mode", ["direct", "saved", "partitioned"])
@pytest.mark.usefixtures("memory")
def test_review_reference_arrives_after_successful_prepare_result(tmp_path, monkeypatch, mode):
    reference = tmp_path / "project-review.md"
    reference.write_text("# プロジェクト参照\n判断前の固有基準\n", encoding="utf-8")
    output = tmp_path / "prepare.json"
    body = json.dumps({"reference_document": str(reference), "status": "ok"}, indent=1)
    output.write_text(body, encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("tool:Do",), (), selector="review-reference")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    response = body if mode == "direct" else "保存先: " + str(output)
    read_delivery.observe(
        {"tool_response": {"stdout": response, "exit_code": 0}},
        "Bash",
        {"command": "atk run-script session-review-prepare --"},
        "test",
    )
    if mode != "direct":
        assert "prepare" in _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
        if mode == "saved":
            read_delivery.observe(
                {"tool_response": {"stdout": body, "exit_code": 0}}, "Bash", {"command": f"cat -- {output}"}, "test"
            )
        else:
            lines = body.splitlines(keepends=True)
            for start, end in ((1, 2), (3, len(lines))):
                read_delivery.observe(
                    {"tool_response": {"stdout": "".join(lines[start - 1 : end]), "exit_code": 0}},
                    "Bash",
                    {"command": f"sed -n '{start},{end}p' -- {output}"},
                    "test",
                )
    assert "判断前の固有基準" in _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.usefixtures("memory")
def test_external_index_requires_the_selected_page(monkeypatch, tmp_path):
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("tool:Do",), (), selector="external-pytilpack-index")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    index = "https://ak110.github.io/pytilpack/llms.txt"
    page = "https://ak110.github.io/pytilpack/api/functools/index.md"
    read_delivery.observe({"tool_response": "索引"}, "WebFetch", {"url": index}, "test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    read_delivery.observe({"tool_response": {"exit_code": 1}}, "WebFetch", {"url": page}, "test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    read_delivery.observe({"tool_response": "truncated output"}, "WebFetch", {"url": page}, "test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    read_delivery.observe({"tool_response": "選んだ対象ページの本文"}, "WebFetch", {"url": page}, "test")
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.usefixtures("memory")
def test_artifact_standards_use_the_author_declaration(monkeypatch, tmp_path):
    source = tmp_path / "skills/review-standards/SKILL.md"
    author = tmp_path / "skills/writing-standards/SKILL.md"
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
        source = root / "skills/writing-standards/SKILL.md"
        source.parent.mkdir(parents=True)
        source.write_text("# 基準\n", encoding="utf-8")
        rows.append(ReadTrigger(source, 1, "起動時", ("skill:writing-standards",), (source,)))
    assert read_delivery.row_matches(rows[0], "Skill", {"skill": "agent-toolkit:writing-standards"}, str(tmp_path))
    assert not read_delivery.row_matches(rows[1], "Skill", {"skill": "agent-toolkit:writing-standards"}, str(tmp_path))
    assert read_delivery.row_matches(rows[1], "Skill", {"skill": "agent-basics:writing-standards"}, str(tmp_path))
    assert not read_delivery.row_matches(rows[0], "Skill", {"skill": "agent-basics:writing-standards"}, str(tmp_path))


@pytest.mark.parametrize(
    "role",
    [
        "bulk-replace-review",
        "copilot-review-audit",
        "defect-investigation",
        "external-write-review",
        "lane-integration",
        "pick-wi-explain",
        "recurrence-self-check",
        "refine-prompt",
    ],
)
@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_integrated_parent_contract_arrives_before_launch(monkeypatch, role, codex):
    root = read_delivery.plugin_resources.plugin_root()
    row = ReadTrigger(root / "skills/delegation/SKILL.md", 1, "操作", ("tool:Launch",), (), selector="role-contract")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    payload = {"turn_id": "turn"} if codex else {}
    decision = read_delivery.deliver(payload, "Launch", {"subagent_md_path": role}, "test")
    targets = read_delivery.read_triggers.parent_contract_targets(role, root)
    assert _guidance(decision)
    assert all(str(target.path) in _notice_body(decision) for target in targets)
    if not codex:
        assert all(
            target.body().replace("${CLAUDE_PLUGIN_ROOT}", str(root)).strip() in _notice_body(decision) for target in targets
        )
        assert not _guidance(read_delivery.deliver(payload, "Launch", {"subagent_md_path": role}, "test"))


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("codex", [False, True])
def test_startup_input_target_survives_compaction(tmp_path, monkeypatch, memory, duplicate, codex):
    root = tmp_path / "plugin"
    share = root / "share"
    share.mkdir(parents=True)
    method = share / "read-trigger-contract.md"
    method.write_text("# 共通契約\n## 名指し参照の読込方法\n起動共通取得順\n", encoding="utf-8")
    source = share / "example.subagent.md"
    source.write_text(
        "# 役割\n## 名指し参照の読込方法\n"
        "名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。\n\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 起動時 | 入力ファイル | 起動時 | `role:example` | `input:原本` |\n",
        encoding="utf-8",
    )
    original = tmp_path / "original.md"
    original.write_text("# 原本\n起動入力の本文\n", encoding="utf-8")
    inputs = {"原本": str(original)}
    if duplicate:
        with source.open("a", encoding="utf-8") as stream:
            stream.write("| 起動時 | 同じ入力ファイル | 起動時 | `role:example` | `input:補足原本` |\n")
        inputs["補足原本"] = str(original)
    context = read_delivery.read_triggers.startup_context(source, root, inputs)
    assert context.count("起動入力の本文") == 1
    assert context.count("起動共通取得順") == 1
    log = tmp_path / "transcript.jsonl"
    text = read_delivery.task_documents.task_prompt_header(source) + "\n" + context
    entry = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    if codex:
        entry = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": read_delivery.task_documents.task_prompt_header(source)},
                    {"type": "input_text", "text": context},
                ],
            },
        }
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: parse_tables(source, root))
    payload = {"transcript_path": str(log)}
    assert not _guidance(read_delivery.deliver(payload, "Do", {}, "test"))
    assert not _guidance(read_delivery.deliver(payload, "Do", {}, "test"))
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    rules_context.reset_main_operation_skill_ready("test")
    restored = _notice_body(read_delivery.deliver(payload, "Do", {}, "test"))
    assert "起動入力の本文" in restored
    assert str(method) in restored


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_tool_output_does_not_assign_the_readers_role(tmp_path, monkeypatch, codex):
    root = tmp_path / "plugin"
    share = root / "share"
    share.mkdir(parents=True)
    role = share / "example.subagent.md"
    role.write_text("# 役割\n", encoding="utf-8")
    text = read_delivery.task_documents.task_prompt_header(role)
    entry = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": text}]}}
    if codex:
        entry = {"type": "response_item", "payload": {"type": "function_call_output", "output": text}}
    log = tmp_path / "transcript.jsonl"
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    assert not read_delivery.active_roles({"transcript_path": str(log)}, "test", {}, "main")


@pytest.fixture(name="memory")
def _memory(monkeypatch: pytest.MonkeyPatch):
    state: dict = {}

    def read(_session):
        return state

    def update(_session, mutate):
        mutate(state)

    monkeypatch.setattr(read_delivery, "read_state", read)
    monkeypatch.setattr(read_delivery, "update_state", update)
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    return state


def _rows(tmp_path, monkeypatch, operation="root-cause-writing", count=2):
    paths = []
    for index in range(count):
        path = tmp_path / f"reference-{index}.md"
        path.write_text(f"# 資料{index}\n\n必須基準{index}\n", encoding="utf-8")
        paths.append(path)
    rows = [ReadTrigger(tmp_path / "SKILL.md", 1, "操作", (operation,), tuple(paths))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    return paths


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_missing_full_and_partial_reads(tmp_path, monkeypatch, codex):
    paths = _rows(tmp_path, monkeypatch)
    payload = {"session_id": "test", "cwd": str(tmp_path)}
    if codex:
        payload["turn_id"] = "turn"
    operation = {"file_path": str(tmp_path / "plan.md"), "content": "## 原因分析\n"}
    decision = read_delivery.deliver(payload, "Write", operation, "test")
    assert _guidance(decision)
    assert str(paths[0]) in _notice_body(decision)
    if not codex:
        assert "必須基準0" in _notice_body(decision)
        assert not _guidance(read_delivery.deliver(payload, "Write", operation, "test"))
        return
    for path in paths:
        read_delivery.observe({**payload, "tool_response": "# 資料0\n"}, "Read", {"file_path": str(path), "limit": 1}, "test")
    assert _guidance(read_delivery.deliver(payload, "Write", operation, "test"))
    for path in paths:
        read_delivery.observe(
            {**payload, "tool_response": {"stdout": path.read_text(encoding="utf-8"), "exit_code": 0}},
            "Bash",
            {"command": f"cat -- {path}"},
            "test",
        )
    assert not _guidance(read_delivery.deliver(payload, "Write", operation, "test"))


def test_warning_has_all_paths_and_never_blocks(tmp_path, monkeypatch, memory):
    paths = _rows(tmp_path, monkeypatch, "confirmation", count=30)
    for _ in range(2):
        decision = read_delivery.deliver({}, "AskUserQuestion", {}, "test")
        assert decision.block is None
        assert len(decision.notices) == 1
        assert all(str(path) in decision.notices[0] for path in paths)
    assert all(
        not read_delivery.target_ready(memory.get(rules_context.OPERATION_SKILL_READY_KEY, {}), ReadTarget(path), "main")
        for path in paths
    )


@pytest.mark.parametrize("tool", ["AskUserQuestion", "ExitPlanMode", "request_user_input", "request_user_input_async"])
def test_invalid_declaration_keeps_confirmation_warning_only(monkeypatch, memory, tool):
    def invalid(*_args, **_kwargs):
        raise ValueError("資料が存在しない")

    monkeypatch.setattr(read_delivery, "declarations", invalid)
    result = read_delivery.deliver({}, tool, {}, "test")
    assert result.block is None
    assert len(result.notices) == 1
    assert "資料が存在しない" in result.notices[0]
    assert memory == {}


@pytest.mark.usefixtures("memory")
def test_delivery_records_the_body_snapshot_hash(tmp_path, monkeypatch):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    original = ReadTarget.body

    def replace_after_read(target, text=None):
        result = original(target, text)
        path.write_text("# 変更後\n新しい基準\n", encoding="utf-8")
        return result

    monkeypatch.setattr(ReadTarget, "body", replace_after_read)
    first = read_delivery.deliver({}, "Do", {}, "test")
    assert "必須基準0" in _notice_body(first)
    monkeypatch.setattr(ReadTarget, "body", original)
    second = read_delivery.deliver({}, "Do", {}, "test")
    assert "新しい基準" in _notice_body(second)


@pytest.mark.usefixtures("memory")
def test_bounded_delivery_retries_in_declaration_order(tmp_path, monkeypatch):
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=3)
    monkeypatch.setattr(read_delivery, "CLAUDE_BODY_BUDGET", 150 + len(str(paths[0])) + len(str(tmp_path)))
    notices = [_guidance(read_delivery.deliver({}, "Do", {}, "test")) for _ in range(4)]
    assert all(notice for notice in notices[:3])
    assert notices[3] is None
    for index, notice in enumerate(notices[:3]):
        assert notice is not None
        assert f"必須基準{index}" in notice
        assert all(f"必須基準{other}" not in notice for other in range(3) if other != index)


def test_agent_scope_and_compaction(tmp_path, monkeypatch, memory):
    _rows(tmp_path, monkeypatch, "tool:Do", count=1)
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    assert _guidance(read_delivery.deliver({"agent_id": "child"}, "Do", {}, "test"))
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    rules_context.reset_main_operation_skill_ready("test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    assert not _guidance(read_delivery.deliver({"agent_id": "child"}, "Do", {}, "test"))


@pytest.mark.parametrize("separator", ["", "-- "])
def test_sed_partitioned_reads_require_complete_coverage(tmp_path, monkeypatch, memory, separator):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for script, start, end in [("2,$p", 2, len(lines)), ("1p", 1, 1)]:
        read_delivery.observe(
            {"tool_response": {"stdout": "".join(lines[start - 1 : end]), "exit_code": 0}},
            "Bash",
            {"command": f"sed -n '{script}' {separator}{path}"},
            "test",
        )
        if start > 1:
            assert read_delivery.document_key(path) not in memory[rules_context.OPERATION_SKILL_READY_KEY]
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.parametrize(
    ("case", "ready"),
    [
        ("exact", True),
        ("missing-line", False),
        ("changed-line", False),
        ("extra-output", False),
        ("truncated", False),
        ("beyond-end", False),
        ("other-command", False),
        ("joined", True),
    ],
)
def test_sed_range_output_must_match_the_document_lines(tmp_path, monkeypatch, memory, case, ready):
    """行範囲の出力が資料の該当行と一致した場合だけ取得とし、欠落・相違・追加・切り詰めを取得にしない。"""
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=2 if case == "joined" else 1)
    lines = paths[0].read_text(encoding="utf-8").splitlines(keepends=True)
    command = f"sed -n '1,{len(lines)}p' -- {paths[0]}"
    output = "".join(lines)
    if case == "missing-line":
        output = "".join(lines[:-1])
    elif case == "changed-line":
        output = output.replace("必須基準0", "別の基準")
    elif case == "extra-output":
        output += "追加の出力\n"
    elif case == "truncated":
        output += "\nOutput too large"
    elif case == "beyond-end":
        command = f"sed -n '{len(lines) + 1},{len(lines) + 5}p' -- {paths[0]}"
        output = ""
    elif case == "other-command":
        command = f"echo 前置き; {command}"
        output = "前置き\n" + output
    elif case == "joined":
        command = "; ".join(f"sed -n '1,$p' -- {path}" for path in paths)
        output = "".join(path.read_text(encoding="utf-8") for path in paths)
    read_delivery.observe({"tool_response": {"stdout": output, "exit_code": 0}}, "Bash", {"command": command}, "test")
    records = memory.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert all(read_delivery.target_ready(records, ReadTarget(path), "main") is ready for path in paths)


@pytest.mark.usefixtures("memory")
def test_codex_notice_ranges_lead_to_completed_section_and_document_reads(tmp_path, monkeypatch):
    """Codexへ示した行範囲を分割して読むと、節と全文の取得が完了する。"""
    path = tmp_path / "reference.md"
    path.write_text("# 基準\n\n## 共通\n選択基準\n補足\n\n## 後段\n後段本文\n", encoding="utf-8")
    other = tmp_path / "full.md"
    other.write_text("# 全文\n一行目\n二行目\n", encoding="utf-8")
    rows = [ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Do",), (path, other), ("共通", ""))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    payload = {"turn_id": "turn"}
    notice = _notice_body(read_delivery.deliver(payload, "Do", {}, "test"))
    assert f"`sed -n '3,6p' -- {path}`" in notice
    assert f"`sed -n '1,3p' -- {other}`" in notice
    assert "atk read-file" not in notice
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for start, end in ((3, 4), (5, 6)):
        command = {"command": f"sed -n '{start},{end}p' -- {path}"}
        read_delivery.observe({**payload, "tool_response": "".join(lines[start - 1 : end])}, "Bash", command, "test")
    assert _guidance(read_delivery.deliver(payload, "Do", {}, "test"))
    read_delivery.observe(
        {**payload, "tool_response": other.read_text(encoding="utf-8")}, "Bash", {"command": f"sed -n '1,3p' {other}"}, "test"
    )
    assert not _guidance(read_delivery.deliver(payload, "Do", {}, "test"))


def test_claude_notice_shows_ranges_for_documents_over_the_budget(tmp_path, monkeypatch, memory):
    """配送本文に収まらない資料へ、行範囲とReadの範囲指定による取得方法を示す。"""
    del memory
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=2)
    monkeypatch.setattr(read_delivery, "CLAUDE_BODY_BUDGET", 150 + len(str(paths[0])) + len(str(tmp_path)))
    notice = _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
    assert f"`sed -n '1,3p' -- {paths[1]}`" in notice
    assert "`offset`" in notice and "`limit`" in notice


@pytest.mark.parametrize("failure", [{"exit_code": 1}, {"interrupted": True}])
def test_failed_read_does_not_mark_ready(tmp_path, monkeypatch, memory, failure):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    read_delivery.observe(
        {"tool_response": {"stdout": path.read_text(encoding="utf-8"), **failure}},
        "Bash",
        {"command": f"cat -- {path}"},
        "test",
    )
    assert not memory


@pytest.mark.usefixtures("memory")
def test_explicit_section_delivery_keeps_other_sections_out(tmp_path, monkeypatch):
    path = tmp_path / "reference.md"
    path.write_text("# 基準\n\n## 共通\n共通内容\n\n## 領域\n領域内容\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Do",), (path,), ("共通",))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    notice = _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
    assert "共通内容" in notice
    assert "領域内容" not in notice
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    assert ReadTarget(path, "領域").body() == "## 領域\n領域内容\n"


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


def test_unclassified_table_is_rejected_in_strict_mode(tmp_path):
    source = tmp_path / "SKILL.md"
    source.write_text("| 時点または条件 | 全文読む資料 |\n| --- | --- |\n| 検索 | `reference.md` |\n", encoding="utf-8")
    with pytest.raises(ValueError, match="契機と配送操作"):
        parse_tables(source, tmp_path, strict=True)


def test_selected_section_acquisition_can_complete_before_next_operation(tmp_path, monkeypatch, memory):
    path = tmp_path / "reference.md"
    path.write_text("# 基準\n\n## 共通\n選択基準\n\n## 後段\n後段本文\n", encoding="utf-8")
    target = ReadTarget(path, "共通")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("section:*reference.md#共通",), (path,), ("共通",))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    payload = {"turn_id": "turn"}
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for start, end in [(3, 3), (4, 5)]:
        tool_input = {"command": f"sed -n '{start},{end}p' -- {path}"}
        assert not _guidance(read_delivery.deliver(payload, "Bash", tool_input, "test"))
        response = {"stdout": "".join(lines[start - 1 : end]), "exit_code": 0}
        read_delivery.observe({**payload, "tool_response": response}, "Bash", tool_input, "test")
        if start == 3:
            assert not read_delivery.target_ready(memory[rules_context.OPERATION_SKILL_READY_KEY], target, "main")
    assert not _guidance(read_delivery.deliver(payload, "Do", {}, "test"))
    records = memory[rules_context.OPERATION_SKILL_READY_KEY]
    assert read_delivery.document_key(path) not in records
    assert read_delivery.target_ready(records, target, "main")


@pytest.mark.usefixtures("memory")
def test_modified_document_invalidates_previous_acquisition(tmp_path, monkeypatch):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    path.write_text("# 更新した基準\n新しい本文\n", encoding="utf-8")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.parametrize(
    ("script", "matched"),
    [("3,4p", True), ("1,4p", True), ("4p", True), ("1,2p", False), ("5,$p", False)],
)
def test_section_trigger_matches_ranges_overlapping_the_section_lines(tmp_path, script, matched):
    """節の宣言は、資料から算出した節の行と重なる行範囲の読取に一致する。"""
    document = tmp_path / "criteria.md"
    document.write_text("# 基準\n共通前提\n## 必要節\n必要本文\n## 別用途\n別用途本文\n", encoding="utf-8")
    section = "section:*/criteria.md#「必要節」"
    inputs = {"command": f"sed -n '{script}' -- {document}"}
    assert read_delivery.matches(section, "Bash", inputs, str(tmp_path)) is matched
    assert not read_delivery.matches(section, "Bash", {"command": f"cat -- {document}"}, str(tmp_path))
    full = "read:*/criteria.md"
    assert read_delivery.matches(full, "Bash", {"command": f"cat -- {document}"}, str(tmp_path))
    assert read_delivery.matches(full, "Read", {"file_path": str(document)}, str(tmp_path))


def test_document_review_keeps_test_material_acquisition_conditional():
    root = read_delivery.plugin_resources.plugin_root()
    owner = root / "share/exec-review.subagent.md"
    rows = parse_tables(owner, root, strict=True)
    writing = root.parent / "agent-basics/skills/writing-standards/SKILL.md"
    testing = writing.parent / "references/testing.md"
    assert not any(
        testing in row.documents and read_delivery.row_matches(row, "Read", {"file_path": str(writing)}, str(root.parent))
        for row in rows
    )
    assert any(
        testing in row.documents and read_delivery.row_matches(row, "Read", {"file_path": str(testing)}, str(root.parent))
        for row in rows
    )


@pytest.mark.parametrize("section_first", [False, True])
def test_section_and_full_read_in_one_call_keep_the_full_read(tmp_path, section_first):
    document = tmp_path / "criteria.md"
    document.write_text("# 基準\n## 必要節\n本文\n", encoding="utf-8")
    full = f"cat -- {document}"
    section = f"sed -n '2,3p' -- {document}"
    commands = [section, full] if section_first else [full, section]
    assert read_delivery.matches("read:*/criteria.md", "Bash", {"command": "; ".join(commands)}, str(tmp_path))


@pytest.mark.parametrize(
    ("tool", "inputs", "allowed"),
    [
        ("Read", {"file_path": "/source/criteria.md"}, True),
        ("Glob", {"pattern": "*.md"}, True),
        ("Grep", {"pattern": "read-table-source"}, True),
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
    assert "読込宣言を取得できない" in guidance
    assert result.notices


@pytest.mark.parametrize(
    ("case", "ready"),
    [
        ("read", True),
        ("bash", True),
        ("joined-parts", True),
        ("partial", False),
        ("failed", False),
        ("json-failed", False),
        ("truncated", False),
        ("sidechain", False),
        ("other-actor", False),
        ("own-child", True),
        ("unpaired", False),
        ("before-compaction", False),
        ("split-compaction", False),
        ("after-compaction", True),
        ("reset-without-boundary", False),
        ("changed-file", False),
        ("invalid-name", False),
    ],
)
@pytest.mark.usefixtures("memory")
def test_history_restore_preserves_content_actor_failure_and_context_boundaries(tmp_path, monkeypatch, memory, case, ready):
    target = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    body = target.read_text(encoding="utf-8")
    tool = "Bash" if case in {"bash", "json-failed"} else "Read"
    inputs = {"command": f"cat -- {target}"} if tool == "Bash" else {"file_path": str(target)}
    call_block: dict[str, object] = {"type": "tool_use", "id": "read", "name": tool, "input": inputs}
    call: dict = {"type": "assistant", "message": {"content": [call_block]}}
    result: dict = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "read", "content": body}]}}
    boundary = {"type": "system", "subtype": "compact_boundary"}
    entries: list[dict] = [call, result]
    payload = {"cwd": str(tmp_path), "turn_id": "turn"}
    if case == "failed":
        result["message"]["content"][0]["is_error"] = True
    elif case == "invalid-name":
        call_block["name"] = ["Read"]
    elif case == "json-failed":
        result["message"]["content"][0]["content"] = json.dumps({"exit_code": 1, "stdout": body})
    elif case == "truncated":
        result["message"]["content"][0]["content"] = body + "\nOutput too large"
    elif case in {"sidechain", "other-actor", "own-child"}:
        for entry in entries:
            entry.update({"isSidechain": True} if case == "sidechain" else {"agentId": "child"})
        if case == "own-child":
            payload["agent_id"] = "child"
    elif case == "unpaired":
        entries = [call]
    elif case == "before-compaction":
        entries.append(boundary)
    elif case == "after-compaction":
        entries.insert(0, boundary)
    elif case == "reset-without-boundary":
        memory[rules_context.OPERATION_SKILL_READY_KEY] = {rules_context.READ_CONTEXT_RESET_KEY: ["main"]}
    elif case == "changed-file":
        target.write_text(body + "変更後の基準\n", encoding="utf-8")
    elif case in {"partial", "joined-parts", "split-compaction"}:
        lines = body.splitlines(keepends=True)

        def part(identifier, start, count):
            text = "".join(lines[start - 1 : start - 1 + count])
            return [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "id": identifier, "name": "Read", "input": inputs}]},
                },
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "tool_use_id": identifier, "content": text}]},
                    "toolUseResult": {
                        "file": {"content": text, "startLine": start, "numLines": count, "totalLines": len(lines)}
                    },
                },
            ]

        entries = part("part1", 1, 2)
        if case == "split-compaction":
            entries.append(boundary)
        if case != "partial":
            entries.extend(part("part2", 3, len(lines) - 2))
    log = tmp_path / "history.jsonl"
    log.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    payload["transcript_path"] = str(log)
    decision = read_delivery.deliver(payload, "Do", {}, "test")
    assert (_guidance(decision) is None) is ready


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

    calls = []

    def plugin_list(argv, **_kwargs):
        calls.append(argv)
        if error == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        installed = [
            {"name": "agent-toolkit", "installed": True, "enabled": True, "marketplaceName": name} for name in marketplaces
        ]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"installed": installed}), stderr="")

    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    monkeypatch.setattr(read_delivery.process_tree, "run_with_timeout", plugin_list)
    result = read_delivery.deliver({}, "Bash", {"command": command}, "test")
    assert result.block is None
    assert result.notices
    reason = (
        ValueError("読込表の列数が一致しない")
        if error == "syntax"
        else PluginRootError("Codexのagent-basics導入先が一意でない: 0件")
    )
    assert _declaration_recovery_operation("Bash", {"command": command}, reason) is allowed
    assert all(argv == ["codex", "plugin", "list", "--json"] for argv in calls)
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


def _two_plugin_roots(tmp_path, monkeypatch):
    roots = [tmp_path / name / "agent-toolkit" for name in ("cache", "source")]
    for root in roots:
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(
            json.dumps({"name": "agent-toolkit", "version": "2.224.0"}), encoding="utf-8"
        )
        skill = root / "skills/chosen/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "# 選択したskill\n"
            "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n"
            "| --- | --- | --- | --- |\n"
            "| 初動 | `initial.md` | 起動時 | `skill:chosen` |\n"
            "| 次の操作 | `later.md` | 操作 | `tool:Do` |\n",
            encoding="utf-8",
        )
        (skill.parent / "initial.md").write_text("# 初動\n判断前の本文\n", encoding="utf-8")
        (skill.parent / "later.md").write_text("# 詳細\n操作前の本文\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", roots[0])
    monkeypatch.setattr(read_delivery, "sibling_plugin_root", lambda *_args: None)
    return roots


def test_reference_to_another_root_does_not_activate_its_whole_skill_tree(tmp_path, monkeypatch):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    project = tmp_path / ".claude/skills/local/SKILL.md"
    project.parent.mkdir(parents=True)
    selected = foreign / "skills/chosen/SKILL.md"
    project.write_text(
        "| 条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        f"| 選択時 | `{selected}` | 判断 | `read:*/chosen/SKILL.md` |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(read_delivery.read_triggers, "document_sources", lambda *_args: [project])
    rows = read_delivery.declarations(str(tmp_path), host="codex")
    assert [row.source for row in rows] == [project]
    assert rows[0].documents == (selected,)
    assert all(not row.source.is_relative_to(foreign) for row in rows)
    assert active != foreign


@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("agy", [False, True])
def test_same_named_plugin_read_uses_the_selected_physical_root(tmp_path, monkeypatch, shell, agy):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    source = foreign / "skills/chosen/SKILL.md"
    row = parse_tables(source, active)[0]
    path = active / "skills/chosen/SKILL.md"
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
    skill = foreign / "skills/chosen/SKILL.md"
    initial, later = skill.parent / "initial.md", skill.parent / "later.md"
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    inputs = {"file_path": str(skill)}
    first = read_delivery.deliver(payload, "Read", inputs, "test")
    assert str(initial) in _notice_body(first)
    read_delivery.observe({**payload, "tool_response": initial.read_text()}, "Read", {"file_path": str(initial)}, "test")
    assert _guidance(read_delivery.deliver(payload, "Read", inputs, "test")) is None
    read_delivery.observe({**payload, "tool_response": skill.read_text()}, "Read", inputs, "test")
    selected = read_delivery.deliver(payload, "Do", {}, "test")
    assert str(later) in _notice_body(selected)
    assert str(active) not in _notice_body(selected)


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.usefixtures("memory")
def test_packaged_skill_read_delivers_startup_material_before_its_own_pending_read(tmp_path, monkeypatch, codex, shell):
    root = tmp_path / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-toolkit", "version": "2.224.0"}), encoding="utf-8"
    )
    skill = root / "skills/chosen/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 選択したskill\n", encoding="utf-8")
    initial = skill.parent / "initial.md"
    initial.write_text("# 初動\n判断前の本文\n", encoding="utf-8")
    rows = [
        ReadTrigger(skill, 1, "起動時", ("skill:chosen",), (initial,)),
        ReadTrigger(skill, 2, "判断", ("read:*/skills/chosen/SKILL.md",), (skill,), selector="same-document"),
    ]
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    tool, inputs = ("Bash", {"command": f"cat -- {skill}"}) if shell else ("Read", {"file_path": str(skill)})

    first = read_delivery.deliver(payload, tool, inputs, "test")
    assert str(initial) in _notice_body(first)
    read_delivery.observe({**payload, "tool_response": initial.read_text()}, "Read", {"file_path": str(initial)}, "test")
    assert _guidance(read_delivery.deliver(payload, tool, inputs, "test")) is None


@pytest.mark.parametrize("case", ["full", "section", "actor", "changed", "data"])
def test_only_current_full_actor_proof_activates_plugin_declarations(tmp_path, monkeypatch, case):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    path = foreign / "skills/chosen/SKILL.md"
    if case == "data":
        path = tmp_path / "task-input.md"
        path.write_text((foreign / "skills/chosen/SKILL.md").read_text(), encoding="utf-8")
    key = read_delivery.document_key(path)
    if case == "section":
        key += "#heading:選択したskill"
    records = {key: ["other" if case == "actor" else "main"]}
    if case == "changed":
        path.write_text(path.read_text() + "変更後\n", encoding="utf-8")
    rows = _project_skill_rows(records, "main")
    assert bool(rows) is (case == "full")
    assert active != foreign


@pytest.mark.parametrize("case", ["duplicate", "version", "changed", "actor", "explicit", "modified", "startup"])
def test_obsolete_duplicate_selection_does_not_impersonate_an_explicit_source_read(tmp_path, monkeypatch, memory, case):
    active, foreign = _two_plugin_roots(tmp_path, monkeypatch)
    counterpart = ReadTarget(active / "skills/chosen/SKILL.md")
    old = ReadTarget(foreign / "skills/chosen/SKILL.md")
    _mark("test", counterpart.path, "other" if case == "actor" else "main")
    records = memory[rules_context.OPERATION_SKILL_READY_KEY]
    if case == "version":
        (foreign / ".claude-plugin/plugin.json").write_text(
            json.dumps({"name": "agent-toolkit", "version": "2.225.0"}), encoding="utf-8"
        )
    elif case == "changed":
        old.path.write_text(old.path.read_text() + "別の本文\n", encoding="utf-8")
    elif case in {"explicit", "modified"}:
        prefix = "resolved-project-skill:" if case == "explicit" else "modified-agent-document:"
        records[prefix + str(old.path)] = ["main"]
    elif case == "startup":
        records["startup-input-target:" + json.dumps({"path": str(old.path)})] = ["main"]
    assert _redundant_selection(old, {counterpart}, records, "main") is (case == "duplicate")
    assert read_delivery.document_key(old.path) not in records


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
    calls = []

    def plugin_list(argv, **_kwargs):
        calls.append(argv)
        installed = [{"name": "agent-toolkit", "installed": True, "enabled": True, "marketplaceName": "fixture-market"}]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"installed": installed}), stderr="")

    monkeypatch.setattr(read_delivery.process_tree, "run_with_timeout", plugin_list)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    root = tmp_path / "toolkit"

    def unresolved(*_args, **_kwargs):
        return read_delivery.read_triggers.resolve_plugin_root("agent-basics", root, "codex")

    with pytest.raises(PluginRootError) as observed:
        unresolved()
    assert isinstance(observed.value, ValueError)
    assert str(observed.value) == "codexのagent-basics導入先が一意でない: 0件"
    monkeypatch.setattr(read_delivery, "declarations", unresolved)
    tool, inputs = "Grep", {"pattern": "x"}
    if operation == "install":
        tool, inputs = "Bash", {"command": "codex plugin add agent-basics@fixture-market --json"}
    result = read_delivery.deliver({}, tool, inputs, "test")
    assert result.block is None and result.notices
    assert calls and all(argv == ["codex", "plugin", "list", "--json"] for argv in calls)
    assert memory == {}
