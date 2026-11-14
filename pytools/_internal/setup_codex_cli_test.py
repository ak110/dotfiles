"""pytools._internal.setup_codex_cliのテスト。"""

import errno
import logging
import os
import subprocess
import types
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pytools._internal import codex_runtime, post_apply_outcome, prune_codex_packages, remove_codex_claude_mcp, setup_codex_cli
from pytools._internal._test_helpers import FakeEnvironmentRegistry
from pytools.prune_codex_packages_test import _release

_Call = tuple[list[str], dict[str, object]]
_INSTALLED_LISTING = '[{"version": "0.1.0", "installed": true, "active": true}]'
_UNINSTALLED_LISTING = '[{"version": "0.1.0", "installed": false, "active": false}]'


@pytest.mark.parametrize("operation", ["readlink", "resolve", "stat"])
@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EIO])
def test_windows_inspection_failure_prevents_mcp_removal(
    monkeypatch, tmp_path: Path, operation: str, error_number: int
) -> None:
    """リンクと本体の検査失敗を未導入にせず、MCPコマンドも実行しない。"""
    _isolate(monkeypatch, tmp_path, "win32")
    monkeypatch.setattr(remove_codex_claude_mcp, "sys", types.SimpleNamespace(platform="win32"))
    release = tmp_path / ".codex/packages/standalone/releases/version"
    executable = release / "bin/codex.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native")
    current = release.parent.parent / "current"
    current.symlink_to(release, target_is_directory=True)
    original = getattr(Path, operation)
    rejected = current if operation == "readlink" else executable

    def inspect(path: Path, *args, **kwargs):
        if path == rejected:
            raise OSError(error_number, "inspection denied")
        return original(path, *args, **kwargs)

    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("検査失敗後にMCPコマンドを起動した")

    monkeypatch.setattr(Path, operation, inspect)
    monkeypatch.setattr(remove_codex_claude_mcp.common, "run_subprocess", forbidden)
    with pytest.raises(OSError) as caught:
        remove_codex_claude_mcp.run()
    assert caught.value.errno == error_number


@pytest.mark.parametrize("kind", ["owned", "outside", "cycle", "missing", "npm"])
def test_windows_runtime_selects_only_owned_current(monkeypatch, tmp_path: Path, kind: str) -> None:
    """可視binを使えなくても所有currentの実体だけを選ぶ。"""
    _isolate(monkeypatch, tmp_path, "win32")
    home = tmp_path / "configured-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    root = home / "packages/standalone"
    release = root / "releases/version"
    release.mkdir(parents=True)
    executable = release / "bin/codex.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"native")
    current = root / "current"
    if kind == "owned":
        current.symlink_to(release, target_is_directory=True)
    elif kind == "outside":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "codex.exe").write_bytes(b"native")
        current.symlink_to(outside, target_is_directory=True)
    elif kind == "cycle":
        current.symlink_to(current, target_is_directory=True)
    elif kind == "npm":
        visible = codex_runtime.visible_bin_dir()
        visible.mkdir(parents=True)
        (visible / "codex.cmd").write_text("node missing.js", encoding="utf-8")
    assert codex_runtime.find_runtime_launcher(windows=True) == (executable if kind == "owned" else None)


def _isolate(monkeypatch, tmp_path: Path, platform: str) -> None:
    """ホーム・PATH・Codex用環境変数をtmp_path配下へ隔離する。"""
    original_which = setup_codex_cli.shutil.which
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # 配置の模擬OSだけを差し替え、実際の排他APIにはホストOSを使う。
    monkeypatch.setattr(setup_codex_cli, "sys", types.SimpleNamespace(platform=platform))
    monkeypatch.setattr(codex_runtime, "sys", types.SimpleNamespace(platform=platform))
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_INSTALL_DIR", raising=False)
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.delenv("MISE_DATA_DIR", raising=False)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    root = tmp_path / "dotfiles"
    (root / "bin").mkdir(parents=True)
    for name in ("codex", "codex.cmd"):
        (root / "bin" / name).write_text("wrapper\n", encoding="utf-8")
    monkeypatch.setattr(setup_codex_cli.common, "find_dotfiles_root", lambda: root)
    if platform == "win32":
        FakeEnvironmentRegistry().install(monkeypatch)

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
                # 版リンクを作るのはbin配置だけとし、旧current直下は通常ディレクトリを保つ。
                if launcher.suffix == ".exe" and launcher.parent.name == "bin":
                    current = launcher.parent.parent
                    if not current.exists():
                        release = current.parent / "releases/test-version"
                        release.mkdir(parents=True)
                        current.symlink_to(release, target_is_directory=True)
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


@pytest.mark.parametrize("use_environment", [False, True])
def test_nvm_bin_directories_use_existing_version_directories(monkeypatch, tmp_path: Path, use_environment: bool) -> None:
    """NVM_DIR、未設定ならホーム直下の.nvmから、実在するバージョン別binだけを返す。"""
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


def test_run_skips_all_work_when_windows_process_is_running(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: True)
    monkeypatch.setattr(
        setup_codex_cli.common,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )
    _forbid_migration(monkeypatch)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("取得してはならない")))
    ) as client:
        assert not setup_codex_cli.run(client).changed


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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))
    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        assert setup_codex_cli.run(client).changed

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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))
    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        assert setup_codex_cli.run(client).changed

    assert len(requests) == 2


def test_run_does_not_retry_permanent_http_status(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    _forbid_migration(monkeypatch)
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400)

    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        assert setup_codex_cli.run(client) == post_apply_outcome.PostApplyOutcome()

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
    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        assert setup_codex_cli.run(client) == post_apply_outcome.PostApplyOutcome()

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
    with httpx.Client(transport=httpx.MockTransport(handle_request)) as client:
        assert setup_codex_cli.run(client) == post_apply_outcome.PostApplyOutcome()

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
    with _make_client() as client:
        assert setup_codex_cli.run(client) == post_apply_outcome.PostApplyOutcome()

    assert not temp_path.exists()


def test_run_keeps_old_versions_when_installer_fails(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    calls: list[_Call] = []
    _forbid_migration(monkeypatch)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, failing="installer"))

    with _make_client() as client:
        assert setup_codex_cli.run(client) == post_apply_outcome.PostApplyOutcome()

    assert len(calls) == 1
    assert not Path(calls[0][0][1]).exists()


def test_run_keeps_old_versions_when_launcher_is_missing(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    calls: list[_Call] = []
    _forbid_migration(monkeypatch)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls))

    with _make_client() as client:
        assert setup_codex_cli.run(client) == post_apply_outcome.PostApplyOutcome()

    assert len(calls) == 1


def test_run_keeps_old_versions_when_verification_fails(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    _forbid_migration(monkeypatch)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher, failing="version"))

    with _make_client() as client:
        assert setup_codex_cli.run(client) == post_apply_outcome.PostApplyOutcome()

    assert not any("uninstall" in command for command, _ in calls)


def test_run_skips_mise_removal_when_no_version_is_installed(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

    assert not any(command[1] in {"uninstall", "reshim"} for command, _ in calls[2:])


def test_run_skips_mise_removal_when_listed_version_is_not_installed(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        setup_codex_cli.common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_list=_UNINSTALLED_LISTING),
    )

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

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
        if command[1:] == ["reshim", "--force"]:
            shim.unlink()
        return result

    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", fake_run)

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

    assert any(command[1:] == ["ls", "--json"] for command, _ in calls)
    assert calls[-1][0][1:] == ["reshim", "--force"]
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
        setup_codex_cli.common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_all="{}"),
    )

    with _make_client() as client, caplog.at_level(logging.WARNING, logger="pytools._internal.setup_codex_cli"):
        assert setup_codex_cli.run(client).changed

    assert shim.is_file()
    assert any(f"mise管理外の中継を保持: {shim}" in message for message in caplog.messages)


def test_run_reshims_after_npm_migration_without_mise_versions(monkeypatch, tmp_path: Path) -> None:
    """mise管理版が無くてもnpm版の除去でshimが実体を失うため、移行後にreshimする。"""
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: True)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

    assert not any(command[1] == "uninstall" for command, _ in calls[2:])
    assert calls[-1][0][1:] == ["reshim", "--force"]


@pytest.mark.parametrize("failing", ["mise_list", "mise_json", "mise_uninstall", "reshim"])
def test_run_propagates_mise_failures(monkeypatch, tmp_path: Path, failing: str) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        setup_codex_cli.common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_list=_INSTALLED_LISTING, failing=failing),
    )

    with _make_client() as client:
        assert setup_codex_cli.run(client).failure is not None


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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).failure is not None


def test_run_skips_mise_removal_when_mise_is_absent(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name, **kwargs: None)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

    assert len(calls) == 2


def test_install_propagates_package_removal_failure(monkeypatch, tmp_path: Path) -> None:
    """導入が成功しても整理失敗をpost_applyへ伝える。"""
    _isolate(monkeypatch, tmp_path, "linux")
    current = _release(tmp_path / ".codex", "standalone", "0.162.1")
    _release(tmp_path / ".codex", "standalone", "0.150.0")
    (current.parent.parent / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run([]))
    monkeypatch.setattr(prune_codex_packages.codex_processes, "codex_processes", lambda: ())

    def fail_remove(path, **kwargs):
        del path, kwargs
        raise PermissionError("denied")

    monkeypatch.setattr(prune_codex_packages.shutil, "rmtree", fail_remove)
    with _make_client() as client:
        assert setup_codex_cli.run(client).failure is not None


@pytest.mark.windows
def test_native_reference_is_replaced_and_tracks_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """参照は1個だけを交換し、currentの世代更新に追随して旧版へ固定しない。"""
    _isolate(monkeypatch, tmp_path, "linux")
    visible = tmp_path / ".local/bin"
    reference = visible / ".dotfiles-codex-native"
    for name in ("first-home", "second-home"):
        home = tmp_path / name
        selected = _release(home, "standalone", "0.166.0")
        current = selected.parent.parent / "current"
        current.symlink_to(selected, target_is_directory=True)
        native = current / "bin/codex"
        setup_codex_cli.install_recovery_launcher(native, windows=False)
        assert reference.readlink().samefile(native)
        runtime = codex_runtime.find_runtime_launcher(windows=False)
        assert runtime is not None
        assert runtime == reference
        assert runtime.samefile(native)
        newer = _release(home, "standalone", "0.167.0")
        (newer / "bin/codex").write_text("new-generation", encoding="utf-8")
        current.unlink()
        current.symlink_to(newer, target_is_directory=True)
        assert reference.samefile(newer / "bin/codex")
        assert runtime.read_text(encoding="utf-8") == "new-generation"
        assert list(visible.glob(".dotfiles-codex-native*")) == [reference]


@pytest.mark.parametrize("windows", [False, True])
def test_runtime_uses_path_native_and_excludes_wrapper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, windows: bool) -> None:
    """PATH上の自己参照を除外し、設定ホーム外の既存nativeを実行せず選ぶ。"""
    _isolate(monkeypatch, tmp_path, "win32" if windows else "linux")
    root = tmp_path / "dotfiles"
    alias = tmp_path / "alias"
    alias.mkdir()
    name = "codex.cmd" if windows else "codex"
    (alias / name).symlink_to(root / "bin" / name)
    native = tmp_path / "native-bin" / ("codex.exe" if windows else "codex")
    native.parent.mkdir()
    native.write_bytes(b"native")
    monkeypatch.setenv("PATH", (";" if windows else os.pathsep).join(map(str, (root / "bin", alias, native.parent))))
    assert codex_runtime.find_runtime_launcher(windows=windows) == (None if windows else native)
    native.unlink()
    assert codex_runtime.find_runtime_launcher(windows=windows) is None
