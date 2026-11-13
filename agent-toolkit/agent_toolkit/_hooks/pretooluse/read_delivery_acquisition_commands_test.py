"""役割の起動配送と、主体別の取得手段をまたぐ読込契約を確かめる。"""

import json
import shlex
import subprocess

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._hooks.pretooluse.read_delivery import _merged_steps
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_acquisition_support import (
    _assert_continues_without_guidance,
    _assert_no_read_guidance,
    _state,
)

__all__ = ["_assert_continues_without_guidance", "_assert_no_read_guidance", "_state"]


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
    assert "19バイト。" in step
    assert "sed -n '3,5p' --" in step
    assert step.count("各コードブロックを別々のfunctions.exec") == 1
    assert "前提・例外" in step
    whole = _merged_steps(  # pylint: disable=protected-access
        [target, read_triggers.ReadTarget(document)], {}, "codex"
    )
    assert len(whole) == 1 and "cat --" in whole[0]


@pytest.mark.parametrize("host,route", [("claude", "normal"), ("codex", "normal"), ("codex", "failure"), ("codex", "compact")])
@pytest.mark.parametrize(
    "command,success",
    [
        ("cat -- reference.md", True),
        ("wc -l reference.md && cat -- reference.md", True),
        ("cd nested && ls && wc -l ../reference.md && sed -n '1,$p' -- ../reference.md", True),
        ("cat -- reference.md | cut -c1-3", False),
        ("cat -- reference.md > copy.md", False),
        ("cat -- reference.md; ls", True),
        ("wc -l missing && cat -- reference.md; ls", False),
        ("cat -- reference.md && printf done; ls", True),
        # 追従は終了しないため実行結果を得られない。終了したと報告されても取得にしない。
        ("tail -f reference.md", False),
    ],
)
def test_hook_chain_records_acquisition_without_blocking_write(tmp_path, monkeypatch, state, host, route, command, success):
    """単独・複合読取の取得証拠を判定し、未取得でも同じ書込の継続を許可する。"""
    reference = tmp_path / "reference.md"
    reference.write_text("# 基準\n必要な内容\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Write",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "chain", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    writing = {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "本文"}}
    first = _run_hook_in_process("pretooluse", writing)
    assert first.returncode == 0 and str(reference) in first.stdout
    _assert_continues_without_guidance(_run_hook_in_process("pretooluse", writing))
    if host == "codex":
        guidance = first.stdout
        if route == "failure":
            guidance = rules_context.acquisition_failure(read_triggers.ReadTarget(reference), OSError("読取失敗"), shell=True)
        elif route == "compact":
            monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: "")
            monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
            result = _run_hook_in_process(
                "rules_context_codex", {**payload, "hook_event_name": "SessionStart", "source": "compact"}
            )
            assert result.returncode == 0 and not result.stderr
            assert "工程を定めるスキルと役割文書を全文読み直し" in result.stdout
            assert str(reference) not in result.stdout
            resumed = _run_hook_in_process("pretooluse", writing)
            assert resumed.returncode == 0 and not resumed.stderr
            guidance = resumed.stdout
        if route in {"normal", "compact"}:
            assert "max_output_tokens" in guidance and "text(result)" in guidance
        else:
            assert "必要範囲の本文を実出力として返せ" in guidance
            assert "省略・退避した部分は実際に回収" in guidance
        # 成功ケースは通知が示した実コマンドをそのまま取得観測へ渡す。
        if success:
            advised = f"cat -- {shlex.quote(str(reference))}"
            assert advised in guidance
            command = advised if command.startswith("cat") else f"wc -l {shlex.quote(str(reference))} && {advised}"
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": reference.read_text(encoding="utf-8") if success else "基準", "exit_code": 0},
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(reference), "main") is success
    after = _run_hook_in_process("pretooluse", writing)
    assert after.returncode == 0 and not after.stderr
    _assert_continues_without_guidance(after)
    if success:
        assert str(reference) not in after.stdout


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    "command,target,success",
    [
        # 読取計画が受理する表示形式は、同じ計画のファイル位置から対象パスを特定して記録する。
        ("sed -n '1,1p;2,$p' -- reference.md", "reference.md", True),
        ("sed -n '/^# 基準/,$p' -- reference.md", "reference.md", True),
        ("wc -l reference.md && sed -n '/^# 基準/,/^必要/p' -- reference.md", "reference.md", True),
        ("cd nested && sed -n '1,1p;2,2p' ../reference.md", "reference.md", True),
        ("head -n 20 reference.md", "reference.md", True),
        ("wc -l reference.md && head -n 20 reference.md", "reference.md", True),
        ("tail -n 20 -- reference.md", "reference.md", True),
        ("cd nested && tail -n 20 ../reference.md", "reference.md", True),
        ("jq -r '.body' reference.json", "reference.json", False),
        ("wc -l reference.json && jq -r '.body' reference.json", "reference.json", False),
        # 印字以外のsed命令、外部入力、展開、失敗を隠す連結は取得にしない。
        ("sed -n '1p;w copy.md' -- reference.md", "reference.md", False),
        ("sed -n '1e true' -- reference.md", "reference.md", False),
        ('sed -n "/^# 基準/,$p" -- reference.md', "reference.md", False),
        ("sed -n '/^# 基準/,$p' -- reference.md; ls", "reference.md", True),
        ("head -n 20 reference.md | cut -c1-3", "reference.md", False),
        ("jq --rawfile extra copy.md -r '.body' reference.json", "reference.json", False),
        ("jq -r 'input' reference.json", "reference.json", False),
        # brace展開は実在する別名のファイルを読むので、展開前の語と同名の未読ファイルを取得にしない。
        ("head -n 20 ref{a,b}.md", "ref{a,b}.md", False),
    ],
)
def test_executed_static_read_records_acquisition_without_blocking_write(
    tmp_path, monkeypatch, state, host, command, target, success
):
    """実際に実行した静的読取の結果を公開hookへ渡し、取得記録と同じ書込の許可を確かめる。"""
    (tmp_path / "reference.md").write_text("# 基準\n必要な内容\n", encoding="utf-8")
    (tmp_path / "reference.json").write_text(json.dumps({"body": "本文"}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "copy.md").write_text("写し\n", encoding="utf-8")
    for name in ("refa.md", "refb.md", "ref{a,b}.md"):
        (tmp_path / name).write_text(f"# {name}\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    reference = tmp_path / target
    row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Write",), (reference,))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"session_id": "executed", "cwd": str(tmp_path), **({"turn_id": "turn"} if host == "codex" else {})}
    writing = {**payload, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "out.md"), "content": "本文"}}
    _run_hook_in_process("pretooluse", writing)
    _assert_continues_without_guidance(_run_hook_in_process("pretooluse", writing))
    executed = subprocess.run(
        # `cut -c`はバイト単位で抽出するため、マルチバイト文字の一部だけを含む出力を置換文字で受け取る。
        ["bash", "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if success:
        assert executed.returncode == 0, executed.stderr
    observed = _run_hook_in_process(
        "posttooluse",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": executed.stdout, "stderr": executed.stderr, "exit_code": executed.returncode},
        },
    )
    assert observed.returncode == 0 and not observed.stderr
    records = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert read_delivery.target_ready(records, read_triggers.ReadTarget(reference), "main") is success
    after = _run_hook_in_process("pretooluse", writing)
    assert after.returncode == 0 and not after.stderr
    _assert_continues_without_guidance(after)


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
        assert read_delivery.target_ready(
            state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main", invocation=True
        )
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
    if order == "reference-first":
        assert "`Skill`で`agent-toolkit:process-wi`" in following
    else:
        _assert_no_read_guidance(following)
    assert not read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main", invocation=True
    )
    acquire_skill()
    assert read_delivery.target_ready(
        state[rules_context.OPERATION_SKILL_READY_KEY], read_triggers.ReadTarget(skill), "main", invocation=True
    )
    assert str(skill) not in invoke("PreToolUse", "Write", write)
    records = state[rules_context.OPERATION_SKILL_READY_KEY]
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "other", invocation=True)
    skill.write_text(skill.read_text(encoding="utf-8") + "変更\n", encoding="utf-8")
    assert not read_delivery.target_ready(records, read_triggers.ReadTarget(skill), "main", invocation=True)
