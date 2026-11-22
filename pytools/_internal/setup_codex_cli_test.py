"""pytools._internal.setup_codex_cliのテスト。"""

import errno
import json
import logging
import os
import subprocess
import types
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pytools._internal import (
    codex_runtime,
    post_apply_outcome,
    prune_codex_packages,
    remove_codex_claude_mcp,
    setup_cli_common,
    setup_codex_cli,
)
from pytools._internal._test_helpers import FakeEnvironmentRegistry, npm_windows_shims, patch_windows_processes, windows_process
from pytools.prune_codex_packages_test import _release

_Call = tuple[list[str], dict[str, object]]
_FIND_WINDOWS_CLI_PROCESSES = setup_cli_common.find_windows_cli_processes
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: [])
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
        "warn_about_npm_launchers",
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
    running = [setup_cli_common.CliProcess(1, setup_cli_common.Evidence.STARTED, "codex.exe")]
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "find_windows_cli_processes", lambda *args, **kwargs: running)
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


_DAEMON_EXE = r"C:\Users\Me\.codex\packages\app-server-daemon\releases\0.170.0-x86_64-pc-windows-msvc\bin\codex.exe"
_DESKTOP_APP = r"C:\Program Files\WindowsApps\OpenAI.Codex_26.1106.6628.0_x64__2p2nqsd0c76g0\app"
_NODE = r"C:\Program Files\nodejs\node.exe"
_MISE_NODE = r"C:\Users\Me\AppData\Local\mise\installs\node\24.11.0\node.exe"
_MISE_CODEX = r"C:\Users\Me\AppData\Local\mise\installs\npm-openai-codex\0.1.0"
_NPX_CODEX = r"C:\Users\Me\AppData\Local\npm-cache\_npx\9f2c\node_modules\@openai\codex"
_NATIVE_IN_PACKAGE = r"node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\codex\codex.exe"
# Windowsのmiseが`mise ls --json`で返す、導入済みの版の要素。
_MISE_LISTING = json.dumps([{"version": "0.1.0", "install_path": _MISE_CODEX, "installed": True, "active": True}])


def _install_npm_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Callable[[list[str]], subprocess.CompletedProcess[str] | None]]:
    """PATH上のnpmがグローバルに導入したCodexを配置し、パッケージと、npmの導入先を答える代用関数を返す。"""
    prefix = tmp_path / "npm"
    package = prefix / "node_modules" / "@openai" / "codex"
    entrypoint = package / "bin" / "codex.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("", encoding="utf-8")
    (package / "package.json").write_text('{"bin":{"codex":"bin/codex.js"}}', encoding="utf-8")
    for suffix, content in npm_windows_shims("node_modules/@openai/codex/bin/codex.js").items():
        (prefix / f"codex{suffix}").write_text(content, encoding="utf-8", newline="")
    (prefix / "npm.cmd").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(prefix))
    answers = {"prefix": prefix, "root": prefix / "node_modules"}

    def answer(command: list[str]) -> subprocess.CompletedProcess[str] | None:
        if command[2:3] != ["--global"] or command[1] not in answers:
            return None
        return subprocess.CompletedProcess(command, 0, f"{answers[command[1]]}\n", "")

    return package, answer


def _prepare_windows_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> tuple[list[_Call], Callable[..., subprocess.CompletedProcess[str] | None]]:
    """npm版とmise版のCodexが残るWindowsで、場面の名前のプロセスが動く状態を作る。

    呼んだコマンドの記録と、導入とnpm・miseの照会に成功する`run_subprocess`の代用関数を返す。
    """
    _isolate(monkeypatch, tmp_path, "win32")
    monkeypatch.setattr(setup_cli_common, "find_windows_cli_processes", _FIND_WINDOWS_CLI_PROCESSES)
    monkeypatch.setattr(prune_codex_packages, "run", post_apply_outcome.PostApplyOutcome)
    monkeypatch.setattr(setup_codex_cli, "restore_native_launcher", lambda *args, **kwargs: None)
    npm_package, answer_npm = _install_npm_codex(monkeypatch, tmp_path)
    standalone = codex_runtime.standalone_root() / "releases" / "0.170.0-x86_64-pc-windows-msvc"
    processes = {
        # 2026-11-19のsthenoで、Codexの会話が無いときに稼働していた管理daemonと更新ループ。
        "daemon-and-update-loop": [
            windows_process(_DAEMON_EXE, "app-server", "daemon", "pid-update-loop"),
            windows_process(_DAEMON_EXE, "app-server", "--listen", "unix://", "--managed-daemon"),
        ],
        "daemon-cmdline-denied": [windows_process(_DAEMON_EXE, "app-server", "--managed-daemon", denied=["cmdline"])],
        "desktop-app": [
            windows_process(rf"{_DESKTOP_APP}\Codex.exe"),
            windows_process(rf"{_DESKTOP_APP}\resources\codex.exe", "app-server"),
        ],
        # 移行はnpxのキャッシュを削除しない。
        "npx-session": [
            windows_process(_NODE, rf"{_NPX_CODEX}\bin\codex.js"),
            windows_process(rf"{_NPX_CODEX}\{_NATIVE_IN_PACKAGE}"),
        ],
        # Codexと無関係なnodeの常駐サービスで、権限が無く起動したスクリプトを読めない。
        "node-cmdline-denied": [windows_process(_NODE, r"C:\Services\worker.js", denied=["cmdline"])],
        "standalone-session": [windows_process(str(standalone / "bin" / "codex.exe"))],
        # 会話が起動した同梱の実行体で、実行ファイルだけを読めない。
        "standalone-helper-exe-denied": [windows_process(str(standalone / "codex-path" / "rg.exe"), denied=["exe"])],
        "npm-session": [
            windows_process(_NODE, str(npm_package / "bin" / "codex.js")),
            windows_process(str(npm_package / _NATIVE_IN_PACKAGE.replace("\\", "/"))),
        ],
        "mise-session": [
            windows_process(_MISE_NODE, rf"{_MISE_CODEX}\node_modules\@openai\codex\bin\codex.js"),
            windows_process(rf"{_MISE_CODEX}\node_modules\@openai\codex\{_NATIVE_IN_PACKAGE}"),
        ],
        "mise-session-exe-denied": [
            windows_process(_MISE_NODE, rf"{_MISE_CODEX}\node_modules\@openai\codex\bin\codex.js", denied=["exe"])
        ],
    }[scenario]
    patch_windows_processes(monkeypatch, processes)
    calls: list[_Call] = []
    launcher = codex_runtime.standalone_root() / "current" / "bin" / "codex.exe"
    fake_run = _make_fake_run(calls, launcher=launcher, mise_list=_MISE_LISTING)
    return calls, lambda command, **kwargs: answer_npm(command) or fake_run(command, **kwargs)


def _uninstalled(calls: list[_Call]) -> set[str]:
    """呼んだ`uninstall`を、npm版とmise版の別で返す。"""
    return {"mise" if "--all" in command else "npm" for command, _ in calls if command[1:2] == ["uninstall"]}


@pytest.mark.parametrize(
    ("scenario", "installed", "uninstalled"),
    [
        ("daemon-and-update-loop", True, {"mise"}),
        ("daemon-cmdline-denied", True, {"mise"}),
        ("desktop-app", True, {"mise"}),
        ("npx-session", True, {"mise"}),
        ("standalone-session", False, set()),
        ("standalone-helper-exe-denied", False, set()),
        ("npm-session", True, {"mise"}),
        ("mise-session", True, set()),
        ("mise-session-exe-denied", True, set()),
        ("node-cmdline-denied", True, set()),
    ],
)
def test_windows_defers_install_and_migration_separately(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, scenario: str, installed: bool, uninstalled: set[str]
) -> None:
    """Windowsでは、スタンドアローン版から動くCodexがある間だけ導入を延期し、mise版から動く可能性がある間はmise版の移行だけを延期する。"""
    calls, run = _prepare_windows_run(monkeypatch, tmp_path, scenario)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", run)
    requests: list[httpx.Request] = []

    with caplog.at_level(logging.INFO), _make_client(requests) as client:
        setup_codex_cli.run(client)

    assert bool(requests) is installed
    assert ("導入と移行を次回へ延期" in caplog.text) is not installed
    assert _uninstalled(calls) == uninstalled
    assert ("旧npm版を保持" in caplog.text) is installed
    assert ("mise版の移行を次回へ延期" in caplog.text) is (installed and "mise" not in uninstalled)


@pytest.mark.parametrize(
    ("scenario", "deferred", "confirmed"),
    [
        # 検証4の再現。無関係なnodeが常駐し、権限が無く起動引数を読めない。
        ("node-cmdline-denied", {"mise版": _NODE}, False),
        ("mise-session", {"mise版": rf"{_MISE_CODEX}\node_modules\@openai\codex\bin\codex.js"}, True),
    ],
)
def test_windows_migration_deferral_shows_evidence_and_how_to_finish(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    deferred: dict[str, str],
    confirmed: bool,
) -> None:
    """旧版の移行を延期したら、判定に使ったPIDとパスと、旧版の実行を確かめたのか否定できないだけなのかを示す。

    旧版がPATHで先に見つかっても更新済みの版を使えるよう、その起動先のフルパスと、
    推奨するPowerShell 7でそのまま実行できる、旧版を削除するコマンドも示す。
    """
    _, run = _prepare_windows_run(monkeypatch, tmp_path, scenario)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", run)

    with caplog.at_level(logging.INFO), _make_client() as client:
        setup_codex_cli.run(client)

    removal = {
        # `_isolate`がPATH上に置くmiseは、実行中のOSの絶対パスの表記で示される。
        "mise版": (Path("/usr/bin/mise").absolute(), "uninstall --all --yes npm:@openai/codex"),
    }
    deferrals = {
        target: [message for message in caplog.messages if f"{target}の移行を次回へ延期" in message] for target in removal
    }
    assert {target for target, messages in deferrals.items() if messages} == set(deferred)
    for target, shown in deferred.items():
        [message] = deferrals[target]
        # 一時ディレクトリ配下のパスは、実行中のOSの区切りで表示される。
        assert f"PID 0（{Path(shown.format(tmp=tmp_path))}）" in message
        assert (f"{target}を実行中" in message) is confirmed
        assert ("起動引数を読めず" in message and f"{target}の実行を否定できない" in message) is not confirmed
        executable, arguments = removal[target]
        assert f"PowerShellで`& '{executable}' {arguments}`を実行して{target}を削除する" in message
        official = tmp_path / ".codex/packages/standalone/releases/test-version/bin/codex.exe"
        assert f"`{official}`で更新済みの版を起動する" in message


def test_windows_install_deferral_shows_running_process(monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """導入を延期したら、延期の原因になったCodexのPIDと実行ファイルのパスを示す。"""
    _, run = _prepare_windows_run(monkeypatch, tmp_path, "standalone-session")
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", run)

    with caplog.at_level(logging.INFO), _make_client() as client:
        setup_codex_cli.run(client)

    session = codex_runtime.standalone_root() / "releases" / "0.170.0-x86_64-pc-windows-msvc" / "bin" / "codex.exe"
    assert f"導入と移行を次回へ延期: PID 0（{session}）でCodexを実行中" in caplog.text


@pytest.mark.parametrize("failing_query", ["mise", "npm"])
@pytest.mark.parametrize("scenario", ["daemon-and-update-loop", "node-cmdline-denied", "npx-session"])
def test_windows_installs_and_skips_migration_when_files_to_remove_cannot_be_listed(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, failing_query: str, scenario: str
) -> None:
    """旧版の削除先を照会できない場合も導入は進め、その旧版の移行だけを見送り、失敗した照会のコマンドと診断を示す。

    無関係なnodeの起動したスクリプトを読めなくても、毎回の導入は延期しない。
    """
    calls, succeed = _prepare_windows_run(monkeypatch, tmp_path, scenario)
    # 壊れたnpmと、信頼されていない設定を読むmiseが返す失敗。
    diagnostic = {
        "npm": "Error: Cannot find module 'C:\\npm\\node_modules\\npm\\bin\\npm-cli.js' code: 'MODULE_NOT_FOUND'",
        "mise": "mise ERROR Config files in ~/work/mise.toml are not trusted.",
    }[failing_query]
    query = {"mise": ["ls", "--json", "npm:@openai/codex"], "npm": ["root", "--global"]}[failing_query]

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str] | None:
        if command[1:] == query:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 1, "", diagnostic)
        return succeed(command, **kwargs)

    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", run)
    requests: list[httpx.Request] = []

    with caplog.at_level(logging.INFO), _make_client(requests) as client:
        outcomes = [setup_codex_cli.run(client) for _ in range(2)]

    assert len(requests) == 2
    assert "導入と移行を次回へ延期" not in caplog.text
    assert failing_query not in _uninstalled(calls)
    failed = next(command for command, _ in calls if command[1:] == query)
    reported = caplog.text if failing_query == "npm" else outcomes[-1].failure or ""
    assert ("旧npm版を確認できない" if failing_query == "npm" else "移行を見送り") in reported
    if failing_query == "npm":
        assert setup_cli_common.format_command([failed[0], *query]) in reported
    else:
        assert f'`"{failed[0]}" {" ".join(query)}`' in reported
    assert diagnostic in reported


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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

    assert not any(command[1] in {"uninstall", "reshim"} for command, _ in calls[2:])


def test_run_skips_mise_removal_when_listed_version_is_not_installed(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup_codex_cli.common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_all="{}"),
    )

    with _make_client() as client, caplog.at_level(logging.WARNING, logger="pytools._internal.setup_codex_cli"):
        assert setup_codex_cli.run(client).changed

    assert shim.is_file()
    assert any(f"mise管理外の中継を保持: {shim}" in message for message in caplog.messages)


@pytest.mark.parametrize("failing", ["mise_list", "mise_json", "mise_uninstall", "reshim"])
def test_run_propagates_mise_failures(monkeypatch, tmp_path: Path, failing: str) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        setup_codex_cli.common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_list=_INSTALLED_LISTING, failing=failing),
    )

    with _make_client() as client:
        assert setup_codex_cli.run(client).failure is not None


def test_run_skips_mise_removal_when_mise_is_absent(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "linux")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name, **kwargs: None)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex"
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
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
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "warn_about_npm_launchers", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run([]))
    monkeypatch.setattr(prune_codex_packages.codex_processes, "codex_processes", lambda: ())

    def fail_remove(path, **kwargs):
        del path, kwargs
        raise PermissionError("denied")

    monkeypatch.setattr(prune_codex_packages.shutil, "rmtree", fail_remove)
    with _make_client() as client:
        assert setup_codex_cli.run(client).failure is not None


@pytest.mark.windows
def test_dangling_wrapper_is_replaced_and_tracks_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """古いリンク先が配布元から消えても、currentへ到達するリンクへ戻せる。"""
    _isolate(monkeypatch, tmp_path, "linux")
    visible = tmp_path / ".local/bin"
    visible.mkdir(parents=True)
    root = tmp_path / "dotfiles"
    monkeypatch.setattr(setup_codex_cli.common, "find_dotfiles_root", lambda: root)
    (root / "bin/codex").unlink()
    target = visible / "codex"
    target.symlink_to(root / "bin/codex")
    assert not target.exists()
    home = tmp_path / ".codex"
    selected = _release(home, "standalone", "0.166.0")
    current = selected.parent.parent / "current"
    current.symlink_to(selected, target_is_directory=True)
    native = current / "bin/codex"
    reference = visible / ".dotfiles-codex-native"
    reference.symlink_to(native)

    setup_codex_cli.restore_native_launcher(native, windows=False)
    setup_codex_cli.restore_native_launcher(native, windows=False)
    assert not reference.is_symlink()
    assert target.samefile(native)
    newer = _release(home, "standalone", "0.167.0")
    current.unlink()
    current.symlink_to(newer, target_is_directory=True)
    assert target.samefile(newer / "bin/codex")


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
