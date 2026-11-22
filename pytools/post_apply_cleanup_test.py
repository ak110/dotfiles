# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

# pylint: disable=protected-access

from pathlib import Path

import pytest

from pytools._internal import removed_paths
from pytools._internal.post_apply_test_helpers import _IMPORT_HOME, _redirect_removed_paths_to, _registered_paths
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log


# test-copy-contract: 全撤去表の削除基点を配布先rootへ限定し、作業ツリーを削除対象へ含めない。
def test_removed_paths_bases_are_distribution_targets() -> None:
    startup = Path("AppData") / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    allowed = {
        _IMPORT_HOME / base
        for base in (
            Path(".claude"),
            Path(".codex"),
            Path(".config"),
            Path(".ipython"),
            Path("bin"),
            Path(".local") / "bin",
            startup,
        )
    }

    assert set(removed_paths._REMOVED_PATHS) <= allowed  # noqa: SLF001
    assert set(removed_paths._REMOVED_PATHS_IF_CONTENT) <= allowed | {_IMPORT_HOME}  # noqa: SLF001
    bases = set(removed_paths._REMOVED_PATHS) | set(removed_paths._REMOVED_PATHS_IF_CONTENT)  # noqa: SLF001
    assert _IMPORT_HOME / "dotfiles" not in bases


def test_removes_legacy_plans_viewer_config_and_shim() -> None:
    assert Path("pytools/claude-plans-viewer.toml") in _registered_paths(Path(".config"))
    local_bin = _registered_paths(Path(".local") / "bin")
    assert Path("claude-plans-viewer") in local_bin
    assert Path("claude-plans-viewer.exe") in local_bin

    for entries in removed_paths._REMOVED_PATHS.values():  # noqa: SLF001
        assert not [entry for entry in entries if "claude-plans-viewer.service" in entry.path.name]


def test_removes_legacy_atk_launcher_but_keeps_current_wrappers() -> None:
    local_bin = _registered_paths(Path(".local") / "bin")
    assert Path("atk") in local_bin
    assert Path("atk.cmd") in local_bin
    for kept in ("atk-serve", "atk-hook", "atk-hook.cmd"):
        assert Path(kept) not in local_bin


def test_removes_flag_files_of_retired_steps_but_keeps_current_config() -> None:
    config = _registered_paths(Path(".config"))
    assert Path("agent-toolkit/feedback-inbox.enabled") in config
    assert Path("agent-toolkit/review-balance-mode.claude-heavy") in config
    for kept in ("agent-toolkit/config.json", "agent-toolkit/serve.toml"):
        assert Path(kept) not in config


@pytest.mark.parametrize(
    ("base", "removed", "kept"),
    [
        (
            Path(".local") / "bin",
            ("atk", "atk.cmd"),
            ("atk-serve", "atk-hook", "atk-hook.cmd", "uv"),
        ),
        (
            Path(".config"),
            ("agent-toolkit/feedback-inbox.enabled", "agent-toolkit/review-balance-mode.claude-heavy"),
            ("agent-toolkit/config.json", "agent-toolkit/serve.toml"),
        ),
    ],
)
def test_cleanup_applies_registered_legacy_paths_without_touching_current_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base: Path,
    removed: tuple[str, ...],
    kept: tuple[str, ...],
) -> None:
    registered = removed_paths._REMOVED_PATHS[_IMPORT_HOME / base]  # noqa: SLF001
    home_dir = tmp_path / "home"
    target_dir = home_dir / base
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(removed_paths, "_REMOVED_PATHS", {target_dir: registered})
    monkeypatch.setattr(removed_paths, "_REMOVED_PATHS_IF_CONTENT", {})
    for name in (*removed, *kept):
        (target_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_text("x\n", encoding="utf-8")

    changed = removed_paths.cleanup().changed

    assert changed is True
    for name in removed:
        assert not (target_dir / name).exists()
    for name in kept:
        assert (target_dir / name).is_file()


@pytest.mark.parametrize("codex_user_file", [False, True])
def test_cleanup_removes_retired_always_on_rules_but_keeps_personal_rules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    codex_user_file: bool,
) -> None:
    home_dir = tmp_path / "home"
    claude_dir = home_dir / ".claude"
    codex_dir = home_dir / ".codex"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(
        removed_paths,
        "_REMOVED_PATHS",
        {
            claude_dir: removed_paths._REMOVED_PATHS[_IMPORT_HOME / ".claude"],  # noqa: SLF001
            codex_dir: removed_paths._REMOVED_PATHS[_IMPORT_HOME / ".codex"],  # noqa: SLF001
        },
    )
    monkeypatch.setattr(removed_paths, "_REMOVED_PATHS_IF_CONTENT", {})
    retired = [
        claude_dir / "rules/agent-toolkit/01-agent.md",
        claude_dir / "rules/agent-toolkit/02-agent-operations.md",
        codex_dir / "agent-toolkit/rules/01-agent.md",
    ]
    kept = [
        claude_dir / "rules/myprojects-common.md",
        claude_dir / "rules/myprojects.md",
        codex_dir / "AGENTS.md",
    ]
    if codex_user_file:
        kept.append(codex_dir / "agent-toolkit/notes.md")
    for path in (*retired, *kept):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")

    assert removed_paths.cleanup().changed is True

    assert not (claude_dir / "rules/agent-toolkit").exists()
    assert not (codex_dir / "agent-toolkit/rules").exists()
    # Codexの親は他の配布物が無いため空なら削除し、ユーザーのファイルが残る場合は保持する。
    assert (codex_dir / "agent-toolkit").exists() is codex_user_file
    for path in kept:
        assert path.read_text(encoding="utf-8") == "x\n"


def test_removed_ipython_profile_is_limited_to_profile_default() -> None:
    paths = _registered_paths(Path(".ipython"))
    assert Path("profile_default/startup/README") in paths
    assert not any(path.is_relative_to("profile_ipy") for path in paths)


def test_cleanup_removes_empty_ipython_dirs_from_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")

    changed = removed_paths.cleanup().changed

    assert changed is True
    assert not (ipython_dir / "profile_default").exists()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_script = default_readme.parent / "00-user.py"
    user_script.write_text("print('user')\n", encoding="utf-8")

    changed = removed_paths.cleanup().changed

    assert changed is True
    assert not default_readme.exists()
    assert user_script.read_text(encoding="utf-8") == "print('user')\n"
    assert (ipython_dir / "profile_default/startup").is_dir()
    assert (ipython_dir / "profile_default").is_dir()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_profile_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_config = ipython_dir / "profile_default/ipython_config.py"
    user_config.write_text("c = get_config()\n", encoding="utf-8")
    active_config = ipython_dir / "profile_ipy/ipython_config.py"
    active_config.parent.mkdir(parents=True)
    active_config.write_text("active\n", encoding="utf-8")

    changed = removed_paths.cleanup().changed

    assert changed is True
    assert not default_readme.exists()
    assert not (ipython_dir / "profile_default/startup").exists()
    assert user_config.read_text(encoding="utf-8") == "c = get_config()\n"
    assert (ipython_dir / "profile_default").is_dir()
    assert active_config.read_text(encoding="utf-8") == "active\n"


def test_removed_ipython_profile_cleanup_skips_missing_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _redirect_removed_paths_to(monkeypatch, tmp_path)

    changed = removed_paths.cleanup().changed

    assert changed is False


def test_removed_ipython_profile_cleanup_preserves_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    outside_profile = tmp_path / "outside-profile"
    outside_startup = outside_profile / "startup"
    outside_startup.mkdir(parents=True)
    ipython_dir.mkdir(parents=True)
    profile_link = ipython_dir / "profile_default"
    profile_link.symlink_to(outside_profile, target_is_directory=True)

    changed = removed_paths.cleanup().changed

    assert changed is False
    assert profile_link.is_symlink()
    assert outside_startup.is_dir()


def test_cleanup_removes_unedited_ipython_kernel_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
) -> None:
    """撤去前の配布物と同じ`ipython_kernel_config.py`は削除し、編集されたものは残す。"""
    distributed = (pytestconfig.rootpath / "testdata/post_apply/ipython_kernel_config.py.txt").read_bytes()
    home_dir = tmp_path / "home"
    ipython_dir = home_dir / ".ipython"
    monkeypatch.setattr(removed_paths, "_REMOVED_PATHS", {})
    monkeypatch.setattr(
        removed_paths,
        "_REMOVED_PATHS_IF_CONTENT",
        {ipython_dir: removed_paths._REMOVED_PATHS_IF_CONTENT[_IMPORT_HOME / ".ipython"]},  # noqa: SLF001
    )
    target = ipython_dir / "profile_ipy" / "ipython_kernel_config.py"
    target.parent.mkdir(parents=True)

    target.write_bytes(distributed + b"c.IPKernelApp.matplotlib = 'inline'\n")
    assert removed_paths.cleanup().changed is False
    assert target.exists()

    target.write_bytes(distributed)
    assert removed_paths.cleanup().changed is True
    assert not target.exists()


@pytest.mark.parametrize(
    ("base", "relative", "fixture_name"),
    [
        (Path(), ".screenrc", "screenrc.txt"),
        (Path(".config"), "xonsh/rc.xsh", "xonsh_rc.xsh.txt"),
        (Path(".config"), "yapf/style", "yapf_style.txt"),
        (Path(".config"), "pypoetry/config.toml", "pypoetry_config.toml.txt"),
        (Path(".config"), "rest-client/environment.json", "rest_client_environment.json.txt"),
    ],
)
def test_cleanup_removes_unedited_retired_configs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
    base: Path,
    relative: str,
    fixture_name: str,
) -> None:
    distributed = (pytestconfig.rootpath / "testdata/post_apply" / fixture_name).read_bytes()
    target_dir = tmp_path / "home" / base
    monkeypatch.setattr(removed_paths, "_REMOVED_PATHS", {})
    monkeypatch.setattr(
        removed_paths,
        "_REMOVED_PATHS_IF_CONTENT",
        {target_dir: removed_paths._REMOVED_PATHS_IF_CONTENT[_IMPORT_HOME / base]},  # noqa: SLF001
    )
    target = target_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)

    target.write_bytes(distributed + b"#")
    assert removed_paths.cleanup().changed is False
    assert target.exists()

    target.write_bytes(distributed)
    assert removed_paths.cleanup().changed is True
    assert not target.exists()
