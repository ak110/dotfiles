# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

# pylint: disable=protected-access,subprocess-run-check

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._testing import plan_fixture
from agent_toolkit._testing.wi_add_support import (
    _cmd_add_args,
    _patch_cmd_add_operations,
    _run_public_add,
    _with_cause,
)
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY as _AGENT_AWI_BODY
from agent_toolkit.atk_test import _FIXED_DT, _make_subprocess_fake, _setup_notes, _write_awi_file

_RECORD = plan_fixture.cause_record()
_RECORD_WITHOUT_SPREAD = _RECORD.replace(f"{plan_fixture.cause_record_line('広がり')}\n", "")
_PREVIOUS_TABLE = plan_fixture.rows_table(
    ("直接的原因", "原因分析の根拠", "対策", "類似見直し観点", "再発防止策"), plan_fixture.BUG_FILLER
)
"""6区分化より前にAWIの`## 原因分析`へ置いた5行の表。"""

_SECTIONS: dict[str, tuple[str, str | None]] = {
    "valid": (_RECORD, None),
    "two_units": (f"### 事象A\n\n{_RECORD}\n\n### 事象B\n\n{_RECORD}", None),
    "missing_line": (_RECORD_WITHOUT_SPREAD, "`広がり:`の行が無い（書く内容: 同じ仕組みの他の箇所"),
    "empty_line": (_RECORD.replace(plan_fixture.cause_record_line("確認"), "- 確認:"), "`確認:`の行に本文が無い"),
    "second_unit_missing": (
        f"### 事象A\n\n{_RECORD}\n\n### 事象B\n\n{_RECORD_WITHOUT_SPREAD}",
        "`### 事象B`: `広がり:`の行が無い",
    ),
    "legacy_tables": (plan_fixture.legacy_cause_tables(), "原因分析を廃止した表で書いている"),
    "previous_table": (_PREVIOUS_TABLE, "原因分析を廃止した表で書いている"),
    "short_record": ("誤った設定値を訂正し、期待した値で再検証した。", "`期待:`の行が無い"),
}
"""`## 原因分析`の本文と、エージェントの投入を拒否する場合に診断へ含まれる文言。"""


@pytest.mark.parametrize("case", list(_SECTIONS))
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
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    for variable in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(variable, raising=False)
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
    section, diagnostic = _SECTIONS[case]
    body = f"# 表題\n\n説明\n\n## 反映内容と反映先\n対象\n\n## 原因分析\n\n{section}\n"
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

    if agent and diagnostic is not None:
        with pytest.raises(SystemExit) as rejected:
            run()
        assert rejected.value.code != 0
        err = capsys.readouterr().err
        assert "原因分析の書式" in err
        assert diagnostic in err
    else:
        run()
        assert "成功:" in capsys.readouterr().out


@pytest.mark.parametrize("legacy", ["legacy_tables", "previous_table"])
@pytest.mark.parametrize("edit", ["append", "replace_other_section", "replace_cause_section"])
def test_public_edit_keeps_saved_legacy_cause_table(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    legacy: str,
    edit: str,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    body = f"# 表題\n\n説明\n\n## 反映内容と反映先\n対象\n\n## 原因分析\n\n{_SECTIONS[legacy][0]}\n"
    path = _write_awi_file(notes, "legacy.md", body=body, source="test")
    edited = tmp_path / "edited.md"
    if edit == "append":
        edited.write_text("## 原因分析の訂正\n\n対策を分けて示す。\n", encoding="utf-8")
        argv = ["wi", "edit", "legacy.md", "--append", "--body-file", str(edited)]
    elif edit == "replace_other_section":
        edited.write_text(body.replace("## 反映内容と反映先\n対象", "## 反映内容と反映先\n対象を直した"), encoding="utf-8")
        argv = ["wi", "edit", "legacy.md", "--body-file", str(edited), "--dry-run"]
    else:
        edited.write_text(body.replace(plan_fixture.BUG_FILLER, "表の記入を直した。", 1), encoding="utf-8")
        argv = ["wi", "edit", "legacy.md", "--body-file", str(edited), "--dry-run"]

    with pytest.raises(SystemExit) as finished:
        atk.main(argv, home=tmp_path)

    err = capsys.readouterr().err
    if edit == "replace_cause_section":
        assert finished.value.code != 0
        assert "原因分析を廃止した表で書いている" in err
    else:
        assert finished.value.code == 0, err
        assert "原因分析の書式" not in err
    assert ("対策を分けて示す。" in path.read_text(encoding="utf-8")) is (edit == "append")


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
        ("　未確定", False),
        ("　**未確定**（再現待ち）", False),
        ("** 　調査中**", False),
    ],
)
def test_add_accepts_undetermined_cause_in_awi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
    dry_run: bool,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")

    _run_public_add(
        _cmd_add_args(tmp_path, _with_cause(_AGENT_AWI_BODY, value), source="test", dry_run=dry_run),
        notes,
        _FIXED_DT,
        tmp_path,
    )

    assert "原因が未確定のまま保存しようとした" not in capsys.readouterr().err
    if dry_run:
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    else:
        assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


@pytest.mark.parametrize(
    "material",
    [
        "| 項目 | 内容 |\n| --- | --- |\n| 直接的原因 | 調査中 |\n",
        "- **原因**: 未確定（再現待ち）\n",
    ],
    ids=["saved-table-row", "record-line"],
)
def test_add_rejects_undetermined_cause_in_uwi(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    material: str,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")

    with pytest.raises(SystemExit) as exc_info:
        add_module.cmd_add(
            _cmd_add_args(tmp_path, f"どちらを選ぶか？\n\n## 判断材料\n\n{material}", source="test", entry_type=WI_TYPE_UWI),
            notes,
            _FIXED_DT,
            tmp_path,
        )

    assert exc_info.value.code == 1
    assert "原因が未確定のまま保存しようとした" in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize(
    "message",
    [
        _with_cause(_AGENT_AWI_BODY, "旧値の残存。`git log -S`で、それまで不明だった設定値の由来を特定した"),
        _with_cause(_AGENT_AWI_BODY, "不明だった設定を特定した"),
        _with_cause(_AGENT_AWI_BODY, "　不明だった設定を特定した"),
        _with_cause(_AGENT_AWI_BODY, "調査中の設定変更が旧値を残していたと特定した"),
        _AGENT_AWI_BODY.replace(
            "## 反映内容と反映先\n対象と反映先",
            "## 反映内容と反映先\n対象と反映先\n\n## 原因分析\n\n確定した原因の説明。例示:\n\n"
            f"```markdown\n- 原因: 未確定\n```\n\n{_RECORD}",
            1,
        ),
    ],
)
def test_add_accepts_determined_cause_variants(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")

    _run_public_add(_cmd_add_args(tmp_path, message, source="test"), notes, _FIXED_DT, tmp_path)

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1


def test_add_accepts_undetermined_cause_from_human(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)

    _run_public_add(
        _cmd_add_args(tmp_path, _with_cause(_AGENT_AWI_BODY, "未確定"), source="test"),
        notes,
        _FIXED_DT,
        tmp_path,
    )

    assert len(list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))) == 1
