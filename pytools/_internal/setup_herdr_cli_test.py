"""pytools._internal.setup_herdr_cliのテスト。"""

import errno
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pytools._internal import post_apply_outcome, setup_herdr_cli


@pytest.mark.parametrize("operation", ["readlink", "resolve", "stat"])
@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EIO])
def test_windows_io_failure_never_reinstalls(monkeypatch, tmp_path: Path, operation: str, error_number: int) -> None:
    """リンク・本体の権限不足と未知I/O失敗は未導入に変換しない。"""
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "win32")
    home = tmp_path / "home"
    monkeypatch.setenv("HERDR_HOME", str(home))
    monkeypatch.setenv("HERDR_INSTALL_DIR", str(tmp_path / "visible"))
    release = home / "packages/standalone/releases/version"
    release.mkdir(parents=True)
    executable = release / "herdr.exe"
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
        pytest.fail("I/O失敗後に導入または本体実行を行った")

    monkeypatch.setattr(Path, operation, inspect)
    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", forbidden)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "run_official_installer", forbidden)
    with pytest.raises(OSError) as caught:
        setup_herdr_cli.run()
    assert caught.value.errno == error_number


@pytest.mark.parametrize("kind", ["missing", "outside", "cycle", "npm"])
def test_windows_unowned_launcher_is_not_executed(monkeypatch, tmp_path: Path, kind: str) -> None:
    """未導入と不正なリンク、旧ランチャーだけの状態から未知の本体を起動しない。"""
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "win32")
    home = tmp_path / "home"
    visible = tmp_path / "bin"
    monkeypatch.setenv("HERDR_HOME", str(home))
    monkeypatch.setenv("HERDR_INSTALL_DIR", str(visible))
    root = home / "packages/standalone"
    (root / "releases").mkdir(parents=True)
    current = root / "current"
    if kind == "outside":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "herdr.exe").write_bytes(b"native")
        current.symlink_to(outside, target_is_directory=True)
    elif kind == "cycle":
        current.symlink_to(current, target_is_directory=True)
    elif kind == "npm":
        visible.mkdir()
        (visible / "herdr.cmd").write_text("node missing.js", encoding="utf-8")
    installers: list[bool] = []

    def installer(*args: object, **kwargs: object) -> tuple[None, str]:
        del args, kwargs
        installers.append(True)
        return None, "installer unavailable"

    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("未知の本体を起動した")

    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "run_official_installer", installer)
    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", forbidden)
    assert setup_herdr_cli.run().changed is False
    assert installers == [True]


@pytest.mark.parametrize("failure", [False, True])
def test_windows_owned_release_update_avoids_visible_alias(monkeypatch, tmp_path: Path, failure: bool) -> None:
    """設定先の版実体で更新と確認を行い、本体の失敗は変更成功にしない。"""
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "win32")
    home = tmp_path / "custom-home"
    monkeypatch.setenv("HERDR_HOME", str(home))
    monkeypatch.setenv("HERDR_INSTALL_DIR", str(tmp_path / "custom-bin"))
    release = home / "packages/standalone/releases/version"
    release.mkdir(parents=True)
    executable = release / "herdr.exe"
    executable.write_bytes(b"native")
    (release.parent.parent / "current").symlink_to(release, target_is_directory=True)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, int(failure), "", "unknown failure" if failure else "")

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)
    outcome = setup_herdr_cli.run()
    assert outcome.changed is not failure
    assert calls == (
        [[str(executable), "update"]] if failure else [[str(executable), "update"], [str(executable), "--version"]]
    )


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

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", prepended.append)

    assert setup_herdr_cli.run().changed is True
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

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)

    with _fake_client(handler) as client:
        assert setup_herdr_cli.run(client).changed is True

    assert requested == ["https://herdr.dev/install.sh"]
    assert commands[0][0] == "bash"
    assert commands[-1] == [str(launcher), "--version"]


@pytest.mark.parametrize(
    ("returncode", "stderr", "inside_session"),
    [
        (1, "update failed: run `herdr update` outside herdr after detaching from the session", True),
        (
            0,
            "downloaded v0.9.6\nHerdr was not updated.\nStop running Herdr sessions when ready, then run `herdr update` again.",
            False,
        ),
    ],
    ids=["inside-session", "server-restart-required"],
)
def test_run_defers_update_inside_herdr_when_existing_launcher_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    stderr: str,
    inside_session: bool,
) -> None:
    """セッション内の拒否と、稼働サーバーの再起動を要するため導入しなかった更新は、既存版を確認して延期を返す。

    手動でHerdr外から更新するよう案内する。
    非対話の`herdr update`は導入しなかった場合も終了0で返すため、診断を確認して延期を判定する。
    """
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
            return subprocess.CompletedProcess(command, returncode, "", stderr)
        return subprocess.CompletedProcess(command, 0, "herdr 0.9.1", "")

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", prepended.append)

    result = setup_herdr_cli.run()

    assert result.changed is False
    assert result.deferred is True
    assert result.failure is None
    (notice,) = result.notices
    reason = (
        "Herdrセッション内では自己更新できないため、更新を保留しました。"
        if inside_session
        else "稼働中のHerdrサーバーの再起動を要する更新のため、稼働中のセッションを保って更新を保留しました。"
    )
    assert notice.message.startswith(reason)
    assert "次のコマンドを実行する" in notice.message
    assert notice.command == "herdr update"
    assert commands == [[str(launcher), "update"], [str(launcher), "--version"]]
    assert prepended == [launcher.parent]


def test_run_skips_deferred_update_when_existing_launcher_is_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """既知の更新制約でも既存版を確認できなければ、導入の失敗として警告しスキップと数える。"""
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

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)

    with caplog.at_level("WARNING"):
        assert setup_herdr_cli.run() == post_apply_outcome.PostApplyOutcome()
    assert "更新後の確認に失敗" in caplog.text


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
    monkeypatch.setenv("HERDR_HOME", str(tmp_path / ".herdr"))
    monkeypatch.delenv("HERDR_INSTALL_DIR", raising=False)
    launcher = tmp_path / ".herdr/packages/standalone/releases/version/herdr.exe"
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
            (launcher.parent.parent.parent / "current").symlink_to(launcher.parent, target_is_directory=True)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)

    with _fake_client(handler) as client:
        assert setup_herdr_cli.run(client).changed is True

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
    失効確認先へ到達できないネットワークで更新が失敗し、Herdrを更新できない。
    起動を個別に列挙せず記録した全起動を判定し、ステップへ加わる子プロセスの漏れも検出する。
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "find_powershell", lambda: "pwsh")
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setenv("CURL_HOME", str(tmp_path / "original-curl-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HERDR_HOME", str(tmp_path / ".herdr"))
    monkeypatch.delenv("HERDR_INSTALL_DIR", raising=False)
    launcher = tmp_path / ".herdr/packages/standalone/releases/version/herdr.exe"
    if installed:
        launcher.parent.mkdir(parents=True)
        launcher.write_text("", encoding="utf-8")
        (launcher.parent.parent.parent / "current").symlink_to(launcher.parent, target_is_directory=True)
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
            (launcher.parent.parent.parent / "current").symlink_to(launcher.parent, target_is_directory=True)
        returncode = update_returncode if command[-1] == "update" else 0
        return subprocess.CompletedProcess(command, returncode, "ok", "update failed")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"Write-Host")

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)

    with _fake_client(handler) as client:
        outcome = setup_herdr_cli.run(client)
    assert outcome.changed is not bool(update_returncode)

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

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)

    with _fake_client(handler) as client:
        assert setup_herdr_cli.run(client).changed is True

    assert len(overrides) == 2
    assert all(value is None for value in overrides)


def test_run_skips_when_installer_is_unreachable(monkeypatch, tmp_path: Path, caplog) -> None:
    """公式インストーラーを取得できない場合は失敗を呼び出し元へ伝える。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    with _fake_client(handler) as client, caplog.at_level("WARNING"):
        assert setup_herdr_cli.run(client) == post_apply_outcome.PostApplyOutcome()
    assert "取得に失敗" in caplog.text


def test_run_skips_when_installer_fails(monkeypatch, tmp_path: Path, caplog) -> None:
    """公式インストーラーの非0終了を成功として扱わない。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_herdr_cli.sys, "platform", "linux")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"#!/bin/bash\n")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1, "", "installer failed")

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)

    with _fake_client(handler) as client, caplog.at_level("WARNING"):
        assert setup_herdr_cli.run(client) == post_apply_outcome.PostApplyOutcome()
    assert "導入または更新に失敗" in caplog.text


@pytest.mark.parametrize("failure_command", ["update", "--version"])
def test_run_skips_on_update_or_verification_failure(
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

    monkeypatch.setattr(setup_herdr_cli.common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_herdr_cli.setup_cli_common, "prepend_path", fail_prepend)

    assert setup_herdr_cli.run() == post_apply_outcome.PostApplyOutcome()
