"""review table showの契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import shlex

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table as table
from agent_toolkit._common import next_action
from agent_toolkit._testing import review_bodies
from agent_toolkit._testing.review_table_support import _COMPAT_TRACK, _TRACK, _isolated_home

__all__ = ["_isolated_home"]


def test_show_can_filter_by_track(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "統合後指摘")
    table.add(path, "1", "independent", "module.py:10", "盲検指摘")
    capsys.readouterr()

    assert table.show(path, track=_TRACK) == 0
    output = capsys.readouterr().out
    assert "統合後指摘" in output
    assert "盲検指摘" not in output


def test_show_filters_rows_by_round(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """指定ラウンドだけを保存順で表示し、存在しないラウンドは空出力にする。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "1件目")
    table.add(path, "2", _TRACK, "module.py:20", "2件目")
    table.add(path, "1", _TRACK, "module.py:30", "3件目")
    capsys.readouterr()

    assert table.show(path, round_value=1) == 0
    output = capsys.readouterr().out
    assert output.index("1件目") < output.index("3件目")
    assert "2件目" not in output

    assert table.show(path, round_value=99) == 0
    assert capsys.readouterr().out == ""


def test_show_combines_round_track_and_jsonl(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """ラウンド・track・JSON Linesの指定を同時に適用する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "対象")
    table.add(path, "2", _TRACK, "module.py:20", "別ラウンド")
    table.add(path, "1", "independent", "module.py:30", "別track")
    capsys.readouterr()

    assert table.show(path, track=_TRACK, output_format="jsonl", round_value=1) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["issue"] for row in rows] == ["対象"]


def test_public_summary_counts_current_unanswered_rows_without_waiting_for_responses(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """過去の応答、現在の再指摘と他trackを分け、同じ表から返却件数を得る。"""
    path = tmp_path / "plan.exec-review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "再指摘")
    table.respond(path, "1", _TRACK, "module.py:10", "再指摘", review_bodies.response_body("修正済み"), "")
    table.add(path, "1", _TRACK, "module.py:11", "過去の未応答")
    table.add(path, "2", _TRACK, "module.py:10", "再指摘")
    table.add(path, "2", _TRACK, "module.py:20", "対応不要")
    table.respond(path, "2", _TRACK, "module.py:20", "対応不要", "", review_bodies.reason_body("確定要件と一致する"))
    table.add(path, "2", "independent", "module.py:30", "別track")
    capsys.readouterr()
    before = path.read_bytes()
    with pytest.raises(SystemExit) as returned:
        atk.main(["review-table", "show", str(path), "--format=summary", "--round=2"], home=tmp_path)
    assert returned.value.code == 0
    result = capsys.readouterr()
    assert not result.err
    summary = json.loads(result.out)
    assert {key: summary[key] for key in ("round", "track", "total_count", "unanswered_count", "answered_count")} == {
        "round": 2,
        "track": "exec-review",
        "total_count": 2,
        "unanswered_count": 1,
        "answered_count": 1,
    }
    assert summary["previous_findings"] == [{"round": 1, "row_ids": [1, 2]}]
    assert path.read_bytes() == before


def test_public_summary_returns_zero_counts_for_no_current_rows(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "plan.exec-review.tsv"
    table.init(path)
    capsys.readouterr()
    with pytest.raises(SystemExit) as returned:
        atk.main(["review-table", "show", str(path), "--format=summary", "--round=3"], home=tmp_path)
    assert returned.value.code == 0
    assert json.loads(capsys.readouterr().out) == {
        "round": 3,
        "track": "exec-review",
        "total_count": 0,
        "unanswered_count": 0,
        "answered_count": 0,
    }


def test_summary_requires_current_round(tmp_path: pathlib.Path) -> None:
    with pytest.raises(next_action.ActionableError, match="roundが必要"):
        table.show(tmp_path / "plan.exec-review.tsv", output_format="summary")


@pytest.mark.parametrize("track", (_TRACK, _COMPAT_TRACK))
def test_show_filters_compat_track_without_rewriting_raw_tsv(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    track: str,
) -> None:
    """保存済み旧trackは新旧どちらの指定でも限定し、raw TSVを変更しない。"""
    path = tmp_path / "review.tsv"
    cells = ("1", _COMPAT_TRACK, "module.py:10", "旧表の指摘", "詳細", "", "", "")
    raw = "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n"
    path.write_text(raw, encoding="utf-8")

    assert table.show(path, track=track) == 0
    assert capsys.readouterr().out == f"1\t{raw}"
    assert path.read_text(encoding="utf-8") == raw


def test_respond_matches_compat_track_and_rewrites_all_rows_canonically(tmp_path: pathlib.Path) -> None:
    """応答更新は保存済み旧trackへ一致し、同時に全行を正規値へ移行する。"""
    path = tmp_path / "review.tsv"
    rows = (
        ("1", _COMPAT_TRACK, "module.py:10", "更新対象", "詳細", "", "", ""),
        ("1", _COMPAT_TRACK, "module.py:20", "別の指摘", "詳細", "対応済み", ""),
    )
    path.write_text(
        "".join("\t".join(json.dumps(value, ensure_ascii=False) for value in row) + "\n" for row in rows),
        encoding="utf-8",
    )

    assert table.respond(path, "1", _TRACK, "module.py:10", "更新対象", review_bodies.response_body("修正した"), "") == 0
    saved = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row[1] for row in saved] == [_TRACK, _TRACK]
    assert saved[0][5:] == [review_bodies.response_body("修正した"), ""]


def test_add_normalizes_compat_track_before_saving(tmp_path: pathlib.Path) -> None:
    """追加時の旧track入力は正規値へ変換して保存する。"""
    path = tmp_path / "review.tsv"

    assert table.add(path, "1", _COMPAT_TRACK, "module.py:10", "互換入力") == 0

    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").rstrip().split("\t")]
    assert row[1] == _TRACK


def test_show_jsonl_decodes_control_characters_and_quotes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """jsonl出力は制御文字と引用符を含むセルを保存前の本文へデコードする。"""
    path = tmp_path / "review.tsv"
    issue = '改行\nタブ\t二重引用符"と`backtick`'
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", issue)
    capsys.readouterr()

    assert table.show(path, output_format="jsonl") == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    expected = ["1", _TRACK, "module.py:10", issue, "詳細", "", ""]
    assert rows == [{"row-id": 1, **dict(zip(table.COLUMNS, expected, strict=True))}]


def test_show_jsonl_supplies_empty_level_for_legacy_row(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存済み7列行のjsonl出力は空のlevelを補う。"""
    path = tmp_path / "review.tsv"
    legacy = ("1", _TRACK, "位置", "指摘", "", "", "")
    path.write_text("\t".join(json.dumps(value, ensure_ascii=False) for value in legacy) + "\n", encoding="utf-8")

    assert table.show(path, output_format="jsonl") == 0
    row = json.loads(capsys.readouterr().out)
    assert row["level"] == ""
    assert list(row) == ["row-id", *table.COLUMNS]


def test_show_jsonl_filters_decoded_rows_by_track(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """jsonl出力でも指定したtrackのデコード済み行だけを表示する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "統合後指摘")
    table.add(path, "1", "independent", "module.py:20", "盲検指摘")
    capsys.readouterr()

    assert table.show(path, track=_TRACK, output_format="jsonl") == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["issue"] for row in rows] == ["統合後指摘"]


def test_legacy_seven_columns_are_read_and_rewritten_as_eight_columns(tmp_path: pathlib.Path) -> None:
    """保存済み7列行は空のlevelを補って読み、更新時に8列へ移行する。"""
    path = tmp_path / "review.tsv"
    legacy = ("1", _TRACK, "位置", "指摘", "", "", "")
    path.write_text("\t".join(json.dumps(value, ensure_ascii=False) for value in legacy) + "\n", encoding="utf-8")

    assert table.validate(path, require_responses=False) == 0
    table.respond(path, "1", _TRACK, "位置", "指摘", review_bodies.response_body("修正した"), "")

    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", _TRACK, "位置", "指摘", "", review_bodies.response_body("修正した"), ""]


def test_show_preserves_legacy_seven_columns_with_and_without_track_filter(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """showは保存済み7列行を変換せず、track限定でもraw TSVを保持する。"""
    path = tmp_path / "review.tsv"
    rows = [
        ("1", _TRACK, "位置", "指摘", "", "", ""),
        ("1", "plan-review", "別位置", "別指摘", "", "", ""),
    ]
    raw = "".join("\t".join(json.dumps(value, ensure_ascii=False) for value in row) + "\n" for row in rows)
    path.write_text(raw, encoding="utf-8")

    assert table.show(path) == 0
    assert capsys.readouterr().out == "".join(
        f"{row_id}\t{line}" for row_id, line in enumerate(raw.splitlines(keepends=True), start=1)
    )
    assert table.show(path, track=_TRACK) == 0
    assert capsys.readouterr().out == f"1\t{raw.splitlines(keepends=True)[0]}"
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("track", (None, _TRACK))
@pytest.mark.parametrize(
    ("cells", "expected_error"),
    (
        (("1", _TRACK, "位置", "指摘", "", ""), "列数が7ではない"),
        (("1", _TRACK, "位置", None, "仕様", "", "", ""), "列番号 4がJSON文字列ではない"),
    ),
)
def test_show_rejects_malformed_rows_before_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    track: str | None,
    cells: tuple[str | None, ...],
    expected_error: str,
) -> None:
    """showはtrack限定の有無にかかわらず、全行・全セルを出力前に検証する。"""
    path = tmp_path / "review.tsv"
    raw = "\t".join("broken" if value is None else json.dumps(value, ensure_ascii=False) for value in cells) + "\n"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        table.show(path, track=track)

    assert not capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("level", table.LEVEL_VALUES)
def test_add_accepts_all_level_values_and_respond_preserves_level(tmp_path: pathlib.Path, level: str) -> None:
    """追加は全指摘レベルを受理し、応答更新はlevelを保持する。"""
    path = tmp_path / f"{level}.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "位置", "指摘", level)
    table.respond(path, "1", _TRACK, "位置", "指摘", review_bodies.response_body("修正した"), "")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row[4] == level


def test_validate_rejects_invalid_level(tmp_path: pathlib.Path) -> None:
    """8列行のlevelは空または正規値だけを受理する。"""
    path = tmp_path / "review.tsv"
    cells = ("1", _TRACK, "位置", "指摘", "重大", "修正", "")
    path.write_text("\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="levelが正規値ではない"):
        table.validate(path)


@pytest.mark.parametrize("field", ("response", "no-response-reason"))
@pytest.mark.parametrize("layout", ("current", "legacy-seven", "legacy-eight"))
def test_public_show_selected_body_preserves_full_decoded_text(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], field: str, layout: str
) -> None:
    """現行・旧形式の指定行の本文を、空行を除く保存順IDで全文取得する。"""
    path = tmp_path / "review.exec-review.tsv"
    body = '  日本語の応答\n"引用符"\t逆斜線\\と`記号`\n\n'
    first = ["1", _TRACK, "先の行", "別指摘", "詳細", "別の本文", ""]
    second = ["2", _COMPAT_TRACK, "後の行", "対象指摘", "仕様", "", ""]
    second[table.COLUMNS.index(field)] = body
    if layout == "legacy-seven":
        second[4] = "yes" if field == "response" else "no"
    elif layout == "legacy-eight":
        second.insert(5, "yes" if field == "response" else "no")
    raw = "\n" + "\n\n".join("\t".join(json.dumps(cell) for cell in row) for row in (first, second)) + "\n"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "show", str(path), "--row-id=2", f"--field={field}"], home=tmp_path)

    assert result.value.code == 0
    output = capsys.readouterr()
    assert output.out == body
    assert not output.err
    assert path.read_text(encoding="utf-8") == raw
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "show", str(path), "--row-id=2", "--format=jsonl"], home=tmp_path)
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)["row-id"] == 2


@pytest.mark.parametrize(
    "options",
    (
        ["--field=response"],
        ["--row-id=1", "--field=response", "--format=jsonl"],
        ["--row-id=1", "--format=summary", "--round=1"],
        ["--row-id=0"],
        ["--row-id=bad"],
        ["--row-id=2"],
        ["--row-id=1", "--field=response", "--round=2"],
    ),
)
def test_public_show_rejects_invalid_body_selection(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], options: list[str]
) -> None:
    """本文を特定できない指定は診断を返し、本文を出力しない。"""
    path = tmp_path / "review.exec-review.tsv"
    table.add(path, "1", _TRACK, "場所", "指摘")
    capsys.readouterr()
    before = path.read_bytes()
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "show", str(path), *options], home=tmp_path)
    assert result.value.code in (1, 2)
    output = capsys.readouterr()
    assert not output.out
    assert "次の操作" in output.err
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ("plan.exec-review.tsv", "ci-abc123.exec-review.tsv"))
def test_public_summary_guides_history_without_deciding_issue_identity(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    """箇所・文言・発生条件が変わる指摘でも、同trackの過去指摘と判定先へ到達できる。"""
    path = tmp_path / "space dir" / name
    table.add(path, "1", _TRACK, "module.py:10", "完成条件4: 取得失敗時に記録へ進む")
    table.respond(path, "", "", "", "", review_bodies.response_body("初回修正"), "", row_id=1)
    table.add(path, "2", "independent", "module.py:11", "他trackの指摘")
    table.add(path, "2", _TRACK, "module.py:20", "完成条件4: 入力形式が異なる条件で失敗する")
    table.add(path, "3", _TRACK, "other.py:30", "取得失敗の扱いに不足がある")
    capsys.readouterr()
    before = path.read_bytes()
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "show", str(path), "--round=3", "--format=summary"], home=tmp_path)
    assert result.value.code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["previous_findings"] == [{"round": 1, "row_ids": [1]}, {"round": 2, "row_ids": [3]}]
    assert summary["total_count"] == summary["unanswered_count"] == 1
    assert summary["answered_count"] == 0
    assert "違反規定と発生条件" in summary["repetition_guidance"]
    assert "反復原因の判定" in summary["repetition_guidance"]
    assert "同じ指摘の反復" in summary["repetition_guidance"]
    with pytest.raises(SystemExit) as result:
        atk.main(shlex.split(summary["history_command"])[1:], home=tmp_path)
    assert result.value.code == 0
    history = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["row-id"] for row in history] == [1, 3, 4]
    assert history[0]["response"] == review_bodies.response_body("初回修正")
    assert all(len(line.split("\t")) == 7 for line in path.read_text(encoding="utf-8").splitlines())
    assert path.read_bytes() == before


@pytest.mark.parametrize("case", ("initial", "no-current", "other-track"))
def test_public_summary_omits_history_when_comparison_has_no_input(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """初回・現在の指摘なし・同trackの過去指摘なしでは件数だけを返す。"""
    path = tmp_path / "review.exec-review.tsv"
    table.add(path, "1", "independent" if case == "other-track" else _TRACK, "以前", "指摘")
    if case == "other-track":
        table.add(path, "2", _TRACK, "今回", "指摘")
    capsys.readouterr()
    current_round = 1 if case == "initial" else 2
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "show", str(path), f"--round={current_round}", "--format=summary"], home=tmp_path)
    assert result.value.code == 0
    summary = json.loads(capsys.readouterr().out)
    assert set(summary) == {"round", "track", "total_count", "unanswered_count", "answered_count"}
    assert summary["total_count"] == summary["unanswered_count"] == (0 if case == "no-current" else 1)
