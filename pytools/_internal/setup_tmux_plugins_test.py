"""pytools._internal.setup_tmux_plugins のテスト。"""

import logging
import subprocess
from pathlib import Path

import pytest
from agent_toolkit._testing.git_repository import commit_all, git_output, init_repository, run_git

from pytools._internal import common, setup_tmux_plugins


def _make_plugin(plugins_dir: Path, *, pin_is_tag: bool) -> setup_tmux_plugins._Plugin:  # pylint: disable=protected-access
    if pin_is_tag:
        return setup_tmux_plugins._Plugin(  # pylint: disable=protected-access
            dest=plugins_dir / "tmux",
            origin="https://github.com/catppuccin/tmux.git",
            pin="v2.3.0",
            pin_is_tag=True,
        )
    return setup_tmux_plugins._Plugin(  # pylint: disable=protected-access
        dest=plugins_dir / "tpm",
        origin="https://github.com/tmux-plugins/tpm.git",
        pin="master",
        pin_is_tag=False,
    )


def _install_env(
    monkeypatch: pytest.MonkeyPatch,
    plugins_dir: Path,
    plugin: setup_tmux_plugins._Plugin,  # pylint: disable=protected-access
) -> list[list[str]]:
    monkeypatch.setattr(setup_tmux_plugins, "_TMUX_PLUGINS_DIR", plugins_dir)
    monkeypatch.setattr(setup_tmux_plugins, "_PLUGINS", (plugin,))
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        stdout = ""
        if cmd[:4] == ["git", "-C", str(plugin.dest), "remote"]:
            stdout = f"{plugin.origin}\n"
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(common, "run_subprocess", fake_run)
    return calls


@pytest.fixture(name="branch_env")
def branch_env_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, list[list[str]]]:
    """`pin_is_tag=False`（TPM相当）の単独プラグインで`run_subprocess`を記録する環境を構築する。"""
    plugins_dir = tmp_path / "plugins"
    plugin = _make_plugin(plugins_dir, pin_is_tag=False)
    calls = _install_env(monkeypatch, plugins_dir, plugin)
    return plugins_dir, calls


def test_skips_when_origin_mismatched(
    branch_env: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置先既存かつ`origin`不一致時は更新・clone コマンドを発行しない（`remote get-url`の発行はする）。"""
    plugins_dir, calls = branch_env
    (plugins_dir / "tpm" / ".git").mkdir(parents=True)

    def fake_run(
        cmd: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="https://github.com/other/fork.git\n",
            stderr="",
        )

    monkeypatch.setattr(common, "run_subprocess", fake_run)
    assert setup_tmux_plugins.run().changed is False
    assert calls == [["git", "-C", str(plugins_dir / "tpm"), "remote", "get-url", "origin"]]


def test_skips_when_git_dir_missing(branch_env: tuple[Path, list[list[str]]]) -> None:
    """配置先既存かつ`.git`不存在時は`git`コマンドを発行しない。"""
    plugins_dir, calls = branch_env
    (plugins_dir / "tpm").mkdir(parents=True)
    assert setup_tmux_plugins.run().changed is False
    assert calls == []


def test_returns_false_when_subprocess_fails(
    branch_env: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`git`未インストール等で`run_subprocess`が`None`を返した場合は`False`を返す。"""
    _, calls = branch_env

    def fake_run(cmd: list[str], **_kwargs: object) -> None:
        calls.append(cmd)

    monkeypatch.setattr(common, "run_subprocess", fake_run)
    assert setup_tmux_plugins.run().changed is False


@pytest.mark.parametrize(
    ("pin_is_tag", "failing_subcommand", "warning_prefix"),
    [
        (False, "pull", "pull失敗"),
        (True, "fetch", "fetch失敗"),
        (True, "checkout", "checkout失敗"),
    ],
)
def test_update_failure_warning_includes_git_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    pin_is_tag: bool,
    failing_subcommand: str,
    warning_prefix: str,
) -> None:
    """更新に失敗した場合の警告へ、対象と併せてGitが返した失敗理由を含める。"""
    plugins_dir = tmp_path / "plugins"
    plugin = _make_plugin(plugins_dir, pin_is_tag=pin_is_tag)
    _install_env(monkeypatch, plugins_dir, plugin)
    (plugin.dest / ".git").mkdir(parents=True)
    failure_reason = "fatal: refusing to merge unrelated histories"

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[3] == "remote":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=f"{plugin.origin}\n", stderr="")
        if cmd[3] == failing_subcommand:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=failure_reason)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(common, "run_subprocess", fake_run)

    with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_tmux_plugins"):
        assert setup_tmux_plugins.run().changed is False

    messages = [message for message in caplog.messages if warning_prefix in message]
    assert len(messages) == 1
    assert str(plugin.dest) in messages[0]
    assert failure_reason in messages[0]


def test_effective_origin_uses_override_when_set(
    branch_env: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DOTFILES_TMUX_PLUGIN_ORIGIN_BASE`設定時は`origin`がオーバーライド値へ差し替わる。"""
    plugins_dir, calls = branch_env
    monkeypatch.setenv("DOTFILES_TMUX_PLUGIN_ORIGIN_BASE", "file:///tmp/mirrors")
    assert setup_tmux_plugins.run().changed is True
    assert calls == [
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "master",
            "file:///tmp/mirrors/tmux-plugins/tpm.git",
            str(plugins_dir / "tpm"),
        ],
    ]


@pytest.mark.parametrize("pin_is_tag", [False, True])
def test_clone_and_update_preserve_git_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pin_is_tag: bool) -> None:
    """実Gitのbranch/tagを取得し、更新HEAD・originと変更済み原本の保存を検査する。"""
    remote = init_repository(tmp_path / "remote", initial_branch="master", files={"plugin": "old\n"}, commit_message="初期状態")
    first = git_output(remote, "rev-parse", "HEAD")
    run_git(remote, "tag", "v1")
    if pin_is_tag:
        (remote / "plugin").write_text("master after v1\n", encoding="utf-8")
        assert commit_all(remote, "v1後に既定branchだけを進める") != first
    dest = tmp_path / "plugins" / "plugin"
    origin = remote.as_uri()
    plugin = setup_tmux_plugins._Plugin(dest, origin, "v1" if pin_is_tag else "master", pin_is_tag)  # pylint: disable=protected-access
    monkeypatch.delenv("DOTFILES_TMUX_PLUGIN_ORIGIN_BASE", raising=False)
    monkeypatch.setattr(setup_tmux_plugins, "_PLUGINS", (plugin,))
    assert setup_tmux_plugins.run().changed
    assert git_output(dest, "rev-parse", "HEAD") == first
    assert git_output(dest, "remote", "get-url", "origin") == origin
    (remote / "plugin").write_text("new\n", encoding="utf-8")
    second = commit_all(remote, "更新")
    run_git(remote, "tag", "v2")
    if pin_is_tag:
        (remote / "plugin").write_text("master after v2\n", encoding="utf-8")
        assert commit_all(remote, "v2後に既定branchだけを進める") != second
        plugin = setup_tmux_plugins._Plugin(dest, origin, "v2", True)  # pylint: disable=protected-access
        monkeypatch.setattr(setup_tmux_plugins, "_PLUGINS", (plugin,))
    assert setup_tmux_plugins.run().changed
    assert git_output(dest, "rev-parse", "HEAD") == second
    assert (dest / "plugin").read_text(encoding="utf-8") == "new\n"
    assert git_output(dest, "remote", "get-url", "origin") == origin

    (dest / "plugin").write_bytes(b"user changes\x00\xff")
    (remote / "plugin").write_text("remote changes\n", encoding="utf-8")
    commit_all(remote, "競合する更新")
    run_git(remote, "tag", "v3")
    if pin_is_tag:
        monkeypatch.setattr(setup_tmux_plugins, "_PLUGINS", (setup_tmux_plugins._Plugin(dest, origin, "v3", True),))  # pylint: disable=protected-access
    assert not setup_tmux_plugins.run().changed
    assert git_output(dest, "rev-parse", "HEAD") == second
    assert (dest / "plugin").read_bytes() == b"user changes\x00\xff"

    fork = init_repository(tmp_path / "fork", commit_message="ユーザーfork")
    run_git(dest, "remote", "set-url", "origin", fork.as_uri())
    assert not setup_tmux_plugins.run().changed
    assert git_output(dest, "remote", "get-url", "origin") == fork.as_uri()
    assert git_output(dest, "rev-parse", "HEAD") == second
    assert (dest / "plugin").read_bytes() == b"user changes\x00\xff"


def test_branch_divergence_preserves_local_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ff-onlyで統合できない履歴はローカルcommitとファイルを保持する。"""
    remote = init_repository(tmp_path / "remote", initial_branch="master", files={"base": "base"}, commit_message="初期状態")
    dest = tmp_path / "plugin"
    plugin = setup_tmux_plugins._Plugin(dest, remote.as_uri(), "master", False)  # pylint: disable=protected-access
    monkeypatch.setattr(setup_tmux_plugins, "_PLUGINS", (plugin,))
    monkeypatch.delenv("DOTFILES_TMUX_PLUGIN_ORIGIN_BASE", raising=False)
    assert setup_tmux_plugins.run().changed
    (dest / "local").write_text("keep", encoding="utf-8")
    local_head = commit_all(dest, "ローカル変更")
    (remote / "remote").write_text("remote", encoding="utf-8")
    commit_all(remote, "remote変更")
    assert not setup_tmux_plugins.run().changed
    assert git_output(dest, "rev-parse", "HEAD") == local_head
    assert (dest / "local").read_text(encoding="utf-8") == "keep"
    assert not (dest / "remote").exists()
