import datetime
import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config_settings
from agent_toolkit._atk.wi import alerts, entries, frontmatter, process_loop_alerts, process_loop_env
from agent_toolkit._atk.wi.alerts_test import _forge_responses, _prepare_alert_cli, _saved_awis_by_heading
from agent_toolkit._common import state_paths
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_mutations_support import _init_notes_with_origin


def _invoke_alert_cli(repo: pathlib.Path, *, periodic: bool = False) -> int:
    argv = ["wi", "check-alerts", "--target-repo", str(repo)]
    if periodic:
        argv.append("--periodic")
    with pytest.raises(SystemExit) as result:
        atk.main(argv, now=datetime.datetime(2026, 1, 1))
    assert isinstance(result.value.code, int)
    return result.value.code


@pytest.mark.usefixtures("leaf_git_commands")
def test_periodic_alert_cli_respects_settings_interval_and_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path, mock_notes=False)
    remote = tmp_path / "notes.git"
    _init_notes_with_origin(notes, remote)
    repo_id = "github.com/owner/repo"
    state = tmp_path / "state"
    monkeypatch.setattr(state_paths, "state_dir", lambda: state)
    clock = SimpleNamespace(value=10000)
    monkeypatch.setattr(process_loop_alerts, "time", SimpleNamespace(time=lambda: clock.value))
    monkeypatch.delenv(process_loop_env.NO_ALERTS_ENV, raising=False)
    monkeypatch.delenv(process_loop_env.ALERT_INTERVAL_ENV, raising=False)
    settings: dict[str, dict] = {}
    monkeypatch.setattr(config_settings, "repository_settings", lambda **_kwargs: settings)

    def response(command: list[str]) -> object:
        if command[:3] == ["gh", "run", "list"]:
            return []
        match command[6]:
            case "repos/owner/repo":
                return {
                    "visibility": "public",
                    "private": False,
                    "full_name": "owner/repo",
                    "owner": {"type": "User", "login": "owner"},
                }
            case "repos/owner/repo/issues?state=open&per_page=100":
                return [
                    [
                        {
                            "state": "open",
                            "number": 7,
                            "html_url": f"https://{repo_id}/issues/7",
                            "user": {"login": "owner"},
                            "body": "改善を提案する。",
                        }
                    ]
                ]
            case "repos/owner/repo/pulls?state=open&per_page=100":
                return [[]]
            case _:
                pytest.fail(f"想定外の照会: {command}")

    calls: list[list[str]] = []
    _forge_responses(monkeypatch, response, calls)
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert not calls
    settings[repo_id] = {"github_issues": True}
    monkeypatch.setenv(process_loop_env.NO_ALERTS_ENV, "1")
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert not calls
    assert not state.exists()
    assert _invoke_alert_cli(repo) == 0
    assert [command[:3] for command in calls] == [["gh", "run", "list"]]
    calls.clear()

    monkeypatch.setenv(process_loop_env.NO_ALERTS_ENV, "0")
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert len(calls) == 4
    assert alerts.existing_github_sources(notes, repo_id, entry_type="awi") == {f"https://{repo_id}/issues/7"}
    clock.value += 1799
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert len(calls) == 4
    clock.value += 1
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert len(calls) == 8
    monkeypatch.setenv(process_loop_env.ALERT_INTERVAL_ENV, "5")
    clock.value += 5
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert len(calls) == 12
    (record,) = state.glob("wi-periodic-alerts-*.time")
    record.write_bytes(b"\xff")
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert len(calls) == 16
    assert len(_saved_awis_by_heading(notes)) == 1
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == git_repository.git_output(remote, "rev-parse", "main")


@pytest.mark.parametrize(("interval", "due"), [(None, 1800), ("3600", 3600)])
def test_periodic_gitlab_schedule_is_submitted_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, interval: str | None, due: int
) -> None:
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path, forge="gitlab")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    clock = SimpleNamespace(value=10000)
    monkeypatch.setattr(process_loop_alerts, "time", SimpleNamespace(time=lambda: clock.value))
    if interval is None:
        monkeypatch.delenv(process_loop_env.ALERT_INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(process_loop_env.ALERT_INTERVAL_ENV, interval)
    schedule_list = "projects/owner%2Frepo/pipeline_schedules?per_page=100"

    def response(command: list[str]) -> object:
        assert command[0] == "glab"
        if command[1:3] == ["ci", "list"]:
            return []
        if command[2] == schedule_list:
            return [{"id": 1, "ref": "main", "active": True}]
        return {"last_pipeline": {"id": 23, "status": "failed", "ref": "main"}}

    calls: list[list[str]] = []
    _forge_responses(monkeypatch, response, calls)
    monkeypatch.setenv(process_loop_env.NO_ALERTS_ENV, "1")
    assert _invoke_alert_cli(repo, periodic=True) == 0
    assert not calls
    monkeypatch.setenv(process_loop_env.NO_ALERTS_ENV, "0")
    for elapsed, expected_checks in [(0, 1), (due - 1, 1), (1, 2)]:
        clock.value += elapsed
        assert _invoke_alert_cli(repo, periodic=True) == 0
        assert sum(schedule_list in command for command in calls) == expected_checks
    assert list(_saved_awis_by_heading(notes)) == ["# パイプライン23失敗"]


@pytest.mark.usefixtures("leaf_git_commands")
def test_periodic_import_refreshes_and_publishes_shared_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path, mock_notes=False)
    remote = tmp_path / "notes.git"
    _init_notes_with_origin(notes, remote)
    other = tmp_path / "other-notes"
    git_repository.run_git(tmp_path, "clone", "--branch", "main", str(remote), str(other))
    git_repository.run_git(other, "config", "user.name", "queue-test")
    git_repository.run_git(other, "config", "user.email", "queue-test@example.invalid")
    repo_id = "github.com/owner/repo"
    existing_url = f"https://{repo_id}/issues/7"
    proposal_url = f"https://{repo_id}/pull/8"
    path = entries.entry_path(other, "20251113-101112-001.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        frontmatter.serialize_frontmatter(
            {"state": "adopted", "type": "awi", "target_repo": repo_id, "github_source": existing_url},
            "\n# 共有済みの報告\n",
        ),
        encoding="utf-8",
    )
    git_repository.run_git(other, "add", "-A")
    git_repository.run_git(other, "commit", "-m", "save existing issue")
    git_repository.run_git(other, "push")
    assert alerts.existing_github_sources(notes, repo_id) == set()
    monkeypatch.setattr(config_settings, "repository_settings", lambda **_kwargs: {repo_id: {"github_issues": True}})
    monkeypatch.delenv(process_loop_env.NO_ALERTS_ENV, raising=False)

    def response(command: list[str]) -> object:
        if command[:3] == ["gh", "run", "list"]:
            return []
        assert command[:6] == ["gh", "api", "--hostname", "github.com", "--method", "GET"]
        match command[6]:
            case "repos/owner/repo":
                return {
                    "visibility": "public",
                    "private": False,
                    "full_name": "owner/repo",
                    "owner": {"type": "User", "login": "owner"},
                }
            case "repos/owner/repo/issues?state=open&per_page=100":
                return [[{"state": "open", "number": 7, "html_url": existing_url, "user": {"login": "owner"}}]]
            case "repos/owner/repo/pulls?state=open&per_page=100":
                return [[{"state": "open", "number": 8, "html_url": proposal_url, "user": {"login": "external"}}]]
            case "repos/owner/repo/pulls/8/files?per_page=100":
                return [[{"filename": "test.py"}]]
            case _:
                pytest.fail(f"想定外の照会: {command}")

    _forge_responses(monkeypatch, response, [])
    assert _invoke_alert_cli(repo, periodic=True) == 0
    git_repository.run_git(other, "pull", "--ff-only")
    assert alerts.existing_github_sources(other, repo_id, entry_type="awi") == {existing_url}
    assert alerts.existing_github_sources(other, repo_id, entry_type="uwi") == {proposal_url}
    assert len(list(entries.iter_entry_paths(other))) == 2
