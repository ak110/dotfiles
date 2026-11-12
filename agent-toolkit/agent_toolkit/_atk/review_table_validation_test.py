"""review table validationの契約を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import review_table as table
from agent_toolkit._common import next_action
from agent_toolkit._testing import review_bodies
from agent_toolkit._testing.review_table_support import _RESPONSE_WITHOUT_SEARCH, _TRACK, _isolated_home

__all__ = ["_isolated_home"]


def test_add_requires_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    table.init(path)
    with pytest.raises(ValueError, match="先頭4列は空にできない"):
        table.add(path, "", _TRACK, "module.py:10", "指摘")


def test_validate_rejects_non_integer_round(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("R1", _TRACK, "位置", "指摘", "詳細", "内容", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ラウンドが1以上の整数ではない"):
        table.validate(path)


def test_validate_rejects_invalid_track(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("1", "invalid", "位置", "指摘", "詳細", "内容", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(next_action.ActionableError, match="trackが正規値ではない") as exc_info:
        table.validate(path)
    assert "trackの正規値集合" in exc_info.value.next_action


def test_validate_rejects_unanswered_response(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "review.tsv"
    cells = ("1", _TRACK, "位置", "指摘", "詳細", "", "", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(next_action.ActionableError, match="未応答") as exc_info:
        table.validate(path)
    assert "atk review-table respond <PATH> --row-id 1" in exc_info.value.next_action


def test_row_id_diagnostic_guides_response(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """空行と応答済み行の後の指摘へ、診断とshowの同じ識別子で応答できる。"""
    path = tmp_path / "positions.exec-review.tsv"
    rows = [
        ["1", _TRACK, "first", "対応済み", "詳細", "修正した", ""],
        ["1", _TRACK, "second", "未対応", "仕様", "", ""],
    ]
    path.write_text("\n" + "\n\n".join("\t".join(json.dumps(cell) for cell in row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "validate", str(path)], home=tmp_path)
    assert result.value.code == 1
    error = capsys.readouterr().err
    assert "row-id 2が未応答" in error
    assert "--row-id 2" in error
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "show", str(path), "--format=jsonl"], home=tmp_path)
    assert result.value.code == 0
    assert [json.loads(line)["row-id"] for line in capsys.readouterr().out.splitlines()] == [1, 2]
    response = tmp_path / "response.md"
    response.write_text(review_bodies.response_body("修正した"), encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "respond", str(path), "--row-id=2", f"--response-file={response}"], home=tmp_path)
    assert result.value.code == 0
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "validate", str(path)], home=tmp_path)
    assert result.value.code == 0


@pytest.mark.parametrize(
    ("cells", "reason"),
    [
        (["1", _TRACK], "列数が7ではない"),
        (["1", _TRACK, "位置", None, "詳細", "", ""], "列番号 4がJSON文字列ではない"),
        (["1", _TRACK, "位置", 7, "詳細", "", ""], "列番号 4が文字列ではない"),
    ],
)
def test_syntax_diagnostic_identifies_physical_line(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], cells: list[object], reason: str
) -> None:
    """構文不正は空行を数える物理位置を示し、行識別子として案内しない。"""
    path = tmp_path / "syntax.exec-review.tsv"
    line = "\t".join("broken" if cell is None else json.dumps(cell) for cell in cells)
    path.write_text("\n\n" + line + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["review-table", "show", str(path)], home=tmp_path)
    assert result.value.code == 1
    error = capsys.readouterr().err
    assert "物理行番号 3" in error
    assert reason in error
    assert "row-id 3" not in error


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [(0, "", "先頭4列"), (0, "R2", "ラウンド"), (1, "invalid", "track"), (4, "重大", "level")],
)
def test_invalid_row_diagnostic_uses_show_identifier(tmp_path: pathlib.Path, column: int, value: str, reason: str) -> None:
    """未応答以外の行内容の診断も空行を除く識別子へ対応する。"""
    path = tmp_path / "invalid.exec-review.tsv"
    row = ["1", _TRACK, "位置", "指摘", "詳細", "対応済み", ""]
    row[column] = value
    path.write_text("\n" + "\t".join(json.dumps(cell) for cell in row) + "\n", encoding="utf-8")
    with pytest.raises(next_action.ActionableError) as error:
        table.validate(path)
    assert "row-id 1" in error.value.reason
    assert reason in error.value.reason


def test_invalid_column_count_has_recovery_guidance_for_all_mutations(tmp_path: pathlib.Path) -> None:
    """不正な列数の表を検出したとき、修復に必要な列構造と正規値を全操作で示す。"""
    path = tmp_path / "review.tsv"
    cells = ("1", _TRACK, "旧重大度", "位置", "指摘", "詳細", "", "", "")
    path.write_text(
        "\t".join(json.dumps(value, ensure_ascii=False) for value in cells) + "\n",
        encoding="utf-8",
    )
    for operation in (
        lambda: table.validate(path, require_responses=False),
        lambda: table.add(path, "1", _TRACK, "位置2", "追加"),
        lambda: table.respond(path, "1", _TRACK, "位置", "指摘", review_bodies.response_body("修正"), ""),
    ):
        before = path.read_text(encoding="utf-8")
        with pytest.raises(next_action.ActionableError) as exc_info:
            operation()
        message = exc_info.value.message
        assert path.read_text(encoding="utf-8") == before
        assert "期待列数は7" in message
        assert "trackの位置はroundの直後" in message
        assert "levelの正規値集合は要件, 仕様, 詳細, 実装" in message
        assert "plan-review, exec-review, plan-conformance, independent" in message
        assert "implementation-reviewはexec-reviewとして読み取る" in message
        assert "levelの位置はissueの直後" in message
        assert "保存済み8列形式はresponse-neededを読み込みの対象から外す" in message


@pytest.mark.parametrize(
    ("option", "body", "missing"),
    [
        ("--response-file", "修正した", "違反を確認した規定:、同じ規定の検索:、採用する修正範囲:、採用しない修正方針:"),
        ("--response-file", _RESPONSE_WITHOUT_SEARCH, "同じ規定の検索:"),
        (
            "--response-file",
            review_bodies.response_body("修正した").replace("採用しない修正方針: なし", "採用しない修正方針:"),
            "採用しない修正方針:",
        ),
        ("--response-file", "前置き 違反を確認した規定: 規定\n" + _RESPONSE_WITHOUT_SEARCH, "同じ規定の検索:"),
        ("--no-response-reason-file", "既存契約を維持する", "根拠の所在:、判断主体:"),
        ("--no-response-reason-file", "根拠の所在:   \n補足", "根拠の所在:、判断主体:"),
    ],
)
def test_respond_rejects_body_without_required_labels_and_keeps_table(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], option: str, body: str, missing: str
) -> None:
    """必須ラベルが欠けた本文と内容が空のラベルを持つ本文は、表を変えずに欠けたラベルと定義元を示して失敗する。"""
    path = tmp_path / "review.tsv"
    table.add(path, "1", _TRACK, "位置", "指摘")
    before = path.read_text(encoding="utf-8")
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "respond", str(path), "--row-id=1", f"{option}={body_file}"], home=tmp_path)

    assert exc_info.value.code != 0
    error = capsys.readouterr().err
    assert error.startswith("失敗: ")
    assert f"{option}の本文に必須ラベルが無いか、ラベルの後の記述が空である: {missing}\n" in error
    assert "`agent-toolkit:/share/exec-review-responses.md`" in error
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    ("option", "body", "column"),
    [
        ("--response-file", review_bodies.response_body("修正した"), 5),
        ("--no-response-reason-file", review_bodies.reason_body("README.md:10の既存契約"), 6),
    ],
)
def test_respond_records_body_with_required_labels(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], option: str, body: str, column: int
) -> None:
    """必須ラベルを全て持つ本文は従来どおり記録し、渡したファイルの種類が対応要否を決める。"""
    path = tmp_path / "review.tsv"
    table.add(path, "1", _TRACK, "位置", "指摘")
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["review-table", "respond", str(path), "--row-id=1", f"{option}={body_file}"], home=tmp_path)

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.startswith("成功: ")
    row = [json.loads(cell) for cell in path.read_text(encoding="utf-8").splitlines()[0].split("\t")]
    assert row[column] == body
    assert row[11 - column] == ""
