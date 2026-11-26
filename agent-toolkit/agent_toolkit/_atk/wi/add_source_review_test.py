# pylint: disable=protected-access

import argparse
import pathlib

import pytest

from agent_toolkit._atk.wi import add_cli
from agent_toolkit._common import external_source_review, session_review

_URL = "https://example.com/first"


def _write_table(directory: pathlib.Path, url: str) -> None:
    header = "| " + " | ".join(external_source_review.COLUMNS) + " |\n"
    separator = "| " + " | ".join(["---"] * len(external_source_review.COLUMNS)) + " |\n"
    row = f"| {url} | 主張 | 既存 | 新しい条件 | 規定を統合する | 採用 |\n"
    (directory / external_source_review.TABLE_NAME).write_text(header + separator + row, encoding="utf-8")


def test_explicit_contract_controls_case_and_table(tmp_path: pathlib.Path) -> None:
    contract = tmp_path / "work-contract.md"
    args = argparse.Namespace(source_contract=str(contract), source=None, scope=None, type="awi", question_type=None)
    contract.write_text("# 作業契約\n", encoding="utf-8")
    assert add_cli._check_external_source_review(args, ["# 原稿\n"], tmp_path) is None
    contract.write_text(f"# 作業契約\n\n## 原本\n\n- {_URL}\n", encoding="utf-8")
    error = add_cli._check_external_source_review(args, ["# 原稿\n"], tmp_path)
    assert error and external_source_review.TABLE_NAME in error
    _write_table(tmp_path, "https://example.com/another")
    error = add_cli._check_external_source_review(args, ["# 原稿\n"], tmp_path)
    assert error and _URL in error
    _write_table(tmp_path, _URL)
    assert add_cli._check_external_source_review(args, ["# 原稿\n"], tmp_path) is None


def test_weekly_review_uses_collected_urls(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 作業契約\n\n## 原本\n\n- https://example.com/version-only\n", encoding="utf-8")
    slot = "research@2026-11-25"
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", "weekly-run")
    monkeypatch.setenv(session_review.WEEKLY_SLOT_ENV, slot)
    monkeypatch.setattr(add_cli.research_sources, "collected_source_urls", lambda value: {_URL} if value == slot else set())
    args = argparse.Namespace(
        source_contract=str(contract),
        source="run-skill",
        scope=session_review.RESEARCH_SCOPE,
        type="uwi",
        question_type="choice",
    )
    messages = ["---\nrun_skill_report: true\n---\n\n# 調査報告\n"]
    error = add_cli._check_external_source_review(args, messages, tmp_path)
    assert error and external_source_review.TABLE_NAME in error
    _write_table(tmp_path, _URL)
    assert add_cli._check_external_source_review(args, messages, tmp_path) is None
