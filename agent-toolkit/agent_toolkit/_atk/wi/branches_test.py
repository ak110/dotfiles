# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import dataclasses
import datetime
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config, lane
from agent_toolkit._atk.wi import alerts
from agent_toolkit._atk.wi.mutations import targets
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._git import remote
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment

REPO_ID = "example.test/team/repo"
PLAIN_ID = "example.test/team/plain"
_NOW = datetime.datetime(2026, 11, 18, 9, 0, 0)

pytestmark = pytest.mark.cpu_limit(
    2.0,
    reason="2ブランチのbare originとclone・worktreeを実Gitで作って系統ごとの動作を確かめる（Linux CPU合計0.6〜1.0秒）。",
)
_LEGACY_JSON_KEYS = {
    "filename",
    "type",
    "target_repo",
    "state",
    "ready",
    "blocked_reason",
    "source",
    "important",
    "priority",
    "priority_reason",
    "effect_check",
    "effect_check_reason",
    "summary",
}


@dataclasses.dataclass(frozen=True)
class BranchRepository:
    origin: pathlib.Path
    seed: pathlib.Path
    repo: pathlib.Path
    plain: pathlib.Path
    notes: pathlib.Path


@pytest.fixture(name="branch_repo")
def _branch_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> BranchRepository:
    origin = git.init_bare_repository(tmp_path / "origin.git")
    seed = git.init_repository(tmp_path / "seed", initial_branch="main", origin=str(origin), commit_message="base")
    git.run_git(seed, "push", "--quiet", "-u", "origin", "main")
    git.run_git(seed, "switch", "--quiet", "-c", "release")
    git.commit_all(seed, "保守ブランチだけの修正")
    git.run_git(seed, "push", "--quiet", "-u", "origin", "release")
    git.run_git(seed, "switch", "--quiet", "main")
    git.commit_all(seed, "次期開発だけの変更")
    git.run_git(seed, "push", "--quiet")
    repo = tmp_path / "repo"
    git.run_git(tmp_path, "clone", "--quiet", str(origin), str(repo))
    plain_origin = git.init_bare_repository(tmp_path / "plain.git")
    plain = git.init_repository(tmp_path / "plain", initial_branch="main", origin=str(plain_origin), commit_message="base")
    git.run_git(plain, "push", "--quiet", "-u", "origin", "main")
    # ローカルのbareリポジトリをoriginにしたまま、repository識別子だけをURL形にそろえる。
    original = remote.normalize_remote_url
    aliases = {str(origin): REPO_ID, str(plain_origin): PLAIN_ID}
    monkeypatch.setattr(remote, "normalize_remote_url", lambda value: aliases.get(value.strip()) or original(value))
    monkeypatch.setenv(
        "AGENT_TOOLKIT_CONFIG_REPOSITORIES",
        json.dumps(
            {
                REPO_ID: {
                    "edit": "allow",
                    "path": str(repo),
                    "process_loop": True,
                    "branches": ["release", "main"],
                    "default_branch": "main",
                    "merge_from": {"main": "release"},
                },
                PLAIN_ID: {"edit": "allow", "path": str(plain), "process_loop": True},
            }
        ),
    )
    notes_origin = git.init_bare_repository(tmp_path / "notes.git")
    notes = git.init_repository(
        tmp_path / "private-notes", initial_branch="main", origin=str(notes_origin), commit_message="base"
    )
    git.run_git(notes, "push", "--quiet", "-u", "origin", "main")
    return BranchRepository(origin, seed, repo, plain, notes)


def _write(notes: pathlib.Path, name: str, repo_id: str, extra: str = "") -> None:
    path = notes / "2026/11" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntarget_repo: {repo_id}\n{extra}type: awi\nstate: inbox\n---\n\n# {name}\n", encoding="utf-8")


def _add(tmp_path: pathlib.Path, *arguments: str) -> int:
    body = tmp_path / "body.md"
    body.write_text(AGENT_AWI_BODY, encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "add", "--body-file", str(body), "--source", "add-awi-by-user", *arguments], home=tmp_path, now=_NOW)
    return int(result.value.code or 0)


def _saved(notes: pathlib.Path) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(notes.glob("2026/11/20261118-*.md"))]


def _list(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, str, str]:
    capsys.readouterr()
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--skip-pull", *arguments], home=tmp_path)
    captured = capsys.readouterr()
    return int(result.value.code or 0), captured.out, captured.err


def test_unregistered_repository_keeps_current_behavior(
    branch_repo: BranchRepository, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add(tmp_path, "--target-repo", str(branch_repo.plain)) == 0
    (saved,) = _saved(branch_repo.notes)
    assert "target_branch" not in saved
    assert f"target_commit: {git.git_output(branch_repo.plain, 'rev-parse', 'HEAD')}" in saved
    code, out, _err = _list(tmp_path, capsys, f"--target-repo={branch_repo.plain}", "--jsonl")
    assert code == 0
    assert set(json.loads(out)) == _LEGACY_JSON_KEYS
    code, out, _err = _list(tmp_path, capsys, f"--target-repo={branch_repo.plain}", "--no-jsonl")
    assert "target_branch" not in out
    plain = next(item for item in config.resolve_configured_repositories() if item.repo_id == PLAIN_ID)
    assert (plain.key, plain.branch, plain.is_default) == (PLAIN_ID, None, True)
    assert _add(tmp_path, "--target-repo", str(branch_repo.plain), "--target-branch", "main") == 1
    assert "系統を登録していない" in capsys.readouterr().err
    assert len(_saved(branch_repo.notes)) == 1


def test_add_defaults_to_default_branch_and_rejects_unknown(
    branch_repo: BranchRepository, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _add(tmp_path, "--target-repo", str(branch_repo.repo)) == 0
    assert _add(tmp_path, "--target-repo", str(branch_repo.repo), "--target-branch", "release") == 0
    default, release = _saved(branch_repo.notes)
    assert "target_branch: main" in default
    assert f"target_commit: {git.git_output(branch_repo.repo, 'rev-parse', 'origin/main')}" in default
    assert "target_branch: release" in release
    assert f"target_commit: {git.git_output(branch_repo.repo, 'rev-parse', 'origin/release')}" in release
    capsys.readouterr()
    assert _add(tmp_path, "--target-repo", str(branch_repo.repo), "--target-branch", "unknown") == 1
    assert "登録した系統: release, main" in capsys.readouterr().err
    assert len(_saved(branch_repo.notes)) == 2


def test_list_filters_and_shows_branches(
    branch_repo: BranchRepository, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(branch_repo.notes, "rel.md", REPO_ID, "target_branch: release\n")
    _write(branch_repo.notes, "legacy.md", REPO_ID)
    _write(branch_repo.notes, "gone.md", REPO_ID, "target_branch: removed\n")
    code, out, _err = _list(tmp_path, capsys, f"--target-repo={branch_repo.repo}", "--jsonl")
    records = {record["filename"]: record for record in map(json.loads, out.splitlines())}
    assert code == 0
    assert {name: record["target_branch"] for name, record in records.items()} == {
        "rel.md": "release",
        "legacy.md": "main",
        "gone.md": "removed",
    }
    assert records["gone.md"]["blocked_reason"] == "branch-unregistered"
    assert records["legacy.md"]["ready"] is True
    code, out, _err = _list(tmp_path, capsys, f"--target-repo={branch_repo.repo}", "--target-branch=release", "--jsonl")
    assert [json.loads(line)["filename"] for line in out.splitlines()] == ["rel.md"]
    code, out, _err = _list(tmp_path, capsys, f"--target-repo={branch_repo.repo}", "--target-branch=auto", "--no-jsonl")
    assert "legacy.md" in out
    assert "rel.md" not in out
    assert "target_branch=main" in out
    other = tmp_path / "topic"
    git.run_git(branch_repo.repo, "worktree", "add", "--quiet", "--track", "-b", "topic", str(other), "origin/release")
    git.run_git(other, "push", "--quiet", "origin", "HEAD:refs/heads/topic")
    git.run_git(other, "branch", "--quiet", "--set-upstream-to=origin/topic")
    code, _out, err = _list(tmp_path, capsys, f"--target-repo={other}", "--target-branch=auto")
    assert code == 1
    assert "topicは登録した系統ではない" in err
    assert "登録した系統: release, main" in err


@pytest.mark.usefixtures("branch_repo")
def test_edit_validates_branch_and_drops_target_commit() -> None:
    original = f"---\ntarget_repo: {REPO_ID}\ntarget_branch: main\ntarget_commit: abc\ntype: awi\n---\n\n本文\n"
    moved = targets.invalidate_repo_bound_metadata(original, original.replace("target_branch: main", "target_branch: release"))
    assert "target_branch: release" in moved
    assert "target_commit" not in moved
    with pytest.raises(WebInputError, match="登録されていない系統"):
        targets.invalidate_repo_bound_metadata(original, original.replace("target_branch: main", "target_branch: x"))


def test_lane_hook_names_differ_per_branch(
    branch_repo: BranchRepository,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    created = tmp_path / "hook-created"
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,pathlib,subprocess,sys\n"
        "name=json.load(sys.stdin)['name']\n"
        "path=pathlib.Path(sys.argv[1],name)\n"
        "subprocess.run(['git','worktree','add','--quiet','-b','worktree-'+name,str(path)],check=True,stdout=sys.stderr)\n"
        "print(path)\n",
        encoding="utf-8",
    )
    argv = [sys.executable, str(script), str(created)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = branch_repo.repo / ".claude/settings.local.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(
        json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8"
    )
    git.run_git(branch_repo.repo, "branch", "--quiet", "release", "origin/release")
    selected = tmp_path / "selection.yaml"
    selected.write_text(json.dumps({"レーンの所要時間": [{"レーン": "lane-01", "統合状態": "未統合"}]}), encoding="utf-8")
    worktrees = {}
    for name in ("release", "main"):
        monkeypatch.setenv(lane.SESSION_ENV, f"branch-session-{name}")
        capsys.readouterr()
        arguments = ["lane", "create", "--repo", str(branch_repo.repo), "--base-branch", name, "--lane", "lane-01"]
        with pytest.raises(SystemExit) as result:
            atk.main([*arguments, "--selection-file", str(selected)])
        output = capsys.readouterr()
        assert result.value.code == 0, output.err
        worktrees[name] = json.loads(output.out)["worktree"]
    assert sorted(pathlib.Path(path).name for path in worktrees.values()) == [
        "process-wi-main-lane-01",
        "process-wi-release-lane-01",
    ]


def test_ci_failures_are_filed_per_branch(
    branch_repo: BranchRepository, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI失敗の監視は登録した系統ごとにブランチを照会し、起票するAWIは失敗した系統を宛先に持つ。"""
    queried: list[str] = []

    def run_list(repo: str, branch: str) -> list[dict]:
        del repo
        queried.append(branch)
        conclusion = "failure" if branch == "release" else "success"
        run = {"workflowName": "CI", "event": "push", "status": "completed", "conclusion": conclusion}
        return [{**run, "databaseId": 10, "url": "https://example.test/run/10", "headSha": "a" * 40}]

    monkeypatch.setattr(alerts, "_github_head", lambda _repo, _branch: "a" * 40)
    monkeypatch.setattr(alerts, "_github_failure_diagnostics", lambda _repo, run_id: f"失敗run{run_id}の診断")
    for branch in ("release", "main"):
        alerts.check_and_submit_alerts(
            branch_repo.notes,
            REPO_ID,
            branch_repo.repo,
            forge="github",
            now=_NOW,
            run_list_fn=run_list,
            branch=branch,
            include_repository_alerts=branch == "main",
        )
    assert queried == ["release", "main"]
    (saved,) = _saved(branch_repo.notes)
    assert "target_branch: release" in saved
    assert "source: alert-monitor" in saved
    assert "失敗run10の診断" in saved
    calls: list[tuple[str | None, bool]] = []

    def record(*_args: object, branch: str | None = None, include_repository_alerts: bool = True, **_kwargs: object):
        calls.append((branch, include_repository_alerts))
        return alerts.AlertCheckResult(0, ())

    monkeypatch.setattr(alerts, "check_and_submit_alerts", record)
    monkeypatch.chdir(branch_repo.repo)
    with pytest.raises(SystemExit) as result:
        atk.main(
            ["wi", "check-alerts", "--target-repo", str(branch_repo.repo), "--forge", "github"], home=branch_repo.notes.parent
        )
    assert result.value.code == 0
    assert calls == [("release", False), ("main", True)]
    assert "系統: release, main" in capsys.readouterr().out
