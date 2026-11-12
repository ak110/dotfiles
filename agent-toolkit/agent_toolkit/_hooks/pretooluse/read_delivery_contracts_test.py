"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json

import pytest

from agent_toolkit._agents_server import launch_requests
from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger, parse_tables
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.read_delivery import (
    _mark,
)
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.read_delivery_support import (
    _assert_acquired,
    _canonical_method,
    _guidance,
    _local_plugin_root,
    _memory,
    _notice_body,
    _rows,
)

__all__ = ["_assert_acquired", "_canonical_method", "_guidance", "_local_plugin_root", "_memory", "_notice_body", "_rows"]


@pytest.mark.parametrize("codex", [False, True])
def test_common_method_is_delivered_for_direct_and_pending_reads_without_starting_the_skill(
    tmp_path, monkeypatch, memory, codex
):
    root = tmp_path / "plugin"
    skill = root / "skills/example/SKILL.md"
    rule = skill.parent / "references/rule.md"
    rule.parent.mkdir(parents=True)
    method = _canonical_method(root)
    skill.write_text("# 技能\n## 別用途\n無関係な技能本文\n", encoding="utf-8")
    rule.write_text(
        "# 個別資料\n## 名指し参照の読込方法\n名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。\n",
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
    assert not _guidance(read_delivery.deliver(payload, "Read", {"file_path": str(rule)}, "test"))
    first = _notice_body(read_delivery.deliver(payload, "Judge", {}, "test"))
    assert str(method) in first
    assert str(skill) not in first
    assert str(extra) not in first
    assert "無関係な技能本文" not in first
    assert "共通取得順" not in first
    _mark("test", method, "main")
    assert not _guidance(read_delivery.deliver(payload, "Read", {"file_path": str(rule)}, "test"))
    method_read = {"command": f"cat -- {method}"}
    assert not _guidance(read_delivery.deliver(payload, "Bash", method_read, "test"))
    memory.clear()
    pending = _notice_body(read_delivery.deliver(payload, "Judge", {}, "test"))
    assert str(method) in pending
    assert str(skill) not in pending
    assert str(extra) not in pending
    assert "無関係な技能本文" not in pending


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_pending_documents_preserve_the_own_handoff_exemption(tmp_path, monkeypatch, codex):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    handoff = tmp_path / "own-handoff.md"
    monkeypatch.setattr(read_delivery.delegated_session, "handoff_path", lambda _env: str(handoff))
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    first = read_delivery.deliver(payload, "Do", {}, "test")
    assert _guidance(first)
    notice_module.record_delivery("\n".join(first.notices))
    assert not _guidance(read_delivery.deliver(payload, "Write", {"file_path": str(handoff), "content": "到達点"}, "test"))
    other = tmp_path / "draft.md"
    patch = f"*** Begin Patch\n*** Add File: {handoff}\n+到達点\n*** Add File: {other}\n+原稿\n*** End Patch"
    mixed = read_delivery.deliver(payload, "apply_patch", {"command": patch}, "test")
    assert not _guidance(mixed)
    assert not _guidance(read_delivery.deliver(payload, "apply_patch", {"command": patch}, "test"))
    _assert_acquired(payload, path, False)
    assert not _guidance(read_delivery.deliver(payload, "Write", {"file_path": str(handoff), "content": "到達点"}, "test"))
    observe_received({**payload, "tool_response": path.read_text()}, "Read", {"file_path": str(path)}, "test")
    assert not _guidance(read_delivery.deliver(payload, "apply_patch", {"command": patch}, "test"))


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
    observe_received({**payload, "tool_response": contract.read_text()}, "Read", {"file_path": str(contract)}, "test")
    assert not _guidance(read_delivery.deliver(payload, "Judge", {}, "test"))
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    rules_context.reset_main_operation_skill_ready("test")
    contract.write_text("# 現行作業契約\n追加後の完成条件\n", encoding="utf-8")
    later = read_delivery.deliver(payload, "Judge", {}, "test")
    assert str(contract) in _notice_body(later)
    assert "追加後の完成条件" not in _notice_body(later)


def test_main_resolves_work_contract_without_a_startup_input(tmp_path, monkeypatch):
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 現行作業契約\n完成条件\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "起動時", ("skill:report-completion",), (), selector="input:作業契約")
    monkeypatch.setattr(read_delivery.work_contract, "record_path", lambda _session: contract)
    assert read_delivery.dynamic_targets(row, {}, "Skill", {}, {}, "main", "session") == [ReadTarget(contract)]


def test_main_missing_work_contract_explains_its_expected_path(tmp_path, monkeypatch):
    contract = tmp_path / "work-contract.md"
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "起動時", ("skill:report-completion",), (), selector="input:作業契約")
    monkeypatch.setattr(read_delivery.work_contract, "record_path", lambda _session: contract)
    with pytest.raises(ValueError, match="work-contract.md.*作成と所有"):
        read_delivery.dynamic_targets(row, {}, "Skill", {}, {}, "main", "session")


@pytest.mark.usefixtures("memory")
def test_public_hook_delivers_main_work_contract_and_reuses_it_after_read(tmp_path, monkeypatch):
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 現行作業契約\n完成条件\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "起動時", ("skill:report-completion",), (), selector="input:作業契約")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    monkeypatch.setattr(read_delivery.work_contract, "record_path", lambda _session: contract)
    assert not _guidance(read_delivery.deliver({}, "Skill", {"skill": "report-completion"}, "test"))
    first = _notice_body(read_delivery.deliver({}, "Inspect", {}, "test"))
    assert str(contract) in first
    observe_received({"tool_response": contract.read_text()}, "Read", {"file_path": str(contract)}, "test")
    assert not _guidance(read_delivery.deliver({}, "Skill", {"skill": "report-completion"}, "test"))


@pytest.mark.usefixtures("memory")
def test_public_hook_explains_missing_main_work_contract(tmp_path, monkeypatch):
    contract = tmp_path / "work-contract.md"
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "起動時", ("skill:report-completion",), (), selector="input:作業契約")
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    monkeypatch.setattr(read_delivery.work_contract, "record_path", lambda _session: contract)
    assert str(contract) in _notice_body(read_delivery.deliver({}, "Skill", {"skill": "report-completion"}, "test"))


@pytest.mark.usefixtures("memory")
def test_public_hook_keeps_delegated_work_contract_input_preferred(tmp_path, monkeypatch, memory):
    delegated = tmp_path / "delegated-contract.md"
    delegated.write_text("# 委譲契約\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "起動時", ("skill:report-completion",), (), selector="input:作業契約")
    memory[rules_context.OPERATION_SKILL_READY_KEY] = {
        "startup-input-target:" + json.dumps({"path": str(delegated), "heading": "", "selectors": ["input:作業契約"]}): [
            "child"
        ]
    }
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    monkeypatch.setattr(read_delivery.work_contract, "record_path", lambda _session: (_ for _ in ()).throw(AssertionError()))
    payload = {"agent_id": "child"}
    assert not _guidance(read_delivery.deliver(payload, "Skill", {"skill": "report-completion"}, "test"))
    notice = _notice_body(read_delivery.deliver(payload, "Inspect", {}, "test"))
    assert str(delegated) in notice


@pytest.mark.usefixtures("memory")
def test_public_hook_artifact_standards_uses_code_operation_only(tmp_path, monkeypatch, memory):
    source = tmp_path / "agent-basics/skills/review-standards/SKILL.md"
    author = tmp_path / "agent-basics/skills/writing-standards/SKILL.md"
    reference = tmp_path / "python.md"
    reference.write_text("# 作成規範\n", encoding="utf-8")
    author.parent.mkdir(parents=True)
    author.write_text("# 作成側\n", encoding="utf-8")
    review = ReadTrigger(source, 1, "判断", ("tool:Write",), (), selector="artifact-standards")
    declared = ReadTrigger(author, 1, "操作", ("dependency:sqlalchemy",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [review, declared])
    monkeypatch.setattr(read_delivery.read_triggers, "resolve_document", lambda *_args, **_kwargs: author)
    markdown = _notice_body(
        read_delivery.deliver(
            {"cwd": str(tmp_path)}, "Write", {"file_path": str(tmp_path / "note.md"), "content": "sqlalchemy"}, "test"
        )
    )
    assert str(author) in markdown
    assert str(reference) not in markdown
    memory.clear()
    assert str(reference) in _notice_body(
        read_delivery.deliver(
            {"cwd": str(tmp_path)},
            "Write",
            {"file_path": str(tmp_path / "module.py"), "content": "import sqlalchemy\n"},
            "test",
        )
    )
    memory.clear()
    attribute = _notice_body(
        read_delivery.deliver(
            {"cwd": str(tmp_path)},
            "Write",
            {"file_path": str(tmp_path / "page.html"), "content": "<div title=\"require('sqlalchemy')\"></div>\n"},
            "test",
        )
    )
    assert str(author) in attribute
    assert str(reference) not in attribute


@pytest.mark.parametrize(
    "role",
    [
        "bulk-replace-review",
        "copilot-review-audit",
        "defect-investigation",
        "external-write-review",
        "lane-integration",
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
    assert all(target.body().strip() not in _notice_body(decision) for target in targets)
    assert _guidance(read_delivery.deliver(payload, "Launch", {"subagent_md_path": role}, "test"))


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("codex", [False, True])
def test_startup_input_target_survives_compaction(tmp_path, monkeypatch, memory, duplicate, codex):
    root = tmp_path / "plugin"
    share = root / "share"
    share.mkdir(parents=True)
    method = _canonical_method(root)
    source = share / "example.subagent.md"
    source.write_text(
        "# 役割\n## 名指し参照の読込方法\n"
        "名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。\n\n"
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
    assert context.count("共通取得順") == 1
    assert context.count("後段の必須基準") == 1
    log = tmp_path / "transcript.jsonl"
    text = launch_requests.wrap_delivery_body(read_delivery.task_documents.task_prompt_header(source) + "\n" + context)
    entry = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    if codex:
        entry = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": text},
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
    assert "起動入力の本文" not in restored
    assert str(original) in restored
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
