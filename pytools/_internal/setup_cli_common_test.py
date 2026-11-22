import os
import subprocess
import sys
import types
import typing
from pathlib import Path

import httpx
import psutil
import pytest

from pytools._internal import setup_cli_common
from pytools._internal._test_helpers import patch_windows_processes, windows_process


@pytest.mark.parametrize(
    ("platform", "explicit_ca", "uses_system_store"),
    [("win32", False, True), ("win32", True, False), ("linux", False, False)],
)
def test_official_installer_uses_platform_trust_and_explicit_ca(
    monkeypatch: pytest.MonkeyPatch, platform: str, explicit_ca: bool, uses_system_store: bool
) -> None:
    """CAの指定がないWindowsで使うOSの信頼済みCAと、環境変数で指定したCAを分け、取得後の実行経路を維持する。"""
    monkeypatch.setattr(setup_cli_common, "sys", types.SimpleNamespace(platform=platform))
    if explicit_ca:
        monkeypatch.setenv("SSL_CERT_FILE", "/example/custom-ca.pem")
    else:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    context = object()
    context_calls: list[bool] = []

    def default_context() -> object:
        context_calls.append(True)
        return context

    monkeypatch.setattr(setup_cli_common.ssl, "create_default_context", default_context)
    options: list[dict[str, object]] = []
    requested: list[str] = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_error: object) -> None:
            self.close()

        def __init__(self, **kwargs: object) -> None:
            options.append(kwargs)

        def get(self, url: str) -> httpx.Response:
            requested.append(url)
            return httpx.Response(200, content=b"installer", request=httpx.Request("GET", url))

        def close(self) -> None:
            pass

    monkeypatch.setattr(setup_cli_common.httpx, "Client", Client)
    monkeypatch.setattr(setup_cli_common.shutil, "which", lambda name: "/usr/bin/pwsh" if name == "pwsh" else None)
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", run)
    result, reason = setup_cli_common.run_official_installer(
        None,
        posix_url="https://example.test/install.sh",
        windows_url="https://example.test/install.ps1",
        tag="test",
        timeout=5.0,
    )

    assert result is not None and result.returncode == 0
    assert reason == ""
    assert options[0]["verify"] is (context if uses_system_store else True)
    assert len(context_calls) == int(uses_system_store)
    assert requested == ["https://example.test/install.ps1" if platform == "win32" else "https://example.test/install.sh"]
    assert commands[0][0] == ("/usr/bin/pwsh" if platform == "win32" else "bash")


def test_find_powershell_uses_windows_powershell_when_pwsh_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        setup_cli_common.shutil, "which", lambda name: "C:/Windows/powershell.exe" if name == "powershell" else None
    )

    assert setup_cli_common.find_powershell() == "C:/Windows/powershell.exe"


def test_official_installer_decodes_windows_powershell_with_local_code_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PowerShell 5.1の日本語診断だけロケールのコードページで受け取る。"""
    monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
    monkeypatch.setattr(setup_cli_common, "find_powershell", lambda: "C:/Windows/powershell.exe")
    monkeypatch.setattr(setup_cli_common.locale, "getpreferredencoding", lambda _setlocale: "cp932")
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"Write-Host", request=request)

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[0] == "C:/Windows/powershell.exe"
        observed.append(str(kwargs["encoding"]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_cli_common.common, "run_subprocess", run)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, reason = setup_cli_common.run_official_installer(
            client,
            posix_url="https://example.test/install.sh",
            windows_url="https://example.test/install.ps1",
            tag="test",
            timeout=5.0,
        )
    assert result is not None and result.returncode == 0
    assert reason == ""
    assert observed == ["cp932"]


def test_find_powershell_reports_missing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_cli_common.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="PowerShellが見つからない"):
        setup_cli_common.find_powershell()


def test_prepend_path_moves_existing_entry_to_front(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first"
    canonical = tmp_path / "canonical"
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(canonical), str(first)]))

    setup_cli_common.prepend_path(canonical)

    assert os.environ["PATH"].split(os.pathsep) == [str(canonical), str(first), str(first)]


_STANDALONE = r"C:\Users\Me\.codex\packages\standalone"
_STANDALONE_RELEASE = rf"{_STANDALONE}\releases\0.170.0-x86_64-pc-windows-msvc"
_DAEMON_EXE = r"C:\Users\Me\.codex\packages\app-server-daemon\releases\0.170.0-x86_64-pc-windows-msvc\bin\codex.exe"
_NPM_CODEX = r"C:\Users\Me\AppData\Roaming\npm\node_modules\@openai\codex"
_NPX_CODEX = r"C:\Users\Me\AppData\Local\npm-cache\_npx\9f2c\node_modules\@openai\codex"
_NATIVE_IN_PACKAGE = r"node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\codex\codex.exe"
_NODE = r"C:\Program Files\nodejs\node.exe"
_MISE = r"C:\Users\Me\AppData\Local\Microsoft\WinGet\Links\mise.exe"
_CODEX_FILES: dict[str, tuple[Path, ...]] = {"native": (Path(_STANDALONE),), "packages": (Path(_NPM_CODEX),)}


@pytest.mark.parametrize(
    ("process", "expected"),
    [
        pytest.param(windows_process(rf"{_STANDALONE_RELEASE}\bin\codex.exe"), True, id="standalone-session"),
        pytest.param(windows_process(rf"{_STANDALONE_RELEASE}\codex-path\rg.exe"), True, id="standalone-helper"),
        pytest.param(
            windows_process(rf"{_STANDALONE_RELEASE}\codex-path\rg.exe", denied=["exe"]),
            True,
            id="standalone-helper-exe-denied",
        ),
        pytest.param(
            windows_process(rf"\\?\{_STANDALONE_RELEASE}\codex-path\rg.exe", denied=["exe"]),
            True,
            id="standalone-helper-extended-path",
        ),
        pytest.param(windows_process(_DAEMON_EXE, "app-server", "daemon", "pid-update-loop"), False, id="update-loop"),
        pytest.param(
            windows_process(_DAEMON_EXE, "app-server", "--managed-daemon", denied=["cmdline"]),
            False,
            id="daemon-cmdline-denied",
        ),
        pytest.param(
            windows_process(
                r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1106.6628.0_x64__2p2nqsd0c76g0\app\resources\codex.exe"
            ),
            False,
            id="desktop-app",
        ),
        pytest.param(windows_process("C:/node/codex.exe"), False, id="other-codex"),
        pytest.param(windows_process(r"C:\tools\codex.exe", denied=["exe"]), True, id="codex-exe-denied"),
        pytest.param(windows_process(rf"{_NPM_CODEX}\{_NATIVE_IN_PACKAGE}"), True, id="npm-native"),
        pytest.param(windows_process(_NODE, rf"{_NPM_CODEX}\bin\codex.js"), True, id="npm-node"),
        pytest.param(windows_process(_NODE, rf"{_NPM_CODEX}\bin\codex.js", denied=["exe"]), True, id="npm-node-exe-denied"),
        pytest.param(
            windows_process(_MISE, "exec", "--", "node", rf"{_NPM_CODEX}\bin\codex.js", denied=["exe"]),
            True,
            id="npm-mise-exe-denied",
        ),
        pytest.param(windows_process(_NODE, denied=["cmdline"]), True, id="node-cmdline-denied"),
        pytest.param(windows_process(_MISE, denied=["exe", "cmdline"]), True, id="mise-unreadable"),
        pytest.param(windows_process(rf"{_NPX_CODEX}\{_NATIVE_IN_PACKAGE}"), False, id="npx-native"),
        pytest.param(windows_process(_NODE, rf"{_NPX_CODEX}\bin\codex.js"), False, id="npx-node"),
        pytest.param(windows_process(_MISE, "exec", "codex"), False, id="mise-without-separator"),
        pytest.param(windows_process(_MISE, "exec", "--", "codex"), True, id="mise-launcher-name"),
        pytest.param(windows_process(_MISE, "exec", "--", "server", "codex"), False, id="mise-other-command"),
        pytest.param(windows_process(_NODE, "--no-warnings", f"{_NPM_CODEX}/bin/codex.js"), True, id="node-option"),
        pytest.param(windows_process(_NODE, "--require", rf"{_NPM_CODEX}\bin\codex.js", "server.js"), False, id="require"),
        pytest.param(windows_process(_NODE, "--title", "codex", "server.js"), False, id="title"),
        pytest.param(
            windows_process(_MISE, "x", "--", "node", "--no-warnings", rf"{_NPM_CODEX}\bin\codex.js"),
            True,
            id="mise-node-option",
        ),
        pytest.param(windows_process(_NODE, "--eval", "codex", rf"{_NPM_CODEX}\bin\codex.js"), False, id="eval"),
        pytest.param(windows_process(_NODE, "--", rf"{_NPM_CODEX}\bin\codex.js", "prompt"), True, id="separator"),
        pytest.param(windows_process(_NODE, "C:/bin/codex.cmd"), False, id="launcher-outside-package"),
        pytest.param(windows_process(_NODE, "bin/codex.js"), True, id="relative-launcher"),
        pytest.param(windows_process(r"C:\python\python.exe", "codex"), False, id="python"),
        pytest.param(windows_process(_NODE, "C:/work/codex-project/server.js"), False, id="project-server"),
        pytest.param(windows_process(_NODE, "server.js", "--prompt=codex"), False, id="prompt-option"),
        pytest.param(windows_process(_NODE, "server.js", rf"{_NPM_CODEX}\bin\codex.js"), False, id="later-argument"),
    ],
)
def test_windows_counts_processes_started_from_files_to_replace_or_remove(
    monkeypatch, process: types.SimpleNamespace, expected: bool
) -> None:
    """導入が置き換えるか移行が削除する配置から起動したプロセスと、その可能性を否定できないプロセスだけを数える。"""
    patch_windows_processes(monkeypatch, [process])

    assert bool(setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)) is expected


@pytest.mark.parametrize(
    "process",
    [
        windows_process(_NODE, denied=["cmdline"]),
        windows_process(_MISE, "exec", "--", "codex"),
        windows_process(_NODE, rf"{_NPX_CODEX}\bin\codex.js"),
    ],
)
def test_windows_node_is_not_counted_without_npm_or_mise_version_to_remove(monkeypatch, process: types.SimpleNamespace) -> None:
    patch_windows_processes(monkeypatch, [process])

    assert not setup_cli_common.find_windows_cli_processes("codex", native=(Path(_STANDALONE),))


@pytest.mark.parametrize(
    ("exe", "expected"),
    [
        # 更新が同じパスで置き換える導入先。Windowsのパスは大文字小文字と区切りの表記が揺れる。
        (r"c:\users\me\.local\bin\CLAUDE.EXE", True),
        (r"C:\Program Files\WindowsApps\Claude_5.11492.5.0_x64__pzs8sxrjxfjjc\app\claude.exe", False),
        (r"C:\Users\Me\AppData\Roaming\Claude\claude-code\2.1.325\806e0a45e6f5\claude.exe", False),
    ],
)
def test_windows_claude_counts_only_replaced_launcher(monkeypatch, exe: str, expected: bool) -> None:
    """Claude Codeは、更新が置き換える導入先から起動した場合だけ数え、デスクトップアプリと同梱版は数えない。"""
    patch_windows_processes(monkeypatch, [windows_process(exe)])

    native = (Path(r"C:\Users\Me\.local\bin\claude.exe"),)
    assert bool(setup_cli_common.find_windows_cli_processes("claude", native=native)) is expected


def test_windows_check_is_skipped_on_other_platforms(monkeypatch) -> None:
    """Windows以外では実行中のファイルを置き換えても衝突しないため、書き換える配置から起動したプロセスも数えない。"""
    patch_windows_processes(monkeypatch, [windows_process(rf"{_STANDALONE_RELEASE}\bin\codex.exe")])
    monkeypatch.setattr(setup_cli_common.sys, "platform", "linux")

    assert not setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)


@pytest.mark.parametrize("error", [psutil.NoSuchProcess(1), psutil.ZombieProcess(2)])
def test_windows_process_races_are_ignored(monkeypatch, error: Exception) -> None:
    class VanishedProcess:
        def name(self) -> str:
            raise error

    patch_windows_processes(monkeypatch, [VanishedProcess()])

    assert not setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)


def _inject_previous_generation(monkeypatch: pytest.MonkeyPatch, pid: int, *, name: str, exe: str) -> None:
    stale = psutil.Process(pid)
    monkeypatch.setattr(stale, "_name", name)
    monkeypatch.setattr(stale, "_exe", exe)
    monkeypatch.setitem(vars(psutil)["_pmap"], pid, stale)
    real_process_iter = psutil.process_iter

    def own_process(*args: typing.Any, **kwargs: typing.Any) -> list[psutil.Process]:
        return [item for item in real_process_iter(*args, **kwargs) if item.pid == pid]

    monkeypatch.setattr(psutil, "process_iter", own_process)


@pytest.mark.skipif(
    os.name == "nt", reason="仮想環境のpython.exeは本体を子として起動するため、起動したPIDだけに走査を限定できない"
)
def test_windows_cli_running_reads_current_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) as child:
        try:
            _inject_previous_generation(monkeypatch, child.pid, name="codex.exe", exe=rf"{_STANDALONE_RELEASE}\bin\codex.exe")
            monkeypatch.setattr(setup_cli_common.sys, "platform", "win32")
            running = setup_cli_common.find_windows_cli_processes("codex", **_CODEX_FILES)
        finally:
            child.kill()
    assert not running
