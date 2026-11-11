"""review table readbackの契約を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table as table
from agent_toolkit._common import body_match, next_action
from agent_toolkit._testing import review_bodies
from agent_toolkit._testing.review_table_support import _TRACK, _isolated_home

__all__ = ["_isolated_home"]


def test_init_add_and_show_formats_share_row_ids(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存7列を維持し、TSVとJSON Linesへ同じrow-idを付ける。"""
    path = tmp_path / "review.tsv"
    assert table.init(path) == 0
    assert table.add(path, "1", _TRACK, "module.py:10", "修正が必要") == 0
    assert table.add(path, "1", _TRACK, "README.md", "説明が不足") == 0
    output = capsys.readouterr().out
    assert "成功: 指摘行を1件追加した" in output
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(len(line.split("\t")) == 7 for line in lines)
    assert all(isinstance(json.loads(cell), str) for line in lines for cell in line.split("\t"))
    assert table.show(path) == 0
    shown_tsv = capsys.readouterr().out.splitlines()
    assert all(len(line.split("\t")) == 8 for line in shown_tsv)
    tsv_row_ids = [line.split("\t", 1)[0] for line in shown_tsv]
    assert tsv_row_ids == ["1", "2"]
    assert [line.split("\t", 1)[1] for line in shown_tsv] == lines

    assert table.show(path, output_format="jsonl") == 0
    shown_jsonl = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [str(row["row-id"]) for row in shown_jsonl] == tsv_row_ids
    assert all(list(row) == ["row-id", *table.COLUMNS] for row in shown_jsonl)


def test_add_reports_each_saved_cell_match_without_saved_bodies(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """追加時は指摘箇所と指摘内容がそれぞれ一致するかを確かめ、本文は出力しない。"""
    path = tmp_path / "review.tsv"
    location = "module.py:10\nissue_body_match: 一致"
    issue = "1行目\n2行目"

    assert table.add(path, "1", _TRACK, location, issue) == 0

    assert capsys.readouterr().out == f"成功: 指摘行を1件追加した: {path}（1件）\n"


def test_add_matches_location_and_issue_with_corresponding_saved_cells(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2列の判定は送信本文と対応する保存セルの組を各1回渡す。"""
    path = tmp_path / "review.tsv"
    location = "module.py:10"
    issue = "修正が必要"
    calls: list[tuple[str, str]] = []
    original_verdict = body_match.verdict

    def record_verdict(expected: str, saved: str) -> str:
        calls.append((expected, saved))
        return original_verdict(expected, saved)

    monkeypatch.setattr(body_match, "verdict", record_verdict)

    assert table.add(path, "1", _TRACK, location, issue) == 0

    assert calls == [(location, location), (issue, issue)]
    assert original_verdict(*calls[0]) == "一致"
    assert original_verdict(calls[1][0], calls[1][1] + "差異").startswith("不一致（最初の差異:")


def test_add_rereads_decoded_cells_after_storage_write(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """追加は保存層を経たデコード済みセルを一致判定へ渡す。"""
    path = tmp_path / "review.tsv"
    original_atomic_write = table.atomic_write

    def write_with_changed_location(target: pathlib.Path, content: str, *, fsync: bool = False) -> None:
        cells = content.rstrip("\n").split("\t")
        cells[2] = json.dumps(f" {json.loads(cells[2])} ", ensure_ascii=False)
        original_atomic_write(target, "\t".join(cells) + "\n", fsync=fsync)

    monkeypatch.setattr(table, "atomic_write", write_with_changed_location)

    with pytest.raises(ValueError) as exc_info:
        table.add(path, "1", _TRACK, "module.py:10", "修正が必要")

    message = str(exc_info.value)
    assert str(path) in message
    assert "不一致の列: location" in message
    assert "最初の差異: 1文字目" in message
    assert "送信元本文:" in message
    assert "保存本文:" in message
    assert capsys.readouterr().out == ""


def test_add_saved_mismatch_guides_row_check_instead_of_respond(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`add`の保存後の不一致は、`respond`では直せないlocation列とissue列で生じるため、表の該当行の確認を案内する。

    `respond`は応答列だけを更新するため、`add`の不一致へ`respond`を案内すると実行できない操作を案内することになる。
    """
    path = tmp_path / "review.tsv"
    location_file = tmp_path / "location.txt"
    location_file.write_text("module.py:10", encoding="utf-8")
    issue_file = tmp_path / "issue.md"
    issue_file.write_text("修正が必要", encoding="utf-8")
    # 保存行の解決に使う列を変えずに不一致の分岐を通すため、一致判定だけを不一致にする。
    monkeypatch.setattr(table._body_match, "verdict", lambda _expected, _saved: "不一致")  # pylint: disable=protected-access

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "review-table",
                "add",
                str(path),
                "--round=1",
                f"--track={_TRACK}",
                "--level=詳細",
                f"--location-file={location_file}",
                f"--issue-file={issue_file}",
            ],
            home=tmp_path,
        )

    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    assert "不一致の列: location" in error
    assert "`atk review-table show <PATH>`" in error
    assert "location列とissue列" in error
    assert "respond" not in error


def test_respond_reports_decoded_response_and_match(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対応要ではデコード済み本文が一致するかを確かめ、一致判定だけを出力する。"""
    path = tmp_path / "review.tsv"
    response = '応答本文へ"二重引用符"と\\逆斜線を含める。\nタブ\tも含める。'
    table.add(path, "1", _TRACK, "位置", "指摘")
    capsys.readouterr()

    assert table.respond(path, "1", _TRACK, "位置", "指摘", review_bodies.response_body(response), "") == 0

    output = capsys.readouterr().out
    assert output == f"成功: 応答欄を更新した: {path}\n"
    assert response not in output


def test_respond_reports_body_mismatch_when_saved_response_is_altered(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """応答を保存する処理中に本文が改変された場合、CLIは差異の診断を出力して失敗する。"""
    path = tmp_path / "review.tsv"
    response = review_bodies.response_body("送信元の対応本文")
    response_file = tmp_path / "response.md"
    response_file.write_text(response, encoding="utf-8")
    altered_response = "保存後に改変された対応本文"
    table.add(path, "1", _TRACK, "位置", "指摘")
    capsys.readouterr()
    original_atomic_write = table.atomic_write

    def write_with_changed_response(target: pathlib.Path, content: str, *, fsync: bool = False) -> None:
        cells = content.rstrip("\n").split("\t")
        cells[5] = json.dumps(altered_response, ensure_ascii=False)
        original_atomic_write(target, "\t".join(cells) + "\n", fsync=fsync)

    monkeypatch.setattr(table, "atomic_write", write_with_changed_response)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "review-table",
                "respond",
                str(path),
                "--round=1",
                f"--track={_TRACK}",
                f"--response-file={response_file}",
            ],
            home=tmp_path,
        )

    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    assert "最初の差異: 1文字目" in error
    assert f"送信元本文:\n{response}" in error
    assert f"保存本文:\n{altered_response}" in error
    assert "`atk review-table respond <PATH> --row-id <ROW_ID>`" in error


def test_respond_reports_body_mismatch_when_saved_no_response_reason_is_altered(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対応不要理由が保存時に改変された場合、CLIは差異の診断を出力して失敗する。"""
    path = tmp_path / "review.tsv"
    reason = review_bodies.reason_body("送信元の対応不要理由")
    reason_file = tmp_path / "reason.md"
    reason_file.write_text(reason, encoding="utf-8")
    altered_reason = "保存後に改変された対応不要理由"
    table.add(path, "1", _TRACK, "位置", "指摘")
    capsys.readouterr()
    original_atomic_write = table.atomic_write

    def write_with_changed_reason(target: pathlib.Path, content: str, *, fsync: bool = False) -> None:
        cells = content.rstrip("\n").split("\t")
        cells[6] = json.dumps(altered_reason, ensure_ascii=False)
        original_atomic_write(target, "\t".join(cells) + "\n", fsync=fsync)

    monkeypatch.setattr(table, "atomic_write", write_with_changed_reason)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "review-table",
                "respond",
                str(path),
                "--round=1",
                f"--track={_TRACK}",
                f"--no-response-reason-file={reason_file}",
            ],
            home=tmp_path,
        )

    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    assert "最初の差異: 1文字目" in error
    assert f"送信元本文:\n{reason}" in error
    assert f"保存本文:\n{altered_reason}" in error


def test_respond_reports_only_decoded_no_response_reason(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対応不要では更新した理由の一致を確かめ、一致判定だけを出力する。"""
    path = tmp_path / "review.tsv"
    reason = '対象外の理由へ"引用符"、\\逆斜線、タブ\tおよび\n改行を含める。'
    table.add(path, "1", _TRACK, "位置", "指摘")
    capsys.readouterr()

    assert table.respond(path, "1", _TRACK, "位置", "指摘", "", review_bodies.reason_body(reason)) == 0

    output = capsys.readouterr().out
    assert output == f"成功: 応答欄を更新した: {path}\n"
    assert reason not in output
    assert "response:" not in output


@pytest.mark.parametrize("operation", ("add", "respond"))
def test_saved_body_mismatch_names_show_and_operation_specific_fix(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """保存本文が送信元と一致しない場合は、保存済みであることと確認のコマンド、操作ごとに実行できる修正を示す。"""
    path = tmp_path / "review.tsv"
    table.init(path)
    if operation == "respond":
        table.add(path, "1", _TRACK, "module.py:10", "指摘")
    monkeypatch.setattr(table._body_match, "verdict", lambda _expected, _saved: "不一致")  # pylint: disable=protected-access

    with pytest.raises(next_action.ActionableError, match="保存本文が送信元本文と一致しない") as exc_info:
        if operation == "add":
            table.add(path, "1", _TRACK, "module.py:10", "指摘")
        else:
            table.respond(path, "", "", "", "", review_bodies.response_body("対応した"), "", row_id=1)

    assert "保存は済んでいる" in exc_info.value.next_action
    assert "atk review-table show" in exc_info.value.next_action
    # `respond`は応答列だけを更新するため、`add`の不一致（location列・issue列）には案内しない。
    assert ("atk review-table respond" in exc_info.value.next_action) is (operation == "respond")
