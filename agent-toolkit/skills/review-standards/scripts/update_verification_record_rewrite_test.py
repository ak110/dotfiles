"""未判定検証記録の参照更新を公開操作から検収する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

from agent_toolkit._atk import run_script

WI = "20260101-000000-001.md"


def _run(*args: str) -> int:
    return run_script.dispatch(argparse.Namespace(script_name="verification-record", script_args=list(args)))


def _inputs(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, dict]:
    old = "a" * 40
    evidence = f"commit:{old}、裸のOID={old}、取得版HEAD={old}、git_head={old}、比較元={old}、観測ファイル=記録.txt"
    shared = {"source": f"原文 commit:{old}", "outcome": "", "reviewed_head": "", "evidence": evidence}
    payload = {
        "wi_conditions": [{"awi": WI, "condition": "保存する。", **shared}],
        "user_requirements": [{"awi": "", "origin": "ユーザー指示", "requirement": "観測する。", **shared}],
    }
    record, mapping = tmp_path / "pending.json", tmp_path / "rewrite.json"
    record.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    mapping.write_text(json.dumps({old: "b" * 40}), encoding="utf-8")
    return record, mapping, payload


@pytest.mark.parametrize("split", [False, True], ids=["one-to-one", "split"])
def test_public_rewrite_updates_both_sections_and_guides_only_changed_observations(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], split: bool
) -> None:
    """両配列のcommit標識だけを更新し、実装担当が取り直す観測の範囲を案内する。"""
    record, mapping, original = _inputs(tmp_path)
    old, new, other = "a" * 40, "b" * 40, "c" * 40
    if split:
        mapping.write_text(json.dumps({old: [{"commit": new, "awi": [WI]}, {"commit": other, "awi": [WI]}]}), encoding="utf-8")
    assert _run("--output", str(record), "--rewrite-map", str(mapping)) == 0
    observed = json.loads(record.read_text(encoding="utf-8"))
    replacement = f"commit:{new}" + (f"、commit:{other}" if split else "")
    for section, rows in original.items():
        assert observed[section] == [{**row, "evidence": row["evidence"].replace(f"commit:{old}", replacement)} for row in rows]
    message = capsys.readouterr().out
    assert "未判定検証記録" in message
    action = message.split("次の操作:", 1)[1]
    assert "結果に作用する内容・条件の差がある行だけ" in action
    assert "取り直" in action
    assert "reviewed_head" not in action and "判定する" not in action and "記入" not in action


@pytest.mark.parametrize("invalid", ["map", "ambiguous", "outcome", "reviewed_head"])
def test_public_rewrite_failure_identifies_pending_record_and_preserves_bytes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """不正対応と完成条件行の判定済み入力を対象名付きで拒否し、既存の保存物を保持する。"""
    record, mapping, original = _inputs(tmp_path)
    if invalid == "map":
        mapping.write_text("[]", encoding="utf-8")
    elif invalid == "ambiguous":
        original["wi_conditions"][0]["evidence"] = "commit:aaaaaaa"
        mapping.write_text(json.dumps({"a" * 40: "b" * 40, "a" * 39 + "d": "c" * 40}), encoding="utf-8")
    else:
        original["wi_conditions"][0][invalid] = "達成" if invalid == "outcome" else "a" * 40
    record.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    before = record.read_bytes()
    assert _run("--output", str(record), "--rewrite-map", str(mapping)) == 1
    assert record.read_bytes() == before
    message = capsys.readouterr().err
    assert "未判定検証記録" in message
    action = message.split("次の操作:", 1)[1]
    assert "reviewed_head" not in action and "判定する" not in action and "記入" not in action


@pytest.mark.parametrize(
    "extra",
    [
        ["--wi", WI],
        ["--plan", "unused"],
        ["--list"],
        ["--select-row", "wi_conditions:1"],
        ["--updates-file", "unused"],
        ["--evidence-file", "unused"],
        ["--mode", "replace"],
        ["--results-file", "unused"],
        ["--junit-xml", "unused"],
        ["--diagnostics-file", "unused"],
        ["--list-results"],
    ],
)
def test_public_rewrite_rejects_other_operations_atomically(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    """生成・一覧・行更新・結果取込みとの混在を、別入力の読込前に拒否する。"""
    record, mapping, _ = _inputs(tmp_path)
    before = record.read_bytes()
    assert _run("--output", str(record), "--rewrite-map", str(mapping), *extra) == 1
    assert record.read_bytes() == before
    message = capsys.readouterr().err
    assert "未判定検証記録" in message and extra[0] in message
