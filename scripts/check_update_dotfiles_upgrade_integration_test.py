"""check_update_dotfiles_upgrade.pyの単体テスト。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

import check_update_dotfiles_upgrade as upgrade
import pytest
from check_update_dotfiles_upgrade_test import (
    _SCRIPT,
    _chezmoi_bin,
    _FakeRegistry,
    _git_answers,
    _profile_env,
    fake_preflight_probes,
    mock_hosted_runner,
)

__all__ = ["_chezmoi_bin", "fake_preflight_probes", "mock_hosted_runner"]


@pytest.mark.parametrize("retry_status", [0, 23])
def test_initial_apply_retried_once_and_update_verified_only_after_old_version_applied(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], retry_status: int
) -> None:
    """旧版の初期適用は同じ旧版で1回だけ再試行し、旧版を適用できた場合だけ現行版への更新を検証する。

    再試行でも失敗した場合は、旧版の状態が無いまま更新すれば成功する状況でも検証失敗とし、更新を開始しない。
    """
    uv = tmp_path / "uv"
    uv.write_bytes(b"test-uv")
    monkeypatch.setenv("UV", str(uv))
    calls: list[list[str]] = []
    initial_statuses = iter((23, retry_status))

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        if arguments[0] == "chezmoi":
            attempt = sum(call[0] == "chezmoi" for call in calls)
            status = next(initial_statuses)
            stdout = f"失敗したステップ: 旧版の工程{attempt}\n" if status else ""
            return subprocess.CompletedProcess(arguments, status, stdout=stdout, stderr=f"child-error-{attempt}\n")
        if "--format=%cI" in arguments:
            return subprocess.CompletedProcess(arguments, 0, stdout="1970-01-09T02:26:40+00:00\n", stderr="")
        return _git_answers(arguments)

    def check() -> None:
        upgrade.run_upgrade_check(
            tmp_path, "windows", runner=runner, registry=_FakeRegistry(), profile_env=_profile_env(tmp_path)
        )

    if retry_status == 0:
        check()
    else:
        with pytest.raises(upgrade.UpgradeCheckError, match="終了コード23") as caught:
            check()
        message = str(caught.value)
        assert "旧版の初期適用が1回の再試行でも失敗し、現行版への更新は未実行である" in message
        assert "old-oid" in message and "1970-01-09T02:26:40+00:00" in message
        assert "current-oid" in message and "1970-01-09T13:46:40+00:00" in message
        assert "同じ旧版が選ばれる" in message
        assert "修正commit" in message and "72時間" in message
    stdout, stderr = capsys.readouterr()
    initial_applies = [arguments for arguments in calls if arguments[0] == "chezmoi"]
    assert len(initial_applies) == 2
    assert initial_applies[0] == initial_applies[1]
    assert "失敗したステップ: 旧版の工程1\n" in stdout
    assert "child-error-1\n" in stderr
    assert "旧版old-oidの初期適用に失敗したため、同じ旧版で1回だけ再試行する" in stderr
    assert ("失敗したステップ: 旧版の工程2\n" in stdout) == (retry_status != 0)
    assert sum(arguments[0] == "cmd.exe" for arguments in calls) == (retry_status == 0)
    assert ("旧版更新経路を確認した: old-oid -> current-oid" in stdout) == (retry_status == 0)


@pytest.mark.parametrize("phase", ["clone", "update"])
def test_other_upgrade_failures_keep_general_diagnostic(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """cloneと現行版への更新の失敗には、従来の次操作を示す。"""
    uv = tmp_path / "uv"
    uv.write_bytes(b"test-uv")
    monkeypatch.setenv("UV", str(uv))

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if (phase == "clone" and "clone" in arguments) or (phase == "update" and arguments[0] in {"bash", "cmd.exe"}):
            return subprocess.CompletedProcess(arguments, 17, stdout="", stderr="")
        return _git_answers(arguments)

    with pytest.raises(upgrade.UpgradeCheckError) as caught:
        upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, profile_env=_profile_env(tmp_path))
    message = str(caught.value)
    assert "終了コード17" in message
    assert "Git・依存導入・chezmoi適用の問題を解消して検証を再実行する。" in message
    assert "旧版の初期適用に失敗" not in message


@pytest.mark.parametrize(("old_pin", "expected"), [("3.13\n", "3.13"), (None, None)])
def test_initial_apply_pins_old_python_version(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, old_pin: str | None, expected: str | None
) -> None:
    """初期適用だけ旧commitの`.python-version`へ固定し、更新は固定せずに実行する。"""
    uv = tmp_path / "uv"
    uv.write_bytes(b"test-uv")
    monkeypatch.setenv("UV", str(uv))
    monkeypatch.delenv("UV_PYTHON", raising=False)
    child_envs: dict[str, dict[str, str]] = {}

    def runner(arguments, **kwargs):
        if "ls-tree" in arguments:
            return subprocess.CompletedProcess(arguments, 0, stdout=".python-version\n" if old_pin else "", stderr="")
        if "old-oid:.python-version" in arguments:
            assert old_pin is not None
            return subprocess.CompletedProcess(arguments, 0, stdout=old_pin, stderr="")
        if arguments[0] == "chezmoi":
            child_envs["initial"] = kwargs["env"]
        elif arguments[0] in {"bash", "cmd.exe"}:
            child_envs["upgrade"] = kwargs["env"]
        return _git_answers(arguments)

    upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, profile_env=_profile_env(tmp_path))

    assert child_envs["initial"].get("UV_PYTHON") == expected
    assert child_envs["upgrade"]["UV_PYTHON"] == str(pathlib.Path(child_envs["upgrade"]["UV_PYTHON_INSTALL_DIR"]) / "python")


@pytest.mark.parametrize("install_exit", [0, 17])
def test_windows_provisions_managed_python_inside_verification_home(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, install_exit: int
) -> None:
    uv = tmp_path / "uv"
    uv.write_bytes(b"test-uv")
    monkeypatch.setenv("UV", str(uv))
    stages: list[str] = []

    def runner(arguments, **kwargs):
        if arguments[0] == "chezmoi":
            stages.append("initial")
        elif arguments[1:3] == ["python", "install"]:
            stages.append("install")
            env = kwargs["env"]
            assert arguments[3:] == ["--no-bin", "--no-registry", "--", ">=3.15"]
            assert pathlib.Path(arguments[0]) == pathlib.Path(env["UV_TOOL_BIN_DIR"]) / "uv.exe"
            assert pathlib.Path(env["UV_PYTHON_INSTALL_DIR"]).is_relative_to(pathlib.Path(env["USERPROFILE"]))
            assert env["UV_MANAGED_PYTHON"] == "1"
            assert "UV_PYTHON" not in env
            return subprocess.CompletedProcess(arguments, install_exit, stdout="", stderr="")
        elif arguments[0] == "cmd.exe":
            stages.append("upgrade")
        return _git_answers(arguments)

    def check() -> None:
        upgrade.run_upgrade_check(
            tmp_path, "windows", runner=runner, registry=_FakeRegistry(), profile_env=_profile_env(tmp_path)
        )

    if install_exit:
        with pytest.raises(upgrade.UpgradeCheckError, match="終了コード17"):
            check()
        assert stages == ["initial", "install"]
    else:
        check()
        assert stages == ["initial", "install", "upgrade"]


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


_REG_SZ = 1
_REG_EXPAND_SZ = 2


def _registry_writing_runner(registry: _FakeRegistry, *, fail: bool):
    """公開ランチャーの実行中にレジストリを書き換え、指定時は失敗する偽のrunnerを返す。"""

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "cmd.exe":
            registry.values["LIBARCHIVE"] = ("C:\\temp\\home\\libarchive.dll", _REG_SZ)
            registry.values["Path"] = ("%USERPROFILE%\\bin;C:\\temp\\home\\.herdr", _REG_EXPAND_SZ)
            del registry.values["MSYS"]
            if fail:
                return subprocess.CompletedProcess(arguments, 9, stdout="", stderr="")
        return _git_answers(arguments)

    return runner


@pytest.mark.parametrize("fail", [False, True])
def test_windows_registry_restored(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fail: bool,
) -> None:
    """検証中に追加・変更・削除された値を、成功時と子プロセス失敗時の双方で退避前の値と値型へ戻す。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    saved: dict[str, tuple[object, int]] = {
        "Path": ("%USERPROFILE%\\bin", _REG_EXPAND_SZ),
        "MSYS": ("winsymlinks:nativestrict", _REG_SZ),
    }
    registry = _FakeRegistry(saved)

    runner = _registry_writing_runner(registry, fail=fail)
    if fail:
        with pytest.raises(upgrade.UpgradeCheckError, match="終了コード9"):
            upgrade.run_upgrade_check(tmp_path, "windows", runner=runner, registry=registry, profile_env=_profile_env(tmp_path))
    else:
        upgrade.run_upgrade_check(tmp_path, "windows", runner=runner, registry=registry, profile_env=_profile_env(tmp_path))

    assert registry.values == saved
    stdout = capsys.readouterr().out
    for name in ("LIBARCHIVE", "MSYS", "Path"):
        assert f"検証前の値へ戻したユーザー環境変数: {name}" in stdout


def test_linux_does_not_touch_registry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Linuxではレジストリの実装を呼ばない。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    registry = _FakeRegistry({"Path": ("x", _REG_SZ)})

    upgrade.run_upgrade_check(
        tmp_path,
        "linux",
        runner=lambda arguments, **_kwargs: _git_answers(arguments),
        registry=registry,
        profile_env=_profile_env(tmp_path),
    )

    assert not registry.operations


@pytest.mark.parametrize(
    ("platform_name", "relative"),
    [
        ("linux", ".local/bin/codex"),
        ("windows", "AppData/Local/Programs/Herdr"),
        ("windows", "AppData/Roaming/leaked"),
    ],
)
def test_profile_leak_fails_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, platform_name: str, relative: str
) -> None:
    """検証中に通常profile側の監視ディレクトリへ項目が作成されると、そのパスを含む失敗にする。

    失敗文は検証の子プロセスに加え、ランナー側の常駐プロセス・予約タスクとその対処先も確認先として示す。
    """
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    env = _profile_env(tmp_path)
    leaked = pathlib.Path(env["HOME"]) / relative

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] in ("cmd.exe", "bash"):
            leaked.mkdir(parents=True)
        return _git_answers(arguments)

    with pytest.raises(upgrade.UpgradeCheckError) as exc_info:
        upgrade.run_upgrade_check(tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=env)

    message = str(exc_info.value)
    assert str(leaked) in message
    assert "isolated_environment" in message
    assert "予約タスク" in message
    assert "update-dotfiles-upgrade-windows" in message


def test_profile_unchanged_passes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """既存ファイルの内容が変わっても、項目名が変わらなければ成功する。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    env = _profile_env(tmp_path)
    existing = pathlib.Path(env["HOME"]) / ".bashrc"
    existing.write_text("before\n", encoding="utf-8")

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "cmd.exe":
            existing.write_text("after\n", encoding="utf-8")
        return _git_answers(arguments)

    upgrade.run_upgrade_check(tmp_path, "windows", runner=runner, registry=_FakeRegistry(), profile_env=env)


def test_profile_leak_reported_with_original_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """子プロセスが失敗した検証では元の例外を保ち、通常profileの差分を標準エラーへ書く。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    env = _profile_env(tmp_path)
    leaked = pathlib.Path(env["HOME"]) / ".local" / "share" / "uv"

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "bash":
            leaked.mkdir(parents=True)
            return subprocess.CompletedProcess(arguments, 5, stdout="", stderr="")
        return _git_answers(arguments)

    with pytest.raises(upgrade.UpgradeCheckError, match="終了コード5"):
        upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, registry=None, profile_env=env)

    assert str(leaked) in capsys.readouterr().err


def _fake_mise_shim(mise_dir: pathlib.Path) -> pathlib.Path:
    """uv本体ではない起動スクリプトを`uv`として置いたmiseの`shims`を返す。"""
    shims = mise_dir / "shims"
    shims.mkdir(parents=True)
    shim = shims / "uv"
    shim.write_text("#!/bin/sh\necho fake-mise-shim\n", encoding="utf-8")
    shim.chmod(0o755)
    (mise_dir / "installs" / "uv" / "1.0.0" / "bin").mkdir(parents=True)
    return shims


@pytest.mark.parametrize("location", ["MISE_DATA_DIR", "XDG_DATA_HOME", "HOME", "LOCALAPPDATA"])
def test_upgrade_check_ignores_parent_mise_shims(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, location: str, chezmoi_bin: pathlib.Path
) -> None:
    """親のmiseのshimが`PATH`の先頭にあっても、検証homeへ`UV`のuv本体を置き、子の`PATH`から親のmise配下を除く。

    `uv`の名前解決で選んだshimを複製するか、親の`PATH`を全て継承すると、検証homeでshimとの間のexecが往復して停止する。
    """
    profile_env = _profile_env(tmp_path)
    parent = tmp_path / "parent"
    for name in ("MISE_DATA_DIR", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    if location == "MISE_DATA_DIR":
        mise_dir = parent / "custom-mise"
        monkeypatch.setenv("MISE_DATA_DIR", str(mise_dir))
    elif location == "XDG_DATA_HOME":
        mise_dir = parent / "xdg-data" / "mise"
        monkeypatch.setenv("XDG_DATA_HOME", str(mise_dir.parent))
    elif location == "HOME":
        mise_dir = parent / "home" / ".local" / "share" / "mise"
        monkeypatch.setenv("HOME", str(parent / "home"))
    else:
        # Windowsでは既定の配置に加え、`%LOCALAPPDATA%\mise`も親のmiseのデータディレクトリとして扱う。
        mise_dir = parent / "local-app-data" / "mise"
        monkeypatch.setenv("LOCALAPPDATA", str(mise_dir.parent))
    platform_name = "windows" if location == "LOCALAPPDATA" else "linux"
    shims = _fake_mise_shim(mise_dir)
    installs_bin = mise_dir / "installs" / "uv" / "1.0.0" / "bin"
    native_bin = tmp_path / "native-bin"
    native_bin.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join((str(shims), str(installs_bin), str(native_bin), str(chezmoi_bin))))
    real_uv = tmp_path / "real-uv"
    real_uv.write_bytes(b"real-uv-binary")
    monkeypatch.setenv("UV", str(real_uv))
    child_envs: list[dict[str, str]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if isinstance(env, dict):
            uv_name = "uv.exe" if platform_name == "windows" else "uv"
            assert (pathlib.Path(env["HOME"]) / ".local" / "bin" / uv_name).read_bytes() == b"real-uv-binary"
            child_envs.append(env)
        return _git_answers(arguments)

    upgrade.run_upgrade_check(tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=profile_env)

    assert len(child_envs) == (3 if platform_name == "windows" else 2)
    for env in child_envs:
        entries = [pathlib.Path(entry) for entry in env["PATH"].split(os.pathsep) if entry]
        assert not any(entry.is_relative_to(mise_dir) for entry in entries), entries
        assert native_bin in entries


def test_cli_without_uv_variable_asks_to_restart_with_uv_run(tmp_path: pathlib.Path) -> None:
    """`UV`の無い起動は、`uv run --script`で起動し直すよう案内して終了コード1で終わる。"""
    env = {name: value for name, value in os.environ.items() if name != "UV"}

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--platform", "linux", "--repo", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1
    assert "uv run --no-cache --script" in result.stderr


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_upgrade_check_cuts_routes_to_real_home(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """検証homeの子へ、実ホームを探索起点・参照先とする経路を渡さない。

    実ホーム配下の一時領域から祖先探索したmiseが実ホームの設定を読むこと、
    親の`PATH`と`NVM_DIR`を通して更新処理が実ホームのツール配置を走査することを防ぐ。
    実ホームの`~/.local/bin`にだけあるchezmoiは、検証homeへ複製して名前で起動できるようにする。
    Windowsでは、更新処理がwingetを起動するapp execution aliasの`WindowsApps`だけを実ホーム配下でも残し、
    npmの全体導入先を検証homeの`%APPDATA%\npm`へ向けて`PATH`へ加える。
    """
    real_home = tmp_path / "real-home"
    home_bin = real_home / ".local" / "bin"
    home_bin.mkdir(parents=True)
    (home_bin / "chezmoi").write_bytes(b"real-home-chezmoi")
    (home_bin / "chezmoi").chmod(0o755)
    nvm_bin = real_home / ".nvm" / "versions" / "node" / "v1" / "bin"
    nvm_bin.mkdir(parents=True)
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    for name in ("MISE_DATA_DIR", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("USERPROFILE", str(real_home))
    monkeypatch.setenv("NVM_DIR", str(real_home / ".nvm"))
    # 親の値を引き継いだだけで探索上限が検証rootの外を指す状態を作り、検証rootへの設定を区別する。
    monkeypatch.setenv("MISE_CEILING_PATHS", str(real_home))
    windows_apps = real_home / "AppData" / "Local" / "Microsoft" / "WindowsApps"
    monkeypatch.setenv("LOCALAPPDATA", str(real_home / "AppData" / "Local"))
    monkeypatch.setenv("PATH", os.pathsep.join((str(home_bin), str(nvm_bin), str(windows_apps), str(system_bin))))
    uv_dir = tmp_path / "uv-dist"
    uv_dir.mkdir()
    uv_suffix = ".exe" if platform_name == "windows" else ""
    (uv_dir / f"uv{uv_suffix}").write_bytes(b"uv")
    (uv_dir / f"uvx{uv_suffix}").write_bytes(b"uvx")
    monkeypatch.setenv("UV", str(uv_dir / f"uv{uv_suffix}"))
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(real_home / ".nvm" / "versions" / "node" / "v1"))
    monkeypatch.setenv("UV_CACHE_DIR", str(real_home / ".cache" / "uv"))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(real_home / ".local" / "share" / "uv" / "python"))
    monkeypatch.setenv("UV_PYTHON_BIN_DIR", str(home_bin))
    monkeypatch.setenv("XDG_BIN_HOME", str(home_bin))
    child_envs: list[dict[str, str]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if isinstance(env, dict):
            child_envs.append(dict(env))
            shared_bin = pathlib.Path(env["HOME"]) / ".local" / "bin"
            assert (shared_bin / "chezmoi").read_bytes() == b"real-home-chezmoi"
            assert (shared_bin / f"uvx{uv_suffix}").read_bytes() == b"uvx"
        return _git_answers(arguments)

    upgrade.run_upgrade_check(
        tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=_profile_env(tmp_path)
    )

    assert len(child_envs) == (3 if platform_name == "windows" else 2)
    for env in child_envs:
        verification_home = pathlib.Path(env["HOME"])
        ceiling = pathlib.Path(env["MISE_CEILING_PATHS"])
        assert verification_home.is_relative_to(ceiling)
        assert not ceiling.is_relative_to(real_home)
        entries = [pathlib.Path(entry) for entry in env["PATH"].split(os.pathsep) if entry]
        assert system_bin in entries
        assert "NVM_DIR" not in env
        assert pathlib.Path(env["UV_CACHE_DIR"]).is_relative_to(verification_home)
        assert pathlib.Path(env["UV_PYTHON_INSTALL_DIR"]).is_relative_to(verification_home)
        assert pathlib.Path(env["UV_PYTHON_BIN_DIR"]) == verification_home / ".local" / "bin"
        assert pathlib.Path(env["XDG_BIN_HOME"]) == verification_home / ".local" / "bin"
        if platform_name == "linux":
            assert not any(entry.is_relative_to(real_home) for entry in entries), entries
            assert env["NPM_CONFIG_PREFIX"] == str(verification_home / ".local")
        else:
            assert [entry for entry in entries if entry.is_relative_to(real_home)] == [windows_apps]
            npm_prefix = pathlib.Path(env["APPDATA"]) / "npm"
            assert npm_prefix.is_relative_to(verification_home)
            assert env["NPM_CONFIG_PREFIX"] == str(npm_prefix)
            assert npm_prefix in entries


def test_upgrade_check_reports_missing_chezmoi(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """複製元のchezmoiを解決できない場合は、導入とPATHの確認を案内して止まる。"""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    uv = tmp_path / "uv-binary"
    uv.write_bytes(b"uv")
    monkeypatch.setenv("UV", str(uv))

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _git_answers(arguments)

    with pytest.raises(upgrade.UpgradeCheckError, match="chezmoiが見つからない"):
        upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, registry=None, profile_env=_profile_env(tmp_path))


@pytest.mark.parametrize("keep", [False, True])
def test_verification_root_is_kept_only_when_requested(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, keep: bool
) -> None:
    """使い捨てrunner向けの指定時だけ検証rootを残し、既定では検証の終了時に回収する。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    applied: list[pathlib.Path] = []

    def runner(arguments, **kwargs):
        if arguments[0] == "chezmoi":
            applied.append(pathlib.Path(kwargs["cwd"]))
        return _git_answers(arguments)

    upgrade.run_upgrade_check(
        tmp_path,
        "linux",
        runner=runner,
        profile_env=_profile_env(tmp_path),
        keep_verification_root=keep,
    )

    assert len(applied) == 1
    checkout = applied[0]
    # 偽のrunnerはcloneしないため、検証が作成する検証rootそのものの残存で判定する。
    verification_root = checkout.parents[1]
    assert verification_root.parent == temp_root
    assert verification_root.is_dir() == keep
