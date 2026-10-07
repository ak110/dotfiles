"""pytools._internal.setup_herdr_cliのテスト。"""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pytools._internal import post_apply_outcome, setup_herdr_cli


def _fake_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_run_updates_existing_direct_install_and_prepends_path(monkeypatch, tmp_path: Path) -> None:
    """既存の公式直接インストール版を更新し、確認後にPATHの先頭へ加える。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "herdr"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    prepended: list[Path] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", prepended.append)

    assert setup_herdr_cli.run() is True
    assert commands == [[str(launcher), "update"], [str(launcher), "--version"]]
    assert prepended == [launcher.parent]


def test_run_installs_posix_direct_install(monkeypatch, tmp_path: Path) -> None:
    """POSIXの未導入環境では公式インストーラーを実行して確認する。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "herdr"
    requested: list[str] = []
    commands: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"#!/bin/bash\n")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if command[0] == "bash":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)

    with _fake_client(handler) as client:
        assert setup_herdr_cli.run(client) is True

    assert requested == ["https://herdr.dev/install.sh"]
    assert commands[0][0] == "bash"
    assert commands[-1] == [str(launcher), "--version"]


def test_run_defers_update_inside_herdr_when_existing_launcher_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """セッション離脱後の更新を求める既知診断は、既存版を確認して案内へ変換する。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "herdr"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    commands: list[list[str]] = []
    prepended: list[Path] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if command[-1] == "update":
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "update failed: run `herdr update` outside herdr after detaching from the session",
            )
        return subprocess.CompletedProcess(command, 0, "herdr 0.9.1", "")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", prepended.append)

    result = setup_herdr_cli.run()

    assert result == post_apply_outcome.PostApplyOutcome(
        changed=False,
        notices=(
            post_apply_outcome.PostApplyNotice(
                message="Herdrセッション内では自己更新できないため、更新を保留しました。",
                command="herdr update",
            ),
        ),
    )
    assert commands == [[str(launcher), "update"], [str(launcher), "--version"]]
    assert prepended == [launcher.parent]


def test_run_keeps_deferred_update_fatal_when_existing_launcher_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """既知の更新制約でも既存版を確認できなければ失敗を上位へ伝える。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "herdr"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[-1] == "update":
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "update failed: run `herdr update` outside herdr after detaching from the session",
            )
        return subprocess.CompletedProcess(command, 1, "", "version failed")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)

    with pytest.raises(RuntimeError, match="更新後の確認に失敗"):
        setup_herdr_cli.run()


@pytest.mark.parametrize("use_local_app_data", [False, True])
def test_run_installs_windows_direct_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    use_local_app_data: bool,
) -> None:
    """Windowsでは安定互換パスを対象にPowerShellで公式インストーラーを実行する。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "find_powershell", lambda: "pwsh")
    monkeypatch.setenv("CURL_HOME", str(tmp_path / "original-curl-home"))
    local_app_data = tmp_path / "custom-local" if use_local_app_data else tmp_path / "AppData" / "Local"
    if use_local_app_data:
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    else:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    launcher = local_app_data / "Programs" / "Herdr" / "bin" / "herdr.exe"
    requested: list[str] = []
    commands: list[list[str]] = []
    installer_curl_home: list[Path] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"Write-Host")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        env_overrides = kwargs["env_overrides"]
        assert isinstance(env_overrides, dict)
        curl_home = Path(env_overrides["CURL_HOME"])
        installer_curl_home.append(curl_home)
        assert (curl_home / ".curlrc").read_text(encoding="ascii") == "ssl-revoke-best-effort\n"
        if command[0] == "pwsh":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)

    with _fake_client(handler) as client:
        assert setup_herdr_cli.run(client) is True

    assert requested == ["https://herdr.dev/install.ps1"]
    assert commands[0][:2] == ["pwsh", "-NoProfile"]
    assert commands[-1] == [str(launcher), "--version"]
    assert len(set(installer_curl_home)) == 1
    assert not installer_curl_home[0].exists()
    assert os.environ["CURL_HOME"] == str(tmp_path / "original-curl-home")


@pytest.mark.parametrize(
    ("installed", "update_returncode"),
    [
        pytest.param(True, 0, id="update"),
        pytest.param(True, 1, id="update-failure"),
        pytest.param(False, 0, id="install"),
    ],
)
def test_run_passes_same_curl_home_to_all_windows_children(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed: bool,
    update_returncode: int,
) -> None:
    """Windowsではステップが起動する全子プロセスへ同じ失効確認の設定を渡し、終了後に除去する。

    導入済みの`herdr update`もmanifestの取得で`curl`を起動する。設定が導入経路にだけ渡ると、
    失効確認先へ到達できないネットワークで更新が失敗し、post-apply全体が失敗する。
    起動を個別に列挙せず記録した全起動を判定し、ステップへ加わる子プロセスの漏れも検出する。
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "find_powershell", lambda: "pwsh")
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setenv("CURL_HOME", str(tmp_path / "original-curl-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    launcher = tmp_path / "local" / "Programs" / "Herdr" / "bin" / "herdr.exe"
    if installed:
        launcher.parent.mkdir(parents=True)
        launcher.write_text("", encoding="utf-8")
    received: list[tuple[list[str], object, str | None]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env_overrides = kwargs.get("env_overrides")
        curlrc = None
        if isinstance(env_overrides, dict) and "CURL_HOME" in env_overrides:
            curlrc = (Path(env_overrides["CURL_HOME"]) / ".curlrc").read_text(encoding="ascii")
        received.append((command, env_overrides, curlrc))
        if command[0] == "pwsh":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        returncode = update_returncode if command[-1] == "update" else 0
        return subprocess.CompletedProcess(command, returncode, "ok", "update failed")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"Write-Host")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)

    with _fake_client(handler) as client:
        if update_returncode:
            with pytest.raises(RuntimeError, match="導入または更新に失敗"):
                setup_herdr_cli.run(client)
        else:
            assert setup_herdr_cli.run(client) is True

    assert received
    overrides = [env_overrides for _, env_overrides, _ in received]
    assert all(isinstance(value, dict) for value in overrides), received
    curl_homes = {value["CURL_HOME"] for value in overrides if isinstance(value, dict)}
    assert len(curl_homes) == 1
    assert all(curlrc == "ssl-revoke-best-effort\n" for _, _, curlrc in received)
    assert not Path(next(iter(curl_homes))).exists()
    assert os.environ["CURL_HOME"] == str(tmp_path / "original-curl-home")


@pytest.mark.parametrize("installed", [True, False])
def test_run_does_not_pass_curl_home_outside_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed: bool,
) -> None:
    """Windows以外では失効確認の設定がSchannelに作用しないため、子プロセスへ環境上書きを渡さない。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)
    launcher = tmp_path / ".local" / "bin" / "herdr"
    if installed:
        launcher.parent.mkdir(parents=True)
        launcher.write_text("", encoding="utf-8")
    overrides: list[object] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        overrides.append(kwargs.get("env_overrides"))
        if command[0] == "bash":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"#!/bin/bash\n")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)

    with _fake_client(handler) as client:
        assert setup_herdr_cli.run(client) is True

    assert len(overrides) == 2
    assert all(value is None for value in overrides)


def test_run_raises_when_installer_is_unreachable(monkeypatch, tmp_path: Path) -> None:
    """公式インストーラーを取得できない場合は失敗を呼び出し元へ伝える。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    with _fake_client(handler) as client, pytest.raises(RuntimeError, match="取得に失敗"):
        setup_herdr_cli.run(client)


def test_run_raises_when_installer_fails(monkeypatch, tmp_path: Path) -> None:
    """公式インストーラーの非0終了を成功として扱わない。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"#!/bin/bash\n")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1, "", "installer failed")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)

    with _fake_client(handler) as client, pytest.raises(RuntimeError, match="導入または更新に失敗"):
        setup_herdr_cli.run(client)


@pytest.mark.parametrize("failure_command", ["update", "--version"])
def test_run_raises_on_update_or_verification_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_command: str,
) -> None:
    """更新または更新後の確認が失敗した状態でPATHを変更しない。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "herdr"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1 if command[-1] == failure_command else 0, "", "failed")

    def fail_prepend(path: Path) -> None:
        raise AssertionError(f"失敗後にPATHへ追加した: {path}")

    monkeypatch.setattr(setup_herdr_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", fail_prepend)

    with pytest.raises(RuntimeError):
        setup_herdr_cli.run()
