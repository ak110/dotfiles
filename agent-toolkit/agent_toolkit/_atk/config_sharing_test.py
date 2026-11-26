"""同じ同期先を持つprivate-notesの2 cloneと別々の設定ディレクトリで、共有と端末固有の境界を検証する。  pyfltr: allow-comment"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._atk import (
    codex_provider_check,
    config_repositories,
    config_settings,
    config_shared_migration,
    reset_reservations,
)
from agent_toolkit._atk import config as config_module
from agent_toolkit._atk.wi import sync
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.config_support import _isolate_platformdirs, _main

__all__ = ["_isolate_platformdirs"]


class _Hosts:
    def __init__(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        remote = git.init_bare_repository(tmp_path / "notes.git")
        seed = git.init_repository(tmp_path / "seed", initial_branch="main", origin=str(remote), files={".gitkeep": ""})
        git.commit_all(seed, "initialize")
        git.run_git(seed, "push", "-u", "origin", "main")
        self.notes = {}
        for name in ("a", "b"):
            clone = tmp_path / f"notes-{name}"
            git.run_git(tmp_path, "clone", str(remote), str(clone))
            git.run_git(clone, "config", "user.name", name)
            git.run_git(clone, "config", "user.email", f"{name}@example.invalid")
            self.notes[name] = clone

    def use(self, name: str, *, pull: bool = True) -> pathlib.Path:
        """以降の操作を端末nameの設定ディレクトリとprivate-notesで行う。  pyfltr: allow-comment"""
        root = self.tmp_path / f"host-{name}"
        platformdirs = config_module.platformdirs
        self.monkeypatch.setattr(platformdirs, "user_config_dir", lambda _name, **_kwargs: str(root / "config"))
        self.monkeypatch.setattr(platformdirs, "user_state_dir", lambda _name, **_kwargs: str(root / "state"))
        self.monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(self.notes[name]))
        if pull:
            git.run_git(self.notes[name], "pull", "--ff-only")
        return root

    def local(self, name: str) -> dict[str, str]:
        path = self.tmp_path / f"host-{name}" / "config" / config_settings.CONFIG_FILENAME
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def write_local(self, name: str, values: dict[str, str]) -> None:
        path = self.tmp_path / f"host-{name}" / "config" / config_settings.CONFIG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values), encoding="utf-8")


@pytest.fixture(name="hosts")
def _hosts(tmp_path, monkeypatch) -> _Hosts:
    return _Hosts(tmp_path, monkeypatch)


def _clone(tmp_path: pathlib.Path, name: str, origin: str) -> pathlib.Path:
    return git.init_repository(tmp_path / name, origin=origin)


def test_every_mutable_key_has_one_storage() -> None:
    """全キーを共有・端末固有（理由つき）・分割保存のいずれか1つへ対応付ける。  pyfltr: allow-comment"""
    keys = set(config_settings.MUTABLE_KEY_DEFAULTS)
    groups = [
        config_settings.SHARED_SETTING_KEYS,
        set(config_settings.HOST_SETTING_REASONS),
        config_settings.SPLIT_SETTING_KEYS,
    ]
    assert set().union(*groups) == keys
    assert sum(len(group) for group in groups) == len(keys)
    assert all(config_settings.HOST_SETTING_REASONS.values())
    assert {"usage_reset_permissions", "codex_credits_provider", "codex_payg_provider"} <= config_settings.SHARED_SETTING_KEYS


def test_settings_reach_other_clone_but_host_values_stay(hosts, capsys):
    hosts.use("a")
    assert _main(hosts.tmp_path, "config", "set", "usage_reset_permissions", "[]") == 0
    assert _main(hosts.tmp_path, "config", "set", "codex_payg_provider", "my-payg") == 0
    assert _main(hosts.tmp_path, "config", "set", "serve_port", "30001") == 0
    assert _main(hosts.tmp_path, "config", "set", "weekly_research_auto_start", "true") == 0
    assert _main(hosts.tmp_path, "config", "set", "agents_concurrency", "3") == 0
    hosts.use("b")
    capsys.readouterr()
    assert _main(hosts.tmp_path, "config", "get", "codex_payg_provider", "serve_port", "weekly_research_auto_start") == 0
    shown = capsys.readouterr().out.splitlines()
    assert shown[0] == "my-payg"
    assert shown[1] == str(config_settings.serve_default_port())
    assert shown[2] == config_settings.MUTABLE_KEY_DEFAULTS["weekly_research_auto_start"]
    assert hosts.local("b") == {}


def test_show_does_not_contact_remote(hosts, monkeypatch):
    hosts.use("a")
    run_git = sync.run_git

    def offline(args, *positional, **kwargs):
        assert args[0] not in {"fetch", "push", "pull", "ls-remote"}
        return run_git(args, *positional, **kwargs)

    monkeypatch.setattr(sync, "run_git", offline)
    assert _main(hosts.tmp_path, "config", "show") == 0


def test_repository_is_linked_with_one_command(hosts):
    hosts.use("a")
    clone_a = _clone(hosts.tmp_path, "proj-a", "https://example.test/team/proj.git")
    clone_b = _clone(hosts.tmp_path, "proj-b", "https://example.test/team/proj.git")
    other_b = _clone(hosts.tmp_path, "other-b", "https://example.test/team/other.git")
    repo = "example.test/team/proj"
    assert (
        _main(hosts.tmp_path, "config", "repos", "add", repo, "--edit", "allow", "--path", str(clone_a), "--process-loop") == 0
    )
    assert json.loads(hosts.local("a")["repositories"]) == {repo: {"path": str(clone_a.resolve())}}

    hosts.use("b")
    # pathの無い登録は一覧に残るが、この端末では巡回しない。  # pyfltr: allow-comment
    assert config_settings.repository_settings()[repo]["edit"] == "allow"
    assert not config_settings.resolve_configured_repositories()
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--path", str(other_b)) == 2
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--path", str(clone_b)) == 0
    assert [item.worktree for item in config_settings.resolve_configured_repositories()] == [clone_b.resolve()]

    hosts.use("a")
    assert [item.worktree for item in config_settings.resolve_configured_repositories()] == [clone_a.resolve()]
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--edit", "awi") == 0
    hosts.use("b")
    item = config_settings.repository_settings(check_paths=False)[repo]
    assert item["edit"] == "awi" and item["path"] == str(clone_b.resolve())


def test_local_only_repository_stays_on_host(hosts):
    hosts.use("a")
    local = git.init_repository(hosts.tmp_path / "no-origin")
    assert _main(hosts.tmp_path, "config", "repos", "add", str(local), "--edit", "allow") == 0
    assert "repositories" not in config_settings.load_shared_settings(hosts.notes["a"] / "atk-config.json")
    hosts.use("b")
    assert config_settings.repository_settings(check_paths=False) == {}


def test_migration_imports_new_repositories_and_asks_only_for_differences(hosts):
    hosts.use("a")
    clone_a = _clone(hosts.tmp_path, "proj-a", "https://example.test/team/proj.git")
    assert (
        _main(hosts.tmp_path, "config", "repos", "add", "example.test/team/proj", "--edit", "awi", "--path", str(clone_a)) == 0
    )
    assert _main(hosts.tmp_path, "config", "set", "review_model", "claude:sonnet/medium") == 0
    hosts.use("b")
    clone_b = _clone(hosts.tmp_path, "proj-b", "https://example.test/team/proj.git")
    hosts.write_local(
        "b",
        {
            "review_model": "claude:sonnet/medium",
            "execute_model": "codex:sol/high",
            "serve_port": "30002",
            "repositories": json.dumps(
                {
                    "example.test/team/proj": {"edit": "allow", "path": str(clone_b)},
                    "example.test/team/new": {"edit": "deny"},
                }
            ),
        },
    )
    plan = config_shared_migration.compare()
    kinds = {item["key"]: item["kind"] for item in plan["items"]}
    assert kinds == {
        "execute_model": "different",
        "review_model": "equal",
        "repositories:example.test/team/new": "local_only",
        "repositories:example.test/team/proj": "different",
    }
    proj = next(item for item in plan["items"] if item["label"] == "example.test/team/proj")
    assert proj["loosens_edit"] is False
    choices: dict[str, config_shared_migration.Choice] = {
        "execute_model": "local",
        "repositories:example.test/team/proj": "shared",
    }
    assert config_shared_migration.apply(plan["revision"], choices) == 4
    assert hosts.local("b") == {
        "serve_port": "30002",
        "repositories": json.dumps({"example.test/team/proj": {"path": str(clone_b.resolve())}}),
    }
    merged = config_settings.repository_settings(check_paths=False)
    assert merged["example.test/team/proj"]["edit"] == "awi"
    assert merged["example.test/team/new"]["edit"] == "deny"
    hosts.use("a")
    assert config_settings.raw_mutable_setting("execute_model") == "codex:sol/high"
    assert config_settings.repository_settings(check_paths=False)["example.test/team/proj"]["path"] == str(clone_a.resolve())


def test_migration_keeps_local_values_when_revision_changes(hosts):
    hosts.use("a")
    assert _main(hosts.tmp_path, "config", "set", "review_model", "claude:sonnet/medium") == 0
    hosts.use("b")
    hosts.write_local("b", {"review_model": "codex:sol/high"})
    plan = config_shared_migration.compare()
    hosts.use("a")
    assert _main(hosts.tmp_path, "config", "set", "review_model", "claude:opus/medium") == 0
    hosts.use("b", pull=False)
    with pytest.raises(ValueError, match="比較後に"):
        config_shared_migration.apply(plan["revision"], {"review_model": "local"})
    assert hosts.local("b") == {"review_model": "codex:sol/high"}


@pytest.mark.parametrize(
    ("toml", "providers", "states"),
    [
        ('[model_providers.my-payg]\nname = "x"\n', {"codex_payg_provider": "my-payg"}, []),
        ("", {"codex_payg_provider": "my-payg"}, ["undefined"]),
        ("", {"codex_payg_provider": "openai"}, []),
        ("", {"codex_payg_provider": ""}, []),
        ("not toml = [", {"codex_payg_provider": "my-payg"}, ["unchecked"]),
        (
            '[model_providers.p]\nname = "x"\n',
            {"codex_payg_provider": "p", "codex_credits_provider": "p"},
            ["conflict", "conflict"],
        ),
    ],
)
def test_provider_warnings_only_for_unusable_ids(tmp_path, toml, providers, states):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(toml, encoding="utf-8")
    warnings = codex_provider_check.provider_warnings(providers, {"CODEX_HOME": str(home)})
    assert [warning["state"] for warning in warnings] == states
    assert all(warning["key"] in warning["message"] for warning in warnings)


def test_shared_repository_file_rejects_paths(tmp_path):
    with pytest.raises(ValueError, match="path"):
        config_repositories.parse_shared(json.dumps({"example.test/team/proj": {"edit": "allow", "path": str(tmp_path)}}))


@pytest.mark.parametrize("setting", ["true", "false"])
@pytest.mark.parametrize("already_shared", [False, True])
def test_migration_preserves_the_previewed_fast_mode(hosts, setting, already_shared):
    hosts.use("a")
    if already_shared:
        assert _main(hosts.tmp_path, "config", "set", "codex_fast_mode", "auto") == 0
    hosts.write_local("a", {"codex_fast_mode": setting})
    plan = config_shared_migration.compare()
    assert plan["required"] == (["codex_fast_mode"] if already_shared else [])
    assert plan["items"][0]["local"] == setting
    choices: dict[str, config_shared_migration.Choice] = {"codex_fast_mode": "local"} if already_shared else {}
    assert config_shared_migration.apply(plan["revision"], choices) == 1
    assert config_module.resolve_mutable_setting("codex_fast_mode") == setting
    hosts.use("b")
    assert config_module.resolve_mutable_setting("codex_fast_mode") == setting


def test_reissue_transfers_only_remaining_known_count(hosts):
    root = hosts.use("a")
    state = root / "state" / "agents-server" / "codex-auto-reset"
    state.mkdir(parents=True)
    (state / "account.json").write_text('{"used_permissions":{"permitted-period":1}}', encoding="utf-8")
    permissions = [
        {
            "id": "permitted-period",
            "engine": "codex",
            "account": "example@example.invalid",
            "scope": "codex",
            "count": 2,
            "from": "2020-01-01T00:00:00Z",
            "until": "2099-01-01T00:00:00Z",
        }
    ]
    value = json.dumps(permissions)
    hosts.write_local("a", {reset_reservations.PERMISSIONS: value})
    plan = config_shared_migration.compare()
    config_shared_migration.apply(plan["revision"], {})
    snapshot = config_module.settings_snapshot(hosts.tmp_path, read_only=True)
    assert snapshot["reset_reissue"] == {"permitted-period": 1}
    corrupt = state / "invalid.json"
    corrupt.write_text("{", encoding="utf-8")
    assert config_module.settings_snapshot(hosts.tmp_path, read_only=True)["reset_reissue"] == {"permitted-period": None}
    assert _main(hosts.tmp_path, "config", "reissue-reset", "permitted-period") == 2
    corrupt.unlink()
    assert _main(hosts.tmp_path, "config", "set", reset_reservations.PERMISSIONS, value) == 0
    assert config_module.settings_snapshot(hosts.tmp_path, read_only=True)["reset_reissue"] == {"permitted-period": 1}
    assert _main(hosts.tmp_path, "config", "reissue-reset", "permitted-period", "--count", "2") == 2
    assert _main(hosts.tmp_path, "config", "reissue-reset", "permitted-period") == 0
    assert _main(hosts.tmp_path, "config", "reissue-reset", "permitted-period") == 2
    assert _main(hosts.tmp_path, "config", "set", reset_reservations.PERMISSIONS, value) == 2
    hosts.use("b")
    [refreshed] = json.loads(config_module.resolve_mutable_setting(reset_reservations.PERMISSIONS))
    assert refreshed["id"] != "permitted-period"
    assert refreshed["count"] == 1
    assert reset_reservations.ISSUED not in refreshed
    assert reset_reservations.is_issued(hosts.notes["b"], refreshed)


@pytest.mark.parametrize("case", ["equal", "mixed", "changed", "retired"])
def test_migration_preserves_only_the_same_issued_codex_permission(hosts, case):
    key = reset_reservations.PERMISSIONS
    identifier = "shared-permission"
    grant = {
        "id": identifier,
        "engine": "codex",
        "account": "example@example.invalid",
        "scope": "codex",
        "count": 1,
        "from": "2020-01-01T00:00:00Z",
        "until": "2099-01-01T00:00:00Z",
    }
    hosts.use("a")
    if case == "retired":
        hosts.write_local("a", {key: json.dumps([grant])})
        plan = config_shared_migration.compare()
        config_shared_migration.apply(plan["revision"], {})
        assert _main(hosts.tmp_path, "config", "reissue-reset", identifier) == 0
    else:
        assert _main(hosts.tmp_path, "config", "set", key, json.dumps([grant])) == 0

    hosts.use("b")
    selected = [grant]
    if case == "mixed":
        selected.append(dict(grant, id="legacy-permission", account="legacy@example.invalid"))
    elif case == "changed":
        selected = [dict(grant, count=2)]
    original = {key: reset_reservations.encode_permissions(selected)}
    hosts.write_local("b", original)
    plan = config_shared_migration.compare()
    assert plan["required"] == ([] if case == "equal" else [key])
    choices: dict[str, config_shared_migration.Choice] = {} if case == "equal" else {key: "local"}
    if case == "retired":
        with pytest.raises(ValueError, match="発行し直した旧許可は復元できません"):
            config_shared_migration.apply(plan["revision"], choices)
        assert hosts.local("b") == original
        assert config_shared_migration.apply(plan["revision"], {key: "shared"}) == 1
    else:
        assert config_shared_migration.apply(plan["revision"], choices) == 1
    assert hosts.local("b") == {}

    for name in ("b", "a"):
        hosts.use(name)
        visible = json.loads(config_module.resolve_mutable_setting(key))
        if case == "retired":
            assert len(visible) == 1 and visible[0]["id"] != identifier
            assert reset_reservations.is_issued(hosts.notes[name], visible[0])
        else:
            assert visible == selected
            assert reset_reservations.is_issued(hosts.notes[name], visible[0]) == (case != "changed")
            if case == "mixed":
                assert not reset_reservations.is_issued(hosts.notes[name], visible[1])
