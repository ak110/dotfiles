import hashlib
import json
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import wait_ci_forge
from agent_toolkit._atk.wi import merge_verification
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file

_COMMIT = "a" * 40
_REPO = "github.com/example/test"


def _body(evidence=None):
    extra = f"merge_local_verification: {json.dumps(str(evidence))}\n" if evidence is not None else ""
    return f"---\ntarget_repo: {_REPO}\nmerge_branch: fix/demo\nmerge_commit: {_COMMIT}\n{extra}---\n\n# 項目\n"


@pytest.mark.parametrize(
    ("runs", "jobs", "allowed"),
    [
        ([(1, _COMMIT, "completed", "success")], [{"name": "test", "conclusion": "success"}], True),
        ([(1, _COMMIT, "completed", "success")], [{"name": "test (non-owner)", "conclusion": "success"}], False),
        ([(1, "b" * 40, "completed", "success")], [{"name": "test", "conclusion": "success"}], False),
        ([(1, _COMMIT, "completed", "cancelled")], [], False),
        ([(1, _COMMIT, "in_progress", None)], [], False),
        ([(1, _COMMIT, "completed", "failure")], [], False),
        ([], [], False),
    ],
)
def test_ci_success_requires_exact_oid_and_execution(monkeypatch, runs, jobs, allowed):
    def list_for(forge, repository, ref, _timeout):
        assert (forge, repository, ref) == ("github", _REPO, "fix/demo")
        return lambda sha: (
            [
                dict(databaseId=ident, headSha=oid, status=status, conclusion=conclusion, event="push", workflowName="CI")
                for ident, oid, status, conclusion in runs
            ]
            if sha == _COMMIT
            else []
        )

    monkeypatch.setattr(wait_ci_forge, "_default_run_list_fn", list_for)
    monkeypatch.setattr(wait_ci_forge, "_gh_job_list", lambda *_args: jobs)
    monkeypatch.setattr(wait_ci_forge, "_github_ci_configured", lambda *_args: True)
    monkeypatch.setattr(merge_verification, "_declared_github_exclusion", lambda *_args: False)
    if allowed:
        assert merge_verification.check(_body(), expected_head=_COMMIT).startswith(f"対象OID {_COMMIT} のpush CI成功")
    else:
        with pytest.raises(ActionableError):
            merge_verification.check(_body(), expected_head=_COMMIT)


def test_expected_head_mismatch_stops_before_ci(monkeypatch):
    def unexpected(*_args):
        pytest.fail("別OIDでCIへ照会した")

    monkeypatch.setattr(wait_ci_forge, "_default_run_list_fn", unexpected)
    with pytest.raises(ActionableError, match="一致しない"):
        merge_verification.check(_body(), expected_head="b" * 40)


@pytest.mark.parametrize("configured", [False, True])
def test_equivalent_evidence_is_only_for_proven_non_ci_target(tmp_path, monkeypatch, configured):
    monkeypatch.setattr(wait_ci_forge, "_default_run_list_fn", lambda *_args: lambda _sha: [])
    monkeypatch.setattr(wait_ci_forge, "_github_ci_configured", lambda *_args: configured)
    monkeypatch.setattr(merge_verification, "_declared_github_exclusion", lambda *_args: False)
    report = tmp_path / "verify.out"
    report.write_text("検証の出力", encoding="utf-8")
    evidence = tmp_path / "verified.json"
    review = tmp_path / "review.md"
    values = {
        "target_repo": _REPO,
        "commit": _COMMIT,
        "argv": ["make", "test"],
        "exit_code": 0,
        "output": str(report),
        "output_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "review": str(review),
    }
    _write_evidence(evidence, review, values)
    if configured:
        with pytest.raises(ActionableError, match="CIが未確認"):
            merge_verification.check(_body(evidence))
    else:
        assert merge_verification.check(_body(evidence)).startswith("CI非対象")
        for changes in ({"commit": "b" * 40}, {"argv": ["true"]}, {"output_sha256": "f" * 64}):
            _write_evidence(evidence, review, {**values, **changes})
            with pytest.raises(ActionableError, match="同等の検証記録"):
                merge_verification.check(_body(evidence))
        _write_evidence(evidence, review, values)
        evidence.write_text(evidence.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(ActionableError, match="独立レビューで"):
            merge_verification.check(_body(evidence))


def _write_evidence(evidence, review, values):
    raw = json.dumps(values).encode("utf-8")
    evidence.write_bytes(raw)
    review.write_text(
        f"状態: completed\nレビューHEAD: {_COMMIT}\n未解決の指摘数: 0\n"
        f"同等検証記録SHA-256: {hashlib.sha256(raw).hexdigest()}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("workflow", "jobs", "allowed"),
    [
        ("Documentation", [{"name": "docs", "conclusion": "success"}], False),
        ("CI", [{"name": "owner", "conclusion": "success"}, {"name": "test", "conclusion": "skipped"}], False),
        ("CI", [{"name": "test", "conclusion": "success"}], True),
    ],
)
def test_does_not_count_unrelated_or_skipped_ci(monkeypatch, workflow, jobs, allowed):
    run = dict(databaseId=1, headSha=_COMMIT, status="completed", conclusion="success", event="push", workflowName=workflow)
    monkeypatch.setattr(wait_ci_forge, "_default_run_list_fn", lambda *_args: lambda _sha: [run])
    monkeypatch.setattr(wait_ci_forge, "_gh_job_list", lambda *_args: jobs)
    monkeypatch.setattr(wait_ci_forge, "_github_ci_configured", lambda *_args: True)
    monkeypatch.setattr(merge_verification, "_declared_github_exclusion", lambda *_args: False)
    if allowed:
        assert merge_verification.check(_body()).startswith(f"対象OID {_COMMIT} のpush CI成功")
    else:
        with pytest.raises(ActionableError):
            merge_verification.check(_body())


def test_dotfiles_requires_all_pytest_jobs_and_allows_other_skips():
    jobs = [
        {"name": name, "conclusion": "success"}
        for name in ("pytest (3.15)", "pytest-toolkit (3.15)", *(f"pytest-toolkit (3.15, {i}/3)" for i in range(1, 4)))
    ]
    jobs.append({"name": "plugin-version", "conclusion": "skipped"})
    assert merge_verification._configured_checks_executed(jobs, dotfiles=True)  # pylint: disable=protected-access
    jobs[2]["conclusion"] = "skipped"
    assert not merge_verification._configured_checks_executed(jobs, dotfiles=True)  # pylint: disable=protected-access
    assert not merge_verification._configured_checks_executed(jobs[3:], dotfiles=True)  # pylint: disable=protected-access


@pytest.mark.parametrize(
    ("on", "might_push"),
    [
        ("push", True),
        (["pull_request", "push"], True),
        ({"pull_request": ""}, False),
        ({"push": {"branches": ["main"]}}, False),
        ({"push": {"branches": ["main", "fix/demo"]}}, True),
        ({"push": {"branches": ["fix/**"]}}, True),
        ({"push": {"branches": []}}, True),
    ],
)
def test_only_unambiguous_github_config_proves_exclusion(on, might_push):
    assert merge_verification._might_push(on, "fix/demo") is might_push  # pylint: disable=protected-access


def test_cmd_edit_checks_merge_metadata_before_persisting_it(tmp_path, monkeypatch, capsys):
    notes = _setup_notes(tmp_path)
    name = "20261123-175351-001.md"
    path = _write_awi_file(notes, name, target_repo=_REPO)
    original = path.read_bytes()
    proposal = tmp_path / "merge.md"
    proposal.write_text(_body(), encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    monkeypatch.setattr(wait_ci_forge, "_default_run_list_fn", lambda *_args: lambda _sha: [])
    monkeypatch.setattr(wait_ci_forge, "_github_ci_configured", lambda *_args: True)
    monkeypatch.setattr(merge_verification, "_declared_github_exclusion", lambda *_args: False)
    with pytest.raises(SystemExit) as exit_info:
        atk.main(["wi", "edit", name, "--target-repo", _REPO, "--body-file", str(proposal)], home=tmp_path)
    assert exit_info.value.code != 0
    assert "CIが未確認" in capsys.readouterr().err
    assert path.read_bytes() == original


@pytest.mark.parametrize(("source", "allowed"), [("push", True), ("web", False), ("merge_request_event", False), (None, False)])
def test_gitlab_merge_gate_preserves_observed_pipeline_source(monkeypatch, source, allowed):
    repository = "gitlab.example.com/group/project"
    body = _body().replace(_REPO, repository)
    pipeline = {"id": 7, "status": "success", "sha": _COMMIT}
    if source is not None:
        pipeline["source"] = source

    def forge_json(command, *_args, **_kwargs):
        if command[:3] == ["glab", "ci", "list"]:
            return [pipeline]
        assert command[0:2] == ["glab", "api"]
        assert "pipelines/7/jobs?" in command[2]
        return [{"id": 8, "name": "test", "status": "success", "allow_failure": False}]

    monkeypatch.setattr(wait_ci_forge, "_run_forge_json_command", forge_json)
    monkeypatch.setattr(wait_ci_forge, "_gitlab_ci_exclusion", lambda *_args: None)
    if allowed:
        assert merge_verification.check(body).endswith("push CI成功: 7")
    else:
        with pytest.raises(ActionableError, match="CIが未確認"):
            merge_verification.check(body)
