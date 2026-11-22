import argparse
import pathlib
import re

import pytest

from agent_toolkit._atk import review_table as table
from agent_toolkit._atk import review_table_cli
from agent_toolkit._testing.normative_contract import named_document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atk")
    review_table_cli.build_parser(parser.add_subparsers(dest="command"))
    return parser


def test_required_labels_match_reviewee_definition_and_respond_help(capsys: pytest.CaptureFixture[str]) -> None:
    plugin_root = pathlib.Path(__file__).parents[2]
    reviewee = plugin_root / "skills/atk-mode/playbooks/review-fix.md"
    text = "\n" + named_document(reviewee, plugin_root, "../references/review-record-response.md")
    section = text.split("\n# レビュー指摘管理表への応答\n", 1)[1].split("\n## ", 1)[0]
    defined = re.findall(r"^- `([^`]+:)`", section, flags=re.MULTILINE)
    labels = [*table.RESPONSE_LABELS, *table.NO_RESPONSE_REASON_LABELS]
    assert defined == labels

    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args(["review-table", "respond", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert all(f"`{label}`" in help_text for label in labels)
    assert "`agent-toolkit:atk-mode/references/review-record-response.md`「レビュー指摘管理表への応答」" in help_text


def test_delegation_receiving_refers_to_reviewee_response_section() -> None:
    plugin = pathlib.Path(__file__).parents[2]
    receiving = (plugin / "skills/atk-mode/playbooks/delegation-receiving.md").read_text(encoding="utf-8")
    assert "\n## 受領後の扱い\n" in receiving
    section = receiving.split("\n## 受領後の扱い\n", 1)[1].split("\n## ", 1)[0]
    assert "review-record-response.md" in section
    assert "「レビュー指摘管理表への応答」" in section
    assert not any(f"`{label}`" in receiving for label in table.RESPONSE_LABELS)
    named_document(plugin / "skills/atk-mode/playbooks/review-fix.md", plugin, "../references/review-record-response.md")
