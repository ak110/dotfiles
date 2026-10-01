"""check_update_dotfiles_upgrade.pyの単体テスト。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
    monkeypatch.setattr(upgrade.shutil, "which", lambda _name: uv_executable)
    observations: list[tuple[dict[str, str], pathlib.Path, pathlib.Path]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if env is not None:
            assert isinstance(env, dict)
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


def test_run_propagates_child_failure(tmp_path: pathlib.Path) -> None:
    """子プロセスの失敗を成功として継続しない。"""

    def runner(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 23, stdout="child-out\n", stderr="child-error\n")

    with pytest.raises(upgrade.UpgradeCheckError, match="終了コード23"):
        upgrade._run(  # pylint: disable=protected-access  # noqa: SLF001
            ("failing-command",), cwd=tmp_path, runner=runner
        )


@pytest.mark.parametrize("diagnostic_failure", ["none", "timeout", "invalid-json", "os-profile-unavailable"])
def test_initial_apply_failure_collects_state_before_cleanup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    diagnostic_failure: str,
) -> None:
    """失敗時の実体を回収前に採取し、診断失敗でも元の終了コードを保持する。"""
    uv = tmp_path / "uv.exe"
    uv.write_bytes(b"test-uv")

    def which(name: str, **_kwargs: object) -> str:
        return str(uv if name == "uv" else tmp_path / "powershell.exe")

    monkeypatch.setattr(upgrade.shutil, "which", which)
    monkeypatch.setenv("AUTH_TOKEN", "must-not-be-collected")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex-home"))
    os_profile = tmp_path / "os-profile"
    missing_hook_version = os_profile / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit/without-hook"
    missing_hook_version.mkdir(parents=True)
    observed_checkout: list[pathlib.Path] = []
    diagnostic_calls: list[list[str]] = []
    hook_content = b"hook-at-failure\n"

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if arguments[0] == "chezmoi":
            assert isinstance(env, dict)
            checkout = kwargs["cwd"]
            assert isinstance(checkout, pathlib.Path)
            observed_checkout.append(checkout)
            snapshot = checkout / "agent-toolkit-codex"
            for root in (checkout / "agent-toolkit", snapshot):
                hook = root / "agent_toolkit" / "hook.py"
                hook.parent.mkdir(parents=True)
                hook.write_bytes(hook_content)
            manifest = checkout / ".agents" / "plugins" / "marketplace.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "name": "ak110-dotfiles",
                        "plugins": [
                            {
                                "name": "agent-toolkit",
                                "source": {
                                    "source": "local",
                                    "path": "./agent-toolkit-codex",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            plugin = snapshot / ".codex-plugin" / "plugin.json"
            plugin.parent.mkdir(parents=True)
            plugin.write_text(json.dumps({"name": "agent-toolkit", "version": "1.2.3"}), encoding="utf-8")
            cache_hook = (
                pathlib.Path(env["HOME"]) / ".codex/plugins/cache/ak110-dotfiles/agent-toolkit/local/agent_toolkit/hook.py"
            )
            cache_hook.parent.mkdir(parents=True)
            cache_hook.write_bytes(hook_content)
            (cache_hook.parent / "unnecessary-asset.txt").write_text("unused", encoding="utf-8")
            unrelated_hook = pathlib.Path(env["HOME"]) / ".codex/plugins/cache/unrelated/plugin/1/agent_toolkit/hook.py"
            unrelated_hook.parent.mkdir(parents=True)
            unrelated_hook.write_text("unrelated", encoding="utf-8")
            (pathlib.Path(env["HOME"]) / ".codex/auth.json").write_text("must-not-be-collected", encoding="utf-8")
            return subprocess.CompletedProcess(arguments, 23, stdout="original-out", stderr="original-error")
        if env is not None:
            assert isinstance(env, dict)
            assert observed_checkout[0].is_dir()
            timeout = kwargs["timeout"]
            assert isinstance(timeout, int) and timeout > 0
            diagnostic_calls.append(arguments)
            home = pathlib.Path(env["HOME"])
            if "-c" in arguments:
                output = (
                    "not-json"
                    if diagnostic_failure == "invalid-json"
                    else json.dumps(
                        {
                            "python": "test-python",
                            "home": str(home),
                            "codex": str(home / "codex.exe"),
                            "codex_home": env["CODEX_HOME"],
                        }
                    )
                )
            elif "-Command" in arguments:
                if diagnostic_failure == "os-profile-unavailable":
                    return subprocess.CompletedProcess(arguments, 32, stdout="", stderr="profile-api-error")
                output = str(os_profile)
            elif "--version" in arguments:
                if diagnostic_failure == "timeout":
                    raise subprocess.TimeoutExpired(arguments, 30, output=b"partial-out", stderr=b"partial-error")
                output = "codex-test-version"
            elif "marketplace" in arguments:
                output = json.dumps({"marketplaces": [{"name": "ak110-dotfiles", "root": str(observed_checkout[0])}]})
            else:
                output = json.dumps({"installed": [{"name": "agent-toolkit", "version": "1.2.3"}]})
            return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="diagnostic-stderr")
        values = {"rev-parse": "current-oid", "show": "1000000", "rev-list": "old-oid"}
        output = next((value for option, value in values.items() if option in arguments), "")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    with pytest.raises(upgrade.UpgradeCheckError, match="終了コード23"):
        upgrade.run_upgrade_check(tmp_path, "windows", runner=runner)
    stdout, stderr = capsys.readouterr()
    assert not observed_checkout[0].exists()
    assert "old=old-oid current=current-oid" in stdout
    assert "診断環境:" in stdout
    assert "診断Python検収先:" in stdout
    assert "1.2.3" in stdout
    assert "診断plugin cache:" in stdout
    assert 'versions=["local"]' in stdout
    assert "cache_exists=False exists=False" in stdout
    assert "unnecessary-asset.txt" not in stdout + stderr
    assert "unrelated/plugin" not in stdout + stderr
    assert "診断cache一覧:" not in stdout
    if diagnostic_failure == "os-profile-unavailable":
        assert "診断OS profile採取失敗" in stderr
        assert "profile-api-error" in stderr
    else:
        assert f"診断OS profile: {os_profile}" in stdout
        assert f"root={missing_hook_version.parent} cache_exists=True exists=True" in stdout
        assert f"path={missing_hook_version / 'agent_toolkit/hook.py'} is_file=False" in stdout
    assert hashlib.sha256(hook_content).hexdigest() in stdout
    assert "must-not-be-collected" not in stdout + stderr
    assert "original-error" in stderr
    if diagnostic_failure == "invalid-json":
        assert "診断メタデータ採取失敗" in stderr
        assert len(diagnostic_calls) == 2
    else:
        assert len(diagnostic_calls) == 5
        assert "診断stderr: diagnostic-stderr" in stderr
        assert "codex-test-version" in stdout or "partial-out" in stdout


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
