"""読者別探索の役割文書の返却雛形と、返却検査の受理範囲の一致を確かめる。"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._agents_server.result_contract_test import _reader_fit_reason, _reader_fit_session


@pytest.mark.parametrize("multiline", [False, True])
def test_reader_fit_accepts_both_layouts_of_role_template_fields(tmp_path: pathlib.Path, multiline: bool) -> None:
    """役割文書の雛形の各欄を、同じ行に書く形と字下げして続ける形のどちらで書いても受理する。

    雛形の欄を足すか値の書き方を変えたとき、検査とのずれをこの試験が検出する。
    """
    session, target = _reader_fit_session(tmp_path)
    role = pathlib.Path(__file__).resolve().parents[2] / "share/reader-fit-review.subagent.md"
    template = role.read_text(encoding="utf-8").split("- 成果物: <絶対パス>\n", 1)[1]
    fields = [line.split(":", 1)[0].strip() for line in template.splitlines()[:4]]
    values = {"完了状態": "completed", "比較資料": "新規・全文", "判定": "指摘なし", "根拠": "本文と実装を照合済み"}
    assert set(fields) == set(values), "雛形の欄が変わったら、この試験の値と検査の受理範囲を見直す"
    lines = []
    for name in fields:
        if multiline and name != "完了状態":
            lines += [f"  {name}:", f"  - {values[name]}", "    - 補足: 下位の項目"]
        else:
            lines.append(f"  {name}: {values[name]}")
    message = f"状態: completed\n- 成果物: {target}\n" + "\n".join(lines) + "\n"
    assert _reader_fit_reason(session, message) is None


@pytest.mark.parametrize(
    ("status", "field"),
    [("completed", name) for name in ("比較資料", "根拠", "判定")]
    + [("blocked", name) for name in ("比較資料", "根拠", "続行できない理由")],
)
@pytest.mark.parametrize("empty", [False, True])
def test_reader_fit_multiline_fields(tmp_path: pathlib.Path, status: str, field: str, empty: bool) -> None:
    session, target = _reader_fit_session(tmp_path)
    lines = (["状態: completed"] if status == "completed" else []) + [f"- 成果物: {target}", f"  完了状態: {status}"]
    names = ("比較資料", "根拠", "判定" if status == "completed" else "続行できない理由")
    for name in names:
        if name == "続行できない理由":
            lines.append("  続行できない区分: owner_input")
        lines.append(f"  {name}:")
        if name != field or not empty:
            lines.append("    本人の入力が必要なため未確定")
    reason = _reader_fit_reason(session, "\n".join(lines))
    assert (reason is not None) is empty
