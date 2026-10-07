"""`--user-events`の保存後に表示する発話の一覧のテスト。"""

import json
import pathlib

import pytest

from agent_toolkit._atk import user_events_summary


def test_lists_user_rows_with_record_position_and_head(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`user`の行だけを保存ファイルと同じ順で示し、改行を空白へ置き換えた先頭60文字を超える本文は`…`で切り詰める。

    コロンを含む`record`を`record=`の形で示さないと、呼び出し元は記録位置を区切りと区別して取り出せない。
    """
    long_text = "一" * 30 + "\n" + "二" * 40
    rows = [
        {"kind": "user", "text": "短い発話", "line": 9, "record": "claude:aaa"},
        {"kind": "assistant", "text": "応答", "line": 10, "record": "claude:aaa"},
        {"kind": "user", "text": long_text, "line": 111, "record": "claude:aaa"},
    ]
    saved = tmp_path / "output.txt"
    saved.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows) + "読めない行\n", encoding="utf-8")

    user_events_summary.summarize_saved_user_events(saved)

    assert capsys.readouterr().out.splitlines() == [
        "発話: record=claude:aaa line=9 短い発話",
        "発話: record=claude:aaa line=111 " + "一" * 30 + " " + "二" * 29 + "…",
    ]


def test_reports_zero_when_no_user_rows(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`user`の行が無い保存ファイルでは件数0を1行で示し、呼び出し元が保存先を開かずに空と判別できるようにする。"""
    saved = tmp_path / "output.txt"
    saved.write_text(json.dumps({"kind": "warning", "text": "一致なし"}, ensure_ascii=False) + "\n", encoding="utf-8")

    user_events_summary.summarize_saved_user_events(saved)

    assert capsys.readouterr().out == "発話: 0件\n"
