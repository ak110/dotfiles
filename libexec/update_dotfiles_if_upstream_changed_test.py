"""update_dotfiles_if_upstream_changed.pyのテスト。"""

import pathlib
import subprocess
import sys
import typing

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import update_dotfiles_if_upstream_changed as upstream_update  # noqa: E402  # pylint: disable=wrong-import-position


def _fake_run(
    calls: list[list[str]],
    *,
    branch: str = "develop",
    upstream: str = "origin/develop",
    remote_commit: str = "b" * 40,
    local_commit: str = "a" * 40,
    remote_returncode: int = 0,
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
            return subprocess.CompletedProcess(command, remote_returncode, stdout, "")
        if "--symbolic-full-name" in command:
            return subprocess.CompletedProcess(command, 0, f"{upstream}\n", "")
        if "--abbrev-ref" in command:
            return subprocess.CompletedProcess(command, 0, f"{branch}\n", "")
        return subprocess.CompletedProcess(command, 0, f"{local_commit}\n", "")

    return run


def _prepare_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """テスト専用dotfilesルートとupdate-dotfilesを用意する。"""
    root = tmp_path / "dotfiles"
    update_dotfiles = root / "bin" / "update-dotfiles"
    update_dotfiles.parent.mkdir(parents=True)
    update_dotfiles.write_text("", encoding="utf-8")
    monkeypatch.setattr(upstream_update, "_DOTFILES_ROOT", root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-appdata"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    return update_dotfiles


def test_matching_commit_skips_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """commit ID一致時は正常終了しupdate-dotfilesを起動しない。"""
    update_dotfiles = _prepare_root(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    commit = "a" * 40
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, remote_commit=commit, local_commit=commit))

    assert upstream_update.main([]) == 0
    assert [str(update_dotfiles)] not in calls


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
