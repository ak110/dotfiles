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
        ("2", "module.py:10", review_bodies.issue_body("指摘", "前回から残存"), True),
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
        "own-record",
    ],
)
def test_cli_add_requires_count_and_classification_from_round_two(
    tmp_path: pathlib.Path, round_value: str, location: str, issue: str, accepted: bool
) -> None:
    """レビュー担当の指摘は類似見直し完了件数を、2以上のroundでは区分も必須とし、担当の記録は対象外にする。"""
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
    """応答の件数は整数だけを受け付け、レビュー担当の判定に使う再確認ラベルを応答本文に書かせない。"""
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
    tmp_path: pathlib.Path, path: pathlib.Path, row_id: int, round_value: str, verdict: str, reregistered: int | None = None
) -> int:
    arguments = ["review-table", "recheck", str(path), f"--row-id={row_id}", f"--round={round_value}", f"--verdict={verdict}"]
    if reregistered is not None:
        arguments.append(f"--reregistered-row-id={reregistered}")
    with pytest.raises(SystemExit) as returned:
        atk.main(arguments, home=tmp_path)
    assert isinstance(returned.value.code, int)
    return returned.value.code


def test_recheck_records_verdict_and_summary_reports_pending_previous_rows(tmp_path: pathlib.Path) -> None:
    """再確認の無い前回指摘を件数と行で返し、判定を応答のある列へ1行で記録した行を除く。記録し直すと判定が置き換わる。"""
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    table.add(path, "2", _TRACK, "module.py:20", review_bodies.issue_body("指摘Bの残り", "前回から残存"), "仕様")
    before = table.summary(path, 2)
    assert before.get("recheck_round") == 1
    assert before.get("recheck_pending_row_ids") == [1, 2]
    assert before.get("recheck_pending_count") == 2

    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 2, "2", "対応不十分", reregistered=3) == 0
    rows = table.read_rows(path)
    assert rows[0][5].endswith("\n再確認: ラウンド2 完了")
    assert rows[1][6].endswith("\n再確認: ラウンド2 対応不十分 行3")
    assert review_table_format.response_state(rows[1]) == "rejected"
    after = table.summary(path, 2)
    assert after.get("recheck_pending_count") == 0
    assert after.get("recheck_counts") == {"完了": 1, "対応不十分": 1, "未対応": 0}

    table.add(path, "2", _TRACK, "module.py:10", review_bodies.issue_body("指摘Aの残り", "前回から残存"), "仕様")
    assert _cli_recheck(tmp_path, path, 1, "2", "未対応", reregistered=4) == 0
    corrected = table.read_rows(path)[0][5]
    assert corrected.count("再確認:") == 1
    assert corrected.endswith("\n再確認: ラウンド2 未対応 行4")
    assert corrected.startswith(rows[0][5].removesuffix("\n再確認: ラウンド2 完了"))
    assert table.summary(path, 2).get("recheck_counts") == {"完了": 0, "対応不十分": 1, "未対応": 1}


@pytest.mark.parametrize(
    ("verdict", "reregistered", "classification"),
    [
        ("対応不十分", None, "前回から残存"),
        ("未対応", 9, "前回から残存"),
        ("対応不十分", 3, "新しい変更"),
        ("対応不十分", 1, "前回から残存"),
        ("完了", 3, "前回から残存"),
    ],
    ids=["missing-link", "absent-row", "other-classification", "other-round", "link-on-done"],
)
def test_recheck_requires_link_to_reregistered_finding(
    tmp_path: pathlib.Path, verdict: str, reregistered: int | None, classification: str
) -> None:
    """対応不十分・未対応は今回のroundへ前回から残存で登録し直した行の指定を必須とし、完了には指定させない。"""
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    table.add(path, "2", _TRACK, "module.py:20", review_bodies.issue_body("指摘Bの残り", classification), "仕様")
    saved = path.read_bytes()
    assert _cli_recheck(tmp_path, path, 2, "2", verdict, reregistered) != 0
    assert path.read_bytes() == saved


def test_recheck_keeps_other_round_verdicts_and_lines(tmp_path: pathlib.Path) -> None:
    """記録し直しは同じroundの判定だけを置き換え、別のroundの判定と応答の他の行を残す。"""
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    rows = table.read_rows(path)
    rows[0][5] = f"{rows[0][5]}\n再確認: 旧形式の検証メモ"
    _write_rows(path, rows)
    assert _cli_recheck(tmp_path, path, 1, "2", "完了") == 0
    assert _cli_recheck(tmp_path, path, 1, "3", "完了") == 0
    body = table.read_rows(path)[0][5]
    assert "\n再確認: 旧形式の検証メモ" in body
    assert "\n再確認: ラウンド2 完了" in body
    assert body.endswith("\n再確認: ラウンド3 完了")
    assert table.summary(path, 2).get("recheck_pending_row_ids") == [2]


def test_recheck_rejects_row_already_linked_by_another_finding(tmp_path: pathlib.Path) -> None:
    """登録し直した1行を複数の前回指摘から指させず、前回の指摘1行ごとに別の行を求める。"""
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    table.add(path, "2", _TRACK, "module.py:20", review_bodies.issue_body("指摘Bの残り", "前回から残存"), "仕様")
    assert _cli_recheck(tmp_path, path, 2, "2", "対応不十分", reregistered=3) == 0
    saved = path.read_bytes()
    assert _cli_recheck(tmp_path, path, 1, "2", "未対応", reregistered=3) != 0
    assert path.read_bytes() == saved
    assert _cli_recheck(tmp_path, path, 2, "2", "未対応", reregistered=3) == 0


def _write_rows(path: pathlib.Path, rows: list[list[str]]) -> None:
    path.write_text("".join("\t".join(json.dumps(cell, ensure_ascii=False) for cell in row) + "\n" for row in rows), "utf-8")


def test_summary_keeps_findings_sharing_one_reregistered_row_pending(tmp_path: pathlib.Path) -> None:
    """表を直接書き換えて2つの前回指摘が同じ行を指した場合も、どちらも判定として数えない。"""
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)
    table.add(path, "2", _TRACK, "module.py:20", review_bodies.issue_body("指摘Bの残り", "前回から残存"), "仕様")
    rows = table.read_rows(path)
    rows[0][5] = f"{rows[0][5]}\n再確認: ラウンド2 未対応 行3"
    rows[1][6] = f"{rows[1][6]}\n再確認: ラウンド2 対応不十分 行3"
    _write_rows(path, rows)
    assert table.summary(path, 2).get("recheck_pending_row_ids") == [1, 2]


@pytest.mark.parametrize(
    "line",
    ["再確認: ラウンド2 対応不十分", "再確認: ラウンド3 完了", "再確認:"],
    ids=["unlinked-insufficient", "later-round", "empty-label"],
)
def test_summary_keeps_rows_without_valid_verdict_pending(tmp_path: pathlib.Path, line: str) -> None:
    """登録し直した行を指さない対応不十分、照会roundより後の判定、形式外の行は判定として数えない。"""
    path = tmp_path / "plan.exec-review.tsv"
    _answered_round_one(path)

    rows = table.read_rows(path)
    rows[0][5] = f"{rows[0][5]}\n{line}"
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
    """未応答の行、現在以後のroundの行、担当の記録は表を変えずに拒否する。"""
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
    """初回のroundでは再確認の対象が無いため、再確認の項目を返さない。"""
    path = tmp_path / "plan.exec-review.tsv"
    table.add(path, "1", _TRACK, "module.py:10", review_bodies.issue_body("指摘"), "仕様")
    assert "recheck_pending_count" not in table.summary(path, 1)
