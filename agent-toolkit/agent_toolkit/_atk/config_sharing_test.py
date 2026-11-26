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
from agent_toolkit._common import agents_generation_index, state_paths
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.config_support import _isolate_platformdirs, _main

__all__ = ["_isolate_platformdirs"]


class _Hosts:
    def __init__(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(agents_generation_index, "generation_process_alive", lambda process: process == "test-service")
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

    def use(self, name: str, *, pull: bool = True, compatible_acceptor: bool = True) -> pathlib.Path:
        """以降の操作を端末nameの設定ディレクトリとprivate-notesで行う。  pyfltr: allow-comment"""
        root = self.tmp_path / f"host-{name}"
        platformdirs = config_module.platformdirs
        self.monkeypatch.setattr(platformdirs, "user_config_dir", lambda _name, **_kwargs: str(root / "config"))
        self.monkeypatch.setattr(platformdirs, "user_state_dir", lambda _name, **_kwargs: str(root / "state"))
        self.monkeypatch.setattr(state_paths, "state_dir", lambda **_kwargs: root / "state")
        self.monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(self.notes[name]))
        if compatible_acceptor:
            with agents_generation_index.locked_index(root / "state" / "services" / "agents-server") as data:
                data["current"] = "compatible"
                data["generations"]["compatible"] = {
                    "generation": "compatible",
                    "version": "2.311.2",
                    "root": str(root / "plugin"),
                    "ready": True,
                    "process": "test-service",
                    "epoch": 1,
                }
                data["next_epoch"] = 2
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


def test_every_repository_attribute_has_one_storage() -> None:
    host = set(config_repositories.HOST_ATTRIBUTES)
    shared = set(config_repositories.SHARED_ATTRIBUTES)
    assert host == {"path", "process_loop"}
    assert host.isdisjoint(shared)
    assert host | shared == config_repositories.RepositoryAttributes.model_fields.keys()
    units = [field for unit in config_repositories.SHARED_UNITS for field in unit]
    assert set(units) == shared and len(units) == len(shared)


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
    assert json.loads(hosts.local("a")["repositories"]) == {repo: {"path": str(clone_a.resolve()), "process_loop": True}}

    hosts.use("b")
    # pathの無い登録は一覧に残るが、この端末では巡回しない。  # pyfltr: allow-comment
    assert config_settings.repository_settings()[repo]["edit"] == "allow"
    assert not config_settings.resolve_configured_repositories()
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--path", str(other_b)) == 2
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--path", str(clone_b)) == 0
    assert not config_settings.resolve_configured_repositories()
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--process-loop") == 0
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
    assert kinds["execute_model"] == "local_only"
    assert kinds["review_model"] == "equal"
    assert kinds["repositories:example.test/team/new"] == "local_only"
    assert plan["required"] == ["repositories:example.test/team/proj:edit"]
    proj = next(item for item in plan["items"] if item["key"] in plan["required"])
    assert proj["loosens_edit"] is False
    assert config_shared_migration.apply(plan["revision"], {"repositories:example.test/team/proj:edit": "shared"}) == len(kinds)
    assert hosts.local("b") == {
        "serve_port": "30002",
        "repositories": json.dumps({"example.test/team/proj": {"path": str(clone_b.resolve()), "process_loop": False}}),
    }
    merged = config_settings.repository_settings(check_paths=False)
    assert merged["example.test/team/proj"]["edit"] == "awi"
    assert merged["example.test/team/new"]["edit"] == "deny"
    hosts.use("a")
    assert config_settings.raw_mutable_setting("execute_model") == "codex:sol/high"
    assert config_settings.repository_settings(check_paths=False)["example.test/team/proj"]["path"] == str(clone_a.resolve())


@pytest.mark.cpu_limit(1.5, reason="2端末の登録を実Gitで同期し、保留前後のCLI保存と権限を確かめる。")
def test_shared_save_keeps_legacy_registration_until_compatible_acceptor_is_published(hosts):
    repo = "example.test/team/proj"
    clone = _clone(hosts.tmp_path, "proj", f"https://{repo}.git")
    registration = json.dumps({repo: {"edit": "allow", "path": str(clone), "process_loop": True}})
    hosts.use("b")
    assert _main(hosts.tmp_path, "config", "repos", "add", repo, "--edit", "allow") == 0
    hosts.use("a", compatible_acceptor=False)
    hosts.write_local("a", {"repositories": registration})

    assert _main(hosts.tmp_path, "config", "set", "review_model", "codex:sol/high") == 0
    assert hosts.local("a")["repositories"] == registration
    assert config_settings.raw_mutable_setting("review_model") == "codex:sol/high"
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--edit", "deny") == 2
    assert config_settings.repository_settings(check_paths=False)[repo]["edit"] == "allow"

    hosts.use("a")
    assert _main(hosts.tmp_path, "config", "set", "review_model", "codex:sol/medium") == 0
    assert json.loads(hosts.local("a")["repositories"])[repo] == {"path": str(clone.resolve()), "process_loop": True}
    assert _main(hosts.tmp_path, "config", "repos", "set", repo, "--edit", "deny") == 0
    assert config_settings.repository_settings(check_paths=False)[repo]["edit"] == "deny"


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
    assert plan["required"] == (["codex_fast_mode"] if already_shared or setting == "true" else [])
    assert plan["items"][0]["local"] == setting
    choices: dict[str, config_shared_migration.Choice] = {"codex_fast_mode": "local"} if plan["required"] else {}
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


def test_startup_preserves_each_host_loop_and_does_not_inherit_it_on_new_link(hosts, capsys, monkeypatch):
    projects = ("dotfiles", "gv", "lc")
    ids = {name: f"example.test/team/{name}" for name in projects}
    clones = {
        host: {name: _clone(hosts.tmp_path, f"{host}-{name}", f"https://{ids[name]}.git") for name in projects}
        for host in ("a", "b")
    }
    hosts.use("a")
    notes = hosts.notes["a"]
    shared = {
        ids[name]: {"edit": "allow", "process_loop": enabled}
        for name, enabled in zip(projects, (True, False, False), strict=True)
    }
    path = notes / config_settings.SHARED_SETTINGS_FILENAME
    path.write_text(json.dumps({"repositories": json.dumps(shared)}), encoding="utf-8")
    git.commit_all(notes, "legacy shared settings")
    git.run_git(notes, "push")
    hosts.write_local(
        "a",
        {
            "repositories": json.dumps({ids["dotfiles"]: {"path": str(clones["a"]["dotfiles"])}}),
        },
    )
    hosts.write_local(
        "b",
        {
            "repositories": json.dumps(
                {
                    ids[name]: {"edit": "allow", "path": str(clones["b"][name]), "process_loop": name != "dotfiles"}
                    for name in projects
                }
            ),
        },
    )
    for host, expected in (("a", {"dotfiles"}), ("b", {"gv", "lc"})):
        hosts.use(host, pull=False)
        config_shared_migration.migrate_at_startup()
        assert {entry.repo_id for entry in config_settings.resolve_configured_repositories()} == {
            ids[name] for name in expected
        }
        assert _main(hosts.tmp_path, "config", "show") == 0
        assert "残す共有設定を選ぶ" not in capsys.readouterr().err
        snapshot = config_module.settings_snapshot(hosts.tmp_path, read_only=True)
        assert snapshot["shared_migration"]["plan"] is None
        before = hosts.local(host)
        head = git.git_output(hosts.notes[host], "rev-parse", "HEAD")
        with monkeypatch.context() as settled:
            settled.setattr(sync, "run_git", lambda *_args, **_kwargs: pytest.fail("移行済みの端末で再度同期した"))
            config_shared_migration.migrate_at_startup()
        assert hosts.local(host) == before
        assert git.git_output(hosts.notes[host], "rev-parse", "HEAD") == head
        assert config_settings.load_shared_settings(hosts.notes[host] / path.name)["repositories"] == json.dumps(shared)

    hosts.use("b")
    assert _main(hosts.tmp_path, "config", "repos", "set", ids["dotfiles"], "--process-loop") == 0
    assert _main(hosts.tmp_path, "config", "repos", "set", ids["dotfiles"], "--no-process-loop") == 0
    hosts.use("a")
    assert _main(hosts.tmp_path, "config", "repos", "set", ids["dotfiles"], "--no-process-loop") == 0
    hosts.use("b")
    assert {entry.repo_id for entry in config_settings.resolve_configured_repositories()} == {ids["gv"], ids["lc"]}

    hosts.write_local("b", {})
    assert _main(hosts.tmp_path, "config", "repos", "set", ids["dotfiles"], "--path", str(clones["b"]["dotfiles"])) == 0
    assert not config_settings.resolve_configured_repositories()


def test_startup_migrates_independent_values_and_preserves_conflicts(hosts):
    hosts.use("a")
    assert _main(hosts.tmp_path, "config", "set", "review_model", "claude:sonnet/medium") == 0
    repo = "example.test/team/proj"
    assert _main(hosts.tmp_path, "config", "repos", "add", repo, "--edit", "awi") == 0

    hosts.use("b")
    clone = _clone(hosts.tmp_path, "proj-b", f"https://{repo}.git")
    hosts.write_local(
        "b",
        {
            "review_model": "codex:sol/high",
            "execute_model": "codex:sol/low",
            "codex_fast_mode": "invalid",
            "codex_payg_provider": "provider-to-confirm",
            "codex_worker_connections": "subscription,payg",
            "repositories": json.dumps({repo: {"path": str(clone), "edit": "deny", "process_loop": True}}),
        },
    )
    config_shared_migration.migrate_at_startup()
    local = hosts.local("b")
    assert "execute_model" not in local
    assert json.loads(local["repositories"]) == {
        repo: {
            "path": str(clone.resolve()),
            "process_loop": True,
            "overrides": {"edit": "deny"},
        }
    }
    assert config_settings.repository_settings()[repo]["edit"] == "deny"
    assert config_module.resolve_mutable_setting("execute_model") == "codex:sol/low"
    assert _main(hosts.tmp_path, "config", "set", "write_model", "codex:sol/medium") == 0
    assert _main(hosts.tmp_path, "config", "set", "review_model", "codex:sol/medium") != 0
    assert _main(hosts.tmp_path, "config", "set", "codex_payg_provider", "other") != 0
    snapshot = config_module.settings_snapshot(hosts.tmp_path, read_only=True)
    plan = snapshot["shared_migration"]["plan"]
    assert set(plan["required"]) == {"review_model", "codex_connections", f"repositories:{repo}:edit"}
    assert plan["invalid"] == ["codex_fast_mode"]
    assert {item["kind"] for item in plan["items"]} == {"different", "invalid"}
    choices: dict[str, config_shared_migration.Choice] = {
        "review_model": "local",
        "codex_connections": "shared",
        f"repositories:{repo}:edit": "shared",
    }
    assert config_shared_migration.apply(plan["revision"], choices) == 3
    assert hosts.local("b") == {
        "codex_fast_mode": "invalid",
        "repositories": json.dumps({repo: {"path": str(clone.resolve()), "process_loop": True}}),
    }
    assert config_settings.repository_settings()[repo]["edit"] == "awi"
    assert config_module.resolve_mutable_setting("codex_payg_provider") == ""
    assert config_module.resolve_mutable_setting("review_model") == "codex:sol/high"
    invalid_only = config_shared_migration.compare()
    with pytest.raises(ValueError, match="共有できない値"):
        config_shared_migration.apply(invalid_only["revision"], {})
    assert _main(hosts.tmp_path, "config", "set", "codex_fast_mode", "false") == 0
    assert hosts.local("b")["codex_fast_mode"] == "false"
    config_shared_migration.migrate_at_startup()
    assert "codex_fast_mode" not in hosts.local("b")


def test_automatic_migration_recompares_after_another_host_pushes(hosts, monkeypatch, capsys):
    hosts.use("a")
    hosts.write_local("a", {"execute_model": "codex:sol/low", "review_model": "claude:sonnet/medium"})
    hosts.write_local("b", {"execute_model": "codex:sol/high"})
    git_run = sync.run_git
    diverged = False

    def run_with_competing_push(args, cwd, **kwargs):
        nonlocal diverged
        if args == ["push"] and cwd == hosts.notes["a"] and not diverged:
            diverged = True
            with monkeypatch.context() as other:
                other.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(hosts.notes["b"]))
                other.setattr(
                    config_module.platformdirs,
                    "user_config_dir",
                    lambda *_args, **_kwargs: str(hosts.tmp_path / "host-b/config"),
                )
                config_shared_migration.migrate_at_startup()
        return git_run(args, cwd, **kwargs)

    with monkeypatch.context() as race:
        race.setattr(sync, "run_git", run_with_competing_push)
        config_shared_migration.migrate_at_startup()
    assert diverged
    assert hosts.local("a") == {"execute_model": "codex:sol/low", "review_model": "claude:sonnet/medium"}
    assert hosts.local("b") == {}
    snapshot = config_module.settings_snapshot(hosts.tmp_path, read_only=True)
    assert snapshot["shared_migration"]["sync_failed"]
    assert "自動移行を保留した" in capsys.readouterr().err

    config_shared_migration.migrate_at_startup()
    assert hosts.local("a") == {"execute_model": "codex:sol/low"}
    snapshot = config_module.settings_snapshot(hosts.tmp_path, read_only=True)
    assert snapshot["shared_migration"]["pending"] == ["execute_model"]
    assert not snapshot["shared_migration"]["sync_failed"]
    hosts.use("b")
    assert config_module.resolve_mutable_setting("review_model") == "claude:sonnet/medium"
    assert config_module.resolve_mutable_setting("execute_model") == "codex:sol/high"


def test_startup_without_sync_target_is_read_only(hosts, monkeypatch):
    hosts.use("a")
    notes = hosts.notes["a"]
    git.run_git(notes, "branch", "--unset-upstream")
    hosts.write_local("a", {"execute_model": "codex:sol/low"})

    def no_git(*_args, **_kwargs):
        pytest.fail("同期先がない構成で同期しようとした")

    monkeypatch.setattr(sync, "run_git", no_git)
    config_shared_migration.migrate_at_startup()
    assert hosts.local("a") == {"execute_model": "codex:sol/low"}
    assert not (notes / config_settings.SHARED_SETTINGS_FILENAME).exists()


def test_automatic_migration_rechecks_local_values_after_interruption(hosts, monkeypatch):
    hosts.use("a")
    hosts.write_local("a", {"execute_model": "codex:sol/low"})
    git_run = sync.run_git

    def interrupt_before_push(args, cwd, **kwargs):
        if args == ["push"]:
            raise KeyboardInterrupt
        return git_run(args, cwd, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(sync, "run_git", interrupt_before_push)
        with pytest.raises(KeyboardInterrupt):
            config_shared_migration.migrate_at_startup()
    assert hosts.local("a") == {"execute_model": "codex:sol/low"}
    assert git.git_output(hosts.notes["a"], "rev-list", "--count", "@{u}..HEAD") == "1"
    hosts.write_local("a", {"execute_model": "codex:sol/high"})
    config_shared_migration.migrate_at_startup()
    assert hosts.local("a") == {}
    hosts.use("b")
    assert config_module.resolve_mutable_setting("execute_model") == "codex:sol/high"


@pytest.mark.parametrize("bad_key", ["codex_fast_mode", "repositories"])
def test_read_only_snapshot_and_apply_use_identical_invalid_records(hosts, bad_key):
    hosts.use("a")
    config_module.save_mutable_settings({"review_model": "claude:sonnet/medium"})
    hosts.use("b")
    raw = {"review_model": "codex:sol/high", bad_key: False}
    config_settings.config_file_path().parent.mkdir(parents=True, exist_ok=True)
    config_settings.config_file_path().write_text(json.dumps(raw), encoding="utf-8")

    snapshot = config_module.settings_snapshot(hosts.tmp_path, read_only=True)
    plan = snapshot["shared_migration"]["plan"]
    assert plan["required"] == ["review_model"]
    assert plan["invalid"] == [bad_key]
    assert plan["revision"] == config_shared_migration.compare()["revision"]
    assert config_shared_migration.apply(plan["revision"], {"review_model": "local"}) == 1
    assert hosts.local("b") == {bad_key: False}
    assert config_module.resolve_mutable_setting("review_model") == "codex:sol/high"


@pytest.mark.parametrize(
    ("shared_attrs", "local_attrs", "cli_flags", "form", "unit"),
    [
        ({}, {"edit": "deny"}, ["--edit", "awi"], {"edit": "awi"}, "edit"),
        (
            {"platforms": ["linux"]},
            {"platforms": ["windows"]},
            ["--platforms", "linux"],
            {"platforms": "linux"},
            "platforms",
        ),
        (
            {"github_issues": True},
            {},
            ["--github-issues"],
            {"github_issues": True},
            "github_issues",
        ),
        (
            {"branches": ["develop", "master"], "default_branch": "develop"},
            {"branches": ["develop", "master"], "default_branch": "master"},
            ["--default-branch", "develop"],
            {"default_branch": "develop"},
            "branches",
        ),
    ],
)
def test_explicitly_repeating_shared_repository_value_still_requires_choice(
    hosts, shared_attrs, local_attrs, cli_flags, form, unit
):
    hosts.use("a")
    repo_id = "example.test/team/proj"
    notes = hosts.notes["a"]
    path = notes / config_settings.SHARED_SETTINGS_FILENAME
    path.write_text(json.dumps({"repositories": json.dumps({repo_id: {"edit": "awi", **shared_attrs}})}), encoding="utf-8")
    git.commit_all(notes, "initialize shared repository")
    git.run_git(notes, "push")
    hosts.use("b")
    clone = _clone(hosts.tmp_path, "project", f"https://{repo_id}.git")
    original = {"edit": "awi", "path": str(clone), "process_loop": False, **local_attrs}
    hosts.write_local("b", {"repositories": json.dumps({repo_id: original})})
    config_shared_migration.migrate_at_startup()
    key = f"repositories:{repo_id}:{unit}"
    assert _main(hosts.tmp_path, "config", "repos", "set", repo_id, *cli_flags) == 2
    with pytest.raises(ValueError, match=key):
        config_module.update_settings({"action": "repo", "operation": "set", "repo": repo_id, **form})
    assert _main(hosts.tmp_path, "config", "repos", "set", repo_id, "--process-loop") == 0
    assert config_settings.repository_settings()[repo_id]["process_loop"]
    plan = config_shared_migration.compare()
    assert plan["required"] == [key]
    assert config_shared_migration.apply(plan["revision"], {key: "shared"}) == 1
    assert config_settings.repository_settings()[repo_id]["process_loop"]
    assert not config_shared_migration.compare()["required"]


@pytest.mark.parametrize("alias", ["canonical", "url", "path"])
def test_automatic_repository_migration_matches_aliases_and_preserves_host_values(hosts, alias):
    hosts.use("a")
    repo_id = "example.test/team/proj"
    origin = f"https://{repo_id}.git"
    clone = _clone(hosts.tmp_path, "project", origin)
    local_only = git.init_repository(hosts.tmp_path / "local-only")
    local_id = str(local_only.resolve())
    raw_key = {"canonical": repo_id, "url": origin, "path": str(clone)}[alias]
    linked = {"edit": "deny", "process_loop": True} | ({"path": str(clone)} if alias != "path" else {})
    hosts.write_local(
        "a",
        {
            "repositories": json.dumps({raw_key: linked, local_id: {"edit": "allow", "path": local_id}}),
        },
    )
    config_shared_migration.migrate_at_startup()
    assert json.loads(hosts.local("a")["repositories"]) == {
        repo_id: {"path": str(clone.resolve()), "process_loop": True},
        local_id: {"edit": "allow", "path": local_id},
    }
    assert config_settings.repository_settings()[repo_id]["process_loop"]
    before = hosts.local("a")
    config_shared_migration.migrate_at_startup()
    assert hosts.local("a") == before
    hosts.use("b")
    shared = config_settings.repository_settings(check_paths=False)
    assert list(shared) == [repo_id]
    assert shared[repo_id]["edit"] == "deny" and not shared[repo_id]["process_loop"]


@pytest.mark.parametrize("invalid_alias", [False, True])
def test_automatic_repository_migration_never_drops_normalized_duplicates(hosts, invalid_alias):
    hosts.use("a")
    repo_id = "example.test/team/proj"
    raw = json.dumps({repo_id: {"edit": "deny"}, f"https://{repo_id}.git": False if invalid_alias else {"edit": "awi"}})
    hosts.write_local("a", {"repositories": raw, "execute_model": "codex:sol/low"})
    config_shared_migration.migrate_at_startup()
    assert hosts.local("a") == {"repositories": raw}
    plan = config_shared_migration.compare()
    assert plan["invalid"] == [f"repositories:{repo_id}"]
    assert not plan["required"]
