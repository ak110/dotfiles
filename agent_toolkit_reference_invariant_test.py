from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest
from agent_toolkit._common import skill_modes

from _test_helpers import _PLUGIN_PREFIX, _format_unresolved, _tracked_source_paths

_REFERENCE_BOUNDARY = r"(?<![A-Za-z0-9_:-])"


_SKILL_INVOCATION_PATTERN = re.compile(rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}:([A-Za-z0-9][A-Za-z0-9_-]*)")


_SKILL_PATH_PATTERN = re.compile(
    rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}/skills/[A-Za-z0-9][A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?"
)


_PYTHON_PATH_PATTERN = re.compile(
    rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}/(?:scripts|agent_toolkit)/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py"
)


_ATK_MODE_RETIRED_SKILLS = (
    "add-awi-by-user",
    "wi-standards",
    "user-confirmation-and-report",
    "write-plan",
    "commit",
    "report-completion",
    "workflow-overview",
    "managed-temp",
    "process-wi",
    "review-session",
    "refine-prompt",
    "review-external-writes",
    "export-session",
    "delegation",
)


_FIXED_AUDIT_PATH_DIGESTS = {
    pathlib.Path(
        "docs/development/audit-opus55-hosts-2026-11-15.json"
    ): "60d8eda599b255619a7c1237515ad5877d7efff03a5b8ccd43da313ec46c1563",
    pathlib.Path(
        "docs/development/audit-opus55-hosts-2026-11-15-notifications.json"
    ): "1ca1078b1bd2d06f6766056491bc008fc0babe811ff93c0227b530b276b05600",
    pathlib.Path(
        "docs/development/audit-sol-2026-11-14.json"
    ): "5c097f8e8af332499fc6c33ee13c1c10adcaa8101ba15446a1c47a402b35cab8",
}


def _is_historical_record(source: pathlib.Path) -> bool:
    return source in _FIXED_AUDIT_PATH_DIGESTS


def _names_entry_work(root: pathlib.Path, reference: str, source: pathlib.Path) -> bool:
    if source.suffix == ".md" or reference.removeprefix(f"{_PLUGIN_PREFIX}:") not in skill_modes.ENTRY_WORKS:
        return False
    content = (root / source).read_text(encoding="utf-8")
    return re.search(rf"[/$]{re.escape(reference)}(?![A-Za-z0-9_-])", content) is None


def _names_retired_skill(reference: str, skills: tuple[str, ...]) -> bool:
    return any(
        reference in (f"{_PLUGIN_PREFIX}:{skill}", f"{_PLUGIN_PREFIX}/skills/{skill}")
        or reference.startswith(f"{_PLUGIN_PREFIX}/skills/{skill}/")
        for skill in skills
    )


_CONCEPTS_CONFIRMATION_DECISIONS = pathlib.Path("docs/development/concepts-confirmation-decisions.md")


_DESIGN_HOSTS_AUTHORIZATION = pathlib.Path("docs/development/design-hosts-authorization.md")


_SESSION_RECORDS = pathlib.Path("agent-toolkit/agent_toolkit/_atk/session_records.py")


_SESSION_RECORDS_TEST = pathlib.Path("agent-toolkit/agent_toolkit/_atk/session_records_test.py")


_ALLOWED_UNRESOLVED_REFERENCE_COUNTS = {
    (f"{_PLUGIN_PREFIX}:process-wi-all-repos", _SESSION_RECORDS): 2,
    (f"{_PLUGIN_PREFIX}:process-wi-all-repos", _SESSION_RECORDS_TEST): 1,
    (
        f"{_PLUGIN_PREFIX}:process-wi-all-repos",
        pathlib.Path("agent-toolkit/skills/atk-mode/scripts/session_evidence_candidates_users.py"),
    ): 1,
    (f"{_PLUGIN_PREFIX}:session-review-batch", pathlib.Path("agent-toolkit/agent_toolkit/_common/session_review.py")): 1,
    (f"{_PLUGIN_PREFIX}:session-review", pathlib.Path("agent-toolkit/agent_toolkit/_common/skill_modes.py")): 2,
    (f"{_PLUGIN_PREFIX}:session-review-batch", pathlib.Path("agent-toolkit/agent_toolkit/_common/skill_modes.py")): 1,
    (
        f"{_PLUGIN_PREFIX}:session-review",
        pathlib.Path("agent-toolkit/agent_toolkit/_atk/wi/run_skill_scope_invariant_test.py"),
    ): 2,
    (
        f"{_PLUGIN_PREFIX}:session-review-batch",
        pathlib.Path("agent-toolkit/agent_toolkit/_atk/wi/run_skill_scope_invariant_test.py"),
    ): 1,
    (
        f"{_PLUGIN_PREFIX}:session-review",
        pathlib.Path("agent-toolkit/skills/atk-mode/references/answer-transitions.md"),
    ): 2,
    (
        f"{_PLUGIN_PREFIX}:session-review-batch",
        pathlib.Path("agent-toolkit/skills/atk-mode/references/answer-transitions.md"),
    ): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", pathlib.Path("docs/development/audit-records-session-records.md")): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _SESSION_RECORDS): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _SESSION_RECORDS_TEST): 2,
    (f"{_PLUGIN_PREFIX}:process-wi", pathlib.Path("agent-toolkit/agent_toolkit/_common/skill_modes.py")): 1,
    (f"{_PLUGIN_PREFIX}:process-wi", pathlib.Path("agent-toolkit/agent_toolkit/_atk/wi/constants.py")): 2,
    (f"{_PLUGIN_PREFIX}:process-wi", pathlib.Path("agent-toolkit/skills/atk-mode/references/origin-and-approval.md")): 6,
    (f"{_PLUGIN_PREFIX}:review-session", pathlib.Path("agent-toolkit/skills/atk-mode/references/origin-and-approval.md")): 4,
    (f"{_PLUGIN_PREFIX}:review-session", pathlib.Path("agent-toolkit/skills/atk-mode/playbooks/report-completion.md")): 1,
    (f"{_PLUGIN_PREFIX}:review-session", pathlib.Path("agent-toolkit/skills/atk-mode/playbooks/review-session-batch.md")): 2,
    (f"{_PLUGIN_PREFIX}:review-session", pathlib.Path("agent-toolkit/skills/atk-mode/references/review-session-cost.md")): 1,
    (f"{_PLUGIN_PREFIX}:review-session", pathlib.Path("docs/guide/claude-code-guide.md")): 1,
    (f"{_PLUGIN_PREFIX}:wi-standards", pathlib.Path("agent-toolkit/agent_toolkit/_atk/wi/submit.py")): 1,
    (f"{_PLUGIN_PREFIX}:add-awi-by-user", pathlib.Path("agent-toolkit/skills/atk-mode/scripts/wi_quote_check_test.py")): 2,
    (
        f"{_PLUGIN_PREFIX}:add-awi-by-user",
        pathlib.Path("agent-toolkit/skills/atk-mode/scripts/session_evidence_user_events_test.py"),
    ): 4,
    (
        f"{_PLUGIN_PREFIX}:write-plan",
        pathlib.Path("agent-toolkit/skills/atk-mode/scripts/session_review_evidence_user_events_test.py"),
    ): 2,
    (f"{_PLUGIN_PREFIX}:write-plan", pathlib.Path("agent-toolkit/agent_toolkit/_testing/plan_fixture.py")): 5,
    (f"{_PLUGIN_PREFIX}:write-plan", pathlib.Path("agent-toolkit/agent_toolkit/_plan/structure/constants_test.py")): 1,
    (f"{_PLUGIN_PREFIX}:write-plan", pathlib.Path("agent-toolkit/agent_toolkit/_hooks/posttooluse_skills_test.py")): 1,
    (f"{_PLUGIN_PREFIX}:commit", pathlib.Path("agent-toolkit/agent_toolkit/_hooks/posttooluse_skills_test.py")): 1,
    (f"{_PLUGIN_PREFIX}:write-plan", pathlib.Path("agent-toolkit/agent_toolkit/_hooks/user_prompt_submit_test.py")): 2,
    (
        f"{_PLUGIN_PREFIX}:report-completion",
        pathlib.Path("agent-toolkit/agent_toolkit/_hooks/termination_evidence_completion_test.py"),
    ): 1,
    (f"{_PLUGIN_PREFIX}:realign-with-user", _CONCEPTS_CONFIRMATION_DECISIONS): 1,
    (f"{_PLUGIN_PREFIX}:realign-with-user", _DESIGN_HOSTS_AUTHORIZATION): 2,
}


def _codex_snapshot_path(source: pathlib.Path) -> pathlib.Path | None:
    """agent-toolkit配下の原本に対応するCodex生成snapshotのパスを返す。"""
    if not source.parts or source.parts[0] != _PLUGIN_PREFIX:
        return None
    return pathlib.Path(f"{_PLUGIN_PREFIX}-codex", *source.parts[1:])


def _allowed_unresolved_reference_counts(
    sources: list[pathlib.Path],
) -> dict[tuple[str, pathlib.Path], int]:
    """検査対象に実在する原本とCodex生成snapshotの既知参照数を返す。"""
    source_set = set(sources)
    allowed_counts: dict[tuple[str, pathlib.Path], int] = {}
    for (reference, source), count in _ALLOWED_UNRESOLVED_REFERENCE_COUNTS.items():
        candidates = (source, _codex_snapshot_path(source))
        for candidate in candidates:
            if candidate is not None and candidate in source_set:
                allowed_counts[(reference, candidate)] = count
    return allowed_counts


def _reference_exists(root: pathlib.Path, reference: str) -> bool:
    invocation_prefix = f"{_PLUGIN_PREFIX}:"
    if reference.startswith(invocation_prefix):
        skill_name = reference.removeprefix(invocation_prefix)
        return (root / _PLUGIN_PREFIX / "skills" / skill_name / "SKILL.md").is_file()
    return (root / reference).exists()


def _unresolved_references(root: pathlib.Path, sources: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    allowed_counts = _allowed_unresolved_reference_counts(sources)
    unresolved: list[tuple[str, pathlib.Path]] = []
    for reference, source in _collect_references(root, sources):
        if _reference_exists(root, reference):
            continue
        if _is_historical_record(source) and _names_retired_skill(reference, _ATK_MODE_RETIRED_SKILLS):
            continue
        key = (reference, source)
        if allowed_counts.get(key, 0) > 0:
            allowed_counts[key] -= 1
            continue
        if _names_entry_work(root, reference, source):
            continue
        unresolved.append(key)
    for key, remaining_count in allowed_counts.items():
        unresolved.extend([key] * remaining_count)
    return unresolved


@pytest.mark.cpu_limit(
    4.5, reason="配布規範の全参照を実ファイルへ解決し、欠落と曖昧さを検出する全量検査が必要（Linux CI CPU2.004秒）"
)
def test_agent_toolkit_references_resolve() -> None:
    root = pathlib.Path(__file__).resolve().parent
    sources = _tracked_source_paths(root)
    assert any(path.parts[0] == ".claude" for path in sources)
    unresolved = _unresolved_references(root, sources)
    assert not unresolved, _format_unresolved(unresolved)


def test_existing_references_resolve(tmp_path: pathlib.Path) -> None:
    skill = tmp_path / _PLUGIN_PREFIX / "skills" / "present"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: present\n---\n", encoding="utf-8")
    root_test = skill.parent / "contract_test.py"
    root_test.write_text("", encoding="utf-8")
    source = pathlib.Path("source.md")
    (tmp_path / source).write_text(
        f"{_PLUGIN_PREFIX}:present\n{_PLUGIN_PREFIX}/skills/present/SKILL.md\n{_PLUGIN_PREFIX}/skills/contract_test.py\n",
        encoding="utf-8",
    )

    assert not _unresolved_references(tmp_path, [source])

    root_test.unlink()
    assert _unresolved_references(tmp_path, [source]) == [(f"{_PLUGIN_PREFIX}/skills/contract_test.py", source)]


def test_entry_work_names_resolve_only_outside_documents(tmp_path: pathlib.Path) -> None:
    reference = f"{_PLUGIN_PREFIX}:review-session"
    code = pathlib.Path("scope.py")
    document = pathlib.Path("guide.md")
    for relative in (code, document):
        (tmp_path / relative).write_text(f'SCOPE = "{reference} batch"\n', encoding="utf-8")
    slash = pathlib.Path("slash_notice.py")
    (tmp_path / slash).write_text(f'NOTICE = "`/{reference} batch`を実行せよ"\n', encoding="utf-8")
    dollar = pathlib.Path("dollar_notice.py")
    (tmp_path / dollar).write_text(f'NOTICE = "`${reference}`を起動せよ"\n', encoding="utf-8")

    assert _unresolved_references(tmp_path, [code, document, slash, dollar]) == [
        (reference, document),
        (reference, slash),
        (reference, dollar),
    ]


def test_duration_node_ids_are_not_current_skill_invocations(tmp_path: pathlib.Path) -> None:
    reference = f"{_PLUGIN_PREFIX}:fixture-only"
    record = pathlib.Path("agent-toolkit/agent_toolkit/_testing/test_durations.json")
    (tmp_path / record).parent.mkdir(parents=True)
    (tmp_path / record).write_text(json.dumps({f"example_test.py::test_legacy[{reference}]": 1.0}), encoding="utf-8")
    current = pathlib.Path("current.md")
    (tmp_path / current).write_text(reference, encoding="utf-8")

    assert _unresolved_references(tmp_path, [record, current]) == [(reference, current)]


def test_codex_snapshot_requires_exact_known_legacy_references(tmp_path: pathlib.Path) -> None:
    """Codex生成snapshotでも原本と同数の既知の失効参照だけを受理する。"""
    source = _codex_snapshot_path(_SESSION_RECORDS)
    assert source is not None
    target = tmp_path / source
    target.parent.mkdir(parents=True)
    legacy = f"{_PLUGIN_PREFIX}:exit-session"
    startup = f"{_PLUGIN_PREFIX}:process-wi-all-repos\n" * 2
    target.write_text(startup + legacy, encoding="utf-8")

    assert not _unresolved_references(tmp_path, [source])

    target.write_text(f"{startup}{legacy}\n{legacy}\n", encoding="utf-8")
    assert _unresolved_references(tmp_path, [source]) == [(legacy, source)]


def test_python_reference_templates_and_globs_are_ignored(tmp_path: pathlib.Path) -> None:
    source = pathlib.Path("source.md")
    (tmp_path / source).write_text(
        "\n".join(
            [
                f"{_PLUGIN_PREFIX}/agent_toolkit/*.py",
                f"{_PLUGIN_PREFIX}/agent_toolkit/<name>.py",
                f"{_PLUGIN_PREFIX}/skills/*/scripts/",
            ]
        ),
        encoding="utf-8",
    )

    assert not _unresolved_references(tmp_path, [source])


def _known_legacy_references() -> dict[pathlib.Path, list[str]]:
    sources = (_SESSION_RECORDS, _SESSION_RECORDS_TEST)
    by_source: dict[pathlib.Path, list[str]] = {source: [] for source in sources}
    for (reference, source), count in _ALLOWED_UNRESOLVED_REFERENCE_COUNTS.items():
        if source in by_source:
            by_source[source].extend([reference] * count)
    return by_source


def test_known_legacy_references_require_exact_counts(tmp_path: pathlib.Path) -> None:
    by_source = _known_legacy_references()
    for source, references in by_source.items():
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(references), encoding="utf-8")
    sources = list(by_source)
    assert not _unresolved_references(tmp_path, sources)

    for source, references in by_source.items():
        target = tmp_path / source
        for legacy in dict.fromkeys(references):
            missing = references.copy()
            missing.remove(legacy)
            target.write_text("\n".join(missing), encoding="utf-8")
            assert _unresolved_references(tmp_path, sources) == [(legacy, source)]

            excessive = [*references, legacy]
            target.write_text("\n".join(excessive), encoding="utf-8")
            assert _unresolved_references(tmp_path, sources) == [(legacy, source)]
        target.write_text("\n".join(references), encoding="utf-8")


def _collect_references(root: pathlib.Path, sources: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    references: list[tuple[str, pathlib.Path]] = []
    for relative in sources:
        # キーは実行案内ではなく、旧入力や架空の起動名も含むpytest node IDである。
        if relative == pathlib.Path("agent-toolkit/agent_toolkit/_testing/test_durations.json"):
            continue
        content = (root / relative).read_text(encoding="utf-8")
        references.extend((match.group(), relative) for match in _SKILL_INVOCATION_PATTERN.finditer(content))
        paths = [match.group().rstrip("/") for match in _SKILL_PATH_PATTERN.finditer(content)]
        paths.extend(match.group() for match in _PYTHON_PATH_PATTERN.finditer(content))
        if relative in _FIXED_AUDIT_PATH_DIGESTS:
            record = json.loads(content)
            commit = record.get("target_commit") or record["対象版"]
            snapshot = "\n".join([commit, *sorted(set(paths))])
            assert hashlib.sha256(snapshot.encode()).hexdigest() == _FIXED_AUDIT_PATH_DIGESTS[relative], (
                f"{relative}: 対象版または参照パスの集合が変わったため、対象版 {commit} のGitツリーと照合してください"
            )
        else:
            references.extend((path, relative) for path in paths)
    return references
