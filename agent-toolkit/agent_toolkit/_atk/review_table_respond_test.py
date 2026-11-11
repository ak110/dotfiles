"""review table respondの契約を検証する。"""

from __future__ import annotations

import json
import pathlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_toolkit._atk import review_table as table
from agent_toolkit._common import next_action
from agent_toolkit._testing import review_bodies
from agent_toolkit._testing.review_table_support import _TRACK, _isolated_home, _parser

__all__ = ["_isolated_home"]


def test_cell_files_preserve_issue_and_supply_responses(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "review.tsv"
    location = "module.py:10 `handler`\n"
    issue = '"引用"と`backtick`を含む\n複数行の$本文\n'
    response = f"  {review_bodies.response_body('修正で"引用"と`backtick`を保持する')}\n"
    reason = f"  {review_bodies.reason_body('$仕様の対象外')}\n"
    location_file = tmp_path / "location.txt"
    issue_file = tmp_path / "issue.txt"
    response_file = tmp_path / "response.txt"
    reason_file = tmp_path / "reason.txt"
    location_file.write_text(location, encoding="utf-8")
    issue_file.write_text(issue, encoding="utf-8")
    response_file.write_text(response, encoding="utf-8")
    reason_file.write_text(reason, encoding="utf-8")
    table.init(path)

    add_args = _parser().parse_args(
        [
            "review-table",
            "add",
            str(path),
            "--round=1",
            f"--track={_TRACK}",
            "--level=詳細",
            "--location-file",
            str(location_file),
            "--issue-file",
            str(issue_file),
        ]
    )
    assert table.dispatch(add_args) == 0
    assert table.show(path) == 0
    stored = [[json.loads(cell) for cell in line.split("\t")[1:]] for line in capsys.readouterr().out.splitlines()[-1:]][0]
    assert stored[:4] == ["1", _TRACK, location, issue]

    respond_args = _parser().parse_args(
        [
            "review-table",
            "respond",
            str(path),
            "--round=1",
            "--issue-file",
            str(issue_file),
            "--response-file",
            str(response_file),
        ]
    )
    assert table.dispatch(respond_args) == 0

    table.add(path, "2", _TRACK, "README.md", "対象外")
    no_response_args = _parser().parse_args(
        [
            "review-table",
            "respond",
            str(path),
            "--round=2",
            "--no-response-reason-file",
            str(reason_file),
        ]
    )
    assert table.dispatch(no_response_args) == 0
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == ["1", _TRACK, location, issue, "詳細", response.strip(), ""]
    assert rows[1] == ["2", _TRACK, "README.md", "対象外", "詳細", "", reason.strip()]


@pytest.mark.parametrize(
    "arguments",
    [
        ["review-table", "add", "review.tsv", "--round=1", f"--track={_TRACK}", "--level=詳細", "位置", "指摘"],
        ["review-table", "add", "review.tsv", "--round=1", f"--track={_TRACK}", "--level=詳細", "--location=位置"],
        ["review-table", "add", "review.tsv", "--round=1", f"--track={_TRACK}", "--level=詳細", "--issue=指摘"],
        ["review-table", "respond", "review.tsv", "--round=1", "--response=対応"],
        ["review-table", "respond", "review.tsv", "--round=1", "--no-response-reason=対象外"],
    ],
)
def test_cell_text_arguments_are_rejected(arguments: list[str]) -> None:
    """セル本文をコマンドライン文字列で渡す旧受理形式を拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(arguments)
    assert exc_info.value.code == 2


def test_unreadable_cell_file_does_not_update_table(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    location_file = tmp_path / "location.txt"
    location_file.write_text("位置", encoding="utf-8")
    table.init(path)
    before = path.read_text(encoding="utf-8")
    args = _parser().parse_args(
        [
            "review-table",
            "add",
            str(path),
            "--round=1",
            f"--track={_TRACK}",
            "--level=詳細",
            "--location-file",
            str(location_file),
            "--issue-file",
            str(tmp_path / "missing.txt"),
        ]
    )

    with pytest.raises(ValueError, match="--issue-fileの読み込みに失敗した"):
        table.dispatch(args)
    assert path.read_text(encoding="utf-8") == before


def test_respond_resolves_by_partial_key(tmp_path: pathlib.Path) -> None:
    """`round`・`track`だけの指定でも一意に定まれば応答を更新できる。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "指摘A")
    table.add(path, "2", _TRACK, "README.md", "指摘B")
    assert table.respond(path, "1", _TRACK, "", "", review_bodies.response_body("対応した"), "") == 0
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row == ["1", _TRACK, "module.py:10", "指摘A", "詳細", review_bodies.response_body("対応した"), ""]


def test_respond_rejects_multiple_matches_and_keeps_table_unchanged(tmp_path: pathlib.Path) -> None:
    """部分キーが複数行へ一致する場合は拒否し、表を変更しない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "指摘A")
    table.add(path, "2", _TRACK, "module.py:20", "指摘B")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="一意に解決できない: 2件"):
        table.respond(path, "", _TRACK, "", "", review_bodies.response_body("対応した"), "")
    assert path.read_text(encoding="utf-8") == before


def test_respond_reports_decoded_candidates_when_no_partial_key_matches(tmp_path: pathlib.Path) -> None:
    """一致しない部分キーへ、デコード済み候補行を示して再指定を可能にする。"""
    path = tmp_path / "review.tsv"
    issue = '本文に"引用"を含む指摘'
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", issue)
    encoded_issue = json.dumps(issue, ensure_ascii=False)

    with pytest.raises(ValueError) as exc_info:
        table.respond(path, "1", _TRACK, "module.py:10", encoded_issue, review_bodies.response_body("対応した"), "")

    message = str(exc_info.value)
    assert "一意に解決できない: 0件" in message
    assert "指定された部分キー:" in message
    assert f"issue={issue}" in message
    candidate_section = message.split("候補行（デコード済み）:\n", maxsplit=1)[1]
    assert encoded_issue not in candidate_section

    assert table.respond(path, "1", _TRACK, "module.py:10", issue, review_bodies.response_body("対応した"), "") == 0
    assert table.validate(path) == 0


def test_row_id_is_stable_across_filters_and_updates_only_selected_row(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """row-idは保存順を表し、表示条件の適用後も番号を変えず対象行だけを更新する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "指摘A")
    table.add(path, "1", "plan-review", "plan.md", "指摘B")
    table.add(path, "2", _TRACK, "module.py:20", "指摘C")
    capsys.readouterr()

    assert table.show(path, track="plan-review", output_format="jsonl") == 0
    assert json.loads(capsys.readouterr().out)["row-id"] == 2
    assert table.respond(path, "", "", "", "", review_bodies.response_body("対応した"), "", row_id=2) == 0

    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0][5:] == ["", ""]
    assert rows[1][5:] == [review_bodies.response_body("対応した"), ""]
    assert rows[2][5:] == ["", ""]
    assert all(len(row) == 7 for row in rows)


@pytest.mark.parametrize("row_id", (0, 2))
def test_respond_rejects_invalid_row_id_without_changing_table(tmp_path: pathlib.Path, row_id: int) -> None:
    """0以下または範囲外のrow-idは保存本文を変更せず拒否する。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "指摘")
    before = path.read_text(encoding="utf-8")

    with pytest.raises(next_action.ActionableError, match="row-id") as exc_info:
        table.respond(path, "", "", "", "", review_bodies.response_body("対応した"), "", row_id=row_id)

    assert path.read_text(encoding="utf-8") == before
    if row_id > 0:
        assert "atk review-table show" in exc_info.value.next_action


def test_respond_rejects_row_id_with_legacy_key(tmp_path: pathlib.Path) -> None:
    """row-idと従来の部分複合キーは同時指定できない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    table.add(path, "1", _TRACK, "module.py:10", "指摘")

    with pytest.raises(ValueError, match="同時に指定できない"):
        table.respond(path, "1", "", "", "", review_bodies.response_body("対応した"), "", row_id=1)


def test_concurrent_add_and_reordered_response_preserve_rows(tmp_path: pathlib.Path) -> None:
    """並列追加と並べ替え後の複合キー更新で行の欠落・重複・破損を生じさせない。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    entries = [("1", _TRACK, f"module.py:{index}", f"指摘{index}") for index in range(8)]

    def add_entry(entry: tuple[str, str, str, str]) -> int:
        return table.add(path, *entry)

    with ThreadPoolExecutor(max_workers=len(entries)) as executor:
        results = list(executor.map(add_entry, entries))

    assert results == [0] * len(entries)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(entries)
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    target = entries[3]
    assert table.respond(path, *target, review_bodies.response_body("再現経路を追加"), "") == 0
    for entry in entries:
        if entry != target:
            assert table.respond(path, *entry, "", review_bodies.reason_body("既存契約を維持する")) == 0
    assert table.validate(path) == 0
    rows = [[json.loads(cell) for cell in line.split("\t")] for line in path.read_text(encoding="utf-8").splitlines()]
    matching = [row for row in rows if row[:4] == list(target)]
    assert matching == [[*target, "詳細", review_bodies.response_body("再現経路を追加"), ""]]
