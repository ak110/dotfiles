"""公開plan-checkの由来通知がWI本文のBOMの有無によらず同じになることを検証する。"""

import collections.abc
import os
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._testing import git_repository, isolation, plan_fixture


@pytest.mark.parametrize("kind", ["awi", "uwi"])
@pytest.mark.parametrize("reject_migration", [False, True])
def test_public_plan_check_origin_notices_are_equal_with_bom(
    tmp_path: pathlib.Path,
    host_environ: collections.abc.Callable[[], dict[str, str]],
    kind: str,
    reject_migration: bool,
) -> None:
    """実際のrun-scriptの引数解決から通知・終了コードまでをBOM有無で比較する。"""
    repo = tmp_path / "repo"
    git_repository.init_repository(repo)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    git_repository.commit_all(repo, "base")
    plan = repo / "plan.md"
    plan.write_text(
        plan_fixture.current_plan(repo=repo, related_wi=plan_fixture.WI_FILES),
        encoding="utf-8",
    )
    notes = tmp_path / "notes"
    inbox = notes / "inbox"
    inbox.mkdir(parents=True)
    wi = inbox / plan_fixture.WI_FILES[0][0]
    text = f"---\ntype: {kind}\nsource: session-review\n---\n\n# 表題\n"
    env = host_environ()
    # Pythonの起動に必要なPATHを戻し、設定・保存先は隔離したまま公開CLIへ渡す。
    for name in (*isolation.HOME_ENVIRONMENT_NAMES, *isolation.CONFIG_DIRECTORY_ENVIRONMENT_NAMES):
        env[name] = os.environ[name]
    env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(notes)
    command = [sys.executable, "-m", "agent_toolkit.atk", "run-script", "plan-check", "--", str(plan), "--work-dir", str(repo)]
    if reject_migration:
        command.append("--reject-migration-warnings")
    results = []
    for prefix in ("", "\ufeff"):
        wi.write_text(prefix + text, encoding="utf-8")
        result = subprocess.run(
            command, cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
        )
        results.append((result.returncode, result.stdout, result.stderr))
    assert results[0] == results[1]
    code, stdout, stderr = results[0]
    assert code == int(reject_migration), (stdout, stderr)
    assert "WI本文の由来と一致しない" in stdout + stderr
    assert plan_fixture.WI_FILES[0][0] in stdout + stderr
