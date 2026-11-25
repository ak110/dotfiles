import datetime
import os
import pathlib

import pytest

from agent_toolkit._atk.wi import add, frontmatter, layout, sync
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import session_review
from agent_toolkit._testing import git_repository

_REPO = "github.com/test/package"
_STORAGE = sync.Storage(local_only=True)


@pytest.fixture(name="notes")
def _notes():
    path = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    git_repository.init_repository(path)
    return path


def _saved(notes):
    result = {}
    for path in layout.iter_entry_paths(notes):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        result[path.name] = parsed
    return result


def _add(notes, messages, **kwargs):
    return add.add_entries(
        notes,
        messages=messages,
        target_repo=_REPO,
        now=datetime.datetime.now(datetime.UTC),
        storage=_STORAGE,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("question_type", "report", "scope", "expected"),
    [
        ("choice", False, session_review.RESEARCH_SCOPE, True),
        ("post-approval", True, session_review.RESEARCH_SCOPE, True),
        ("post-approval", False, session_review.RESEARCH_SCOPE, False),
        ("choice", False, session_review.META_SCOPE, False),
        ("post-approval", True, "review-session research", True),
    ],
)
def test_weekly_result_uwi_records_slot(notes, monkeypatch, question_type, report, scope, expected):
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", "run-1")
    monkeypatch.setenv(session_review.WEEKLY_SLOT_ENV, "research@2026-11-22")
    header = "---\nrun_skill_report: true\n---\n" if report else ""
    names = _add(
        notes,
        [f"{header}# 提案一覧\n\n提案"],
        source="run-skill",
        entry_type="uwi",
        scope=scope,
        question_type=question_type,
        choices="推奨の提案を全て採用,回答欄に書いた提案IDだけ採用,全て見送る" if question_type == "choice" else None,
    )
    data, _body = _saved(notes)[names[0]]
    assert (data.get("weekly_slot") == "research@2026-11-22") is expected
    assert (data.get("run_skill_run_id") == "run-1") is (expected or report)


def test_failure_report_from_launcher_has_no_slot(notes, monkeypatch):
    monkeypatch.delenv(session_review.WEEKLY_SLOT_ENV, raising=False)
    names = _add(
        notes,
        ["---\nrun_skill_report: true\n---\n# 失敗\n\n失敗した"],
        source="run-skill",
        entry_type="uwi",
        scope=session_review.RESEARCH_SCOPE,
        question_type="post-approval",
    )
    assert "weekly_slot" not in _saved(notes)[names[0]][0]


def test_selected_proposals_are_registered_once(notes, monkeypatch):
    warnings = []
    monkeypatch.setattr(add._outcome, "report_warning", lambda message, **_kwargs: warnings.append(message))  # noqa: SLF001  # pylint: disable=protected-access
    answer = {"selected": ["P2", "P3"]}
    proposals = {
        proposal: (
            f"---\nproposal_id: research@2026-11-22/{proposal}\n---\n# 提案{proposal}\n\n"
            "## 反映内容と反映先\n\n提案の内容を反映する。\n"
        )
        for proposal in ("P1", "P2", "P3")
    }
    first = _add(notes, [proposals["P2"]], source="add-awi-by-user")
    assert len(first) == 1
    resumed = _add(notes, [proposals[proposal] for proposal in answer["selected"]], source="add-awi-by-user")
    assert len(resumed) == 1
    again = _add(notes, [proposals[proposal] for proposal in answer["selected"]], source="add-awi-by-user")
    assert again == []
    ids = sorted(data["proposal_id"] for data, _body in _saved(notes).values())
    assert ids == ["research@2026-11-22/P2", "research@2026-11-22/P3"]
    assert len(warnings) == 3


def test_different_drafts_with_same_proposal_id_are_rejected_before_saving(notes):
    draft = "---\nproposal_id: research@2026-11-22/P1\n---\n# 提案{}\n\n## 反映内容と反映先\n\n提案の内容を反映する。\n"
    with pytest.raises(WebInputError, match="proposal_idが重複"):
        _add(notes, [draft.format("A"), draft.format("B")], source="add-awi-by-user")
    assert not _saved(notes)
