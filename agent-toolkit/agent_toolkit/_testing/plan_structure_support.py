import pathlib

from agent_toolkit._atk.wi import frontmatter as wi_frontmatter
from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._plan.structure import constants as plan_constants
from agent_toolkit._plan.structure import sections as plan_sections
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing import wi_entry_files

_BASE = _plan_fixture.BASE_COMMIT

_VALID_CONTENT = _plan_fixture.single_file_plan()
_BUG_CONTENT = _plan_fixture.single_file_plan(bug=True)
_LEGACY_CONTENT = _plan_fixture.legacy_materials_single_file_plan()

_BUG_SECTION = _plan_fixture.inline_bug_section()
_BUG_FILE_CONTENT = _plan_fixture.bug_file()
_LEGACY_TABLES_BUG_FILE_CONTENT = _plan_fixture.bug_file(variant=_plan_fixture.BUG_VARIANT_LEGACY_TABLES)
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
    content = _VALID_CONTENT.replace("P-002, R-P-002-001", "P-001, R-P-001-002", 1)
    replaced = _UNCOVERED_REQUIREMENT_ROW.replace("| 公開APIの維持 |", f"| {adopted_scope} |")
    return content.replace(_UNCOVERED_REQUIREMENT_ROW, replaced, 1)


def _canonical_main_content() -> str:
    return _plan_fixture.to_canonical_main(_VALID_MAIN_CONTENT)


def _canonical_human_main_content() -> str:
    return _HUMAN_MAIN_CONTENT


def _wi_source(*, source: bool, trailing_user_comment: bool = False, answer: bool = False) -> str:
    frontmatter = ["---", "state: inbox"]
    if source:
        frontmatter.append(f"{_plan_format.PLAN_WI_SOURCE_KEY}: agent-toolkit:review-session")
    frontmatter.append("---")
    sections = ["# 要求", "", "本文。"]
    if answer:
        sections += ["", f"## {plan_constants.PLAN_WI_ANSWER_HEADING}", "", "回答本文。"]
    if trailing_user_comment:
        sections += ["", f"## {plan_constants.PLAN_WI_USER_COMMENT_HEADING}", "", "ユーザーの記入。"]
    return "\n".join([*frontmatter, "", *sections, ""])


def _origin_check(private_notes: pathlib.Path, content: str = _HUMAN_MAIN_CONTENT) -> tuple[list[str], list[str], list[str]]:
    notices: list[str] = []
    skips: list[str] = []
    _work_type, errors = plan_sections.check_plan_main_structure(
        content, origin_notices=notices, origin_skips=skips, private_notes=private_notes
    )
    return errors, notices, skips


def _write_wi(
    private_notes: pathlib.Path, body: str, name: str = _plan_fixture.WI_FILES[0][0], *, legacy_layout: bool = False
) -> None:
    path = wi_entry_files.entry_path(private_notes, name, legacy_layout=legacy_layout)
    if not legacy_layout:
        parsed = wi_frontmatter.parse_frontmatter(body)
        metadata, content = parsed if parsed is not None else ({}, body)
        body = wi_frontmatter.serialize_frontmatter({**metadata, "state": metadata.get("state", "inbox")}, content)
    path.write_text(body, encoding="utf-8")
