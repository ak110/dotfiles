"""pytools._internal.setup_claude_cliのテスト。"""

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from pytools._internal import post_apply_outcome, setup_claude_cli, setup_cli_common
from pytools._internal._test_helpers import npm_windows_shims, patch_windows_processes, windows_process


def test_run_updates_existing_native_and_migrates_after_verification(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    events: list[str] = []
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])

    def fake_prepend(path: Path) -> None:
        events.append(f"path:{path}")

    def fake_migrate(*args: object, **kwargs: object) -> None:
        del args, kwargs
        events.append("migrate")

    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "prepend_path", fake_prepend)
    monkeypatch.setattr(
        setup_claude_cli.setup_cli_common,
        "warn_about_npm_launchers",
        fake_migrate,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_claude_cli.common, "run_subprocess", fake_run)

    assert setup_claude_cli.run().changed
    assert calls == [[str(launcher), "update"], [str(launcher), "--version"]]
    assert events == [f"path:{launcher.parent}", "migrate"]


def test_run_does_not_search_for_claude_outside_path(monkeypatch, tmp_path: Path) -> None:
    """Codex用の追加走査先に相当する場所のClaude Codeは削除対象へ含めない。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("native", encoding="utf-8")
    outside = tmp_path / ".nvm" / "versions" / "node" / "v24" / "bin"
    outside.mkdir(parents=True)
    legacy = outside / "claude"
    legacy.write_text("legacy", encoding="utf-8")
    path_entry = tmp_path / "path"
    path_entry.mkdir()
    monkeypatch.setenv("PATH", str(path_entry))
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_claude_cli.common, "run_subprocess", fake_run)

    assert setup_claude_cli.run().changed
    assert legacy.read_text(encoding="utf-8") == "legacy"
    assert calls == [[str(launcher), "update"], [str(launcher), "--version"]]


def test_run_installs_with_powershell_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude.exe"
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_powershell", lambda: "pwsh")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args: None)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[0] == "pwsh":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_claude_cli.common, "run_subprocess", fake_run)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"installer"))) as client:
        assert setup_claude_cli.run(client).changed
    assert calls[0][:5] == ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert calls[1] == [str(launcher), "--version"]


def test_windows_install_prepends_canonical_path_before_noncanonical(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude.exe"
    noncanonical = tmp_path / "node"
    noncanonical.mkdir()
    monkeypatch.setenv("PATH", str(noncanonical))
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_powershell", lambda: "pwsh")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args: None)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[0] == "pwsh":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_claude_cli.common, "run_subprocess", fake_run)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"installer"))) as client:
        assert setup_claude_cli.run(client).changed

    assert os.environ["PATH"].split(os.pathsep)[0] == str(launcher.parent)


def test_run_does_not_migrate_when_verification_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        setup_claude_cli.setup_cli_common,
        "warn_about_npm_launchers",
        lambda *args: (_ for _ in ()).throw(AssertionError("移行してはならない")),
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1 if command[-1] == "--version" else 0, "", "")

    monkeypatch.setattr(setup_claude_cli.common, "run_subprocess", fake_run)

    assert setup_claude_cli.run() == post_apply_outcome.PostApplyOutcome()


def test_run_skips_all_work_when_windows_process_is_running(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    running = [setup_cli_common.CliProcess(1, setup_cli_common.Evidence.STARTED, "claude.exe")]
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: running)
    monkeypatch.setattr(
        setup_claude_cli.common,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )

    assert not setup_claude_cli.run().changed


@pytest.mark.parametrize(
    ("running", "updated"),
    [("claude-code", False), ("npm", True), ("npx", True), ("desktop-app", True)],
)
def test_windows_defers_only_while_files_to_replace_are_in_use(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, running: str, updated: bool
) -> None:
    """Windowsでは正規版の実行中だけ更新を延期し、旧npm版は実行状況にかかわらず保持する。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"native")
    # PATH上のnpmがグローバルに導入したClaude Code。
    npm_prefix = tmp_path / "npm"
    package = npm_prefix / "node_modules" / "@anthropic-ai" / "claude-code"
    package.mkdir(parents=True)
    (package / "cli.js").write_text("", encoding="utf-8")
    (package / "package.json").write_text('{"bin":{"claude":"cli.js"}}', encoding="utf-8")
    for suffix, content in npm_windows_shims("node_modules/@anthropic-ai/claude-code/cli.js").items():
        (npm_prefix / f"claude{suffix}").write_text(content, encoding="utf-8", newline="")
    (npm_prefix / "npm.cmd").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(npm_prefix))
    node = r"C:\Program Files\nodejs\node.exe"
    processes = {
        "claude-code": [windows_process(str(launcher))],
        "npm": [windows_process(node, str(package / "cli.js"))],
        "npx": [
            windows_process(
                node, r"C:\Users\Me\AppData\Local\npm-cache\_npx\4b1e\node_modules\@anthropic-ai\claude-code\cli.js"
            )
        ],
        # Claudeデスクトップアプリ本体と、アプリが会話用に取得したClaude Code。
        "desktop-app": [
            windows_process(r"C:\Program Files\WindowsApps\Claude_5.11492.5.0_x64__pzs8sxrjxfjjc\app\claude.exe"),
            windows_process(r"C:\Users\Me\AppData\Roaming\Claude\claude-code\2.1.325\806e0a45e6f5\claude.exe"),
        ],
    }[running]
    patch_windows_processes(monkeypatch, processes)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{npm_prefix}\n", "")
        if command[1:3] == ["root", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{npm_prefix / 'node_modules'}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_claude_cli.common, "run_subprocess", fake_run)

    setup_claude_cli.run()

    assert [command for command in calls if command[1] == "update"] == ([[str(launcher), "update"]] if updated else [])
    assert not any("uninstall" in command for command in calls)
    assert package.is_dir()
    assert ("旧npm版を保持" in caplog.text and str(launcher) in caplog.text) is updated


def test_run_handles_http_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request))) as client:
        assert setup_claude_cli.run(client) == post_apply_outcome.PostApplyOutcome()


def test_run_handles_installer_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        setup_claude_cli.common,
        "run_subprocess",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "installer failed"),
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"installer"))) as client:
        assert setup_claude_cli.run(client) == post_apply_outcome.PostApplyOutcome()


def test_run_handles_native_update_failure_and_keeps_legacy_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    native = tmp_path / ".local" / "bin" / "claude"
    native.parent.mkdir(parents=True)
    native.write_text("", encoding="utf-8")
    legacy = tmp_path / ".claude" / "local" / "claude"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        setup_claude_cli.common,
        "run_subprocess",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "update failed"),
    )

    assert setup_claude_cli.run() == post_apply_outcome.PostApplyOutcome()
    assert legacy.read_text(encoding="utf-8") == "legacy"
