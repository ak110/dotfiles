"""repository設定の公開操作と旧登録からの対象集合維持を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._atk import config, config_repositories, config_settings
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.config_support import _isolate_platformdirs, _main, _saved, _write_config

__all__ = ["_isolate_platformdirs"]


def test_repository_cli_round_trip(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """cloneなしの登録・指定属性だけの変更・全体set・削除を公開CLIから検証する。"""
    repo_id = "example.test/team/repo"
    assert _main(tmp_path, "config", "repos", "add", "git@example.test:team/repo.git", "--edit", "awi") == 0
    assert _main(tmp_path, "config", "repos", "add", repo_id, "--edit", "allow") == 2
    assert _main(tmp_path, "config", "repos", "set", repo_id, "--platforms", "windows", "--no-process-loop") == 0
    capsys.readouterr()
    assert _main(tmp_path, "config", "repos", "list") == 0
    stored = json.loads(capsys.readouterr().out)
    assert stored[repo_id] == {"edit": "awi", "process_loop": False, "platforms": ["windows"]}
    before = config_settings.config_file_path().read_bytes()
    assert _main(tmp_path, "config", "set", "repositories", json.dumps({repo_id: {"edit": "bad"}})) == 2
    assert config_settings.config_file_path().read_bytes() == before
    assert _main(tmp_path, "config", "repos", "remove", repo_id) == 0
    assert json.loads(_saved(tmp_path)["repositories"]) == {}
    assert _main(tmp_path, "config", "repos", "remove", repo_id) == 2


def test_local_and_worktree_identity(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """originあり・なしのworktreeが元repositoryと同じ登録へ解決する。"""
    repository = git.init_repository(tmp_path / "repo", commit_message="初期履歴")
    linked = tmp_path / "linked"
    git.run_git(repository, "worktree", "add", "-b", "linked", str(linked))
    monkeypatch.chdir(tmp_path)
    assert _main(tmp_path, "config", "repos", "add", "./linked", "--edit", "allow", "--process-loop") == 0
    current = config.resolve_configured_repositories()
    assert [(item.repo_id, item.worktree) for item in current] == [(str(repository), linked)]
    assert config_repositories.identifier(str(repository))[0] == config_repositories.identifier(str(linked))[0]
    git.run_git(repository, "remote", "add", "origin", "git@example.test:team/repo.git")
    assert config_repositories.identifier(str(linked))[0] == "example.test/team/repo"


def test_migration_preserves_scope_and_conflict(tmp_path: pathlib.Path) -> None:
    """旧配列を重複originごとにまとめ、不整合の新登録は上書きしない。"""
    one = git.init_repository(tmp_path / "one", origin="https://example.test/team/one.git")
    clone = git.init_repository(tmp_path / "clone", origin="git@example.test:team/one.git")
    two = git.init_repository(tmp_path / "two")
    legacy = {"process_loop_repositories": json.dumps([str(one), str(clone), str(two)]), "other": "keep"}
    _write_config(tmp_path, legacy)
    result = config.resolve_configured_repositories()
    assert [(item.repo_id, item.worktree) for item in result] == [("example.test/team/one", one), (str(two), two)]
    assert "process_loop_repositories" not in _saved(tmp_path)
    assert _saved(tmp_path)["other"] == "keep"
    _write_config(tmp_path, {**legacy, "repositories": json.dumps({"example.test/team/one": {"edit": "deny"}})})
    before = config_settings.config_file_path().read_bytes()
    with pytest.raises(ValueError, match="不整合"):
        config.resolve_configured_repositories()
    assert config_settings.config_file_path().read_bytes() == before


def test_repair_missing_process_loop_path(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """失われたworktreeの登録を専用setで修復し、空パスは未指定として保持する。"""
    repository = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    _write_config(
        tmp_path,
        {
            "repositories": json.dumps(
                {
                    "example.test/team/repo": {
                        "edit": "allow",
                        "process_loop": True,
                        "path": str(tmp_path / "missing"),
                    }
                }
            )
        },
    )
    assert _main(tmp_path, "config", "repos", "list") == 0
    assert "missing" in capsys.readouterr().out
    assert _main(tmp_path, "config", "repos", "set", "example.test/team/repo", "--path", str(repository)) == 0
    assert config.resolve_configured_repositories()[0].worktree == repository
    assert _main(tmp_path, "config", "repos", "set", "example.test/team/repo", "--no-process-loop", "--path", "") == 0
    assert json.loads(_saved(tmp_path)["repositories"])["example.test/team/repo"]["path"] == ""


def test_repository_read_preserves_unrelated_legacy_migration(tmp_path: pathlib.Path) -> None:
    """repository単独の初回読取と、configの全設定移行を両方保つ。"""
    repository = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    _write_config(
        tmp_path,
        {
            "process_loop_repositories": json.dumps([str(repository)]),
            "claude_api_env_file": str(tmp_path / "old.env"),
            "other_setting": "keep",
        },
    )
    assert config_settings.repository_settings()["example.test/team/repo"]["process_loop"] is True
    stored = _saved(tmp_path)
    assert "process_loop_repositories" not in stored
    assert stored["claude_api_env_file"] == str(tmp_path / "old.env")
    assert _main(tmp_path, "config", "get", "repositories") == 0
    stored = _saved(tmp_path)
    assert "claude_api_env_file" not in stored
    assert stored["claude_payg_env_file"] == str(tmp_path / "old.env")
    assert stored["other_setting"] == "keep"


@pytest.mark.parametrize(
    "item",
    [
        {"edit": "allow", "process_loop": 1},
        {"edit": "allow", "platforms": []},
        {"edit": "allow", "platforms": ["macos"]},
        {"edit": "allow", "platforms": ["linux", "linux"]},
        {"edit": "allow", "path": 1},
        {"edit": "allow", "process_loop": True},
        {"edit": "allow", "extra": True},
    ],
)
def test_reject_invalid_attributes(item: dict, tmp_path: pathlib.Path) -> None:
    """専用操作と同じ検証を全体setに適用し、失敗時には保存しない。"""
    assert _main(tmp_path, "config", "set", "repositories", json.dumps({"example.test/team/repo": item})) == 2
    assert not config_settings.config_file_path().exists()


def test_array_update_and_path_normalization(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """重複成功・不在失敗・非配列拒否と、全パス設定の正規化を確認する。"""
    key = "serve_plans_remote_hosts"
    assert _main(tmp_path, "config", "add", key, "host") == 0
    before = config_settings.config_file_path().read_bytes()
    assert _main(tmp_path, "config", "add", key, "host") == 0
    assert config_settings.config_file_path().read_bytes() == before
    assert _main(tmp_path, "config", "remove", key, "missing") == 2
    assert "host" in capsys.readouterr().err
    assert _main(tmp_path, "config", "remove", key, "host") == 0
    assert _main(tmp_path, "config", "add", "repositories", "host") == 2
    monkeypatch.chdir(tmp_path)
    for path_key in config_settings.PATH_KEYS:
        assert _main(tmp_path, "config", "set", path_key, "./relative/../value") == 0
        assert _saved(tmp_path)[path_key] == str(tmp_path / "value")
        assert _main(tmp_path, "config", "set", path_key, "~/value") == 0
        assert _saved(tmp_path)[path_key] == str(pathlib.Path.home() / "value")
        assert _main(tmp_path, "config", "set", path_key, "") == 0
        assert _saved(tmp_path)[path_key] == ""
    capsys.readouterr()
    assert _main(tmp_path, "config", "list") == 0
    listed = capsys.readouterr().out
    assert _main(tmp_path, "config", "show") == 0
    assert capsys.readouterr().out == listed
