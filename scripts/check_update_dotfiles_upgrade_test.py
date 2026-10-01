"""check_update_dotfiles_upgrade.pyの単体テスト。"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import types

import pytest

_SCRIPT = pathlib.Path(__file__).with_name("check_update_dotfiles_upgrade.py")


def _load_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("check_update_dotfiles_upgrade", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upgrade = _load_module()


def test_cli_outputs_japanese_when_default_stream_encoding_is_not_utf8() -> None:
    """非UTF-8の既定ストリームでも日本語のCLI出力を維持する。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    assert isinstance(result.stdout, bytes)
    assert result.stdout.decode("utf-8").find("約3日前のdotfiles") >= 0
    assert not result.stderr


def test_resolve_old_commit_uses_head_timestamp_minus_72_hours(tmp_path: pathlib.Path) -> None:
    """旧commit探索の基準を現行commit時刻の72時間前に固定する。"""
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        output = "1000000\n" if "show" in arguments else "old-oid\n"
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    assert upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner) == "old-oid"
    assert "--before=@740800" in calls[1]


def test_resolve_old_commit_rejects_missing_history(tmp_path: pathlib.Path) -> None:
    """対象時刻以前の履歴が無い場合は検証不能として失敗する。"""
    outputs = iter(("1000000\n", ""))

    def runner(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 0, stdout=next(outputs), stderr="")

    with pytest.raises(upgrade.UpgradeCheckError, match="値を返さなかった"):
        upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner)


def test_resolve_old_commit_reports_command_when_process_cannot_start(tmp_path: pathlib.Path) -> None:
    """子プロセスの起動前失敗へ実行コマンドを付加する。"""

    def runner(arguments, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", arguments[0])

    with pytest.raises(upgrade.UpgradeCheckError, match="子プロセスを起動できなかった") as exc_info:
        upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner)

    expected = ["git", "-C", str(tmp_path), "show", "-s", "--format=%ct", "current-oid"]
    assert repr(expected) in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    (("linux", "update-dotfiles"), ("windows", "update-dotfiles.cmd")),
)
def test_platform_entrypoint_selects_real_launcher(tmp_path: pathlib.Path, platform_name: str, expected: str) -> None:
    """各OSで公開される実ランチャーを旧checkoutから選択する。"""
    assert pathlib.Path(upgrade.platform_entrypoint(tmp_path, platform_name)[-1]).name == expected


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_upgrade_check_isolates_uv_tools_and_child_output(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """CIのtool配置を引き継がず、初期適用と更新へ隔離した配置とUTF-8を渡す。"""
    native_path = os.pathsep.join(part for part in os.environ["PATH"].split(os.pathsep) if pathlib.Path(part).name != "shims")
    uv_executable = shutil.which("uv", path=native_path)
    assert uv_executable is not None
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "ci-tool-bin"))
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "ci-tools"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ci-codex-home"))
    monkeypatch.setenv("CODEX_INSTALL_DIR", str(tmp_path / "ci-codex-bin"))
    monkeypatch.setattr(upgrade.shutil, "which", lambda _name: uv_executable)
    observations: list[tuple[dict[str, str], pathlib.Path, pathlib.Path]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if env is not None:
            assert isinstance(env, dict)
            home = pathlib.Path(env["HOME"])
            assert env["CODEX_HOME"] == str(home / ".codex")
            assert pathlib.Path(env["CODEX_HOME"]).is_dir()
            shared_bin = home / ".local" / "bin"
            codex_bin = home / ".local" / "share" / "codex" / "bin" if platform_name == "windows" else shared_bin
            assert env["CODEX_INSTALL_DIR"] == str(codex_bin)
            # Windowsのinstallerが専用binを作成・置換できるよう、事前に作成しない。
            assert platform_name != "windows" or not codex_bin.exists()
            uv_name = "uv.exe" if platform_name == "windows" else "uv"
            assert (shared_bin / uv_name).is_file()
            visible_bins = list(dict.fromkeys((str(codex_bin), str(shared_bin))))
            assert env["PATH"].split(os.pathsep)[: len(visible_bins)] == visible_bins
            bin_result = subprocess.run(
                [uv_executable, "tool", "dir", "--bin"],
                env=env,
                check=True,
                capture_output=True,
                encoding="utf-8",
                timeout=20,
            )
            tools_result = subprocess.run(
                [uv_executable, "tool", "dir"],
                env=env,
                check=True,
                capture_output=True,
                encoding="utf-8",
                timeout=20,
            )
            observations.append((env, pathlib.Path(bin_result.stdout.strip()), pathlib.Path(tools_result.stdout.strip())))
        values = {"rev-parse": "current-oid", "show": "1000000", "rev-list": "old-oid"}
        output = next((value for option, value in values.items() if option in arguments), "")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    upgrade.run_upgrade_check(tmp_path, platform_name, runner=runner)

    assert len(observations) == 2
    for env, bin_dir, tools_dir in observations:
        home = pathlib.Path(env["HOME"])
        assert env["USERPROFILE"] == str(home)
        assert bin_dir == home / ".local" / "bin"
        assert tools_dir.is_relative_to(home)
        assert tools_dir != pathlib.Path(os.environ["UV_TOOL_DIR"])
        assert env["PYTHONIOENCODING"] == "utf-8"


def test_initial_apply_failure_preserves_child_output_and_stops_update(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """初期適用の失敗を出力と終了コード付きで返し、公開更新を開始しない。"""
    uv = tmp_path / "uv"
    uv.write_bytes(b"test-uv")
    monkeypatch.setattr(upgrade.shutil, "which", lambda _name: str(uv))
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        if arguments[0] == "chezmoi":
            return subprocess.CompletedProcess(arguments, 23, stdout="child-out\n", stderr="child-error\n")
        values = {"rev-parse": "current-oid", "show": "1000000", "rev-list": "old-oid"}
        output = next((value for option, value in values.items() if option in arguments), "")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    with pytest.raises(upgrade.UpgradeCheckError, match="終了コード23"):
        upgrade.run_upgrade_check(tmp_path, "windows", runner=runner)
    stdout, stderr = capsys.readouterr()
    assert "child-out\n" in stdout
    assert stderr == "child-error\n"
    assert calls[-1][0] == "chezmoi"
    assert sum("update-ref" in arguments for arguments in calls) == 1


def test_verify_updated_oid_rejects_mismatch() -> None:
    """更新後OIDが検証開始時の現行OIDと異なる場合は失敗する。"""
    with pytest.raises(upgrade.UpgradeCheckError, match="expected=current actual=other"):
        upgrade.verify_updated_oid("other", "current")


def test_create_local_remote_can_advance_checkout(tmp_path: pathlib.Path) -> None:
    """bare remoteが旧・現行commitのobjectを持ち、旧checkoutを更新できる。"""

    def git(*arguments: str | pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *map(str, arguments)], check=True, capture_output=True, text=True, encoding="utf-8")

    source_repo = tmp_path / "source"
    bare_repo = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    git("init", source_repo)
    git("-C", source_repo, "config", "user.name", "upgrade-check-test")
    git("-C", source_repo, "config", "user.email", "upgrade-check-test@example.invalid")
    tracked = source_repo / "tracked.txt"
    tracked.write_text("old\n", encoding="utf-8")
    git("-C", source_repo, "add", "tracked.txt")
    git("-C", source_repo, "commit", "-m", "old")
    old_oid = git("-C", source_repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("current\n", encoding="utf-8")
    git("-C", source_repo, "commit", "-am", "current")
    current_oid = git("-C", source_repo, "rev-parse", "HEAD").stdout.strip()

    upgrade.create_local_remote(source_repo, bare_repo, old_oid)
    git("clone", "--branch", "upgrade-check", bare_repo, checkout)
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == old_oid

    git("--git-dir", bare_repo, "update-ref", "refs/heads/upgrade-check", current_oid)
    git("-C", checkout, "pull", "--ff-only")
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == current_oid
