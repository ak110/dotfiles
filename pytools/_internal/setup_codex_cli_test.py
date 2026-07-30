"""pytools._internal.setup_codex_cliのテスト。"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pytools._internal import setup_codex_cli

_Call = tuple[list[str], dict[str, object]]
_INSTALLED_LISTING = '[{"version": "0.1.0", "installed": true, "active": true}]'
_UNINSTALLED_LISTING = '[{"version": "0.1.0", "installed": false, "active": false}]'


def _isolate(monkeypatch, tmp_path: Path, platform: str) -> None:
    """ホーム・PATH・Codex用環境変数をtmp_path配下へ隔離する。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_codex_cli.sys, "platform", platform)
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_INSTALL_DIR", raising=False)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: "/usr/bin/mise" if name == "mise" else None)


def _make_client() -> httpx.Client:
    """公式インストーラーの取得に成功するHTTPクライアントを返す。"""
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"installer")))


def _make_fake_run(
    calls: list[_Call],
    *,
    launcher: Path | None = None,
    mise_list: str = "[]",
    failing: str = "",
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """`run_subprocess`の代用関数を組み立てる。

    `launcher`を指定すると公式インストーラーの実行時に管理対象ランチャーを作成する。
    `failing`は失敗させる工程の名前とする。
    """

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        if command[0] in {"sh", "pwsh"}:
            if launcher is not None:
                launcher.parent.mkdir(parents=True, exist_ok=True)
                launcher.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(command, 1 if failing == "installer" else 0, "", "installer failed")
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 1 if failing == "version" else 0, "codex-cli 0.0.0\n", "")
        if command[1:3] == ["ls", "--json"]:
            if failing == "mise_list":
                return subprocess.CompletedProcess(command, 1, "", "failed")
            return subprocess.CompletedProcess(command, 0, "not json" if failing == "mise_json" else mise_list, "")
        failed = (failing == "mise_uninstall" and command[1] == "uninstall") or (failing == "reshim" and command[1] == "reshim")
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "failed")

    return fake_run


def _forbid_migration(monkeypatch) -> None:
    """旧版整理が呼ばれた時点で失敗させる。"""
    monkeypatch.setattr(
        setup_codex_cli.setup_cli_common,
        "migrate_npm_launchers",
        lambda *args: (_ for _ in ()).throw(AssertionError("移行してはならない")),
    )


def test_run_installs_verifies_then_migrates_on_posix(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    events: list[str] = []

    def fake_prepend(path: Path) -> None:
        events.append(f"path:{path}")

    def fake_migrate(*args: object) -> bool:
        del args
        events.append("migrate")
        return True

    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", fake_prepend)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", fake_migrate)
    monkeypatch.setattr(
        setup_codex_cli.claude_common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_list=_INSTALLED_LISTING),
    )

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    installer_command, installer_kwargs = calls[0]
    assert installer_command[0] == "sh"
    assert installer_kwargs["env_overrides"] == {"CODEX_NON_INTERACTIVE": "1"}
    assert not Path(installer_command[1]).exists()
    assert calls[1][0] == [str(launcher), "--version"]
    assert calls[2][0][1:] == ["ls", "--json", "npm:@openai/codex"]
    assert calls[3][0][1:] == ["uninstall", "--all", "--yes", "npm:@openai/codex"]
    assert calls[4][0][1:] == ["reshim"]
    assert events == [f"path:{tmp_path / '.local' / 'bin'}", "migrate"]


def test_run_installs_with_powershell_on_windows(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.exe"
    calls: list[_Call] = []
    prepended: list[Path] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", prepended.append)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert calls[0][0][:5] == ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert calls[1][0] == [str(launcher), "--version"]
    assert prepended == [tmp_path / "localappdata" / "Programs" / "OpenAI" / "Codex" / "bin"]


def test_run_reruns_installer_when_launcher_already_exists(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert calls[0][0][0] == "sh"
    assert calls[1][0] == [str(launcher), "--version"]


@pytest.mark.parametrize(("platform", "name"), [("linux", "codex"), ("win32", "codex.exe")])
def test_run_accepts_legacy_launcher_layout(monkeypatch, tmp_path: Path, platform: str, name: str) -> None:
    _isolate(monkeypatch, tmp_path, platform)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / name
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert calls[1][0] == [str(launcher), "--version"]


def test_run_uses_explicit_codex_home_and_install_dir(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    codex_home = tmp_path / "custom-home"
    install_dir = tmp_path / "custom-bin"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_INSTALL_DIR", str(install_dir))
    launcher = codex_home / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    prepended: list[Path] = []
    migrated: list[tuple[object, ...]] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", prepended.append)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: migrated.append(args))
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert calls[1][0] == [str(launcher), "--version"]
    assert prepended == [install_dir]
    assert migrated == [("codex", "@openai/codex", launcher, codex_home / "packages" / "standalone")]


def test_run_skips_all_work_when_windows_process_is_running(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: True)
    monkeypatch.setattr(
        setup_codex_cli.claude_common,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )
    _forbid_migration(monkeypatch)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("取得してはならない")))
    )
    try:
        assert not setup_codex_cli.run(client)
    finally:
        client.close()


def test_run_reports_http_failure(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    _forbid_migration(monkeypatch)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)))
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()


def test_run_keeps_old_versions_when_installer_fails(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    calls: list[_Call] = []
    _forbid_migration(monkeypatch)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, failing="installer"))

    client = _make_client()
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(calls) == 1
    assert not Path(calls[0][0][1]).exists()


def test_run_keeps_old_versions_when_launcher_is_missing(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    calls: list[_Call] = []
    _forbid_migration(monkeypatch)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls))

    client = _make_client()
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(calls) == 1


def test_run_keeps_old_versions_when_verification_fails(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    _forbid_migration(monkeypatch)
    monkeypatch.setattr(
        setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher, failing="version")
    )

    client = _make_client()
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert not any("uninstall" in command for command, _ in calls)


def test_run_skips_mise_removal_when_no_version_is_installed(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert not any(command[1] in {"uninstall", "reshim"} for command, _ in calls[2:])


def test_run_skips_mise_removal_when_listed_version_is_not_installed(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(
        setup_codex_cli.claude_common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_list=_UNINSTALLED_LISTING),
    )

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert not any(command[1] in {"uninstall", "reshim"} for command, _ in calls[2:])


def test_run_reshims_after_npm_migration_without_mise_versions(monkeypatch, tmp_path: Path) -> None:
    """mise管理版が無くてもnpm版の除去でshimが実体を失うため、移行後にreshimする。"""
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: True)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert not any(command[1] == "uninstall" for command, _ in calls[2:])
    assert calls[-1][0][1:] == ["reshim"]


def test_run_uses_home_default_when_localappdata_is_unset_on_windows(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.exe"
    calls: list[_Call] = []
    prepended: list[Path] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", prepended.append)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert prepended == [tmp_path / "AppData" / "Local" / "Programs" / "OpenAI" / "Codex" / "bin"]


@pytest.mark.parametrize("failing", ["mise_list", "mise_json", "mise_uninstall", "reshim"])
def test_run_propagates_mise_failures(monkeypatch, tmp_path: Path, failing: str) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(
        setup_codex_cli.claude_common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_list=_INSTALLED_LISTING, failing=failing),
    )

    client = _make_client()
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()


def test_run_propagates_npm_migration_failure(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(
        setup_codex_cli.setup_cli_common,
        "migrate_npm_launchers",
        lambda *args: (_ for _ in ()).throw(RuntimeError("旧npm版の削除に失敗")),
    )
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()


def test_run_skips_mise_removal_when_mise_is_absent(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: None)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(calls) == 2
