"""lane rebaseの契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import pytest

from agent_toolkit._atk import lane
from agent_toolkit._atk import lane_integration_input as _lane_integration_input
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing.lane_integration_support import (
    _adopt_argv,
    _integration_args,
    _integration_selection,
    _plan_operation,
    _rebased_integration,
    _record_wi_commit,
    _write_empty_finalization_manifest,
)
from agent_toolkit._testing.lane_support import _create, _invoke


def test_integrate_rebase_returns_mapping_and_requires_diagnostic_acceptance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """分岐した未push成果は内容比較と再検証を保存し、受理後だけffと最終化へ進む。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    (repo / "upstream").write_text("先行統合", encoding="utf-8")
    before = git_repository.commit_all(repo, "先行統合")
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    terminal = [
        sys.executable,
        "-c",
        "import subprocess,sys;assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()==sys.argv[1]",
        head,
    ]
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": [terminal]}],
                "handoff": str(handoff),
                "verification": [
                    [
                        sys.executable,
                        "-c",
                        "import pathlib;assert pathlib.Path('feature').read_text()=='成果';"
                        "assert pathlib.Path('upstream').exists()",
                    ]
                ],
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    code, pending, stderr = _invoke(capsys, *args)
    assert code == 1, stderr
    assert pending["phase"] == "awaiting-validation"
    mapping = pathlib.Path(pending["rewrite_map"])
    assert mapping.is_file()
    # 対応表は所有登録と同じ状態ディレクトリへ置き、managed-tempの失効で失われないようにする。
    assert mapping.parent == pathlib.Path(record["record_path"]).with_name("lane-01-integration")
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["wis"][0]["commands"][0][-1] = pending["new_head"]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    assert code == 2 and "--amend-finalization" in stderr
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"], "--amend-finalization")
    assert code == 0 and final["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == pending["new_head"]
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr
    # 回収後も対応表を証拠として回収結果の領域に残し、状態ディレクトリからは外す。
    assert not mapping.exists()
    assert (pathlib.Path(deleted["integration_materials"]["lane-01"]) / mapping.name).is_file()


@pytest.mark.parametrize("phase", ["rebased", "awaiting-validation", "verified"])
@pytest.mark.parametrize("changed", ["verification", "plans", "wis", "handoff"])
@pytest.mark.parametrize("reviewed_key", ["old_head", "new_head"])
def test_integrate_restart_review_keeps_rebase_validation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
    changed: str,
    reviewed_key: str,
) -> None:
    """全ての工程と入力是正で、同じrebase結果を再検証・検収してから統合する。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    record_path = pathlib.Path(record["record_path"])
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    # Git統合前の永続状態を再現する。verifiedは受理後・ff前の中断に相当する。
    stored["integration"]["phase"] = phase
    record_path.write_text(json.dumps(stored), encoding="utf-8")
    if changed == "verification":
        value[changed] = [[sys.executable, "-c", "assert True"]]
    elif changed in {"plans", "wis"}:
        value[changed] = [
            {
                "name": "plan.md" if changed == "plans" else "20261023-000000-001.md",
                "commands": [[sys.executable, "-c", "pass"]],
            }
        ]
    else:
        handoff = tmp_path / "corrected-handoff.md"
        handoff.touch()
        value[changed] = str(handoff)
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, original[reviewed_key])
    code, pending, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    assert pending["new_head"] == original["new_head"] and pending["rewrite_map"] == original["rewrite_map"]
    previous = json.loads(record_path.read_text(encoding="utf-8"))["previous_integration"]
    assert previous == stored["integration"]
    assert pending["results"]["history-compare"] == original["results"]["history-compare"]
    if changed == "verification":
        assert pending["results"]["verification-0"]["record_path"] != original["results"]["verification-0"]["record_path"]
    else:
        assert pending["results"]["verification-0"]["reused_from"] == original["results"]["verification-0"]["record_path"]
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == original["new_base"]
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", original["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == original["new_head"]


def test_integrate_resumes_failed_history_compare_without_rebasing_again(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """比較コマンド失敗後は保存した旧新端点を再検収し、受理まで統合先を保持する。"""
    original_script = lane._atk_script  # pylint: disable=protected-access

    def failed_compare(*arguments: str, **_kwargs: Any) -> list[str]:
        if arguments[:2] == ("run-script", "history-compare"):
            return [sys.executable, "-c", "raise SystemExit(3)"]
        return original_script(*arguments)

    with monkeypatch.context() as patch:
        patch.setattr(_lane_integration_input, "atk_script", failed_compare)
        repo, selected, record = _create(tmp_path, monkeypatch, capsys)
        _integration_selection(selected, monkeypatch, repo)
        worktree = pathlib.Path(record["worktree"])
        (worktree / "feature").write_text("成果", encoding="utf-8")
        reviewed = git_repository.commit_all(worktree, "成果")
        (repo / "upstream").write_text("先行統合", encoding="utf-8")
        before = git_repository.commit_all(repo, "先行統合")
        manifest = _write_empty_finalization_manifest(tmp_path)
        args = _integration_args(record, repo, manifest, reviewed)
        code, _result, stderr = _invoke(capsys, *args)
        assert code == 2 and "history-compare" in stderr
    state = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))["integration"]
    assert state["phase"] == "rebased"
    assert "history-compare" not in state["completed"]
    reflog = git_repository.git_output(worktree, "reflog", "--format=%H %gs")
    code, pending, stderr = _invoke(capsys, *args)
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    for endpoint in ("old_base", "old_head", "new_base", "new_head"):
        assert pending[endpoint] == state[endpoint]
    assert git_repository.git_output(worktree, "reflog", "--format=%H %gs") == reflog
    assert json.loads(pathlib.Path(pending["rewrite_map"]).read_text(encoding="utf-8")) == {reviewed: pending["new_head"]}
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == pending["new_head"]


@pytest.mark.parametrize("resume_kind", ["accept", "advance", "correct-and-advance", "multiple"])
def test_integrate_resumes_conflict_resolution_following_new_upstream_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], resume_kind: str
) -> None:
    """競合解消で統合先の新規ファイルへ追随したcommitを、--resolved-conflict付きの同じ統合の再開で受理する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "shared.txt").write_text("レーン\n", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "成果")
    resolved = [reviewed]
    if resume_kind == "multiple":
        (worktree / "second.txt").write_text("レーンの追加\n", encoding="utf-8")
        reviewed = git_repository.commit_all(worktree, "第二の成果")
        resolved.append(reviewed)
        (repo / "second.txt").write_text("上流の追加\n", encoding="utf-8")
    (repo / "shared.txt").write_text("上流\n", encoding="utf-8")
    (repo / "added_test.py").write_text("旧名称\n", encoding="utf-8")
    before = git_repository.commit_all(repo, "先行統合")
    manifest = _write_empty_finalization_manifest(tmp_path)
    args = _integration_args(record, repo, manifest, reviewed)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2, stderr
    (worktree / "shared.txt").write_text("レーンと上流\n", encoding="utf-8")
    resolved_paths = ["shared.txt"]
    if resume_kind != "multiple":
        (worktree / "added_test.py").write_text("新名称\n", encoding="utf-8")
        resolved_paths.append("added_test.py")
    git_repository.run_git(worktree, "add", "--", *resolved_paths)
    continued = git_repository.run_git(
        worktree,
        "-c",
        "core.editor=true",
        "-c",
        "submodule.recurse=false",
        "rebase",
        "--continue",
        check=resume_kind != "multiple",
    )
    if resume_kind == "multiple":
        assert continued.returncode == 1
        (worktree / "second.txt").write_text("レーンと上流の追加\n", encoding="utf-8")
        git_repository.run_git(worktree, "add", "--", "second.txt")
        git_repository.run_git(worktree, "-c", "core.editor=true", "-c", "submodule.recurse=false", "rebase", "--continue")
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "history-compare" in stderr
    code, _result, stderr = _invoke(capsys, *args, "--resolved-conflict", before)
    assert code == 2 and "history-compare" in stderr
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))["integration"]
    assert not stored.get("resolved_conflicts")
    conflict_args = [value for oid in resolved for value in ("--resolved-conflict", oid)]
    code, pending, stderr = _invoke(capsys, *args, *conflict_args)
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    assert set(pending["resolved_conflicts"]) == set(resolved)
    mapping = json.loads(pathlib.Path(pending["rewrite_map"]).read_text(encoding="utf-8"))
    assert set(mapping) == set(resolved) and mapping[reviewed] == pending["new_head"]
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before
    if resume_kind in {"advance", "correct-and-advance"}:
        if resume_kind == "correct-and-advance":
            (worktree / "correction").write_text("是正", encoding="utf-8")
            reviewed = git_repository.commit_all(worktree, "是正")
        (repo / "later").write_text("追加の先行成果", encoding="utf-8")
        git_repository.commit_all(repo, "統合先の追加更新")
        args = _integration_args(record, repo, manifest, reviewed)
        code, pending, stderr = _invoke(capsys, *args, "--restart-review")
        assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == pending["new_head"]


@pytest.mark.parametrize("invalid", ["unchanged", "all", "resolved"])
def test_integrate_excludes_paths_identical_to_upstream(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """変更から外れたパスの明示だけを受理し、成功した指定を省略再開でも再利用する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    dropped = ["shared.txt", "shared second.txt"]
    for name in dropped:
        (worktree / name).write_text("レーン\n", encoding="utf-8")
        (repo / name).write_text("上流\n", encoding="utf-8")
    (worktree / "feature").write_text("成果\n", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "成果")
    before = git_repository.commit_all(repo, "先行統合")
    manifest = _write_empty_finalization_manifest(tmp_path)
    args = _integration_args(record, repo, manifest, reviewed)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2, stderr
    for name in dropped:
        (worktree / name).write_text("上流\n", encoding="utf-8")
    git_repository.run_git(worktree, "add", "--", *dropped)
    git_repository.run_git(worktree, "-c", "core.editor=true", "-c", "submodule.recurse=false", "rebase", "--continue")
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2, stderr
    exclusions = [value for name in dropped for value in ("--exclude-path", reviewed[:12], name)]
    if invalid == "unchanged":
        rejected = ["--exclude-path", reviewed, "unchanged.txt"]
    elif invalid == "all":
        rejected = [*exclusions, "--exclude-path", reviewed, "feature"]
    else:
        rejected = [*exclusions, "--resolved-conflict", reviewed]
    code, _result, stderr = _invoke(capsys, *args, *rejected)
    assert code == 2, stderr
    record_path = pathlib.Path(record["record_path"])
    stored = json.loads(record_path.read_text(encoding="utf-8"))["integration"]
    assert not stored.get("excluded_paths")
    assert not stored.get("resolved_conflicts")
    code, pending, stderr = _invoke(capsys, *args, *exclusions)
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    expected = [[reviewed, name] for name in sorted(dropped)]
    stored = json.loads(record_path.read_text(encoding="utf-8"))["integration"]
    assert stored["excluded_paths"] == stored["comparison"]["excluded_paths"] == expected
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == before
    code, _result, stderr = _invoke(capsys, *args, *rejected)
    assert code == 2, stderr
    stored = json.loads(record_path.read_text(encoding="utf-8"))["integration"]
    assert stored["excluded_paths"] == stored["comparison"]["excluded_paths"] == expected
    # 比較を再実行させ、完了済み工程の省略と保存した指定の再利用を区別する。
    pathlib.Path(pending["rewrite_map"]).unlink()
    code, pending, stderr = _invoke(capsys, *args)
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    assert pending["excluded_paths"] == expected
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == pending["new_head"]
    assert (repo / "feature").read_text(encoding="utf-8") == "成果\n"
    assert all((repo / name).read_text(encoding="utf-8") == "上流\n" for name in dropped)


def test_integrate_drops_rejected_conflict_input(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """親が変わっても同じ内容の旧commitへの誤指定を、指定なしの公開再開で外せる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "context.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    git_repository.commit_all(worktree, "準備")
    git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    (worktree / "context.txt").write_text("A\nb\nc\nd\nE\n", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "解消")
    (repo / "context.txt").write_text("A\nb\nc\nd\ne\n", encoding="utf-8")
    git_repository.commit_all(repo, "上流")
    manifest = _write_empty_finalization_manifest(tmp_path)
    args = _integration_args(record, repo, manifest, reviewed)
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 1, stderr
    code, _result, stderr = _invoke(capsys, *args, "--resolved-conflict", reviewed)
    assert code == 2 and "history-compare" in stderr
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))["integration"]
    assert not stored.get("resolved_conflicts")
    code, pending, stderr = _invoke(capsys, *args)
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr


@pytest.mark.parametrize("difference", [None, "git_head", "argv", "cwd", "environment", "failed", "incomplete", "output"])
def test_integrate_imports_only_matching_success(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    difference: str | None,
) -> None:
    """公開manifestへ生成済み記録を渡し、一致した成功だけが子の再実行を省く。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    observed = json.loads(pathlib.Path(original["results"]["verification-0"]["record_path"]).read_text(encoding="utf-8"))
    exported = tmp_path / "success.json"
    if difference in {"git_head", "cwd"}:
        observed[difference] = "different"
    elif difference == "argv":
        observed["argv"] = [sys.executable, "-c", "assert True"]
    elif difference == "environment":
        monkeypatch.setenv("LANE_ACCEPTANCE_CONDITION", "changed")
    elif difference == "failed":
        observed["child_exit_code"] = observed["wrapper_exit_code"] = 3
    elif difference == "incomplete":
        observed["child_exit_code"] = None
    elif difference == "output":
        observed["stdout_path"] = str(tmp_path / "absent-output")
    exported.write_text(json.dumps(observed), encoding="utf-8")
    value["verification_records"] = [str(exported)]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    stored["integration"]["results"].pop("verification-0")
    stored["integration"]["completed"].remove("verification-0")
    pathlib.Path(record["record_path"]).write_text(json.dumps(stored), encoding="utf-8")
    executed = []
    execute = lane.run_command.execute

    def counted(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        executed.append(argv)
        return execute(argv, *args, **kwargs)

    monkeypatch.setattr(lane.run_command, "execute", counted)
    code, pending, stderr = _invoke(
        capsys, *_integration_args(record, repo, manifest, original["old_head"]), "--restart-review"
    )
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    result = pending["results"]["verification-0"]
    if difference is None:
        assert not executed
        assert result["reused_from"] == str(exported)
    else:
        assert executed == value["verification"]
        assert "reused_from" not in result


def test_integrate_restart_tracks_advanced_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検証失敗後に前進した統合先へ追随し、元の成果と対応表を保持する。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys, failed=True)
    (repo / "later").write_text("追加の先行成果", encoding="utf-8")
    latest = git_repository.commit_all(repo, "統合先の追加更新")
    value["verification"] = [[sys.executable, "-c", "pass"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, original["old_head"])
    code, pending, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    assert pending["new_base"] == latest
    worktree = pathlib.Path(record["worktree"])
    assert (worktree / "later").read_text(encoding="utf-8") == "追加の先行成果"
    assert (worktree / "feature").exists()
    mapping = json.loads(pathlib.Path(pending["rewrite_map"]).read_text(encoding="utf-8"))
    assert mapping[original["old_head"]] == pending["new_head"]


def test_integrate_restart_keeps_corrections_when_target_advances(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """追加commitと統合先前進が同居しても、旧系列の対応と追加commitの最新OIDを別々に返す。"""
    repo, record, manifest, value, original = _rebased_integration(
        tmp_path, monkeypatch, capsys, failed=True, record_commits=True
    )
    worktree = pathlib.Path(record["worktree"])
    name = "20261023-000000-001.md"
    handoff = pathlib.Path(value["handoff"])
    (worktree / "correction").write_text("是正", encoding="utf-8")
    correction = git_repository.commit_all(worktree, "是正")
    _record_wi_commit(
        capsys,
        handoff,
        worktree,
        name,
        correction,
        original["new_head"],
        step="是正",
        result="rebase後に追加したcommitのWI対応を記録",
    )
    (repo / "later").write_text("追加の先行成果", encoding="utf-8")
    latest = git_repository.commit_all(repo, "統合先の追加更新")
    value["verification"] = [[sys.executable, "-c", "pass"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, correction)
    code, pending, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    assert pending["old_head"] == original["old_head"] and pending["new_base"] == latest
    assert pending["additional_commits"] == [pending["new_head"]]
    assert pending["additional_rewrites"] == {correction: pending["new_head"]}
    previous = git_repository.git_output(worktree, "rev-parse", "HEAD^")
    assert json.loads(pathlib.Path(pending["rewrite_map"]).read_text(encoding="utf-8")) == {original["old_head"]: previous}
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == latest
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="採用前のWI")
    additional = tmp_path / "additional-rewrites.json"
    additional.write_text(json.dumps(pending["additional_rewrites"]), encoding="utf-8")
    combined = tmp_path / "combined-rewrites.json"
    combined.write_text(
        json.dumps(
            {**json.loads(pathlib.Path(pending["rewrite_map"]).read_text(encoding="utf-8")), **pending["additional_rewrites"]}
        ),
        encoding="utf-8",
    )
    _, diagnostic = _plan_operation(
        capsys,
        "plan-rewrite",
        "--worktree",
        str(worktree),
        "--previous-head",
        original["old_head"],
        "--rewrite-map",
        str(combined),
        "--handoff",
        str(handoff),
        "--completed-step",
        "再統合",
        "--result",
        "異なる旧系列の一括消費",
        expected=1,
    )
    assert "旧OIDが書換え前のHEADから到達できません" in diagnostic
    # 追加分は対応記録の部分更新を持つ公開操作で先に継承し、元系列は範囲検査を持つ公開操作へ渡す。
    _plan_operation(
        capsys,
        "plan-progress",
        str(handoff),
        "--worktree",
        str(worktree),
        "--handoff",
        "--allowed-awi",
        name,
        "--rewrite-map",
        str(additional),
        "--completed-step",
        "是正の履歴継承",
        "--result",
        "追加commitの対応を現在OIDへ継承",
    )
    _plan_operation(
        capsys,
        "plan-rewrite",
        "--worktree",
        str(worktree),
        "--previous-head",
        original["old_head"],
        "--rewrite-map",
        pending["rewrite_map"],
        "--handoff",
        str(handoff),
        "--completed-step",
        "元系列の履歴継承",
        "--result",
        "最初の旧系列から現在OIDへ継承",
    )
    output, _diagnostic = _plan_operation(
        capsys,
        "plan-commits",
        str(handoff),
        "--worktree",
        str(worktree),
        "--handoff",
        "--allowed-awi",
        name,
    )
    commits = json.loads(output)["commits"]
    assert {git_repository.git_output(worktree, "rev-parse", oid) for oid in commits} == {previous, pending["new_head"]}
    value["wis"] = [
        {
            "name": name,
            "commit_record": str(handoff),
            "commands": [_adopt_argv(repo, name, commits)],
        }
    ]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"], "--amend-finalization")
    assert code == 0 and final["phase"] == "finalized", stderr
    assert (repo / "correction").read_text(encoding="utf-8") == "是正"
    adopted = wi_entry_files.entry_path(notes, name, state="adopted").read_text(encoding="utf-8")
    assert all(oid in adopted for oid in commits)


def test_integrate_restart_rejects_non_descendant(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """保存したrebase系列外への変更は、統合前に公開操作で拒否する。"""
    repo, record, manifest, _value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    git_repository.run_git(worktree, "reset", "--hard", original["old_head"])
    args = _integration_args(record, repo, manifest, original["old_head"])
    code, _result, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 2 and "子孫" in stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == original["new_base"]


def test_integrate_rebuilds_expired_rebase_materials(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検収待ちの対応表と検証記録が期限付き領域から失われても、再生成して検収後の統合へ進める。"""
    repo, record, manifest, _value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    record_path = pathlib.Path(record["record_path"])
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    expired = tmp_path / "expired-managed-temp"
    # 旧状態はmanaged-tempの対応表と検証記録を指し、その保存先が失効している。
    stored["integration"]["rewrite_map"] = str(expired / "rewrite-map.json")
    stored["integration"]["results"]["verification-0"]["record_path"] = str(expired / "record.json")
    record_path.write_text(json.dumps(stored), encoding="utf-8")
    args = _integration_args(record, repo, manifest, original["old_head"])
    code, pending, stderr = _invoke(capsys, *args)
    assert code == 1 and pending["phase"] == "awaiting-validation", stderr
    mapping = pathlib.Path(pending["rewrite_map"])
    assert mapping.is_file() and mapping.parent == record_path.with_name("lane-01-integration")
    assert pathlib.Path(pending["results"]["verification-0"]["record_path"]).is_file()
    assert pending["new_head"] == original["new_head"]
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", original["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr
