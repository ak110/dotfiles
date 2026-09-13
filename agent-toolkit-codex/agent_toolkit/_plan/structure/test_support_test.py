# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
"""計画形式の共通解析を検証する。"""

import pathlib
import sys

import pytest
from pyfltr.colloquial import check as _colloquial_check

from agent_toolkit._plan import (
    fixture as _plan_fixture,  # noqa: E402  # pylint: disable=function-redefined,pointless-string-statement,undefined-variable,wrong-import-position
)
from agent_toolkit._plan import structure as _plan_format  # noqa: E402  # pylint: disable=wrong-import-position

_BASE = _plan_fixture.BASE_COMMIT

_VALID_CONTENT = _plan_fixture.single_file_plan()
_BUG_CONTENT = _plan_fixture.single_file_plan(bug=True)
_LEGACY_CONTENT = _plan_fixture.legacy_materials_single_file_plan()

_BUG_SECTION = _plan_fixture.inline_bug_section()
_BUG_CAUSE_TABLE = _plan_fixture.bug_cause_table()
_BUG_INVESTIGATION_TABLE = _plan_fixture.bug_investigation_table()
_BUG_FILE_CONTENT = _plan_fixture.bug_file()
_LEGACY_ROWS_BUG_FILE_CONTENT = _plan_fixture.bug_file(variant=_plan_fixture.BUG_VARIANT_LEGACY_ROWS)
_LEGACY_BUG_FILE_CONTENT = _plan_fixture.bug_file(variant=_plan_fixture.BUG_VARIANT_LEGACY_STANDALONE)

_HUMAN_MAIN_CONTENT = _plan_fixture.human_main(related_wi=_plan_fixture.WI_FILES)
_HUMAN_DETAIL_CONTENT = _plan_fixture.human_detail()
_HUMAN_PARTIAL_ROW = _plan_fixture.WI_ACTION_ROW
_HUMAN_PARTIAL_REASON = _plan_fixture.WI_ACTION_REASON

_VALID_MAIN_CONTENT = _plan_fixture.two_file_main()
_VALID_DETAIL_CONTENT = _plan_fixture.two_file_detail()


_UNCOVERED_REQUIREMENT_ROW = (
    "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |"
)


def _plan_with_uncovered_requirement(adopted_scope: str) -> str:
    """採用要求R-P-002-001を被覆しない計画本文を、指定した`採用範囲`で返す。

    合意表の`素材・要求参照`を別要求へ差し替えることで、R-P-002-001は`根拠`と
    `素材・要求参照`のいずれからも参照されない状態になる。
    """
    content = _VALID_CONTENT.replace("P-002, R-P-002-001", "P-001, R-P-001-002", 1)
    replaced = _UNCOVERED_REQUIREMENT_ROW.replace("| 公開APIの維持 |", f"| {adopted_scope} |")
    return content.replace(_UNCOVERED_REQUIREMENT_ROW, replaced, 1)


_PLAN_FILE_STANDARDS = (
    pathlib.Path(__file__).resolve().parents[3] / "skills" / "plan-mode" / "references" / "plan-file-standards.md"
)


_ROOT_CAUSE_ANALYSIS = (
    pathlib.Path(__file__).resolve().parents[3] / "skills" / "bugfix" / "references" / "root-cause-analysis.md"
)


def _canonical_main_content() -> str:
    """新しい固定H2と、エージェント提案が無い場合の提案詳細節を持つメイン本文を返す。"""
    return _plan_fixture.to_canonical_main(_VALID_MAIN_CONTENT)


def _canonical_human_main_content() -> str:
    """新しい固定H2とエージェント提案の判断表を持つ人間向けメイン本文を返す。"""
    return _HUMAN_MAIN_CONTENT


def _wi_source(*, source: bool, trailing_user_comment: bool = False, answer: bool = False) -> str:
    """WIの正本の本文を組み立てる。"""
    frontmatter = ["---", "status: inbox"]
    if source:
        frontmatter.append(f"{_plan_format.PLAN_WI_SOURCE_KEY}: agent-toolkit:session-review")
    frontmatter.append("---")
    sections = ["# 要求", "", "本文。"]
    if answer:
        sections += ["", f"## {_plan_format.PLAN_WI_ANSWER_HEADING}", "", "回答本文。"]
    if trailing_user_comment:
        sections += ["", f"## {_plan_format.PLAN_WI_USER_COMMENT_HEADING}", "", "ユーザーの記入。"]
    return "\n".join([*frontmatter, "", *sections, ""])


def _origin_check(private_notes: pathlib.Path, content: str = _HUMAN_MAIN_CONTENT) -> tuple[list[str], list[str], list[str]]:
    """由来照合を有効にして(違反, 移行の指摘, 省略の事実)を返す。"""
    notices: list[str] = []
    skips: list[str] = []
    _work_type, errors = _plan_format.check_plan_main_structure(
        content, origin_notices=notices, origin_skips=skips, private_notes=private_notes
    )
    return errors, notices, skips


def _write_wi(private_notes: pathlib.Path, body: str, name: str = _plan_fixture.WI_FILES[0][0]) -> None:
    """キュー管理リポジトリの状態ディレクトリへ正本を作成する。"""
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(body, encoding="utf-8")


__all__ = [
    "_BASE",
    "_BUG_CAUSE_TABLE",
    "_BUG_CONTENT",
    "_BUG_FILE_CONTENT",
    "_BUG_INVESTIGATION_TABLE",
    "_BUG_SECTION",
    "_HUMAN_DETAIL_CONTENT",
    "_HUMAN_MAIN_CONTENT",
    "_HUMAN_PARTIAL_REASON",
    "_HUMAN_PARTIAL_ROW",
    "_LEGACY_BUG_FILE_CONTENT",
    "_LEGACY_CONTENT",
    "_LEGACY_ROWS_BUG_FILE_CONTENT",
    "_PLAN_FILE_STANDARDS",
    "_ROOT_CAUSE_ANALYSIS",
    "_UNCOVERED_REQUIREMENT_ROW",
    "_VALID_CONTENT",
    "_VALID_DETAIL_CONTENT",
    "_VALID_MAIN_CONTENT",
    "_canonical_human_main_content",
    "_canonical_main_content",
    "_origin_check",
    "_plan_with_uncovered_requirement",
    "_wi_source",
    "_write_wi",
]
