"""Codex公式インストーラーの入力・導入配置・設定差を検証する。"""

import contextlib
import errno
import os
import stat
import subprocess
import types
import typing
from pathlib import Path, PureWindowsPath

import httpx
import pytest

from pytools._internal import codex_runtime, prune_codex_packages, setup_codex_cli, setup_herdr_cli
from pytools._internal._test_helpers import FakeEnvironmentRegistry
from pytools._internal.setup_codex_cli_test import _INSTALLED_LISTING, _Call, _isolate, _make_client, _make_fake_run
from pytools.prune_codex_packages_test import _release


@pytest.mark.parametrize(
    "kind", ["current", "release", "outside", "outside-current", "cycle", "missing", "npm", "io", "permission"]
)
def test_windows_visible_native_survives_separate_config_home(monkeypatch, tmp_path: Path, kind: str) -> None:
    """別設定ホームでも公式可視binの所有実体だけを選び、未知の参照とI/O失敗を隠さない。"""
    _isolate(monkeypatch, tmp_path, "win32")
    root = tmp_path / "install-home/packages/standalone"
    selected = root / "releases/version"
    native = selected / "bin/codex.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native")
    current = root / "current"
    current.symlink_to(selected, target_is_directory=True)
    visible = tmp_path / "visible"
    monkeypatch.setenv("CODEX_INSTALL_DIR", str(visible))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-config-home"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "codex.exe").write_bytes(b"unmanaged")
    if kind == "outside-current":
        current.unlink()
        current.symlink_to(outside, target_is_directory=True)
    if kind == "npm":
        visible.mkdir()
        (visible / "codex.cmd").write_text("node missing.js", encoding="utf-8")
    else:
        targets = {
            "current": current / "bin",
            "release": native.parent,
            "outside": outside,
            "outside-current": current / "bin",
            "cycle": visible,
            "missing": root / "releases/missing/bin",
            "io": current / "bin",
            "permission": current / "bin",
        }
        visible.symlink_to(targets[kind], target_is_directory=True)
    original_readlink = Path.readlink
    original_resolve = Path.resolve

    def readlink(path: Path, **kwargs: object) -> Path:
        del kwargs
        if path == visible and kind in {"io", "permission"}:
            raise OSError(errno.EIO if kind == "io" else errno.EACCES, "visible readlink failed")
        return original_readlink(path)

    def resolve(path: Path, *, strict: bool = False, **kwargs: object) -> Path:
        del kwargs
        if path in {visible, current}:
            raise PermissionError("alias traversal denied")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "readlink", readlink)
    monkeypatch.setattr(Path, "resolve", resolve)
    if kind in {"io", "permission"}:
        with pytest.raises(OSError) as caught:
            codex_runtime.find_runtime_launcher(windows=True)
        assert caught.value.errno == (errno.EIO if kind == "io" else errno.EACCES)
    else:
        assert codex_runtime.find_runtime_launcher(windows=True) == (native if kind in {"current", "release"} else None)


@pytest.mark.parametrize("module", [codex_runtime, setup_herdr_cli])
@pytest.mark.parametrize(
    ("selected", "root", "owned"),
    [
        (r"\\?\C:\Home\releases\v1", r"c:\home\releases", True),
        (r"C:\Home\releases\v1", r"\\?\c:\home\releases", True),
        (r"\\?\UNC\Server\Share\releases\v1", r"\\server\share\releases", True),
        (r"\\server\share\releases\v1", r"\\?\UNC\Server\Share\releases", True),
        (r"\\?\C:\Home\releases-other\v1", r"C:\Home\releases", False),
        (r"\\?\C:\Home\releases\..\outside", r"C:\Home\releases", False),
        (r"\\?\UNC\other\share\releases\v1", r"\\server\share\releases", False),
        (r"\\?\D:\Home\releases\v1", r"C:\Home\releases", False),
    ],
)
def test_windows_owned_path_namespace_comparison(monkeypatch, module, selected: str, root: str, owned: bool) -> None:
    """公開選択・版確認で拡張パスとUNCを扱い、外向きの本体を実行しない。"""
    # Python3.15.2/Windowsのreadlinkが拡張パス、releases.resolveが通常パスを返した観測を再現する。
    # OSの字句・属性APIだけを代用し、製品の選択・境界比較とHerdrの版確認経路を通す。
    home = PureWindowsPath(root).parent
    package = home / "packages/standalone"
    current = package / "current"
    releases = package / "releases"
    native = PureWindowsPath(selected) / ("bin/codex.exe" if module is codex_runtime else "herdr.exe")

    class FixturePath(PureWindowsPath):
        def readlink(self, **kwargs: object):
            del kwargs
            if self == current:
                return FixturePath(selected)
            raise FileNotFoundError(errno.ENOENT, "fixture link absent")

        def resolve(self, *, strict: bool = False, **kwargs: object):
            del strict, kwargs
            if self == releases:
                return FixturePath(root)
            if self in {PureWindowsPath(selected), native}:
                return self
            raise FileNotFoundError(errno.ENOENT, "fixture path absent")

        def stat(self, **kwargs: object):
            del kwargs
            if self == native:
                return os.stat_result((stat.S_IFREG, 0, 0, 0, 0, 0, 0, 0, 0, 0))
            raise FileNotFoundError(errno.ENOENT, "fixture executable absent")

    monkeypatch.setattr(module, "Path", FixturePath)
    monkeypatch.setattr(module, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("HERDR_HOME", str(home))
    monkeypatch.setenv("HERDR_INSTALL_DIR", str(home / "visible"))
    if module is codex_runtime:
        assert module.find_standalone_launcher() == (native if owned else None)
    else:
        commands: list[list[str]] = []

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            del kwargs
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "herdr fixture", "")

        monkeypatch.setattr(module, "_curl_env_overrides", lambda: contextlib.nullcontext(None))
        monkeypatch.setattr(module.common, "run_subprocess", run)
        monkeypatch.setattr(module.setup_cli_common, "prepend_path", lambda path: None)
        monkeypatch.setattr(module.setup_cli_common, "run_official_installer", lambda *args, **kwargs: (None, "not installed"))
        assert module.run().changed is owned
        assert commands == ([[str(native), "update"], [str(native), "--version"]] if owned else [])


@pytest.mark.parametrize("layout", ["directory-bin", "directory-root", "link-bin"])
def test_owned_current_layout_selects_native(monkeypatch, tmp_path: Path, layout: str) -> None:
    """旧current実体と、別名参照を拒否されるcurrentリンクの両方から本体を選ぶ。"""
    _isolate(monkeypatch, tmp_path, "win32")
    root = tmp_path / ".codex/packages/standalone"
    current = root / "current"
    selected = root / "releases/version" if layout == "link-bin" else current
    executable = selected / "codex.exe" if layout == "directory-root" else selected / "bin/codex.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"native")
    original = Path.resolve
    if layout == "link-bin":
        current.symlink_to(selected, target_is_directory=True)

    def resolve(path: Path, *, strict: bool = False, **kwargs: object) -> Path:
        del kwargs
        if layout == "link-bin" and path == current:
            raise PermissionError("alias traversal denied")
        return original(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert codex_runtime.find_standalone_launcher() == executable


def test_installer_uses_common_windows_trust_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    trust = object()
    verify_calls: list[bool] = []
    options: list[dict[str, object]] = []

    def verify(*, windows: bool) -> object:
        verify_calls.append(windows)
        return trust

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_error: object) -> None:
            self.close()

        def __init__(self, **kwargs: object) -> None:
            options.append(kwargs)

        def get(self, url: str) -> httpx.Response:
            return httpx.Response(200, content=b"installer", request=httpx.Request("GET", url))

        def close(self) -> None:
            pass

    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "installer_ssl_verify", verify)
    monkeypatch.setattr(setup_codex_cli.httpx, "Client", Client)
    monkeypatch.setattr(
        setup_codex_cli.common,
        "run_subprocess",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    result = setup_codex_cli._run_installer(None)  # noqa: SLF001  # pylint: disable=protected-access

    assert result is not None and result.returncode == 0
    assert options[0]["verify"] is trust
    assert verify_calls == [True]


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
        setup_codex_cli.common,
        "run_subprocess",
        _make_fake_run(calls, launcher=launcher, mise_list=_INSTALLED_LISTING),
    )

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

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
    assert calls[4][0][1:] == ["reshim", "--force"]
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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", fake_run)

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

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
            {
                "extra_search_directories": (),
                "excluded_launchers": {tmp_path / "dotfiles" / "bin" / "codex", tmp_path / "dotfiles" / "bin" / "codex.cmd"},
            },
        )
    ]


def test_run_installs_with_preferred_powershell_on_windows(monkeypatch, tmp_path: Path) -> None:
    _isolate(monkeypatch, tmp_path, "win32")
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.exe"
    calls: list[_Call] = []
    prepended: list[Path] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", prepended.append)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

    assert calls[0][0][:5] == ["/usr/bin/pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert calls[1][0] == [str(launcher.resolve()), "--version"]
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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with expectation, _make_client(requests) as client:
        setup_codex_cli.run(client)

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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client(requests) as client:
        assert setup_codex_cli.run(client).changed

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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

    assert calls[0][0][0] == "sh"
    assert calls[1][0] == [str(launcher), "--version"]


@pytest.mark.parametrize(("platform", "name"), [("linux", "codex"), ("win32", "codex.exe")])
def test_run_accepts_legacy_launcher_layout(monkeypatch, tmp_path: Path, platform: str, name: str) -> None:
    _isolate(monkeypatch, tmp_path, platform)
    launcher = tmp_path / ".codex" / "packages" / "standalone" / "current" / name
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

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
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls, launcher=launcher))

    with _make_client() as client:
        assert setup_codex_cli.run(client).changed

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
            {
                "extra_search_directories": (),
                "excluded_launchers": {tmp_path / "dotfiles" / "bin" / "codex", tmp_path / "dotfiles" / "bin" / "codex.cmd"},
            },
        )
    ]


@pytest.mark.parametrize("configured_home", [False, True])
def test_install_success_prunes_completed_old_packages(monkeypatch, tmp_path: Path, configured_home: bool) -> None:
    """導入・起動確認後に同じCODEX_HOMEのdaemonとstandaloneの旧版を回収する。"""
    _isolate(monkeypatch, tmp_path, "linux")
    home = tmp_path / "custom" if configured_home else tmp_path / ".codex"
    if configured_home:
        monkeypatch.setenv("CODEX_HOME", str(home))
    old = []
    for package in ("standalone", "app-server-daemon"):
        current = _release(home, package, "0.162.1")
        old.append(_release(home, package, "0.150.0"))
        (current.parent.parent / "current").symlink_to(current, target_is_directory=True)
    calls: list[_Call] = []
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run(calls))
    monkeypatch.setattr(prune_codex_packages.codex_processes, "codex_processes", lambda: ())
    with _make_client() as client:
        assert setup_codex_cli.run(client).failure is None
    assert all(not path.exists() for path in old)


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("custom_install", [False, True])
@pytest.mark.windows
def test_public_install_keeps_native_package_and_selects_recovery_launcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, windows: bool, custom_install: bool
) -> None:
    """公式の可視リンクを生成し、native実体を保ったまま通常呼出を起動管理へ接続する。"""
    _isolate(monkeypatch, tmp_path, "win32" if windows else "linux")
    root = tmp_path / "dotfiles"
    home = tmp_path / ("custom-home" if custom_install else ".codex")
    monkeypatch.setenv("CODEX_HOME", str(home))
    selected = _release(home, "standalone", "0.166.0")
    (selected.parent.parent / "current").symlink_to(selected, target_is_directory=True)
    native = home / "packages" / "standalone" / "current" / "bin" / ("codex.exe" if windows else "codex")
    visible = tmp_path / "localappdata" / "Programs" / "OpenAI" / "Codex" / "bin" if windows else tmp_path / ".local" / "bin"
    if custom_install:
        visible = tmp_path / "custom-bin"
        monkeypatch.setenv("CODEX_INSTALL_DIR", str(visible))
    before, between, after = (str(tmp_path / name) for name in ("before", "between", "after"))
    registry = FakeEnvironmentRegistry(user_path=";".join([before, str(visible), between, str(root / "bin"), after]))
    if windows:
        registry.install(monkeypatch)
        monkeypatch.setenv("PATH", registry.user_path())
    base_run = _make_fake_run([], launcher=native)

    def installer(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = base_run(command, **kwargs)
        if Path(command[0]).stem in {"sh", "pwsh", "powershell"}:
            native.write_bytes(b"native-package-content")
            if not visible.exists():
                visible.parent.mkdir(parents=True, exist_ok=True)
                if windows:
                    # install.ps1のEnsure-Junctionが公開する、package binと同じ実体を再現する。
                    visible.symlink_to(native.parent, target_is_directory=True)
                else:
                    visible.mkdir()
                    (visible / "codex").symlink_to(native)
        return result

    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", installer)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda *_args, **_kwargs: None)
    with _make_client() as client:
        outcome = setup_codex_cli.run(client)
    assert outcome.failure is None
    assert native.read_bytes() == b"native-package-content"
    if windows:
        assert visible.samefile(native.parent)
        assert (visible / "codex.exe").samefile(native)
        assert not (native.parent / "codex.cmd").exists()
        entries = registry.user_path().split(";")
        assert entries.index(str(root / "bin")) < entries.index(str(visible))
        assert [entry for entry in entries if entry in {before, between, after}] == [before, between, after]
    else:
        assert (visible / "codex").samefile(root / "bin" / "codex")
        # Windowsのreadlinkは同じ参照先へ\\?\接頭辞を付けるため、ファイルの同一性を検証する。
        assert (visible / ".dotfiles-codex-native").readlink().samefile(native)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty-runtime-home"))
    assert codex_runtime.find_standalone_launcher() is None
    runtime = codex_runtime.find_runtime_launcher(windows=windows)
    assert runtime is not None and runtime.samefile(native)
    assert os.environ["CODEX_HOME"] == str(tmp_path / "empty-runtime-home")
    if windows:
        assert not (visible / ".dotfiles-codex-native").exists()


@pytest.mark.parametrize("name", ["codex", ".dotfiles-codex-native"])
def test_public_install_preserves_unmanaged_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> None:
    """管理外の通常ファイルを保持し、導入の失敗を呼出側へ返す。"""
    _isolate(monkeypatch, tmp_path, "linux")
    protected = tmp_path / ".local/bin" / name
    protected.parent.mkdir(parents=True)
    protected.write_text("unmanaged", encoding="utf-8")
    native = tmp_path / ".codex/packages/standalone/current/bin/codex"
    monkeypatch.setattr(setup_codex_cli.common, "run_subprocess", _make_fake_run([], launcher=native))
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args, **kwargs: False)
    with _make_client() as client:
        outcome = setup_codex_cli.run(client)
    assert outcome.failure is not None and str(protected) in outcome.failure
    assert protected.read_text(encoding="utf-8") == "unmanaged"
    assert not protected.is_symlink()
