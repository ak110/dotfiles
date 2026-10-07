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
    reviewee = pathlib.Path(__file__).parents[2] / "skills" / "review-standards" / "references" / "reviewee.md"
    defined = re.findall(r"^- `([^`]+:)`", reviewee.read_text(encoding="utf-8"), flags=re.MULTILINE)
    labels = [*table.RESPONSE_LABELS, *table.NO_RESPONSE_REASON_LABELS]
    assert defined == labels

    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review-table", "respond", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert all(f"`{label}`" in help_text for label in labels)
    assert "`references/reviewee.md`" in help_text


def test_delegation_skill_refers_to_reviewee_response_section() -> None:
    """サブエージェント出力の統合で応答欄を書く主体が読む`delegation`スキルは、ラベルを転記せず定義元の節を参照する。

    ラベルの定義を転記すると定義元の改訂に追随しない複製が残るため、`reviewee.md`の節への参照だけを持たせる。
    """
    skill = pathlib.Path(__file__).parents[2] / "skills" / "delegation" / "SKILL.md"
    paragraph = next(line for line in skill.read_text(encoding="utf-8").splitlines() if "応答欄の本文は" in line)
    assert "`references/reviewee.md`「レビュー指摘管理表への応答」" in paragraph
    assert not any(f"`{label}`" in paragraph for label in table.RESPONSE_LABELS)
