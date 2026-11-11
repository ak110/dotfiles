"""役割の起動配送と、主体別の取得手段をまたぐ読込契約を確かめる。"""

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.document_delivery import observe_received
from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_acquisition_support import (
    _assert_continues_without_guidance,
    _checkout,
    _deliver_notice_open,
    _read_lines,
    _state,
)

__all__ = ["_assert_continues_without_guidance", "_checkout", "_deliver_notice_open", "_read_lines", "_state"]


@pytest.mark.parametrize("scenario", ["documents", "sections", "skill"])
def test_partial_acquisition_keeps_guidance_until_all_targets_are_acquired(tmp_path, monkeypatch, state, scenario):
    """案内した対象の一部を取得しても未解消を保ち、全対象の取得後だけ案内の抑止記録を解消する。"""
    first = tmp_path / "first.md"
    first.write_text("# 資料1\n\n## 節A\n基準a\n\n## 節B\n基準b\n", encoding="utf-8")
    second = tmp_path / "second.md"
    second.write_text("# 資料2\n固有の基準\n", encoding="utf-8")
    skill = tmp_path / ".claude/skills/local/SKILL.md"
    if scenario == "sections":
        row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Do",), (first, first), ("節A", "節B"))
    elif scenario == "skill":
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: local\ndescription: 試験\n---\n# 基準\n固有の本文\n", encoding="utf-8")
        row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Do",), (skill, second))
    else:
        row = read_triggers.ReadTrigger(tmp_path / "source.md", 1, "操作", ("tool:Do",), (first, second))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    payload = {"cwd": str(tmp_path)}
    hook_payload = {"session_id": "test", "cwd": str(tmp_path), "tool_name": "Do", "tool_input": {}}
    guided = _run_hook_in_process("pretooluse", hook_payload)
    assert guided.returncode == 0 and not guided.stderr
    assert "未取得資料" in guided.stdout
    assert _deliver_notice_open(state)
    if scenario == "sections":
        inputs, response = _read_lines(first, 1, 5)
        observe_received({**payload, "tool_response": response}, "Read", inputs, "test")
    elif scenario == "skill":
        # スキル起動を要する対象は本文の読取だけでは取得済みにならず、案内は未解消のまま残る。
        observe_received(
            {**payload, "tool_response": skill.read_text(encoding="utf-8")}, "Read", {"file_path": str(skill)}, "test"
        )
        observe_received(
            {**payload, "tool_response": second.read_text(encoding="utf-8")}, "Read", {"file_path": str(second)}, "test"
        )
    else:
        observe_received(
            {**payload, "tool_response": first.read_text(encoding="utf-8")}, "Read", {"file_path": str(first)}, "test"
        )
    assert _deliver_notice_open(state)
    if scenario == "sections":
        inputs, response = _read_lines(first, 6, 7)
        observe_received({**payload, "tool_response": response}, "Read", inputs, "test")
    elif scenario == "skill":
        observe_received({**payload, "tool_response": "スキル起動済み"}, "Skill", {"skill": "local"}, "test")
    else:
        observe_received(
            {**payload, "tool_response": second.read_text(encoding="utf-8")}, "Read", {"file_path": str(second)}, "test"
        )
    assert not _deliver_notice_open(state)
    _assert_continues_without_guidance(_run_hook_in_process("pretooluse", hook_payload))


@pytest.mark.parametrize("case", ["worktree", "missing"])
@pytest.mark.usefixtures("state")
def test_project_skill_guidance_uses_the_checkout_version(tmp_path, monkeypatch, case):
    """稼働版に無く対象版にある資料は対象版から案内し、取得後は担当内で続行できる。本当の欠落は参照元とともに示す。"""
    running = _checkout(tmp_path / "running", with_new=False)
    worktree = _checkout(tmp_path / "worktree", with_new=case == "worktree")
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", running / "agent-toolkit")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    hook_payload = {"session_id": "test", "cwd": str(worktree), "tool_name": "Do", "tool_input": {}}
    guided = _run_hook_in_process("pretooluse", hook_payload)
    assert guided.returncode == 0 and not guided.stderr
    expected = worktree / "agent-basics/skills/bugfix/references/new-reference.md"
    assert str(expected) in guided.stdout
    assert str(running / "agent-basics") not in guided.stdout
    if case == "missing":
        assert "存在しない" in guided.stdout
        assert str(worktree / ".claude/skills/local/SKILL.md") in guided.stdout
        return
    assert "存在しない" not in guided.stdout
    observe_received(
        {"cwd": str(worktree), "tool_response": expected.read_text(encoding="utf-8")},
        "Read",
        {"file_path": str(expected)},
        "test",
    )
    _assert_continues_without_guidance(_run_hook_in_process("pretooluse", hook_payload))


@pytest.mark.parametrize("entry", ["share-variable", "absolute-role"])
def test_share_and_role_guidance_use_the_declaring_version(tmp_path, monkeypatch, state, entry):
    """share文書の変数参照と絶対パスの役割指定は、公開hookの案内も宣言側の版を指し、別版の同名資料では解消しない。"""
    running = _checkout(tmp_path / "running", with_new=False)
    worktree = _checkout(tmp_path / "worktree", with_new=True)
    for base in (running, worktree):
        share = base / "agent-toolkit/share"
        share.mkdir()
        (share / "new-review.subagent.md").write_text("# 新設役割\n", encoding="utf-8")
        (share / "new-review.parent.md").write_text(f"# 新設契約（{base.name}）\n\n## 入力\n説明\n", encoding="utf-8")
    # 役割文書の宣言はその担当だけに適用されるため、担当を問わないshare文書を宣言元にする。
    source = worktree / "agent-toolkit/share/guide.md"
    if entry == "share-variable":
        cell, selector, tool_input = "`${CLAUDE_PLUGIN_ROOT}/share/new-review.parent.md`", "", {}
    else:
        role = worktree / "agent-toolkit/share/new-review.subagent.md"
        cell, selector, tool_input = "呼び出し元の起動・受領契約", "`role-contract`", {"subagent_md_path": str(role)}
    source.write_text(
        "# 役割\n\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |\n| --- | --- | --- | --- | --- |\n"
        f"| 操作の前 | {cell} | 操作 | `tool:Do` | {selector} |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(read_delivery.plugin_resources, "_PLUGIN_ROOT", running / "agent-toolkit")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # 作業ツリーのshare文書を取得済み資料として宣言一覧へ加えた状態を、宣言の解析から再現する。
    monkeypatch.setattr(
        read_delivery,
        "declarations",
        lambda *_args, **_kwargs: read_triggers.parse_tables(source, running / "agent-toolkit", project=str(worktree)),
    )
    hook_payload = {"session_id": "test", "cwd": str(worktree), "tool_name": "Do", "tool_input": tool_input}
    expected = worktree / "agent-toolkit/share/new-review.parent.md"
    other = running / "agent-toolkit/share/new-review.parent.md"
    guided = _run_hook_in_process("pretooluse", hook_payload)
    assert guided.returncode == 0 and not guided.stderr
    assert str(expected) in guided.stdout
    assert str(other) not in guided.stdout
    assert "存在しない" not in guided.stdout
    assert _deliver_notice_open(state)
    # 稼働版の同名資料を読んでも、宣言側の版の案内は解消しない。
    observe_received(
        {"cwd": str(worktree), "tool_response": other.read_text(encoding="utf-8")}, "Read", {"file_path": str(other)}, "test"
    )
    assert _deliver_notice_open(state)
    observe_received(
        {"cwd": str(worktree), "tool_response": expected.read_text(encoding="utf-8")},
        "Read",
        {"file_path": str(expected)},
        "test",
    )
    _assert_continues_without_guidance(_run_hook_in_process("pretooluse", hook_payload))
