import argparse
import json
import subprocess

import pytest

from agent_toolkit._atk import config_migration, config_settings, research_subscriptions, run_script
from agent_toolkit._testing import git_repository


@pytest.fixture(name="clones")
def _clones(tmp_path):
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    first = git_repository.init_repository(
        tmp_path / "first",
        initial_branch="main",
        origin=str(remote),
        commit_message="初回",
    )
    git_repository.run_git(first, "push", "--set-upstream", "origin", "main")
    second = tmp_path / "second"
    git_repository.run_git(tmp_path, "clone", str(remote), str(second))
    return remote, first, second


def _run(*args):
    return run_script.dispatch(argparse.Namespace(script_name="research-sources", script_args=list(args)))


def test_answer_updates_latest_subscription_on_another_host(clones, monkeypatch, capsys):
    remote, first, second = clones
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(first))
    assert _run("--add-account=One", "--add-query=two words") == 0
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(second))
    assert _run("--remove-account=@one", "--add-account=Two") == 0
    assert _run("--remove-account=one", "--add-account=two") == 0

    assert research_subscriptions.read(second) == {"accounts": ("Two",), "queries": ("two words",)}
    assert research_subscriptions.read(first)["accounts"] == ("One",)
    exported = git_repository.git_output(remote, "show", f"HEAD:{research_subscriptions.FILENAME}")
    assert json.loads(exported) == {"accounts": ["Two"], "queries": ["two words"]}
    assert git_repository.git_output(remote, "rev-list", "--count", "HEAD") == "3"
    assert capsys.readouterr().out.count('"x_subscriptions"') == 3


def test_legacy_migration_keeps_shared_values_and_existing_permissions(clones, monkeypatch):
    remote, first, second = clones
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(first))
    assert _run("--add-account=shared", "--remove-query=unused") == 0
    config_settings.save_config(
        {
            "weekly_research_x_accounts": '["different"]',
            "weekly_research_x_queries": '["legacy query"]',
            "weekly_research_repos": '["github.com/example/known","https://github.com/example/other.git"]',
            "repositories": '{"github.com/example/known":{"edit":"awi"}}',
        }
    )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(second))

    config_migration.migrate_legacy_settings()

    stored = config_settings.load_config_strict()
    assert set(stored) == {"repositories"}
    registered = config_settings.repository_settings()
    assert {key: item["edit"] for key, item in registered.items()} == {
        "github.com/example/known": "awi",
        "github.com/example/other": "deny",
    }
    assert not registered["github.com/example/other"]["process_loop"]
    assert research_subscriptions.read(second) == {"accounts": ("shared",), "queries": ("legacy query",)}
    config_migration.migrate_legacy_settings()
    assert git_repository.git_output(remote, "rev-list", "--count", "HEAD") == "3"


def test_migration_keeps_old_value_until_sync_can_succeed(clones, monkeypatch):
    remote, first, second = clones
    config_settings.save_config({"weekly_research_x_accounts": '["old"]'})
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(first))
    git_repository.run_git(first, "remote", "set-url", "--push", "origin", str(first / "missing.git"))

    with pytest.raises(subprocess.CalledProcessError):
        config_migration.migrate_legacy_settings()
    assert config_settings.load_config_strict() == {"weekly_research_x_accounts": '["old"]'}

    git_repository.run_git(first, "remote", "set-url", "--push", "origin", str(remote))
    config_migration.migrate_legacy_settings()
    assert config_settings.load_config_strict() == {}
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(second))
    assert _run("--subscription-candidates") == 0
    assert research_subscriptions.read(second) == {"accounts": ("old",)}


def test_legacy_migration_does_not_restore_removed_subscription(clones, monkeypatch):
    _, first, second = clones
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(first))
    assert _run("--add-account=old") == 0
    assert _run("--remove-account=old") == 0
    config_settings.save_config({"weekly_research_x_accounts": '["old"]'})
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(second))

    config_migration.migrate_legacy_settings()

    assert research_subscriptions.read(second) == {"accounts": ()}
    assert config_settings.load_config_strict() == {}
