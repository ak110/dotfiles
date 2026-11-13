"""資料の読取と、宣言元の役割・工程条件の成立を区別する。"""

import pathlib

import pytest

from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger
from agent_toolkit._hooks import notice
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _additional_context, _run_hook_in_process


@pytest.fixture
def _setup(tmp_path, monkeypatch):
    state: dict = {}

    def read(_session):
        return state

    def update(_session, mutate, **_kwargs):
        return mutate(state)

    for module in (read_delivery, notice):
        monkeypatch.setattr(module, "read_state", read)
        monkeypatch.setattr(module, "update_state", update)
    selected = tmp_path / "exec-review.parent.md"
    selected.write_text("# 受領契約\n検証結果の形式\n", encoding="utf-8")
    unrelated = tmp_path / "main-launch.md"
    unrelated.write_text("# メインが起動する工程\nメイン専用の本文\n", encoding="utf-8")
    row = ReadTrigger(
        tmp_path / "SKILL.md",
        49,
        "判断",
        (f"read:{selected}",),
        (unrelated, selected),
        condition="起動経路がメインによる起動で、計画構造の検証を終えて実装へ進む前",
    )
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    notice.begin_delivery()
    return selected, unrelated, row, state


def _progress(tmp_path):
    return {
        "session_id": "test",
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "progress-120.md"), "content": "進捗を記録した。"},
    }


def test_public_read_does_not_confirm_another_role_condition(tmp_path, _setup):
    """一方の資料を読んでも、同じ行のメイン専用資料を必読にしない。"""
    selected, unrelated, _row, _state = _setup
    read = {
        "session_id": "test",
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(selected)},
    }
    candidate = _run_hook_in_process("pretooluse", read)
    assert candidate.returncode == 0
    assert "候補" not in candidate.stdout and str(unrelated) not in candidate.stdout
    assert "未取得資料がある" not in candidate.stdout
    result = _run_hook_in_process("pretooluse", _progress(tmp_path))
    assert result.returncode == 0 and str(unrelated) not in result.stdout
    repeated = _run_hook_in_process("pretooluse", _progress(tmp_path))
    assert repeated.returncode == 0 and str(unrelated) not in repeated.stdout


def test_unconfirmed_judgment_is_not_restored_as_mandatory_from_old_selection(tmp_path, _setup):
    """改訂前の選択元を保存した状態でも、未確定の条件を持ち越さない。"""
    _selected, unrelated, row, _state = _setup
    read_delivery._select_targets("test", [ReadTarget(unrelated)], "main", rows=[row])  # pylint: disable=protected-access
    result = _run_hook_in_process("pretooluse", _progress(tmp_path))
    assert result.returncode == 0 and str(unrelated) not in result.stdout


def test_confirmation_operation_remains_an_observable_judgment_condition(tmp_path):
    """確認本文・選択肢の起草を観測できる契機は保つ。"""
    row = ReadTrigger(tmp_path / "SKILL.md", 1, "判断", ("confirmation",), (), condition="確認本文を書く前")
    assert read_delivery.condition_confirmed(row, "AskUserQuestion", {"questions": []}, str(tmp_path))


def test_real_main_launch_declaration_stays_conditional_after_review_contract_read(tmp_path, monkeypatch, _setup):
    """実際のwrite-plan宣言から、レビュー契約の読取と進捗作成へ通す。"""
    del _setup
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    root = pathlib.Path(read_delivery.__file__).resolve().parents[3]
    monkeypatch.setattr(read_delivery.plugin_resources, "plugin_root", lambda: root)
    rows = read_delivery.read_triggers.parse_tables(root / "skills/write-plan/SKILL.md", root)
    row = next(item for item in rows if any(path.name == "main-launch.md" for path in item.documents))
    assert row.kind == "判断" and "メインによる起動" in row.condition
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "test",
            "cwd": str(tmp_path),
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": str(root / "share/exec-review.parent.md"), "offset": 1, "limit": 20},
        },
    )
    assert result.returncode == 0
    progress = _run_hook_in_process("pretooluse", _progress(tmp_path))
    assert progress.returncode == 0 and "main-launch.md" not in progress.stdout


def test_selected_source_candidates_are_deferred_and_deduplicated(tmp_path, monkeypatch, _setup):
    """取得の次の関連編集に限り、選択済み資料の同じ条件を1件案内する。"""
    selected, unrelated, _row, state = _setup
    source = tmp_path / "selected.md"
    source.write_text("# 選択資料\n", encoding="utf-8")
    rows = [
        ReadTrigger(source, number, "判断", ("body-writing",), (unrelated,), condition="設計を確定する前") for number in (1, 2)
    ]
    foreign = tmp_path / "other.md"
    foreign.write_text("# 別工程\n", encoding="utf-8")
    rows.append(ReadTrigger(foreign, 1, "判断", ("body-writing",), (selected,), condition="別工程の条件"))
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    read_delivery._select_targets("test", [ReadTarget(source)], "main")  # pylint: disable=protected-access
    for tool, inputs in (
        ("Read", {"file_path": str(source)}),
        ("Bash", {"command": f"cat -- {source}"}),
        ("Bash", {"command": f"sed -n '1,$p' -- {source}"}),
    ):
        result = _run_hook_in_process(
            "pretooluse", {"session_id": "test", "cwd": str(tmp_path), "tool_name": tool, "tool_input": inputs}
        )
        assert "候補" not in result.stdout
    result = _run_hook_in_process("pretooluse", _progress(tmp_path))
    assert result.returncode == 0
    # 出力にはユーザー画面向けの写しも入るため、エージェント宛ての本文で数える。
    assert _additional_context(result).count("条件: 設計を確定する前") == 1
    assert "別工程の条件" not in result.stdout
    assert not read_delivery.target_ready(state.get("operation_skill_delivered_agents", {}), ReadTarget(source), "main")


@pytest.mark.parametrize("matches,confirmed,expected", [(False, False, 0), (True, True, 0), (True, False, 1)])
def test_candidate_checks_source_acquisition_only_after_light_conditions(
    tmp_path, monkeypatch, _setup, matches, confirmed, expected
):
    """非該当・確定済みの宣言で、宣言元の本文取得判定を繰り返さない。"""
    selected, _unrelated, _row, _state = _setup
    row = ReadTrigger(selected, 1, "判断", ("tool:Read",), (selected,), condition="資料を選ぶ前")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(read_delivery, "row_matches", lambda *_args, **_kwargs: matches)
    monkeypatch.setattr(read_delivery, "condition_confirmed", lambda *_args, **_kwargs: confirmed)
    calls = []

    def ready(_records, target, _actor, **_kwargs):
        calls.append(target)
        return False

    monkeypatch.setattr(read_delivery, "target_ready", ready)
    assert not read_delivery._condition_candidate_notice(  # pylint: disable=protected-access
        {"cwd": str(tmp_path)}, "Grep", {"pattern": "x"}, "test"
    )
    assert len(calls) == expected


@pytest.fixture
def _named_reference_skill(tmp_path, monkeypatch):
    """本文が資料を名指しし、読込表に行を持たないスキルと、空の取得状態を用意する。"""
    state: dict = {}
    for module in (read_delivery, notice):
        monkeypatch.setattr(module, "read_state", lambda _session: state)
        monkeypatch.setattr(module, "update_state", lambda _session, mutate, **_kwargs: mutate(state))
    monkeypatch.setattr(read_delivery.delegated_session, "is_lightweight_delegated", lambda _env: False)
    notice.begin_delivery()
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    source = root / "skills/example/SKILL.md"
    reference = source.parent / "references/rule.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("# 基準\n" + "本文\n" * 20, encoding="utf-8")
    source.write_text("# 例\n\n判定は`references/rule.md`に従う。\n", encoding="utf-8")
    monkeypatch.setattr(read_delivery.plugin_resources, "plugin_root", lambda: root)
    monkeypatch.setattr(read_delivery, "_declaration_sources", lambda _cwd: [source])
    return source, reference


def _partial_read(tmp_path, reference):
    """資料の冒頭だけを読み、別の操作を続ける呼び出しを返す。

    読取だけの呼び出しは取得操作として扱われ、読込条件の候補の通知は次の関連操作へ保留される。
    """
    return {
        "session_id": "test",
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": f"sed -n '1,3p' -- {reference} && date"},
    }


def test_partial_read_of_named_reference_notifies_supplied_condition(tmp_path, _named_reference_skill):
    """本文の名指しへ補った宣言でも、資料を一部だけ読むと定型の条件と資料で読込条件の候補を通知する。"""
    source, reference = _named_reference_skill
    unread = _run_hook_in_process("pretooluse", _partial_read(tmp_path, reference))
    assert unread.returncode == 0 and "読込条件の候補" not in unread.stdout
    read_delivery._mark("test", source, "main")  # pylint: disable=protected-access
    result = _run_hook_in_process("pretooluse", _partial_read(tmp_path, reference))
    assert result.returncode == 0
    assert "読込条件の候補" in result.stdout
    assert "本文がこの資料の基準を使う場面で、資料を選択して取得する時" in result.stdout
    assert str(reference) in result.stdout and f"{source}:3" in result.stdout


def test_named_reference_rows_are_not_resolved_for_unrelated_reads(tmp_path, monkeypatch, _named_reference_skill):
    """読取対象の名前が本文に無い宣言元では、補完の宣言を求めない。"""
    source, reference = _named_reference_skill
    other = tmp_path / "other.md"
    other.write_text("# 別の資料\n", encoding="utf-8")
    called: list[pathlib.Path] = []

    def _record_call(path, *_args, **_kwargs):
        called.append(path)
        return []

    monkeypatch.setattr(read_delivery.read_triggers, "named_reference_rows", _record_call)
    read_delivery._mark("test", source, "main")  # pylint: disable=protected-access
    _run_hook_in_process("pretooluse", _partial_read(tmp_path, other))
    assert not called
    _run_hook_in_process("pretooluse", _partial_read(tmp_path, reference))
    assert called == [source]


@pytest.mark.parametrize(
    "command,candidate",
    [
        ("cat -- {path}", False),
        ("cat -- {path} && date", False),
        ("sed -n '1,$p' -- {path} && date", False),
        ("sed -n '1,10p' -- {path} && date", True),
        ("cat -- {path} | head -n 10", True),
    ],
)
def test_same_call_full_read_is_not_a_named_reference_candidate(
    tmp_path, monkeypatch, _named_reference_skill, command, candidate
):
    """全文読取予定だけを候補から外し、部分読取・パイプは取得と取り違えない。"""
    source, reference = _named_reference_skill
    row = ReadTrigger(source, 3, "判断", ("tool:Bash",), (reference,), condition="資料を選択して取得する時")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    read_delivery._mark("test", source, "main")  # pylint: disable=protected-access
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "test",
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": command.format(path=reference)},
        },
    )
    assert result.returncode == 0 and not result.stderr
    assert ("読込条件の候補" in result.stdout) is candidate
    assert not read_delivery.target_ready(
        read_delivery.read_state("test").get("operation_skill_delivered_agents", {}), ReadTarget(reference), "main"
    )


def test_same_call_full_read_is_removed_from_a_multi_document_candidate(tmp_path, monkeypatch, _named_reference_skill):
    """同じ宣言に未取得資料が残る場合も、今回読む資料を候補へ再掲しない。"""
    source, reference = _named_reference_skill
    other = tmp_path / "other.md"
    other.write_text("# 別の必要資料\n", encoding="utf-8")
    row = ReadTrigger(source, 3, "判断", ("tool:Bash",), (reference, other), condition="資料を選択して取得する時")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [row])
    read_delivery._mark("test", source, "main")  # pylint: disable=protected-access
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "test",
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": f"cat -- {reference} && date"},
        },
    )
    assert result.returncode == 0 and not result.stderr
    context = _additional_context(result)
    assert "読込条件の候補" in context and str(other) in context
    assert str(reference) not in context
