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
                dict(databaseId=ident, headSha=oid, status=status, conclusion=conclusion, event="push")
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
    values = {"target_repo": _REPO, "commit": _COMMIT, "argv": ["make", "test"], "exit_code": 0, "output": str(report)}
    evidence.write_text(json.dumps(values), encoding="utf-8")
    if configured:
        with pytest.raises(ActionableError, match="CIが未確認"):
            merge_verification.check(_body(evidence))
    else:
        assert merge_verification.check(_body(evidence)).startswith("CI非対象")
        evidence.write_text(json.dumps({**values, "commit": "b" * 40}), encoding="utf-8")
        with pytest.raises(ActionableError, match="同等の検証記録"):
            merge_verification.check(_body(evidence))


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
        atk.main(["wi", "edit", name, "--body-file", str(proposal)], home=tmp_path)
    assert exit_info.value.code != 0
    assert "CIが未確認" in capsys.readouterr().err
    assert path.read_bytes() == original
