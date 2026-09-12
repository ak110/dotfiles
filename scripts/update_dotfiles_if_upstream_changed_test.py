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
    """branch又はupstream不一致時はls-remoteとupdate-dotfilesを起動しない。"""
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
    """ls-remote失敗又は空出力時は終了コード1でupdate-dotfilesを起動しない。"""
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
    """未知引数はargparseの既定終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        upstream_update.main(["--unknown"])
    assert exc_info.value.code == 2
