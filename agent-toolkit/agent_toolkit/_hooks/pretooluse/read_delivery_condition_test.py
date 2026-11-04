"""資料の読取と、宣言元の役割・工程条件の成立を区別する。"""

import pathlib

import pytest

from agent_toolkit._common.read_triggers import ReadTarget, ReadTrigger
from agent_toolkit._hooks import notice
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


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
    assert "候補" in candidate.stdout and str(unrelated) in candidate.stdout
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
