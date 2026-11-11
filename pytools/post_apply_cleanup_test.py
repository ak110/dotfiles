# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyのcleanupの試験。"""

# pylint: disable=protected-access

from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal.post_apply_test_helpers import _IMPORT_HOME, _redirect_removed_paths_to, _registered_paths
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log


def test_removed_paths_bases_are_distribution_targets() -> None:
    """撤去表の基点は配布先だけで、作業ツリー（`~/dotfiles`）を指さない。"""
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
    # ホーム直下へ配布した設定（`~/.screenrc`など）は内容一致の表だけがホームを基点にする。
    assert set(post_apply._REMOVED_PATHS) <= allowed  # noqa: SLF001
    assert set(post_apply._REMOVED_PATHS_IF_CONTENT) <= allowed | {_IMPORT_HOME}  # noqa: SLF001
    bases = set(post_apply._REMOVED_PATHS) | set(post_apply._REMOVED_PATHS_IF_CONTENT)  # noqa: SLF001
    assert _IMPORT_HOME / "dotfiles" not in bases


def test_removed_session_review_skill_paths_cover_claude_and_codex() -> None:
    """旧個人スキルをClaude CodeとCodexの両配布先からcleanupする。"""
    relative = Path("skills/session-review-dotfiles")
    assert relative in _registered_paths(Path(".claude"))
    assert relative in _registered_paths(Path(".codex"))


def test_removed_sync_cross_project_paths_cover_claude_and_codex() -> None:
    """改名前の個人プロジェクト運用スキルを両配布先からcleanupする。"""
    relative = Path("skills/sync-cross-project")
    assert relative in _registered_paths(Path(".claude"))
    assert relative in _registered_paths(Path(".codex"))


def test_removed_project_operations_paths_cover_claude_and_codex() -> None:
    """動詞始まりへ改名した個人スキルの旧配布先を両ホストから回収する。"""
    relative = Path("skills/ak110-projects-operations")
    assert relative in _registered_paths(Path(".claude"))
    assert relative in _registered_paths(Path(".codex"))


def test_legacy_reference_directory_is_cleanup_target() -> None:
    """スキル配下以外の旧`references/`を配布先から削除する。"""
    assert Path("references") in _registered_paths(Path(".claude"))


def test_removes_legacy_plans_viewer_config_and_shim() -> None:
    """旧計画ビューアーの設定・CLIを配布先から除去する。"""
    assert Path("pytools/claude-plans-viewer.toml") in _registered_paths(Path(".config"))
    local_bin = _registered_paths(Path(".local") / "bin")
    assert Path("claude-plans-viewer") in local_bin
    assert Path("claude-plans-viewer.exe") in local_bin
    # 旧systemd unitは停止と無効化を経てから削除するため、この一括削除の対象へ含めない。
    for entries in post_apply._REMOVED_PATHS.values():  # noqa: SLF001
        assert not [entry for entry in entries if "claude-plans-viewer.service" in entry.path.name]


def test_removes_legacy_atk_launcher_but_keeps_current_wrappers() -> None:
    """作業ツリー版を覆い隠す旧atkランチャーを登録し、現行のサービス用・hook用ラッパーは登録しない。"""
    local_bin = _registered_paths(Path(".local") / "bin")
    assert Path("atk") in local_bin
    assert Path("atk.cmd") in local_bin
    for kept in ("atk-serve", "atk-hook", "atk-hook.cmd"):
        assert Path(kept) not in local_bin


def test_removes_flag_files_of_retired_steps_but_keeps_current_config() -> None:
    """廃止した工程のフラグファイルを登録し、現行のagent-toolkit設定は登録しない。"""
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
    """実際の登録内容を一時ホームへ適用し、旧生成物だけが削除されることを確かめる。"""
    registered = post_apply._REMOVED_PATHS[_IMPORT_HOME / base]  # noqa: SLF001
    home_dir = tmp_path / "home"
    target_dir = home_dir / base
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {target_dir: registered})
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    for name in (*removed, *kept):
        (target_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_text("x\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is True
    for name in removed:
        assert not (target_dir / name).exists()
    for name in kept:
        assert (target_dir / name).is_file()


def test_removed_ipython_profile_is_limited_to_profile_default() -> None:
    """旧IPythonプロファイルのcleanup対象に利用中のprofile_ipyを含めない。"""
    paths = _registered_paths(Path(".ipython"))
    assert Path("profile_default/startup/README") in paths
    assert not any(path.is_relative_to("profile_ipy") for path in paths)


def test_cleanup_removes_empty_ipython_dirs_from_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配布済みREADMEの削除後、撤去表の空ディレクトリの項目で空の親を深い順で除去する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is True
    assert not (ipython_dir / "profile_default").exists()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """startupにユーザーファイルが残る場合はprofile_defaultまで保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_script = default_readme.parent / "00-user.py"
    user_script.write_text("print('user')\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is True
    assert not default_readme.exists()
    assert user_script.read_text(encoding="utf-8") == "print('user')\n"
    assert (ipython_dir / "profile_default/startup").is_dir()
    assert (ipython_dir / "profile_default").is_dir()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_profile_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_default直下にユーザーファイルが残る場合はルートだけを保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_config = ipython_dir / "profile_default/ipython_config.py"
    user_config.write_text("c = get_config()\n", encoding="utf-8")
    active_config = ipython_dir / "profile_ipy/ipython_config.py"
    active_config.parent.mkdir(parents=True)
    active_config.write_text("active\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

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
    """配布済みファイルも親ディレクトリも存在しない場合は変更なしとする。"""
    _redirect_removed_paths_to(monkeypatch, tmp_path)

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

    assert changed is False


def test_removed_ipython_profile_cleanup_preserves_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_defaultが外部リンクの場合はリンク先の空ディレクトリを削除しない。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    outside_profile = tmp_path / "outside-profile"
    outside_startup = outside_profile / "startup"
    outside_startup.mkdir(parents=True)
    ipython_dir.mkdir(parents=True)
    profile_link = ipython_dir / "profile_default"
    profile_link.symlink_to(outside_profile, target_is_directory=True)

    changed = post_apply._cleanup_removed_paths().changed  # noqa: SLF001

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
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {})
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS_IF_CONTENT",
        {ipython_dir: post_apply._REMOVED_PATHS_IF_CONTENT[_IMPORT_HOME / ".ipython"]},  # noqa: SLF001
    )
    target = ipython_dir / "profile_ipy" / "ipython_kernel_config.py"
    target.parent.mkdir(parents=True)

    target.write_bytes(distributed + b"c.IPKernelApp.matplotlib = 'inline'\n")
    assert post_apply._cleanup_removed_paths().changed is False  # noqa: SLF001
    assert target.exists()

    target.write_bytes(distributed)
    assert post_apply._cleanup_removed_paths().changed is True  # noqa: SLF001
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
    """撤去した配布設定は撤去前の配布物と同じ内容なら削除し、1バイトでも違えば残す。"""
    distributed = (pytestconfig.rootpath / "testdata/post_apply" / fixture_name).read_bytes()
    target_dir = tmp_path / "home" / base
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {})
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS_IF_CONTENT",
        {target_dir: post_apply._REMOVED_PATHS_IF_CONTENT[_IMPORT_HOME / base]},  # noqa: SLF001
    )
    target = target_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)

    target.write_bytes(distributed + b"#")
    assert post_apply._cleanup_removed_paths().changed is False  # noqa: SLF001
    assert target.exists()

    target.write_bytes(distributed)
    assert post_apply._cleanup_removed_paths().changed is True  # noqa: SLF001
    assert not target.exists()
