"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger, parse_tables
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _PERSISTED_REPLACEMENT,
    _assert_acquired,
    _guidance,
    _local_plugin_root,
    _memory,
    _notice_body,
    _rows,
)

__all__ = ["_PERSISTED_REPLACEMENT", "_assert_acquired", "_guidance", "_local_plugin_root", "_memory", "_notice_body", "_rows"]


@pytest.mark.parametrize("kind", ["操作", "判断"])
@pytest.mark.parametrize("subject", ["main", "task", "delegate", "agent", "codex", "lightweight"])
def test_guidance_requires_real_acquisition_for_each_subject(tmp_path, monkeypatch, memory, kind, subject):
    root = tmp_path / "plugin"
    manifest = root / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"example-plugin"}', encoding="utf-8")
    skill = root / "skills/example/SKILL.md"
    reference = skill.parent / "references/rule.md"
    reference.parent.mkdir(parents=True)
    skill.write_text("# スキル固有本文\n", encoding="utf-8")
    reference.write_text("# 資料\n参照資料固有本文\n", encoding="utf-8")
    rows = [ReadTrigger(skill, 1, kind, ("tool:Do",), (skill, reference))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    monkeypatch.setattr(read_delivery.read_triggers, "reference_method_targets", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: subject == "lightweight")
    if subject in {"task", "delegate"}:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND", subject)
    payload = {"cwd": str(tmp_path)}
    if subject == "agent":
        payload["agent_id"] = "child"
    if subject == "codex":
        payload["turn_id"] = "turn"
    agent = "child" if subject == "agent" else "main"
    decision = read_delivery.deliver(payload, "Do", {}, "test")
    if subject == "lightweight":
        assert not _guidance(decision)
        assert not memory
        return
    notice = _notice_body(decision)
    assert str(reference) in notice
    assert (str(skill) in notice) is (subject in {"codex", "agent"})
    assert "スキル固有本文" not in notice and "参照資料固有本文" not in notice
    assert ("example-plugin:example" in notice) is (subject not in {"codex", "agent"})
    records = memory[rules_context.OPERATION_SKILL_READY_KEY]
    assert not read_delivery.target_ready(records, ReadTarget(skill), agent)
    assert not read_delivery.target_ready(records, ReadTarget(reference), agent)
    observe_received({**payload, "tool_response": reference.read_text()}, "Read", {"file_path": str(reference)}, "test")
    observe_received({**payload, "tool_response": skill.read_text()}, "Read", {"file_path": str(skill)}, "test")
    observe_received(
        {**payload, "tool_response": {"stdout": skill.read_text(), "exit_code": 0}},
        "Bash",
        {"command": f"cat -- {skill}"},
        "test",
    )
    records = memory[rules_context.OPERATION_SKILL_READY_KEY]
    if subject in {"main", "task", "delegate"}:
        assert not read_delivery.target_ready(records, ReadTarget(skill), agent, invocation=True)
        observe_received(
            {**payload, "tool_response": _PERSISTED_REPLACEMENT}, "Skill", {"skill": "example-plugin:example"}, "test"
        )
        assert read_delivery.target_ready(
            memory[rules_context.OPERATION_SKILL_READY_KEY], ReadTarget(skill), agent, invocation=True
        )
        observe_received({**payload, "tool_response": "起動成功"}, "Skill", {"skill": "example-plugin:example"}, "test")
        records = memory[rules_context.OPERATION_SKILL_READY_KEY]
    assert read_delivery.target_ready(records, ReadTarget(skill), agent)
    assert read_delivery.target_ready(records, ReadTarget(reference), agent)
    assert not _guidance(read_delivery.deliver(payload, "Do", {}, "test"))


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
    assert "必須基準0" not in _notice_body(decision)
    # 実配送した案内は未取得のままでも再掲せず、取得証拠は個別に判定する。
    notice_module.record_delivery("\n".join(decision.notices))
    assert not _guidance(read_delivery.deliver(payload, "Write", operation, "test"))
    for path in paths:
        _assert_acquired(payload, path, False)
    for path in paths:
        observe_received({**payload, "tool_response": "# 資料0\n"}, "Read", {"file_path": str(path), "limit": 1}, "test")
    assert not _guidance(read_delivery.deliver(payload, "Write", operation, "test"))
    for path in paths:
        _assert_acquired(payload, path, False)
    for path in paths:
        tool = "Bash" if codex else "Read"
        inputs = {"command": f"cat -- {path}"} if codex else {"file_path": str(path)}
        observe_received(
            {**payload, "tool_response": {"stdout": path.read_text(encoding="utf-8"), "exit_code": 0}},
            tool,
            inputs,
            "test",
        )
    assert not _guidance(read_delivery.deliver(payload, "Write", operation, "test"))
    for path in paths:
        _assert_acquired(payload, path, True)


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.usefixtures("memory")
def test_guidance_gives_mandatory_host_steps_and_edit_review(tmp_path, monkeypatch, codex, write):
    paths = _rows(tmp_path, monkeypatch, "tool:Write" if write else "tool:Do", count=2)
    payload = {"cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    tool = "Write" if write else "Do"
    inputs = {"file_path": str(tmp_path / "report.md"), "content": "## 原因分析\n"} if write else {}
    decision = read_delivery.deliver(payload, tool, inputs, "test")
    notice = _notice_body(decision)
    instruction = notice.splitlines()[1]
    assert "この操作の結果を使う前に" in instruction and "必要な基準を取得" in instruction
    assert ("シェルコマンド" in instruction) is codex
    assert ("Read" in instruction) is (not codex)
    if not codex:
        assert all(notice.count(str(path)) == 1 for path in paths)
    assert ("読んだ後に、書いた内容をその基準で見直し、必要なら直" in notice) is write
    assert "必須基準0" not in notice and decision.block is None
    if codex:
        prefix = "const result = await tools.exec_command("
        requests = [json.loads(line[len(prefix) : -2]) for line in notice.splitlines() if line.startswith(prefix)]
        assert [request["cmd"] for request in requests] == [f"cat -- {path}" for path in paths]
        assert all(request["max_output_tokens"] > 0 for request in requests)
        assert notice.count("内外の出力枠") == 1
    for path in paths:
        _assert_acquired(payload, path, False)


def test_confirmation_denial_has_all_paths_until_read(tmp_path, monkeypatch, memory):
    paths = _rows(tmp_path, monkeypatch, "confirmation", count=30)
    for _ in range(2):
        decision = read_delivery.deliver({}, "AskUserQuestion", {}, "test")
        assert decision.block is not None
        assert all(str(path) in decision.block for path in paths)
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
    first = read_delivery.deliver({}, "Do", {}, "test")
    assert str(path) in _notice_body(first)
    assert "必須基準0" not in _notice_body(first)
    observe_received({"tool_response": path.read_text()}, "Read", {"file_path": str(path)}, "test")
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    path.write_text("# 変更後\n新しい基準\n", encoding="utf-8")
    second = read_delivery.deliver({}, "Do", {}, "test")
    assert str(path) in _notice_body(second)
    assert "新しい基準" not in _notice_body(second)


@pytest.mark.usefixtures("memory")
def test_bounded_delivery_retries_in_declaration_order(tmp_path, monkeypatch):
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=3)
    for index, path in enumerate(paths):
        notice = _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
        assert all(str(p) in notice for p in paths[index:])
        assert all(f"必須基準{i}" not in notice for i in range(3))
        observe_received({"tool_response": path.read_text()}, "Read", {"file_path": str(path)}, "test")
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))


def test_agent_scope_and_compaction(tmp_path, monkeypatch, memory):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    assert _guidance(read_delivery.deliver({"agent_id": "child"}, "Do", {}, "test"))
    observe_received({"agent_id": "child", "tool_response": path.read_text()}, "Read", {"file_path": str(path)}, "test")
    monkeypatch.setattr(rules_context, "update_state", lambda _session, mutate: mutate(memory))
    rules_context.reset_main_operation_skill_ready("test")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    assert not _guidance(read_delivery.deliver({"agent_id": "child"}, "Do", {}, "test"))


@pytest.mark.parametrize("subject", ["main", "agent", "codex"])
def test_bash_reference_acquisition_is_recorded_for_each_actor(tmp_path, monkeypatch, memory, subject):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    payload: dict = {"tool_response": {"stdout": path.read_text(), "exit_code": 0}}
    if subject == "agent":
        payload["agent_id"] = "child"
    if subject == "codex":
        payload["turn_id"] = "turn"
    observe_received(payload, "Bash", {"command": f"cat -- {path}"}, "test")
    agent = "child" if subject == "agent" else "main"
    assert read_delivery.target_ready(memory.get(rules_context.OPERATION_SKILL_READY_KEY, {}), ReadTarget(path), agent)
    other = "main" if agent == "child" else "child"
    assert not read_delivery.target_ready(memory.get(rules_context.OPERATION_SKILL_READY_KEY, {}), ReadTarget(path), other)


@pytest.mark.usefixtures("memory")
def test_codex_notice_guides_full_document_reads(tmp_path, monkeypatch):
    path = tmp_path / "reference.md"
    path.write_text("# 基準\n## 共通\n選択基準\n## 後段\n後段本文\n", encoding="utf-8")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Do",), (path,), ("共通",))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    payload = {"turn_id": "turn"}
    notice = _notice_body(read_delivery.deliver(payload, "Do", {}, "test"))
    assert f"sed -n '2,3p' -- {path}" in notice and "共通" in notice
    observe_received({**payload, "tool_response": path.read_text()}, "Bash", {"command": f"cat -- {path}"}, "test")
    assert not _guidance(read_delivery.deliver(payload, "Do", {}, "test"))


def test_claude_notice_shows_ranges_for_documents_over_the_budget(tmp_path, monkeypatch, memory):
    """全資料の行範囲をReadの取得方法で示し、本文を含めない。"""
    del memory
    paths = _rows(tmp_path, monkeypatch, "tool:Do", count=2)
    notice = _notice_body(read_delivery.deliver({}, "Do", {}, "test"))
    assert all(str(path) in notice for path in paths)
    assert "Read" in notice and "取得" in notice
    assert "必須基準0" not in notice and "必須基準1" not in notice


@pytest.mark.parametrize("failure", [{"exit_code": 1}, {"interrupted": True}])
def test_failed_read_does_not_mark_ready(tmp_path, monkeypatch, memory, failure):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    observe_received(
        {"tool_response": {"stdout": "読取失敗", **failure}},
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
    assert str(path) in notice and "取得" in notice and "共通" in notice
    assert "共通内容" not in notice
    assert "領域内容" not in notice
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    observe_received({"tool_response": path.read_text()}, "Read", {"file_path": str(path)}, "test")
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    assert ReadTarget(path, "領域").body() == "## 領域\n領域内容\n"


def test_selected_section_success_completes_acquisition(tmp_path, monkeypatch, memory):
    path = tmp_path / "reference.md"
    path.write_text("# 基準\n## 共通\n選択基準\n## 後段\n後段本文\n", encoding="utf-8")
    target = ReadTarget(path, "共通")
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("read:*reference.md",), (path,), ("共通",))
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: [row])
    observe_received(
        {"turn_id": "turn", "tool_response": "## 共通\n選択基準\n"}, "Bash", {"command": f"sed -n '2,3p' -- {path}"}, "test"
    )
    assert read_delivery.target_ready(memory[rules_context.OPERATION_SKILL_READY_KEY], target, "main")


@pytest.mark.usefixtures("memory")
def test_modified_document_invalidates_previous_acquisition(tmp_path, monkeypatch):
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    observe_received({"tool_response": path.read_text()}, "Read", {"file_path": str(path)}, "test")
    assert not _guidance(read_delivery.deliver({}, "Do", {}, "test"))
    path.write_text("# 更新した基準\n新しい本文\n", encoding="utf-8")
    assert _guidance(read_delivery.deliver({}, "Do", {}, "test"))


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.usefixtures("memory")
def test_public_updated_receipt_notifies_only_the_current_related_operation(tmp_path, monkeypatch, codex):
    """過去に選択・取得した資料の更新は、無関係な操作へ持ち越さず関連操作で案内する。"""
    path = _rows(tmp_path, monkeypatch, "tool:Do", count=1)[0]
    payload = {"session_id": "test", "cwd": str(tmp_path), **({"turn_id": "turn"} if codex else {})}
    related = {**payload, "tool_name": "Do", "tool_input": {}}
    first = _run_hook_in_process("pretooluse", related)
    assert first.returncode == 0 and str(path) in first.stdout and not first.stderr
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(path)},
            "tool_response": path.read_text(encoding="utf-8"),
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    _assert_acquired(payload, path, True)
    path.write_text("# 更新した資料\n現在の基準\n", encoding="utf-8")
    for _ in range(2):
        unrelated = _run_hook_in_process(
            "pretooluse", {**payload, "tool_name": "Bash", "tool_input": {"command": "git status"}}
        )
        assert unrelated.returncode == 0 and not unrelated.stdout and not unrelated.stderr
    _assert_acquired(payload, path, False)
    current = _run_hook_in_process("pretooluse", related)
    assert current.returncode == 0 and not current.stderr
    assert str(path) in current.stdout and "取得済み資料の更新あり" in current.stdout
    assert "現在の基準" not in current.stdout
    repeated = _run_hook_in_process("pretooluse", related)
    assert repeated.returncode == 0 and not repeated.stdout and not repeated.stderr
    _assert_acquired(payload, path, False)


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


@pytest.mark.usefixtures("memory")
def test_sections_of_the_same_document_share_one_acquisition_step(tmp_path, monkeypatch):
    """同じ資料の複数の節は、1件の全文取得の案内へまとめて注目する節を併記する。"""
    document = tmp_path / "guide.md"
    document.write_text("# 手引き\n\n## 前提\n\n前提の本文\n\n## 手順\n\n手順の本文\n", encoding="utf-8")
    rows = [ReadTrigger(tmp_path / "SKILL.md", 1, "操作", ("tool:Do",), (document, document), ("前提", "手順"))]
    monkeypatch.setattr(read_delivery, "declarations", lambda _cwd, **_kwargs: rows)
    body = _notice_body(read_delivery.deliver({"cwd": str(tmp_path)}, "Do", {}, "test"))
    assert body.count(f"Readで`{document}`を取得せよ") == 1
    assert "前提" in body and "手順" in body


def test_old_section_ready_is_not_full_document_ready(tmp_path):
    path = tmp_path / "reference.md"
    path.write_text("# 基準\n## 共通\n本文\n## 後段\n続き\n", encoding="utf-8")
    target = ReadTarget(path, "共通")
    key = read_delivery.document_key(path)
    assert not read_delivery.target_ready({key + "#heading:共通": ["main"]}, target, "main")
    assert read_delivery.target_ready({key: ["main"]}, target, "main")


def test_single_lane_real_declarations_reach_each_phase_and_preserve_actor_boundary(monkeypatch, memory):
    root = pathlib.Path(__file__).resolve().parents[3]
    sources = (root / "skills/process-wi/references/single-lane.md", root / "share/single-lane-execution.md")
    rows = [row for source in sources for row in parse_tables(source, root, strict=True)]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(read_delivery, "_project_skill_rows", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(read_delivery.read_triggers, "reference_method_targets", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", root)
    payload = {"cwd": str(root), "turn_id": "turn"}
    phases = (
        ("選定", root / "skills/process-wi/references/selection-procedure.md", ""),
        ("計画", root / "skills/write-plan/SKILL.md", ""),
        ("共通工程", root / "share/single-lane-execution.md", ""),
        ("確認", root / "skills/user-confirmation-and-report/SKILL.md", ""),
        ("実装と実行レビュー", root / "share/exec-review.parent.md", ""),
        ("要件検収", root / "share/verification-evidence.md", ""),
        ("レビュー収束", root / "share/review-loop-coordination-rounds.md", ""),
        ("公開前検証", root / "skills/commit/references/publish.md", ""),
        ("終了", root / "skills/report-completion/SKILL.md", ""),
    )
    for phase, path, heading in phases:
        target = ReadTarget(path, heading)
        inputs = {"file_path": str(path)}
        assert any(read_delivery.row_matches(row, "Read", inputs, str(root)) and target in row.targets for row in rows), phase
        decision = read_delivery.deliver(payload, "Read", inputs, "test")
        records = memory.get(rules_context.OPERATION_SKILL_READY_KEY, {})
        confirmed = any(
            row.kind != "判断" and target in row.targets and read_delivery.row_matches(row, "Read", inputs, str(root))
            for row in rows
        )
        assert (target in read_delivery.selected_targets(records, "main")) is confirmed, phase
        assert not decision.notices, phase
        assert not read_delivery.target_ready(records, target, "main"), phase
        body = path.read_text(encoding="utf-8")
        for expected in {
            "選定": ("処理対象の決定",),
            "計画": ("計画ファイルの作成要否",),
            "共通工程": ("実行レビュー", "採否"),
            "確認": ("確認要否の判定",),
            "実装と実行レビュー": ("検証結果",),
            "要件検収": ("完成条件証拠の検証記録",),
            "レビュー収束": ("ラウンドの遷移", "応答済みの判定"),
            "公開前検証": ("公開工程", "CI"),
            "終了": ("終了処理",),
        }[phase]:
            assert expected in target.body(body), phase
        if phase == "要件検収":
            observe_received({**payload, "tool_response": body.splitlines()[0]}, "Read", {**inputs, "limit": 1}, "test")
            assert not read_delivery.target_ready(memory[rules_context.OPERATION_SKILL_READY_KEY], target, "main")
            observe_received({**payload, "agent_id": "child", "tool_response": body}, "Read", inputs, "test")
            assert not read_delivery.target_ready(memory[rules_context.OPERATION_SKILL_READY_KEY], target, "main")
        observe_received({**payload, "tool_response": body}, "Read", inputs, "test")
        records = memory[rules_context.OPERATION_SKILL_READY_KEY]
        assert read_delivery.target_ready(records, target, "main"), phase
        assert str(path) not in (_guidance(read_delivery.deliver(payload, "Read", inputs, "test")) or ""), phase
        if phase == "要件検収":
            assert not read_delivery.target_ready(records, target, "independent")
    # 起動・受領・修正で使う規範資料の全文へ、対応する条件から到達する。
    for name, condition in (
        ("exec-review-parent-launch.md", "起動する前"),
        ("exec-review-parent-acceptance.md", "返却を受領する前"),
        ("exec-review-parent-existing-defects.md", "既存不良を受領する前"),
        ("exec-review-parent-correction.md", "修正と管理表の保存"),
    ):
        target = ReadTarget(root / "share" / name)
        assert any(target in row.targets and condition in row.condition for row in rows), name


@pytest.mark.parametrize("context", [{}, {"agent_id": "child"}, {"turn_id": "turn"}])
def test_public_missing_selected_document_guides_repair_without_blocking(tmp_path, monkeypatch, memory, context):
    source = tmp_path / "SKILL.md"
    source.write_text("# 技能\n", encoding="utf-8")
    missing = tmp_path / "missing.md"
    row = ReadTrigger(source, 1, "操作", ("tool:Write",), (missing,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(read_delivery, "_project_skill_rows", lambda *_args, **_kwargs: [])
    payload = {
        "session_id": "missing-selected",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "output.md"), "content": "内容"},
        **context,
    }
    result = _run_hook_in_process("pretooluse", payload)
    assert result.returncode == 0 and not result.stderr
    assert str(missing) in result.stdout and "存在しない" in result.stdout and "参照先を直せ" in result.stdout
    # 出力にはユーザー画面向けの写しも入るため、エージェント宛ての本文で数える。
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context.count("存在しない") == 1
    assert "委譲元" in result.stdout and "ユーザー" in result.stdout
    assert "permissionDecision" not in result.stdout
    assert not memory.get(rules_context.OPERATION_SKILL_READY_KEY, {})
