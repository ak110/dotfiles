import argparse

import pytest
from agent_toolkit._atk import run_script
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository
from rewrite_plan_commits_test import WI_A, _Rebased, _snapshot
from rewrite_plan_commits_test import _rebased_fixture as _rebased_fixture  # pylint: disable=useless-import-alias


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "新書式の計画は本文を変えずに対応だけを公開名から書き換え、保存済み"
        + "計画の領域は拒否する。 実Gitの旧/新履歴とWI別の対応記録を保持す"
        + "る。Linux同群CPU最大0.909秒。"
    ),
)
def test_awi_plan_rewrites_mapping_without_changing_body(rebased: _Rebased, capsys: pytest.CaptureFixture[str]) -> None:
    content = (
        f"---\nlaunch_route: process-wi lane-01\ntarget_repo: {rebased.repo}\n"
        f"related_wis:\n  - {WI_A}\nwork_kind: 通常変更\n---\n\n# 計画\n\n本文\n"
    )
    rebased.plan.write_text(content, encoding="utf-8")
    body = rebased.plan.read_bytes()
    saved = rebased.repo.parent / "private-notes" / "plans" / "2026" / "10" / rebased.plan.name
    saved.parent.mkdir(parents=True)
    saved.write_bytes(body)
    commit_mapping.mapping_path(saved).write_bytes(commit_mapping.mapping_path(rebased.plan).read_bytes())
    argv = rebased.argv(rebased.range_map("a", "u", "b"))
    records = [saved, rebased.handoff, rebased.unaffected]
    before = _snapshot(records)
    saved_argv = [arg for arg in argv if not arg.startswith("--plan=")] + [f"--plan={saved}"]

    assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=["--", *saved_argv])) == 1
    error = capsys.readouterr().err
    assert str(saved) in error and "atk plans checkout" in error
    assert _snapshot(records) == before

    saved.unlink()
    commit_mapping.mapping_path(saved).unlink()
    assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=["--", *argv])) == 0
    assert f"追記: {rebased.plan}" in capsys.readouterr().out
    assert rebased.plan.read_bytes() == body
    assert not rebased.plan.with_suffix(".wi-rewrite.pending").exists()
    assert rebased.plan.with_suffix(".wi-rewrite.receipt").is_file()
    events = commit_mapping.read_events(rebased.plan, content)
    short = git_repository.git_output(rebased.repo, "rev-parse", "--short", rebased.new["a"])
    assert commit_mapping.get_commits(rebased.repo, events, [WI_A], {WI_A}) == {WI_A: [short]}
    after = _snapshot([rebased.plan])
    assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=["--", *argv])) == 0
    assert f"変更なし: {rebased.plan}" in capsys.readouterr().out
    assert _snapshot([rebased.plan]) == after
