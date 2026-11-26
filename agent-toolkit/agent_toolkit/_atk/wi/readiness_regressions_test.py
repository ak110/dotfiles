import datetime
import pathlib

import pytest

from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing.wi_entry_files import write_awi, write_uwi


@pytest.mark.parametrize("repair_kind", [None, "frontmatter", "other"])
def test_existing_repair_prevents_duplicate_request(tmp_path: pathlib.Path, repair_kind: str | None) -> None:
    metadata = "repair_target: broken.md\n"
    if repair_kind is not None:
        metadata += f"repair_kind: {repair_kind}\n"
    snapshot = (
        (tmp_path / "broken.md", "github.com/example/repo", "broken", "inbox", None),
        (tmp_path / "repair.md", "github.com/example/repo", f"---\ntype: uwi\n{metadata}---\n## 回答\n", "inbox", "uwi"),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo", snapshot=snapshot)
    assert result.frontmatter_broken == ("broken.md",)
    assert result.frontmatter_broken_needs_uwi == (("broken.md",) if repair_kind == "other" else ())


def test_pending_count_filters_repositories(tmp_path: pathlib.Path) -> None:
    write_awi(tmp_path, "local.md")
    write_awi(tmp_path, "other.md", target_repo="github.com/example/other")
    assert _wi_readiness.count_pending_entries(tmp_path, "github.com/example/repo") == 1
    assert _wi_readiness.count_pending_entries(tmp_path) == 2


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("same_repo_answered", [False, True])
def test_unanswered_dependency_prefers_same_repository(legacy: bool, same_repo_answered: bool) -> None:
    repo = "github.com/example/repo"
    other = "github.com/example/other"

    dependency = (
        "queue_schedule:\n  dependency:\n    kind: entries\n    filenames: [question.md]\n"
        if legacy
        else "depends_on: [question.md]\n"
    )
    snapshot = (
        (pathlib.Path("request.md"), repo, f"---\n{dependency}---\n", "inbox", "awi"),
        (pathlib.Path("question.md"), repo, _answer_text(same_repo_answered), "inbox", "uwi"),
        (pathlib.Path("elsewhere/question.md"), other, _answer_text(False), "inbox", "uwi"),
        (pathlib.Path("plain.md"), repo, "---\n---\n", "inbox", "awi"),
    )
    result = _wi_readiness.calculate_readiness(pathlib.Path(), repo, snapshot=snapshot)
    assert result.unanswered_uwi_waits == (frozenset() if same_repo_answered else frozenset({"request.md"}))


def _answer_text(answered: bool) -> str:
    return "---\n---\n\n## 回答\n" + ("回答済み" if answered else "")


@pytest.mark.parametrize(
    "dependency",
    [
        "    kind: none",
        "    kind: inbox-empty",
        (
            "    kind: external-upstream\n"
            "    recheck_after: '2026-08-12T00:00:00+00:00'\n"
            "    condition: ready\n"
            "    hold_reason: upstream"
        ),
    ],
)
def test_satisfied_legacy_conditions_are_ready(tmp_path: pathlib.Path, dependency: str) -> None:
    """空キューと再確認期限の同値境界を着手可能として扱う。"""
    write_awi(tmp_path, "only.md", legacy_dependency=dependency)
    result = _wi_readiness.calculate_readiness(
        tmp_path, "github.com/example/repo", now=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC)
    )
    assert result.ready == ("only.md",)
    assert not result.invalid_dependencies


@pytest.mark.parametrize(
    "dependency",
    [
        "    kind: entries\n    filenames: [1]",
        "    kind: external-user\n    uwi_filename: question.md\n    condition: 1",
        (
            "    kind: external-upstream\n"
            "    recheck_after: '2026-08-12T00:00:00'\n"
            "    condition: ready\n"
            "    hold_reason: upstream"
        ),
        (
            "    kind: external-upstream\n"
            "    recheck_after: '2026-08-12T00:00:00+00:00'\n"
            "    condition: 1\n"
            "    hold_reason: upstream"
        ),
        (
            "    kind: external-upstream\n"
            "    recheck_after: '2026-08-12T00:00:00+00:00'\n"
            "    condition: ready\n"
            "    hold_reason: ''"
        ),
    ],
)
def test_invalid_legacy_conditions_require_repair(tmp_path: pathlib.Path, dependency: str) -> None:
    write_awi(tmp_path, "invalid.md", legacy_dependency=dependency)
    result = _wi_readiness.calculate_readiness(tmp_path, "github.com/example/repo")
    assert not result.ready
    assert result.invalid_dependencies == ("invalid.md",)
    assert result.actionable_count == 1


def test_cooldown_result_preserves_deadline(tmp_path: pathlib.Path) -> None:
    """表示と再確認に使う元の期限を判定結果へ保持する。"""
    deadline = "2026-08-13T09:00:00+09:00"
    write_awi(tmp_path, "pending.md", cooldown_until=deadline)
    result = _wi_readiness.calculate_readiness(tmp_path, None, now=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC))
    assert result.cooldown_values == (("pending.md", deadline),)


def test_same_name_terminal_in_other_repo_does_not_release_dependency(tmp_path: pathlib.Path) -> None:
    repo = "github.com/example/repo"
    other = "github.com/example/other"
    snapshot = (
        (tmp_path / "request.md", repo, "---\ndepends_on: [target.md]\n---\n", "inbox", "awi"),
        (tmp_path / "target.md", repo, "---\ntype: awi\n---\n", "inbox", "awi"),
        (tmp_path / "other/target.md", other, "---\ntype: awi\n---\n", "adopted", "awi"),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.blocked == ("request.md",)
    assert result.ready == ("target.md",)
    assert result.internal_dependency_waits == ("request.md",)


@pytest.mark.parametrize("answered", [False, True])
def test_answered_repair_does_not_suppress_new_request(tmp_path: pathlib.Path, answered: bool) -> None:
    repo = "github.com/example/repo"
    snapshot = (
        (tmp_path / "broken.md", repo, "broken", "inbox", None),
        (
            tmp_path / "repair.md",
            repo,
            "---\nrepair_target: broken.md\n---\n\n## 回答\n" + ("確認した" if answered else ""),
            "inbox",
            "uwi",
        ),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.frontmatter_broken_needs_uwi == (("broken.md",) if answered else ())


@pytest.mark.parametrize("state", ["inbox", "adopted"])
@pytest.mark.parametrize("answer", ["", "回答済み"])
def test_legacy_user_dependency_requires_answer_in_active_or_terminal_entry(
    tmp_path: pathlib.Path, state: str, answer: str
) -> None:
    write_awi(
        tmp_path,
        "request.md",
        legacy_dependency="    kind: external-user\n    uwi_filename: answer.md\n    condition: approved",
    )
    write_uwi(tmp_path, "answer.md", state=state, answer=answer)
    result = _wi_readiness.calculate_readiness(tmp_path, None)
    assert ("request.md" in result.ready) == bool(answer)


def test_report_before_ready_entry_does_not_stop_scan(tmp_path: pathlib.Path) -> None:
    repo = "github.com/example/repo"
    snapshot = (
        (tmp_path / "report.md", repo, "---\nrun_skill_report: true\n---\n\n## 回答\n", "inbox", "uwi"),
        (tmp_path / "ready.md", repo, "---\n---\n", "inbox", "awi"),
        (tmp_path / "waiting.md", repo, "---\ndepends_on: [report.md]\n---\n", "inbox", "awi"),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.ready == ("ready.md",)
    assert result.blocked == ("report.md", "waiting.md")
    assert not result.internal_dependency_waits


def test_unanswered_cross_repository_fallback_and_missing_target() -> None:
    repo = "github.com/example/repo"

    snapshot = (
        (pathlib.Path("answer.md"), "github.com/example/other", _answer_text(False), "inbox", "uwi"),
        (pathlib.Path("request.md"), repo, "---\ndepends_on: [answer.md]\n---\n", "inbox", "awi"),
        (pathlib.Path("missing.md"), repo, "---\ndepends_on: [absent.md]\n---\n", "inbox", "awi"),
    )
    result = _wi_readiness.calculate_readiness(pathlib.Path(), repo, snapshot=snapshot)
    assert result.unanswered_uwi_waits == frozenset({"request.md"})
    assert result.missing_dependencies == ("missing.md",)


@pytest.mark.parametrize("value", [1, True, ["2026-08-12"], {"date": "2026-08-12"}])
def test_public_cooldown_parser_rejects_non_string_values(value: object) -> None:
    """真な非文字列を期限として解析せず、不適用の結果を返す。"""
    assert _wi_readiness.applicable_cooldown_until("awi", value) is None


def test_same_repo_terminal_wins_over_other_active_entry(tmp_path: pathlib.Path) -> None:
    repo = "github.com/example/repo"
    snapshot = (
        (tmp_path / "request.md", repo, "---\ndepends_on: [target.md]\n---\n", "inbox", "awi"),
        (tmp_path / "target.md", repo, "---\n---\n", "adopted", "awi"),
        (tmp_path / "other/target.md", "github.com/example/other", "---\n---\n", "inbox", "awi"),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.ready == ("request.md",)
    assert not result.blocked


def test_later_invalid_user_dependency_is_not_hidden_by_explicit_dependency(tmp_path: pathlib.Path) -> None:
    repo = "github.com/example/repo"
    snapshot = (
        (tmp_path / "first.md", repo, "---\ndepends_on: []\n---\n", "inbox", "awi"),
        (
            tmp_path / "bad.md",
            repo,
            (
                "---\n"
                "queue_schedule:\n"
                "  dependency:\n"
                "    kind: external-user\n"
                "    uwi_filename: first.md\n"
                "    condition: approved\n"
                "---\n"
            ),
            "inbox",
            "awi",
        ),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.invalid_dependencies == ("bad.md",)
    assert result.ready == ("first.md",)


def test_future_upstream_wait_is_not_internal_dependency(tmp_path: pathlib.Path) -> None:
    write_awi(
        tmp_path,
        "waiting.md",
        legacy_dependency=(
            "    kind: external-upstream\n"
            "    recheck_after: '2026-08-13T00:00:00+00:00'\n"
            "    condition: available\n"
            "    hold_reason: upstream"
        ),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, None, now=datetime.datetime(2026, 8, 12, tzinfo=datetime.UTC))
    assert result.blocked == ("waiting.md",)
    assert not result.internal_dependency_waits


@pytest.mark.parametrize("report,answer,explicit", [(False, "", False), (True, "", True)])
def test_processing_uwi_outside_candidates_stays_external_wait(
    tmp_path: pathlib.Path, report: bool, answer: str, explicit: bool
) -> None:
    repo = "github.com/example/repo"
    dependency = (
        "depends_on: [answer.md]"
        if explicit
        else "queue_schedule:\n  dependency:\n    kind: entries\n    filenames: [answer.md]"
    )
    snapshot = (
        (tmp_path / "request.md", repo, f"---\n{dependency}\n---\n", "inbox", "awi"),
        (
            tmp_path / "answer.md",
            repo,
            f"---\nrun_skill_report: {str(report).lower()}\n---\n\n## 回答\n{answer}",
            "processing",
            "uwi",
        ),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert "request.md" in result.blocked
    assert not result.internal_dependency_waits


def test_legacy_external_repo_waits_for_active_retry_even_with_terminal_copy(tmp_path: pathlib.Path) -> None:
    repo = "github.com/example/repo"
    other = "github.com/example/other"
    body = (
        "---\n"
        "queue_schedule:\n"
        "  dependency:\n"
        "    kind: external-repo-entry\n"
        "    filenames: [answer.md]\n"
        "    target_repo: github.com/example/other\n"
        "---\n"
    )
    snapshot = (
        (tmp_path / "request.md", repo, body, "inbox", "awi"),
        (tmp_path / "answer.md", other, "---\n---\n\n## 回答\n承認", "processing", "uwi"),
        (tmp_path / "archive/answer.md", other, "---\n---\n\n## 回答\n承認", "adopted", "uwi"),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.blocked == ("request.md",)
    assert not result.ready


def test_non_mapping_legacy_dependency_is_not_executed(tmp_path: pathlib.Path) -> None:
    repo = "github.com/example/repo"
    text = "---\nqueue_schedule:\n  dependency: [entries]\n---\n"
    snapshot = ((tmp_path / "one.md", repo, text, "inbox", "awi"),)
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.ready == ("one.md",)
    assert not result.unanswered_uwi_waits


def test_answered_same_repo_target_is_not_an_unanswered_dependency() -> None:
    repo = "github.com/example/repo"
    snapshot = (
        (pathlib.Path("answer.md"), repo, _answer_text(True), "inbox", "uwi"),
        (pathlib.Path("request.md"), repo, "---\ndepends_on: [answer.md]\n---\n", "inbox", "awi"),
    )
    result = _wi_readiness.calculate_readiness(pathlib.Path(), repo, snapshot=snapshot)
    assert not result.unanswered_uwi_waits
    assert "request.md" in result.blocked


def test_non_string_repair_target_does_not_suppress_broken_entry(tmp_path: pathlib.Path) -> None:
    repo = "github.com/example/repo"
    snapshot = (
        (tmp_path / "broken.md", repo, "broken", "inbox", None),
        (tmp_path / "repair.md", repo, "---\nrepair_target: [broken.md]\n---\n\n## 回答\n", "inbox", "uwi"),
    )
    result = _wi_readiness.calculate_readiness(tmp_path, repo, snapshot=snapshot)
    assert result.frontmatter_broken_needs_uwi == ("broken.md",)


def test_non_string_legacy_user_target_requires_repair(tmp_path: pathlib.Path) -> None:
    write_awi(
        tmp_path,
        "invalid.md",
        legacy_dependency="    kind: external-user\n    uwi_filename: [answer.md]\n    condition: approved",
    )
    result = _wi_readiness.calculate_readiness(tmp_path, None)
    assert result.invalid_dependencies == ("invalid.md",)
    assert not result.ready
