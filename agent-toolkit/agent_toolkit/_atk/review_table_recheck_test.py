"""レビュー担当の指摘本文の必須ラベル、前回指摘の再確認、未再確認の件数の契約を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table as table
from agent_toolkit._atk import review_table_format
from agent_toolkit._testing import review_bodies
from agent_toolkit._testing.review_table_support import _TRACK, _isolated_home

__all__ = ["_isolated_home"]


def _cli_add(tmp_path: pathlib.Path, path: pathlib.Path, round_value: str, location: str, issue: str) -> int:
    location_file = tmp_path / "location.txt"
    location_file.write_text(location, encoding="utf-8")
    issue_file = tmp_path / "issue.md"
    issue_file.write_text(issue, encoding="utf-8")
    with pytest.raises(SystemExit) as returned:
        atk.main(
            [
                "review-table",
                "add",
                str(path),
                f"--round={round_value}",
                f"--track={_TRACK}",
                "--level=仕様",
                f"--location-file={location_file}",
                f"--issue-file={issue_file}",
            ],
            home=tmp_path,
        )
    assert isinstance(returned.value.code, int)
    return returned.value.code


@pytest.mark.parametrize(
    ("round_value", "location", "issue", "accepted"),
    [
        ("1", "module.py:10", review_bodies.issue_body("指摘"), True),
        ("1", "module.py:10", "類似見直しの件数を書かない指摘", False),
        ("1", "module.py:10", "指摘\n類似見直し完了件数: 全部", False),
        ("1", "module.py:10", "指摘\n類似見直し完了件数: 1.5", False),
        ("2", "module.py:10", review_bodies.issue_body("指摘"), False),
        ("2", "module.py:10", review_bodies.issue_body("指摘", "不明"), False),
        ("2", "module.py:10", review_bodies.issue_body("指摘", "新しい変更ではない"), False),
        ("2", "module.py:10", review_bodies.issue_body("指摘", "新しい変更"), True),
        ("2", "module.py:10", review_bodies.issue_body("指摘", "前回から残存"), False),
        ("1", "module.py:10", review_bodies.issue_body("指摘", "前回から残存"), False),
        ("1", "module.py:10", "類似見直し完了件数: 3", False),
        ("2", "担当の記録: 統合時の競合解消", "競合を解消した", True),
    ],
    ids=[
        "round1-count",
        "missing-count",
        "non-numeric-count",
        "fractional-count",
        "round2-missing-classification",
        "round2-invalid-classification",
        "round2-negated-classification",
        "round2-classified",
        "round2-carried-only-by-recheck",
        "round1-carried-only-by-recheck",
        "labels-only",
        "own-record",
    ],
)
def test_cli_add_requires_count_and_classification_from_round_two(
    tmp_path: pathlib.Path, round_value: str, location: str, issue: str, accepted: bool
) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    table.init(path)
    code = _cli_add(tmp_path, path, round_value, location, issue)
    assert (code == 0) is accepted
    assert len(table.read_rows(path)) == (1 if accepted else 0)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (review_bodies.response_body("直した").replace("類似見直し完了件数: 3", "類似見直し完了件数: 1.5"), ""),
        (review_bodies.response_body("直した") + "\n再確認:", ""),
        ("", review_bodies.reason_body("README.md:10の既存契約") + "\n再確認: ラウンド2 完了"),
    ],
    ids=["fractional-count", "response-recheck-label", "reason-recheck-label"],
)
def test_respond_rejects_fractional_count_and_recheck_label(tmp_path: pathlib.Path, response: str, reason: str) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "1", _TRACK, "module.py:10", review_bodies.issue_body("指摘A"), "仕様")
    saved = path.read_bytes()
    with pytest.raises(ValueError):
        table.respond(path, "", "", "", "", response, reason, row_id=1)
    assert path.read_bytes() == saved


def _answered_round_one(path: pathlib.Path) -> None:
    table.add(path, "1", _TRACK, "module.py:10", review_bodies.issue_body("指摘A"), "仕様")
    table.add(path, "1", _TRACK, "module.py:20", review_bodies.issue_body("指摘B"), "仕様")
    table.respond(path, "", "", "", "", review_bodies.response_body("直した"), "", row_id=1)
    table.respond(path, "", "", "", "", "", review_bodies.reason_body("README.md:10の既存契約"), row_id=2)


def _cli_recheck(
    tmp_path: pathlib.Path, path: pathlib.Path, row_id: int, round_value: str, verdict: str, issue: str | None = None
) -> int:
    arguments = ["review-table", "recheck", str(path), f"--row-id={row_id}", f"--round={round_value}", f"--verdict={verdict}"]
    if issue is not None:
        issue_file = tmp_path / "remaining.md"
        issue_file.write_text(issue, encoding="utf-8")
        arguments.append(f"--issue-file={issue_file}")
    with pytest.raises(SystemExit) as returned:
        atk.main(arguments, home=tmp_path)
    assert isinstance(returned.value.code, int)
    return returned.value.code


def test_recheck_registers_carried_finding_and_summary_counts_verdicts(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    before = table.summary(path, 2)
    assert before.get("recheck_round") == 1
    assert before.get("recheck_pending_row_ids") == [1, 2]
    assert before.get("recheck_pending_count") == 2

    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 2, "2", "対応不十分", review_bodies.issue_body("指摘Bの残り")) == 0
    output = capsys.readouterr().out
    assert "再確認: ラウンド2 完了" in output
    assert "再確認: ラウンド2 対応不十分 行3" in output
    assert "<!--" not in output
    rows = table.read_rows(path)
    assert review_table_format.recheck_entries(rows[0][5], 1) == [(2, "完了", None)]
    assert review_table_format.recheck_entries(rows[1][6], 2) == [(2, "対応不十分", 3)]
    assert review_table_format.response_state(rows[1]) == "rejected"
    carried = rows[2]
    assert [carried[index] for index in (0, 1, 2, 4, 5, 6)] == ["2", _TRACK, rows[1][2], rows[1][4], "", ""]
    assert review_table_format.classification(carried[3]) == "前回から残存"
    assert review_table_format.carried_origin(carried[3]) == 2
    assert "指摘Bの残り" in carried[3]
    after = table.summary(path, 2)
    assert after.get("recheck_pending_count") == 0
    assert after.get("recheck_counts") == {"完了": 1, "対応不十分": 1, "未対応": 0}
    assert after.get("unanswered_count") == 1
    with pytest.raises(SystemExit) as returned:
        atk.main(["review-table", "show", str(path), "--row-id=1", "--field=response"], home=tmp_path)
    assert returned.value.code == 0
    assert capsys.readouterr().out == review_bodies.response_body("直した")


def test_recheck_correction_appends_and_reuses_or_withdraws_carried_row(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 1, "2", "未対応", review_bodies.issue_body("指摘Aの残り")) == 0
    first = table.read_rows(path)[0][5]
    assert review_table_format.recheck_entries(first, 1) == [(2, "完了", None), (2, "未対応", 3)]
    assert table.summary(path, 2).get("recheck_counts") == {"完了": 0, "対応不十分": 0, "未対応": 1}

    assert _cli_recheck(tmp_path, path, 1, "2", "対応不十分", review_bodies.issue_body("指摘Aの残りを書き直した")) == 0
    rows = table.read_rows(path)
    assert len(rows) == 3
    assert "指摘Aの残りを書き直した" in rows[2][3]

    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    rows = table.read_rows(path)
    assert review_table_format.response_state(rows[2]) == "rejected"
    assert review_table_format.rejection_reason_class(rows[2][6]) == "レビュー担当の前提の誤り・撤回"
    withdrawn = table.summary(path, 2)
    assert withdrawn.get("recheck_counts") == {"完了": 1, "対応不十分": 0, "未対応": 0}
    assert withdrawn.get("unanswered_count") == 0

    assert _cli_recheck(tmp_path, path, 1, "2", "未対応", review_bodies.issue_body("指摘Aの残り")) == 0
    rows = table.read_rows(path)
    assert len(rows) == 3
    assert review_table_format.response_state(rows[2]) == "unanswered"
    assert table.summary(path, 2).get("recheck_counts") == {"完了": 0, "対応不十分": 0, "未対応": 1}

    table.respond(path, "", "", "", "", review_bodies.response_body("残りも直した"), "", row_id=3)
    saved = path.read_bytes()
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") != 0
    assert path.read_bytes() == saved


@pytest.mark.parametrize(
    ("verdict", "issue"),
    [
        ("対応不十分", None),
        ("未対応", "残る問題だけを書いた本文"),
        ("対応不十分", review_bodies.issue_body("指摘Bの残り", "前回から残存")),
        ("対応不十分", review_bodies.issue_body("指摘Bの残り") + "\n元の指摘: 行1"),
        ("完了", review_bodies.issue_body("指摘Bの残り")),
        ("未対応", "類似見直し完了件数: 1\n"),
    ],
    ids=["missing-issue", "missing-count", "classification-line", "origin-line", "issue-on-done", "labels-only"],
)
def test_recheck_requires_remaining_issue_only_for_unresolved(tmp_path: pathlib.Path, verdict: str, issue: str | None) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    saved = path.read_bytes()
    assert _cli_recheck(tmp_path, path, 2, "2", verdict, issue) != 0
    assert path.read_bytes() == saved


def test_recheck_appends_and_keeps_other_lines(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    rows = table.read_rows(path)
    rows[0][5] = f"{rows[0][5]}\n再確認: 旧形式の検証メモ\n再確認: ラウンド2 対応不十分"
    _write_rows(path, rows)
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 1, "2", "未対応", review_bodies.issue_body("指摘Aの残り")) == 0
    assert _cli_recheck(tmp_path, path, 1, "3", "完了") == 0
    body = table.read_rows(path)[0][5]
    assert [line.partition(" <!--")[0] for line in review_table_format.recheck_lines(body)] == [
        "再確認: 旧形式の検証メモ",
        "再確認: ラウンド2 対応不十分",
        "再確認: ラウンド2 完了",
        "再確認: ラウンド2 未対応 行3",
        "再確認: ラウンド3 完了",
    ]
    assert table.summary(path, 2).get("recheck_counts") == {"完了": 0, "対応不十分": 0, "未対応": 1}
    assert table.summary(path, 3).get("recheck_pending_row_ids") == [3]


def test_recheck_rejects_round_before_current_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 2, "2", "完了") == 0
    table.add(path, "3", _TRACK, "module.py:30", review_bodies.issue_body("指摘C", "新しい変更"), "仕様")
    table.respond(path, "", "", "", "", review_bodies.response_body("直した"), "", row_id=3)
    saved = path.read_bytes()
    assert _cli_recheck(tmp_path, path, 1, "2", "未対応", review_bodies.issue_body("指摘Aの残り")) != 0
    assert path.read_bytes() == saved
    assert _cli_recheck(tmp_path, path, 1, "4", "未対応", review_bodies.issue_body("指摘Aの残り")) == 0
    state = table.summary(path, 4)
    assert state.get("unanswered_count") == 1
    assert state.get("recheck_pending_row_ids") == [3]


def test_recheck_and_add_reject_round_before_verdict_only_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 2, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 1, "3", "完了") == 0
    assert table.summary(path, 3).get("recheck_pending_count") == 0
    saved = path.read_bytes()
    assert _cli_recheck(tmp_path, path, 1, "2", "未対応", review_bodies.issue_body("指摘Aの残り")) != 0
    assert _cli_add(tmp_path, path, "2", "module.py:30", review_bodies.issue_body("指摘C", "新しい変更")) != 0
    assert path.read_bytes() == saved
    assert _cli_add(tmp_path, path, "3", "module.py:30", review_bodies.issue_body("指摘C", "新しい変更")) == 0
    assert table.summary(path, 3).get("unanswered_count") == 1


def test_respond_rejects_changes_after_recheck_verdict(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    saved = path.read_bytes()
    with pytest.raises(ValueError):
        table.respond(path, "", "", "", "", review_bodies.response_body("書き直した"), "", row_id=1)
    with pytest.raises(ValueError):
        table.respond(path, "", "", "", "", "", review_bodies.reason_body("README.md:10の既存契約"), row_id=1)
    assert path.read_bytes() == saved
    inode = path.stat().st_ino
    assert table.respond(path, "", "", "", "", review_bodies.response_body("直した"), "", row_id=1) == 0
    assert path.read_bytes() == saved
    assert path.stat().st_ino == inode
    assert table.summary(path, 2).get("recheck_counts") == {"完了": 1, "対応不十分": 0, "未対応": 0}


def test_lines_outside_verdict_format_neither_freeze_response_nor_advance_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    rows = table.read_rows(path)
    kept = "再確認: 旧形式の検証メモ\n再確認: ラウンド5 対応不十分\n再確認: ラウンド9 未対応 行2"
    rows[0][5] = f"{rows[0][5]}\n{kept}"
    _write_rows(path, rows)
    assert table.respond(path, "", "", "", "", review_bodies.response_body("書き直した"), "", row_id=1) == 0
    body = table.read_rows(path)[0][5]
    assert review_table_format.response_content(body) == review_bodies.response_body("書き直した")
    assert review_table_format.recheck_lines(body) == kept.splitlines()
    assert _cli_add(tmp_path, path, "2", "module.py:30", review_bodies.issue_body("指摘C", "新しい変更")) == 0


def _write_rows(path: pathlib.Path, rows: list[list[str]]) -> None:
    path.write_text("".join("\t".join(json.dumps(cell, ensure_ascii=False) for cell in row) + "\n" for row in rows), "utf-8")


def test_summary_reads_valid_verdict_in_legacy_table(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    rows = table.read_rows(path)
    rows[0][5] = review_bodies.response_body("直した") + "\n再確認: ラウンド2 完了"
    _write_rows(path, rows)

    state = table.summary(path, 2)
    assert state.get("recheck_pending_row_ids") == [2]
    assert state.get("recheck_counts") == {"完了": 1, "対応不十分": 0, "未対応": 0}


@pytest.mark.parametrize("edit", ["changed-response", "copied-verdict", "unsigned-verdict"])
def test_summary_requires_verdict_for_current_response_and_row(tmp_path: pathlib.Path, capsys, edit: str) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    if edit != "unsigned-verdict":
        assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    rows = table.read_rows(path)

    if edit == "changed-response":
        rows[0][5] = rows[0][5].replace("直した", "違う対処に差し替えた")
        pending = [1, 2]
    elif edit == "copied-verdict":
        rows[1][5:] = [rows[0][5], ""]
        pending = [2]
    else:
        rows[0][5] += "\n再確認: ラウンド2 完了"
        pending = [1, 2]
    _write_rows(path, rows)

    state = table.summary(path, 2)
    assert state.get("recheck_pending_row_ids") == pending
    assert state.get("recheck_counts") == {"完了": 2 - len(pending), "対応不十分": 0, "未対応": 0}
    capsys.readouterr()
    for row_id in pending:
        assert table.show(path, row_id=row_id) == 0
        shown = capsys.readouterr().out
        assert "再確認:" not in shown and "review-response-v2" not in shown


def test_recording_the_original_text_again_does_not_revive_old_verdict(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    rows = table.read_rows(path)
    rows[0][5] = rows[0][5].replace("直した", "異なる本文に差し替えた")
    _write_rows(path, rows)
    assert table.summary(path, 2).get("recheck_pending_row_ids") == [1, 2]
    assert table.respond(path, "", "", "", "", review_bodies.response_body("直した"), "", row_id=1) == 0
    assert table.summary(path, 2).get("recheck_pending_row_ids") == [1, 2]
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert table.summary(path, 2).get("recheck_pending_row_ids") == [2]


@pytest.mark.parametrize(
    "line",
    [
        "再確認: ラウンド2 対応不十分",
        "再確認: ラウンド3 完了",
        "再確認:",
        "再確認: ラウンド2 完了 行3",
        "再確認: ラウンド2 未対応 行3",
    ],
    ids=["unlinked-insufficient", "later-round", "empty-label", "done-with-row", "row-of-other-origin"],
)
def test_summary_keeps_rows_without_valid_verdict_pending(tmp_path: pathlib.Path, line: str) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    carried_from_b = review_table_format.carried_issue(review_bodies.issue_body("指摘Bの残り"), 2)
    table.add(path, "2", _TRACK, "module.py:20", carried_from_b, "仕様")

    rows = table.read_rows(path)
    receipt = review_table_format.bind_recheck(line, 1, rows[0][5])
    rows[0][5] += f"\n{receipt}"
    _write_rows(path, rows)
    state = table.summary(path, 2)
    assert state.get("recheck_pending_row_ids") == [1, 2]
    assert state.get("recheck_counts") == {"完了": 0, "対応不十分": 0, "未対応": 0}


@pytest.mark.parametrize(
    ("row_id", "round_value"),
    [(3, "2"), (1, "1"), (4, "2"), (5, "2")],
    ids=["unanswered", "same-round", "own-record", "current-round"],
)
def test_recheck_rejects_rows_outside_previous_answered_findings(tmp_path: pathlib.Path, row_id: int, round_value: str) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    table.add(path, "1", _TRACK, "module.py:30", review_bodies.issue_body("指摘C"), "仕様")
    table.add(path, "1", _TRACK, "担当の記録: 統合時の競合解消", "競合を解消した", "詳細")
    table.respond(path, "", "", "", "", review_bodies.response_body("記録した"), "", row_id=4)
    table.add(path, "2", _TRACK, "module.py:40", review_bodies.issue_body("指摘D", "新しい変更"), "仕様")
    table.respond(path, "", "", "", "", review_bodies.response_body("直した"), "", row_id=5)
    saved = path.read_bytes()
    assert _cli_recheck(tmp_path, path, row_id, round_value, "完了") != 0
    assert path.read_bytes() == saved


def test_summary_without_previous_round_omits_recheck_fields(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "1", _TRACK, "module.py:10", review_bodies.issue_body("指摘"), "仕様")
    assert "recheck_pending_count" not in table.summary(path, 1)


def test_add_help_items_example_is_accepted_as_displayed(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    with pytest.raises(SystemExit):
        atk.main(["review-table", "add", "--help"], home=tmp_path)
    examples = [line.strip() for line in capsys.readouterr().out.splitlines() if line.startswith("  [{")]
    assert len(examples) == 1
    items_file = tmp_path / "items.json"
    items_file.write_text(examples[0], encoding="utf-8")
    path = tmp_path / "plan.exec-review.tsv"
    with pytest.raises(SystemExit) as returned:
        atk.main(
            ["review-table", "add", str(path), "--round=2", f"--track={_TRACK}", f"--items-file={items_file}"], home=tmp_path
        )
    assert returned.value.code == 0
    assert len(table.read_rows(path)) == 1
