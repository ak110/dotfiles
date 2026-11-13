# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: cause_analysisのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._testing import plan_fixture
from agent_toolkit._testing.wi_add_support import (
    _PREVIOUS_AWI_CAUSE_ITEMS_TABLE,
    _awi_cause_items_table,
    _cmd_add_args,
    _patch_cmd_add_operations,
    _run_public_add,
    _with_direct_cause,
)
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY as _AGENT_AWI_BODY
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file


@pytest.mark.parametrize(
    "case",
    [
        "missing_harm",
        "empty_harm",
        "empty_l4",
        "bare_l4",
        "missing_creation_review",
        "missing_detection_prevention",
        "empty_detection_review",
        "missing_regression",
        "missing_decision",
        "empty_decision",
        "empty_countermeasure",
        "previous_format",
        "missing_table",
        "second_unit_missing_table",
        "two_units",
        "valid",
    ],
)
@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("operation", ["add", "edit"])
def test_public_add_cause_analysis_format(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    operation: str,
    *,
    agent: bool,
) -> None:
    """原因分析の新検査はエージェントに適用し、人間入力を拒否しない。

    AWIの2列表は害の型と処置の6区分と採否2行の9行とし、4区分の候補行・採否行・回帰防止策の欠落と空欄、
    6区分化前の5行表と、バグ単位のL4表に対応する処置表が無い記録（2単位の片方だけ欠ける場合を含む）を拒否する。
    """
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    for variable in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(variable, raising=False)
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
    cause = plan_fixture.bug_cause_table()
    table = _awi_cause_items_table()
    removed = {
        "missing_harm": "害の型と反復",
        "missing_creation_review": "類似見直し観点（作り込み）",
        "missing_detection_prevention": "再発防止策（見逃し）",
        "missing_regression": "回帰防止策",
        "missing_decision": "再発防止策の採否",
    }
    emptied = {
        "empty_harm": "害の型と反復",
        "empty_detection_review": "類似見直し観点（見逃し）",
        "empty_decision": "類似見直しの採否",
        "empty_countermeasure": "対策",
    }
    if case in ("empty_l4", "bare_l4"):
        value = "" if case == "empty_l4" else "該当なし。"
        cause = cause.replace(f"{plan_fixture.CAUSE_FILLER} |", f"{value} |")
    elif case in removed:
        table = "\n".join(line for line in table.splitlines() if not line.startswith(f"| {removed[case]} |"))
    elif case in emptied:
        table = table.replace(f"| {emptied[case]} | {plan_fixture.BUG_FILLER} |", f"| {emptied[case]} |  |")
    elif case == "previous_format":
        table = _PREVIOUS_AWI_CAUSE_ITEMS_TABLE
    elif case == "missing_table":
        table = ""
    elif case == "second_unit_missing_table":
        table = f"{table}\n\n{plan_fixture.bug_cause_table()}"
    elif case == "two_units":
        table = f"{table}\n\n{plan_fixture.bug_cause_table()}\n\n{_awi_cause_items_table()}"
    else:
        cause = cause.replace(f"{plan_fixture.CAUSE_FILLER} |", "該当なし。上流管理外の構造だけで生じた。 |")
    body = f"# 表題\n\n説明\n\n## 反映内容と反映先\n対象\n\n## 原因分析\n\n{cause}\n\n{table}\n"
    args = _cmd_add_args(tmp_path, body, source="test", dry_run=True)

    def run() -> None:
        if operation == "add":
            _run_public_add(args, notes, _FIXED_DT, tmp_path)
            return
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        _write_awi_file(notes, "cause.md", source="test")
        with pytest.raises(SystemExit) as finished:
            atk.main(["wi", "edit", "cause.md", "--body-file", args.body_file[0], "--dry-run"], home=tmp_path)
        if finished.value.code:
            raise SystemExit(finished.value.code)

    if agent and case not in ("valid", "two_units"):
        with pytest.raises(SystemExit) as rejected:
            run()
        assert rejected.value.code != 0
        assert "原因分析の書式" in capsys.readouterr().err
    else:
        run()
        assert "成功:" in capsys.readouterr().out


@pytest.mark.parametrize("legacy_format", ["five_rows", "eight_rows"])
def test_public_append_keeps_saved_legacy_cause_table(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], legacy_format: str
) -> None:
    """保存済みの旧形式の表を持つAWIへの追記は、原因分析節を変えないため拒否しない。

    書式の改訂前に保存された未終了の項目へ、訂正や記録を追記できる必要がある。
    """
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    legacy = (
        _PREVIOUS_AWI_CAUSE_ITEMS_TABLE
        if legacy_format == "five_rows"
        else "\n".join(line for line in _awi_cause_items_table().splitlines() if not line.startswith("| 害の型と反復 |"))
    )
    body = f"# 表題\n\n説明\n\n## 反映内容と反映先\n対象\n\n## 原因分析\n\n{plan_fixture.bug_cause_table()}\n\n{legacy}"
    _write_awi_file(notes, "legacy.md", body=body, source="test")
    appended = tmp_path / "append.md"
    appended.write_text("## 原因分析の訂正\n\n対策を分けて示す。\n", encoding="utf-8")
    with pytest.raises(SystemExit) as finished:
        atk.main(["wi", "edit", "legacy.md", "--append", "--body-file", str(appended)], home=tmp_path)
    assert finished.value.code == 0
    assert "原因分析の書式" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "dry_run"),
    [
        ("未確定。調査を後続へ分ける", False),
        ("**調査中**", True),
        ("`不明`", False),
        ("未特定（再現待ち）", False),
        ("未確認 原因候補は2件", False),
        ("**調査中**: 再現条件を確認している", False),
        ("`不明`: ログが残っていない", False),
        ("\u3000未確定", False),
        ("\u3000**未確定**（再現待ち）", False),
        ("** \u3000調査中**", False),
    ],
)
def test_add_accepts_undetermined_direct_cause_in_awi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
    dry_run: bool,
) -> None:
    """エージェントが原因の未確定を宣言した通常AWIを保存する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")

    _run_public_add(
        _cmd_add_args(tmp_path, _with_direct_cause(_AGENT_AWI_BODY, value), source="test", dry_run=dry_run),
        notes,
        _FIXED_DT,
        tmp_path,
    )

    assert "直接的原因が未確定のまま保存しようとした" not in capsys.readouterr().err
    if dry_run:
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    else:
        assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


def test_add_rejects_undetermined_direct_cause_in_uwi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """UWIも、種別による除外なしに`直接的原因`行の未確定宣言を拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    table = "| 項目 | 内容 |\n| --- | --- |\n| 直接的原因 | 調査中 |\n"

    with pytest.raises(SystemExit) as exc_info:
        add_module.cmd_add(
            _cmd_add_args(tmp_path, f"どちらを選ぶか？\n\n## 判断材料\n\n{table}", source="test", entry_type=WI_TYPE_UWI),
            notes,
            _FIXED_DT,
            tmp_path,
        )

    assert exc_info.value.code == 1
    assert "直接的原因が未確定のまま保存しようとした" in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize(
    "message",
    [
        _with_direct_cause(_AGENT_AWI_BODY, "旧値の残存。`git log -S`で、それまで不明だった設定値の由来を特定した"),
        _with_direct_cause(_AGENT_AWI_BODY, "不明だった設定を特定した"),
        _with_direct_cause(_AGENT_AWI_BODY, "\u3000不明だった設定を特定した"),
        _with_direct_cause(_AGENT_AWI_BODY, "調査中の設定変更が旧値を残していたと特定した"),
        _AGENT_AWI_BODY.replace(
            "## 反映内容と反映先\n対象と反映先",
            "## 反映内容と反映先\n対象と反映先\n\n## 原因分析\n\n確定した原因の説明。例示:\n\n"
            "```markdown\n| 直接的原因 | 未確定 |\n```\n\n"
            f"{plan_fixture.bug_cause_table()}\n\n{_awi_cause_items_table()}",
            1,
        ),
    ],
)
def test_add_accepts_determined_cause_variants(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    """確定した原因の文中の語とコードフェンス内の例は、未確定の宣言として拒否しない。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")

    _run_public_add(_cmd_add_args(tmp_path, message, source="test"), notes, _FIXED_DT, tmp_path)

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


def test_add_accepts_undetermined_cause_from_human(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """エージェント環境の外からの投入は、`直接的原因`行が未確定でも保存する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(
        _cmd_add_args(tmp_path, _with_direct_cause(_AGENT_AWI_BODY, "未確定"), source="test"),
        notes,
        _FIXED_DT,
        tmp_path,
    )

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1
