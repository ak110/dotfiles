"""update_dotfiles_if_upstream_changed.pyのテスト。"""

import pathlib
import subprocess
import sys
import typing

import pytest
from agent_toolkit._testing import git_repository as git

from pytools import prune_codex_packages as prune_cli
from pytools._internal import codex_processes, deferred_updates
from pytools.prune_codex_packages_test import _release

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# pylint: disable-next=wrong-import-position,wrong-import-order
import update_dotfiles_if_upstream_changed as upstream_update  # noqa: E402


def _fake_run(
    calls: list[list[str]],
    *,
    branch: str = "develop",
    upstream: str = "origin/develop",
    remote_commit: str = "b" * 40,
    local_commit: str = "a" * 40,
    remote_returncode: int = 0,
    remote_stderr: str = "",
    branch_failure_stderr: str | None = None,
    update_returncode: int = 0,
    working_directories: list[pathlib.Path] | None = None,
) -> typing.Callable[..., subprocess.CompletedProcess[str]]:
    """gitとupdate-dotfilesの応答を返し、全呼び出しを記録する。"""

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        if working_directories is not None:
            working_directories.append(typing.cast(pathlib.Path, kwargs["cwd"]))
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["check"] is False
        if command[0] != "git":
            return subprocess.CompletedProcess(command, update_returncode, "", "")
        if "ls-remote" in command:
            stdout = f"{remote_commit}\trefs/heads/develop\n" if remote_commit else ""
            return subprocess.CompletedProcess(command, remote_returncode, stdout, remote_stderr)
        if "--symbolic-full-name" in command:
            return subprocess.CompletedProcess(command, 0, f"refs/remotes/{upstream}\n", "")
        if "symbolic-ref" in command:
            if branch_failure_stderr is not None:
                return subprocess.CompletedProcess(command, 128, "", branch_failure_stderr)
            return subprocess.CompletedProcess(command, 0, f"refs/heads/{branch}\n", "")
        return subprocess.CompletedProcess(command, 0, f"{local_commit}\n", "")

    return run


def _prepare_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """テスト専用dotfilesルートとupdate-dotfilesを用意する。"""
    root = tmp_path / "dotfiles"
    update_dotfiles = root / "bin" / "update-dotfiles"
    update_dotfiles.parent.mkdir(parents=True)
    update_dotfiles.write_text("", encoding="utf-8")
    (root / "bin" / "prune-codex-packages").write_text("", encoding="utf-8")
    monkeypatch.setattr(upstream_update, "_DOTFILES_ROOT", root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-appdata"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", str(tmp_path / "state"))
    return update_dotfiles


@pytest.mark.parametrize("collision", ["none", "tag", "branch"])
def test_update_decision_uses_full_refs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, collision: str) -> None:
    """同名refを追加しても正当なdevelopとorigin/developで更新を実行できる。"""
    launcher = _prepare_root(monkeypatch, tmp_path)
    repo = git.init_repository(launcher.parent.parent, initial_branch="develop", commit_message="base")
    git.run_git(repo, "remote", "add", "origin", str(repo))
    git.run_git(repo, "fetch", "origin")
    git.run_git(repo, "branch", "--set-upstream-to=origin/develop", "develop")
    if collision == "tag":
        git.run_git(repo, "tag", "develop")
        git.run_git(repo, "tag", "origin/develop")
    if collision == "branch":
        git.run_git(repo, "branch", "origin/develop")
    original = upstream_update._run  # noqa: SLF001  # pylint: disable=protected-access
    calls: list[list[str]] = []

    def run(command: list[str], *, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return original(command, capture_output=capture_output)
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(upstream_update, "_run", run)
    assert upstream_update.main([]) == 0
    assert [str(launcher)] in calls


def test_matching_commit_skips_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """反映成功の記録も一致するとupdate-dotfilesを起動しない。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    commit = "a" * 40
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_commit=commit, local_commit=commit))

    assert upstream_update.main([]) == 0
    assert calls.count([str(update_dotfiles)]) == 1
    calls.clear()
    assert upstream_update.main([]) == 0
    assert [str(update_dotfiles)] not in calls
    assert [str(update_dotfiles.with_name("prune-codex-packages"))] in calls


def test_external_head_advance_requires_apply(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """別主体がHEADを上流へ進めても、前の反映記録を根拠に省略しない。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_commit="a" * 40, local_commit="a" * 40))
    assert upstream_update.main([]) == 0
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_commit="b" * 40, local_commit="b" * 40))
    assert upstream_update.main([]) == 0
    assert calls.count([str(update_dotfiles)]) == 2


def test_applied_record_failure_retries_then_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """反映記録の保存失敗は再試行し、成功後の同一commitは省略する。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_commit="a" * 40, local_commit="a" * 40))
    original_write = pathlib.Path.write_text

    def fail_record(path: pathlib.Path, data: str, **kwargs: typing.Any) -> int:
        if path.suffix == ".applied":
            raise OSError("反映記録を保存できない")
        return original_write(path, data, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(pathlib.Path, "write_text", fail_record)
        assert upstream_update.main([]) == 1
    assert calls.count([str(update_dotfiles)]) == 1
    assert upstream_update.main([]) == 0
    assert calls.count([str(update_dotfiles)]) == 2
    assert upstream_update.main([]) == 0
    assert calls.count([str(update_dotfiles)]) == 2


def test_changed_commit_runs_update_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """commit ID差異時は絶対パスのupdate-dotfilesを1回だけ起動する。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    working_directories: list[pathlib.Path] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, working_directories=working_directories))

    assert upstream_update.main([]) == 0
    assert calls.count([str(update_dotfiles)]) == 1
    assert working_directories
    assert set(working_directories) == {update_dotfiles.parent.parent}


def test_update_failure_is_propagated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """update-dotfilesの非0終了コードをそのまま返す。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, update_returncode=7))

    assert upstream_update.main([]) == 7
    assert calls.count([str(update_dotfiles)]) == 1


def test_failed_apply_retries_then_skips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Git更新後の反映失敗をHEAD一致の次回に再試行し、成功後は省略する。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, update_returncode=7))

    assert upstream_update.main([]) == 7
    assert calls.count([str(update_dotfiles)]) == 1
    assert len(list((tmp_path / "state").rglob("*.pending"))) == 1

    matching_commit = "b" * 40
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(calls, remote_commit=matching_commit, local_commit=matching_commit),
    )
    assert upstream_update.main([]) == 0
    assert "未完了のため再試行" in capsys.readouterr().out
    assert calls.count([str(update_dotfiles)]) == 2
    assert not list((tmp_path / "state").rglob("*.pending"))

    assert upstream_update.main([]) == 0
    assert calls.count([str(update_dotfiles)]) == 2


def test_pending_state_survives_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """起動例外と中断でも未完了を残し、解除失敗は診断付きで非0にする。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    original_run = _fake_run(calls)

    def fail_launch(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command == [str(update_dotfiles)]:
            raise OSError("起動できない")
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_launch)
    assert upstream_update.main([]) == 1
    assert len(list((tmp_path / "state").rglob("*.pending"))) == 1

    def interrupt(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command == [str(update_dotfiles)]:
            raise KeyboardInterrupt
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        upstream_update.main([])
    assert len(list((tmp_path / "state").rglob("*.pending"))) == 1

    matching_commit = "b" * 40
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_commit=matching_commit, local_commit=matching_commit))
    pending_path = next((tmp_path / "state").rglob("*.pending"))
    original_unlink = pathlib.Path.unlink

    def fail_clear(path: pathlib.Path, missing_ok: bool = False) -> None:
        if path == pending_path:
            raise OSError("解除できない")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", fail_clear)
    assert upstream_update.main([]) == 1
    assert "未完了状態を解除できません" in capsys.readouterr().err
    assert pending_path.exists()


def test_pending_state_save_failure_prevents_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """未完了を保存できなければ更新を起動せず、ユーザーへ理由を示す。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    original_touch = pathlib.Path.touch

    def fail_save(path: pathlib.Path, mode: int = 0o666, exist_ok: bool = True) -> None:
        if path.suffix == ".pending":
            raise OSError("保存できない")
        original_touch(path, mode=mode, exist_ok=exist_ok)

    monkeypatch.setattr(pathlib.Path, "touch", fail_save)

    assert upstream_update.main([]) == 1
    assert "未完了状態を保存できません" in capsys.readouterr().err
    assert [str(update_dotfiles)] not in calls


def test_validation_failure_preserves_pending(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """未完了があってもbranchと取得元の検証が失敗すれば更新を実行しない。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, update_returncode=7))
    assert upstream_update.main([]) == 7
    pending = next((tmp_path / "state").rglob("*.pending"))

    monkeypatch.setattr(subprocess, "run", _fake_run(calls, branch="feature"))
    assert upstream_update.main([]) == 1
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, upstream="origin/main"))
    assert upstream_update.main([]) == 1
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_returncode=2))
    assert upstream_update.main([]) == 1

    assert pending.exists()
    assert calls.count([str(update_dotfiles)]) == 1


@pytest.mark.parametrize(
    ("branch", "upstream", "expected_calls"),
    [("feature", "origin/develop", 1), ("develop", "origin/main", 2)],
)
def test_branch_or_upstream_mismatch_stops_before_remote_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    branch: str,
    upstream: str,
    expected_calls: int,
) -> None:
    """branchまたはupstream不一致時はls-remoteとupdate-dotfilesを起動しない。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, branch=branch, upstream=upstream))

    assert upstream_update.main([]) == 1
    assert len(calls) == expected_calls
    assert not any("ls-remote" in call for call in calls)
    assert [str(update_dotfiles)] not in calls


@pytest.mark.parametrize(
    ("remote_returncode", "remote_commit"),
    [(2, "b" * 40), (0, "")],
)
def test_remote_lookup_failure_or_empty_output_skips_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    remote_returncode: int,
    remote_commit: str,
) -> None:
    """ls-remote失敗または空出力時は終了コード1でupdate-dotfilesを起動しない。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(calls, remote_returncode=remote_returncode, remote_commit=remote_commit),
    )

    assert upstream_update.main([]) == 1
    assert [str(update_dotfiles)] not in calls


def test_remote_lookup_failure_message_includes_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ls-remoteの失敗表示に子プロセスの標準エラーを含める。"""
    _prepare_root(monkeypatch, tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run([], remote_returncode=128, remote_stderr="fatal: Could not read from remote repository"),
    )

    assert upstream_update.main([]) == 1
    assert "fatal: Could not read from remote repository" in capsys.readouterr().err


def test_git_output_failure_message_includes_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """gitコマンドの取得失敗の表示に子プロセスの標準エラーを含める。"""
    _prepare_root(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run([], branch_failure_stderr="fatal: ref HEAD is not a symbolic ref"))

    assert upstream_update.main([]) == 1
    assert "fatal: ref HEAD is not a symbolic ref" in capsys.readouterr().err


def test_git_calls_do_not_modify_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """記録したgit呼び出しに作業ツリー変更サブコマンドを含めない。"""
    _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))

    assert upstream_update.main([]) == 0
    forbidden = {"fetch", "pull", "stash", "reset", "clean", "checkout"}
    git_calls = [call for call in calls if call[0] == "git"]
    assert git_calls
    assert not any(forbidden.intersection(call) for call in git_calls)


def test_missing_update_dotfiles_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """update-dotfiles不在時は終了コード1を返す。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    update_dotfiles.unlink()
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))

    assert upstream_update.main([]) == 1
    assert [str(update_dotfiles)] not in calls


def test_subprocess_exception_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """外部コマンド起動例外を終了コード1へ変換する。"""
    _prepare_root(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")))

    assert upstream_update.main([]) == 1


def test_unknown_argument_exits_2() -> None:
    """未知引数はargparseが引数エラーで返す終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        upstream_update.main(["--unknown"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("delete_error", [False, True])
def test_unchanged_cycle_prunes_daemon_and_propagates_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    delete_error: bool,
) -> None:
    """上流不変周期の公開CLIがdaemonの旧版を回収し、削除失敗はserviceへ返る。"""
    launcher = _prepare_root(monkeypatch, tmp_path).with_name("prune-codex-packages")
    home = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    current = _release(home, "app-server-daemon", "0.162.1")
    old = _release(home, "app-server-daemon", "local-abcd")
    # daemonは公式prepare_install.rsによるlocalコピー形式も整理する。
    (current.parent.parent / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    original = _fake_run([], remote_commit="a" * 40, local_commit="a" * 40)

    def run(command, **kwargs):
        if command == [str(launcher)]:
            return subprocess.CompletedProcess(command, prune_cli.main([]), "", "")
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    if delete_error:

        def fail_remove(path, **kwargs):
            del path, kwargs
            raise PermissionError("denied")

        monkeypatch.setattr(prune_cli.prune_codex_packages.shutil, "rmtree", fail_remove)
    assert upstream_update.main([]) == 0
    assert old.is_dir()
    assert upstream_update.main([]) == (1 if delete_error else 0)
    assert old.exists() == delete_error


def test_deferred_record_retries_update_without_upstream_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """上流不変でも延期記録があればupdate-dotfilesを再実行し、記録はpost-applyに任せて削除しない。

    延期した更新は上流変更が無いと反映されず、利用終了後も手動の再実行が必要になっていた。
    """
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    commit = "a" * 40
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_commit=commit, local_commit=commit))
    assert upstream_update.main([]) == 0
    calls.clear()
    pending_path, _lock = upstream_update._state_paths()  # pylint: disable=protected-access
    deferred_path = pending_path.with_suffix(".deferred")
    deferred_path.write_text('{"steps": ["Codex plugin"]}\n', encoding="utf-8")

    assert upstream_update.main([]) == 0
    assert calls.count([str(update_dotfiles)]) == 1
    assert deferred_path.exists()

    deferred_path.unlink()
    calls.clear()
    assert upstream_update.main([]) == 0
    assert [str(update_dotfiles)] not in calls
    assert [str(update_dotfiles.with_name("prune-codex-packages"))] in calls


def test_deferred_record_path_matches_post_apply(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """post-applyが書く延期記録と、helperが読む延期記録は同じdotfilesルートで同じパスになる。"""
    _prepare_root(monkeypatch, tmp_path)
    pending_path, _lock = upstream_update._state_paths()  # pylint: disable=protected-access

    assert deferred_updates.record_path(tmp_path / "dotfiles") == pending_path.with_suffix(".deferred")
