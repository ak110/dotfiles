"""pytools._internal.setup_tmux_plugins のテスト。"""

import subprocess
from pathlib import Path

import pytest

from pytools._internal import claude_common, setup_tmux_plugins


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
    monkeypatch.setattr(setup_tmux_plugins.platform, "system", lambda: "Linux")
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

    monkeypatch.setattr(claude_common, "run_subprocess", fake_run)
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


@pytest.fixture(name="tag_env")
def tag_env_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, list[list[str]]]:
    """`pin_is_tag=True`（catppuccin相当）の単独プラグインで`run_subprocess`を記録する環境を構築する。"""
    plugins_dir = tmp_path / "plugins"
    plugin = _make_plugin(plugins_dir, pin_is_tag=True)
    calls = _install_env(monkeypatch, plugins_dir, plugin)
    return plugins_dir, calls


def test_skips_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux以外では何も実行せず`False`を返す。"""
    monkeypatch.setattr(setup_tmux_plugins.platform, "system", lambda: "Windows")
    called: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> None:
        called.append(cmd)

    monkeypatch.setattr(claude_common, "run_subprocess", fake_run)
    assert setup_tmux_plugins.run() is False
    assert not called


def test_clones_branch_pin_when_dest_missing(branch_env: tuple[Path, list[list[str]]]) -> None:
    """`pin_is_tag=False`で配置先不在時は`git clone --branch <pin>`が想定引数で呼ばれる。"""
    plugins_dir, calls = branch_env
    assert setup_tmux_plugins.run() is True
    assert calls == [
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "master",
            "https://github.com/tmux-plugins/tpm.git",
            str(plugins_dir / "tpm"),
        ],
    ]


def test_clones_tag_pin_when_dest_missing(tag_env: tuple[Path, list[list[str]]]) -> None:
    """`pin_is_tag=True`で配置先不在時はタグ指定の`git clone`が呼ばれる。"""
    plugins_dir, calls = tag_env
    assert setup_tmux_plugins.run() is True
    assert calls == [
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "v2.3.0",
            "https://github.com/catppuccin/tmux.git",
            str(plugins_dir / "tmux"),
        ],
    ]


def test_updates_branch_when_origin_matches(branch_env: tuple[Path, list[list[str]]]) -> None:
    """`pin_is_tag=False`で配置先既存かつ`origin`一致時は`git pull --ff-only`が呼ばれる。"""
    plugins_dir, calls = branch_env
    (plugins_dir / "tpm" / ".git").mkdir(parents=True)
    assert setup_tmux_plugins.run() is True
    assert calls == [
        ["git", "-C", str(plugins_dir / "tpm"), "remote", "get-url", "origin"],
        ["git", "-C", str(plugins_dir / "tpm"), "pull", "--ff-only"],
    ]


def test_updates_tag_when_origin_matches(tag_env: tuple[Path, list[list[str]]]) -> None:
    """`pin_is_tag=True`で配置先既存かつ`origin`一致時は`git fetch`+`git checkout FETCH_HEAD`が呼ばれる。"""
    plugins_dir, calls = tag_env
    (plugins_dir / "tmux" / ".git").mkdir(parents=True)
    assert setup_tmux_plugins.run() is True
    assert calls == [
        ["git", "-C", str(plugins_dir / "tmux"), "remote", "get-url", "origin"],
        ["git", "-C", str(plugins_dir / "tmux"), "fetch", "--depth", "1", "origin", "v2.3.0"],
        ["git", "-C", str(plugins_dir / "tmux"), "checkout", "FETCH_HEAD"],
    ]


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

    monkeypatch.setattr(claude_common, "run_subprocess", fake_run)
    assert setup_tmux_plugins.run() is False
    assert calls == [["git", "-C", str(plugins_dir / "tpm"), "remote", "get-url", "origin"]]


def test_skips_when_git_dir_missing(branch_env: tuple[Path, list[list[str]]]) -> None:
    """配置先既存かつ`.git`不存在時は`git`コマンドを発行しない。"""
    plugins_dir, calls = branch_env
    (plugins_dir / "tpm").mkdir(parents=True)
    assert setup_tmux_plugins.run() is False
    assert calls == []


def test_returns_false_when_subprocess_fails(
    branch_env: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`git`未インストール等で`run_subprocess`が`None`を返した場合は`False`を返す。"""
    _, calls = branch_env

    def fake_run(cmd: list[str], **_kwargs: object) -> None:
        calls.append(cmd)

    monkeypatch.setattr(claude_common, "run_subprocess", fake_run)
    assert setup_tmux_plugins.run() is False
