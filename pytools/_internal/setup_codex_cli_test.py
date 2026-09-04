"""pytools._internal.setup_codex_cliのテスト。"""

import contextlib
import logging
import os
import subprocess
import typing
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
    original_which = setup_codex_cli.shutil.which
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_codex_cli.sys, "platform", platform)
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_INSTALL_DIR", raising=False)
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.delenv("MISE_DATA_DIR", raising=False)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        if name == "codex":
            return original_which(name, path=path)
        return {
            "mise": "/usr/bin/mise",
            "pwsh": "/usr/bin/pwsh",
            "powershell": "/usr/bin/powershell",
        }.get(name)

    monkeypatch.setattr(setup_codex_cli.shutil, "which", fake_which)


def _make_client(requests: list[httpx.Request] | None = None) -> httpx.Client:
    """公式インストーラーの取得に成功するHTTPクライアントを返す。"""

    def handle_request(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, content=b"installer")

    return httpx.Client(transport=httpx.MockTransport(handle_request))


def _make_fake_run(
    calls: list[_Call],
    *,
    launcher: Path | None = None,
    mise_list: str = "[]",
    mise_all: str = "{}",
    failing: str = "",
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """`run_subprocess`の代用関数を組み立てる。

    `launcher`を指定すると公式インストーラーの実行時に管理対象ランチャーを作成する。
    `failing`は失敗させる工程の名前とする。
    """

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        if Path(command[0]).stem in {"sh", "pwsh", "powershell"}:
            if launcher is not None:
                launcher.parent.mkdir(parents=True, exist_ok=True)
                launcher.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(command, 1 if failing == "installer" else 0, "", "installer failed")
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 1 if failing == "version" else 0, "codex-cli 0.0.0\n", "")
        if command[1:3] == ["ls", "--json"]:
            if failing == "mise_list":
                return subprocess.CompletedProcess(command, 1, "", "failed")
            output = mise_list if len(command) > 3 else mise_all
            return subprocess.CompletedProcess(command, 0, "not json" if failing == "mise_json" else output, "")
        failed = (failing == "mise_uninstall" and command[1] == "uninstall") or (failing == "reshim" and command[1] == "reshim")
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "failed")

    return fake_run


def _forbid_migration(monkeypatch) -> None:
    """旧版整理が呼ばれた時点で失敗させる。"""
    monkeypatch.setattr(
        setup_codex_cli.setup_cli_common,
        "migrate_npm_launchers",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("移行してはならない")),
    )


def test_run_installs_verifies_then_migrates_on_posix(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    events: list[str] = []

    def fake_prepend(path: Path) -> None:
        events.append(f"path:{path}")

    def fake_migrate(*args: object, **kwargs: object) -> bool:
        del args, kwargs
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
    assert installer_kwargs["env_overrides"] == {
        "CODEX_NON_INTERACTIVE": "1",
        "PATH": os.pathsep.join([str(tmp_path / ".local" / "bin"), str(tmp_path / "path")]),
    }
    assert not Path(installer_command[1]).exists()
    assert calls[1][0] == [str(launcher), "--version"]
    assert calls[2][0][1:] == ["ls", "--json", "npm:@openai/codex"]
    assert calls[3][0][1:] == ["uninstall", "--all", "--yes", "npm:@openai/codex"]
    assert calls[4][0][1:] == ["reshim"]
    assert events == [f"path:{tmp_path / '.local' / 'bin'}", "migrate"]


def test_run_keeps_profile_unchanged_when_legacy_codex_is_on_path(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    visible_bin = tmp_path / ".local" / "bin"
    legacy_bin = tmp_path / "legacy-bin"
    other_bin = tmp_path / "other-bin"
    legacy_codex = legacy_bin / "codex"
    legacy_bin.mkdir()
    other_bin.mkdir()
    legacy_codex.write_text("#!/bin/sh\n", encoding="utf-8")
    legacy_codex.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(legacy_bin), str(other_bin)]))
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    profile = tmp_path / ".bashrc"
    calls: list[_Call] = []
    migrated: list[tuple[object, ...]] = []
    base_fake_run = _make_fake_run(calls, launcher=launcher)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "sh":
            env_overrides = typing.cast(dict[str, str], kwargs["env_overrides"])
            installer_path = env_overrides["PATH"]
            entries = installer_path.split(os.pathsep)
            conflicting_codex = any(
                (Path(entry) / "codex").is_file() and os.access(Path(entry) / "codex", os.X_OK) for entry in entries
            )
            if str(visible_bin) not in entries or conflicting_codex:
                profile.write_text("# >>> Codex installer >>>\n", encoding="utf-8")
        return base_fake_run(command, **kwargs)

    def fake_migrate(*args: object, **kwargs: object) -> bool:
        migrated.append((*args, kwargs))
        return True

    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", fake_migrate)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", fake_run)

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    env_overrides = typing.cast(dict[str, str], calls[0][1]["env_overrides"])
    installer_path = env_overrides["PATH"]
    assert installer_path == os.pathsep.join([str(visible_bin), str(other_bin)])
    assert not profile.exists()
    assert migrated == [
        (
            "codex",
            "@openai/codex",
            launcher,
            tmp_path / ".codex" / "packages" / "standalone",
            {"extra_search_directories": ()},
        )
    ]


@pytest.mark.parametrize("use_environment", [False, True])
def test_nvm_bin_directories_use_existing_version_directories(monkeypatch, tmp_path: Path, use_environment: bool) -> None:
    """NVM_DIR又は既定の.nvmから、実在するバージョン別binだけを返す。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    nvm_dir = tmp_path / "configured" if use_environment else tmp_path / "home" / ".nvm"
    if use_environment:
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
    else:
        monkeypatch.delenv("NVM_DIR", raising=False)
    first = nvm_dir / "versions" / "node" / "v22" / "bin"
    second = nvm_dir / "versions" / "node" / "v24" / "bin"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (nvm_dir / "versions" / "node" / "v25" / "bin-file").parent.mkdir(parents=True)
    (nvm_dir / "versions" / "node" / "v25" / "bin-file").write_text("", encoding="utf-8")

    assert setup_codex_cli._nvm_bin_directories() == (first, second)  # pylint: disable=protected-access


def test_run_installs_with_preferred_powershell_on_windows(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.exe"
    calls: list[_Call] = []
    prepended: list[Path] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", prepended.append)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert calls[0][0][:5] == ["/usr/bin/pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert calls[1][0] == [str(launcher), "--version"]
    assert prepended == [tmp_path / "localappdata" / "Programs" / "OpenAI" / "Codex" / "bin"]


@pytest.mark.parametrize(
    ("executables", "expectation", "expected_command", "expected_requests"),
    [
        (
            {"pwsh": "/tools/pwsh", "powershell": "/tools/powershell"},
            contextlib.nullcontext(),
            ["/tools/pwsh"],
            1,
        ),
        (
            {"powershell": "/tools/powershell"},
            contextlib.nullcontext(),
            ["/tools/powershell"],
            1,
        ),
        (
            {},
            pytest.raises(RuntimeError, match="PowerShellが見つからない"),
            [],
            0,
        ),
    ],
)
def test_run_selects_available_powershell(
    monkeypatch,
    tmp_path: Path,
    executables: dict[str, str],
    expectation,
    expected_command: list[str],
    expected_requests: int,
) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.exe"
    requests: list[httpx.Request] = []
    calls: list[_Call] = []

    def fake_which(name: str) -> str | None:
        if name == "mise":
            return "/usr/bin/mise"
        return executables.get(name)

    monkeypatch.setattr(setup_codex_cli.shutil, "which", fake_which)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client(requests)
    try:
        with expectation:
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert [command[0] for command, _ in calls[:1]] == expected_command
    assert len(requests) == expected_requests


@pytest.mark.parametrize(
    ("platform", "url", "command_prefix", "suffix", "launcher_name", "expected_env_template"),
    [
        (
            "linux",
            "https://chatgpt.com/codex/install.sh",
            ["sh"],
            ".sh",
            "codex",
            {
                "CODEX_NON_INTERACTIVE": "1",
                "PATH": "{home}/.local/bin{pathsep}{home}/path",
            },
        ),
        (
            "win32",
            "https://chatgpt.com/codex/install.ps1",
            ["/usr/bin/pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
            ".ps1",
            "codex.exe",
            {"CODEX_NON_INTERACTIVE": "1"},
        ),
    ],
)
def test_run_obeys_official_installer_contract(
    monkeypatch,
    tmp_path: Path,
    platform: str,
    url: str,
    command_prefix: list[str],
    suffix: str,
    launcher_name: str,
    expected_env_template: dict[str, str],
) -> None:
    _isolate(monkeypatch, tmp_path, platform)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / launcher_name
    requests: list[httpx.Request] = []
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client(requests)
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    installer_command, installer_kwargs = calls[0]
    installer_path = Path(installer_command[-1])
    assert [str(request.url) for request in requests] == [url]
    assert installer_command[:-1] == command_prefix
    assert installer_path.suffix == suffix
    assert not installer_path.exists()
    expected_env = {key: value.format(home=tmp_path, pathsep=os.pathsep) for key, value in expected_env_template.items()}
    assert installer_kwargs["env_overrides"] == expected_env


def test_run_reruns_installer_when_launcher_already_exists(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
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
    monkeypatch.setattr(
        setup_codex_cli.setup_cli_common,
        "migrate_npm_launchers",
        lambda *args, **kwargs: migrated.append((*args, kwargs)),
    )
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    installer_kwargs = calls[0][1]
    assert installer_kwargs["env_overrides"] == {
        "CODEX_NON_INTERACTIVE": "1",
        "PATH": os.pathsep.join([str(install_dir), str(tmp_path / "path")]),
    }
    assert calls[1][0] == [str(launcher), "--version"]
    assert prepended == [install_dir]
    assert migrated == [
        (
            "codex",
            "@openai/codex",
            launcher,
            codex_home / "packages" / "standalone",
            {"extra_search_directories": ()},
        )
    ]


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


@pytest.mark.parametrize("status_code", [429, 500, 599])
def test_run_retries_transient_http_status(monkeypatch, tmp_path: Path, status_code: int) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    requests: list[httpx.Request] = []
    calls: list[_Call] = []
    responses = [httpx.Response(status_code), httpx.Response(200, content=b"installer")]

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses.pop(0)

    monkeypatch.setattr(setup_codex_cli.time, "sleep", lambda delay: None)
    monkeypatch.setattr(setup_codex_cli.random, "uniform", lambda start, end: 0.0)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))
    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(requests) == 2


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_run_retries_transient_transport_error(monkeypatch, tmp_path: Path, error_type: type[httpx.TransportError]) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    requests: list[httpx.Request] = []
    calls: list[_Call] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise error_type("一時的な通信障害", request=request)
        return httpx.Response(200, content=b"installer")

    monkeypatch.setattr(setup_codex_cli.time, "sleep", lambda delay: None)
    monkeypatch.setattr(setup_codex_cli.random, "uniform", lambda start, end: 0.0)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))
    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(requests) == 2


def test_run_does_not_retry_permanent_http_status(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    _forbid_migration(monkeypatch)
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400)

    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(requests) == 1


def test_run_stops_retrying_after_finite_attempts(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    _forbid_migration(monkeypatch)
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(setup_codex_cli.time, "sleep", delays.append)
    monkeypatch.setattr(setup_codex_cli.random, "uniform", lambda start, end: 0.0)
    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(requests) == 3
    assert delays == [0.25, 0.5]


def test_run_adds_jitter_to_retry_delays(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    _forbid_migration(monkeypatch)
    delays: list[float] = []
    jitter_ranges: list[tuple[float, float]] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500)

    def fixed_jitter(start: float, end: float) -> float:
        jitter_ranges.append((start, end))
        return 0.125

    monkeypatch.setattr(setup_codex_cli.time, "sleep", delays.append)
    monkeypatch.setattr(setup_codex_cli.random, "uniform", fixed_jitter)
    client = httpx.Client(transport=httpx.MockTransport(handle_request))
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert jitter_ranges == [(0, 0.25), (0, 0.25)]
    assert delays == [0.375, 0.625]


def test_run_removes_temporary_file_when_write_fails(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    _forbid_migration(monkeypatch)
    temp_path = tmp_path / "installer.sh"

    class FailingTemporaryFile:
        name = str(temp_path)

        def __enter__(self):
            temp_path.touch()
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def write(self, content: bytes) -> None:
            del content
            raise OSError("書き込み失敗")

    monkeypatch.setattr(setup_codex_cli.tempfile, "NamedTemporaryFile", lambda **kwargs: FailingTemporaryFile())
    client = _make_client()
    try:
        with pytest.raises(RuntimeError):
            setup_codex_cli.run(client)
    finally:
        client.close()

    assert not temp_path.exists()


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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
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


def test_run_reshims_orphaned_mise_launcher(monkeypatch, tmp_path: Path) -> None:
    """miseがCodexを提供しない状態で中継が実在する場合は再生成する。"""
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    shim = tmp_path / "mise-data" / "shims" / "codex"
    shim.parent.mkdir(parents=True)
    shim.write_text("stale", encoding="utf-8")
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise-data"))
    calls: list[_Call] = []
    base_fake_run = _make_fake_run(calls, launcher=launcher, mise_all="{}")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = base_fake_run(command, **kwargs)
        if command[1:] == ["reshim"]:
            shim.unlink()
        return result

    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", fake_run)

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert any(command[1:] == ["ls", "--json"] for command, _ in calls)
    assert calls[-1][0][1:] == ["reshim"]
    assert not shim.exists()


def test_run_warns_without_deleting_orphaned_mise_launcher(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """再生成後も残る管理外の中継は直接削除せず警告する。"""
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    shim = tmp_path / "mise-data" / "shims" / "codex"
    shim.parent.mkdir(parents=True)
    shim.write_text("stale", encoding="utf-8")
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise-data"))
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        setup_codex_cli.claude_common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_all="{}"),
    )

    client = _make_client()
    try:
        with caplog.at_level(logging.WARNING, logger="pytools._internal.setup_codex_cli"):
            assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert shim.is_file()
    assert any(f"mise管理外の中継を保持: {shim}" in message for message in caplog.messages)


def test_run_reshims_after_npm_migration_without_mise_versions(monkeypatch, tmp_path: Path) -> None:
    """mise管理版が無くてもnpm版の除去でshimが実体を失うため、移行後にreshimする。"""
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: True)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert not any(command[1] == "uninstall" for command, _ in calls[2:])
    assert calls[-1][0][1:] == ["reshim"]


@pytest.mark.parametrize("failing", ["mise_list", "mise_json", "mise_uninstall", "reshim"])
def test_run_propagates_mise_failures(monkeypatch, tmp_path: Path, failing: str) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
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
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("旧npm版の削除に失敗")),
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
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name, **kwargs: None)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    client = _make_client()
    try:
        assert setup_codex_cli.run(client)
    finally:
        client.close()

    assert len(calls) == 2
