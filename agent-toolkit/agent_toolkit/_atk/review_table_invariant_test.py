"""`atk review-table respond`の必須ラベルと、ラベルを定義するエージェント向け文書の一致を検証する。"""

import argparse
import pathlib
import re

import pytest

from agent_toolkit._atk import review_table as table


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atk")
    table.build_parser(parser.add_subparsers(dest="command"))
    return parser


def test_required_labels_match_reviewee_definition_and_respond_help(capsys: pytest.CaptureFixture[str]) -> None:
    """`reviewee.md`の応答本文の定義と`respond`の案内文が、`respond`が記録の前に確かめるラベル集合と一致する。"""
    reviewee = pathlib.Path(__file__).parents[2] / "share" / "exec-review-responses.md"
    defined = re.findall(r"^- `([^`]+:)`", reviewee.read_text(encoding="utf-8"), flags=re.MULTILINE)
    labels = [*table.RESPONSE_LABELS, *table.NO_RESPONSE_REASON_LABELS]
    assert defined == labels

    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review-table", "respond", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert all(f"`{label}`" in help_text for label in labels)
    assert "`agent-toolkit:/share/exec-review-responses.md`" in help_text


def test_delegation_skill_refers_to_reviewee_response_section() -> None:
    """`delegation`の受領手順は、ラベルを転記せず応答欄の定義元へ接続する。

    ラベルの定義を転記すると改訂に追随しない複製が残るため、定義元の節への参照だけを持たせる。
    """
    delegation = pathlib.Path(__file__).parents[2] / "skills" / "delegation"
    skill_text = (delegation / "SKILL.md").read_text(encoding="utf-8")
    receiving_section = skill_text.split("## 受領と検収\n", 1)[1].split("\n## ", 1)[0]
    assert "`references/receiving.md`" in receiving_section

    receiving = (delegation / "references" / "receiving.md").read_text(encoding="utf-8")
    response_lines = [line for line in receiving.splitlines() if "応答欄の記入時は" in line]
    assert len(response_lines) == 1
    paragraph = response_lines[0]
    assert "`agent-toolkit:/share/exec-review-responses.md`「レビュー指摘管理表への応答」" in paragraph
    assert not any(f"`{label}`" in paragraph for label in table.RESPONSE_LABELS)
    assert not any(f"`{label}`" in receiving_section for label in table.RESPONSE_LABELS)


def test_show_help_exposes_body_selection_and_history(capsys: pytest.CaptureFixture[str]) -> None:
    """公開ヘルプから行・本文の選択と履歴を含むサマリーへ到達できる。"""
    with pytest.raises(SystemExit) as result:
        _parser().parse_args(["review-table", "show", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "--row-id" in help_text
    assert "--field" in help_text
    assert "no-response-reason" in help_text
    assert "previous_findings" in help_text
    assert "history_command" in help_text
    assert "repetition_guidance" in help_text


def test_add_help_exposes_batch_input_and_single_exclusion(capsys: pytest.CaptureFixture[str]) -> None:
    """公開ヘルプが配列schema・共通値・単数形との排他・全行読戻しを案内する。"""
    with pytest.raises(SystemExit) as result:
        _parser().parse_args(["review-table", "add", "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    for marker in ("--items-file", "location", "issue", "level", "UTF-8", "非空JSON配列", "全件共通", "併用できない"):
        assert marker in help_text
    assert "既存行も含む全行の一致" in help_text
