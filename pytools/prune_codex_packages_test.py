"""公開整理CLIから、公式の選択先とプロセスの保持契約を検証する。"""

import contextlib
import json
import os
import pathlib

import psutil
import pytest

from pytools import prune_codex_packages as cli
from pytools._internal import codex_processes, prune_codex_packages

_TARGET = "x86_64-unknown-linux-musl"


def _release(home: pathlib.Path, package: str, name: str) -> pathlib.Path:
    """公式install.shのpackage配置に必要なファイルを隔離領域へ配置する。"""
    release = home / "packages" / package / "releases" / f"{name}-{_TARGET}"
    version = "0.162.1" if name.startswith("local-") else name
    for relative in (
        "codex-package.json",
        "bin/codex",
        "bin/codex-code-mode-host",
        "codex",
        "codex-path/rg",
        "codex-resources/bwrap",
    ):
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o755)
    # Codex 0.162.1の配布manifest（codex-package.json）の公開項目。
    (release / "codex-package.json").write_text(
        json.dumps({"version": version, "target": _TARGET, "entrypoint": "bin/codex"}),
        encoding="utf-8",
    )
    return release


@pytest.mark.parametrize("configured_home", [False, True])
def test_cli_retains_selected_and_live_releases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    configured_home: bool,
) -> None:
    """両packageの選択先・更新ループ・補助実行体を保ち、使用終了後は旧版を回収する。"""
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    home = tmp_path / "configured" if configured_home else tmp_path / ".codex"
    if configured_home:
        monkeypatch.setenv("CODEX_HOME", str(home))
    else:
        monkeypatch.delenv("CODEX_HOME", raising=False)
    processes = []
    obsolete = []
    live = []
    selected = []
    for package in ("standalone", "app-server-daemon"):
        root = home / "packages" / package
        current = _release(home, package, "0.162.1")
        migration = _release(home, package, "0.161.0")
        automatic = _release(home, package, "0.160.0")
        old = _release(home, package, "0.150.0")
        busy = _release(home, package, "local-abcd" if package == "app-server-daemon" else "0.149.0")
        (root / "current").symlink_to(current, target_is_directory=True)
        (root / ".migration-current").symlink_to(migration, target_is_directory=True)
        (root / "auto-update-version").write_text(automatic.name, encoding="utf-8")
        executable = busy / "bin" / ("codex-code-mode-host" if package == "standalone" else "codex")
        processes.append(codex_processes.CodexProcess(10, "codex", "update-loop", str(executable)))
        obsolete.append(old)
        live.append(busy)
        selected.extend([current, migration, automatic])
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: tuple(processes))
    assert cli.main(["--dry-run"]) == 0
    assert all(path.is_dir() for path in obsolete)
    assert cli.main([]) == 0
    if os.name == "nt":
        assert all(path.is_dir() for path in obsolete)
    else:
        assert all(not path.exists() for path in obsolete)
    assert all(path.is_dir() for path in [*live, *selected])
    output = capsys.readouterr()
    assert "実行中" in output.err if os.name == "nt" else "使用中 pid 10" in output.out
    processes.clear()
    assert cli.main([]) == 0
    assert all(not path.exists() for path in live)
    assert all(not path.exists() for path in obsolete)
    assert all(path.is_dir() for path in selected)


@pytest.mark.parametrize(
    "condition",
    ["broken-selection", "incomplete-selection", "missing-exe", "scan-error", "delete-error", "lock", "unrelated-unknown"],
)
def test_cli_failure_and_deferral_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    condition: str,
) -> None:
    """取得不能と排他競合では保持し、削除失敗だけを非0として再実行へ渡す。"""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    root = tmp_path / "packages" / "standalone"
    current = _release(tmp_path, "standalone", "0.162.1")
    old = _release(tmp_path, "standalone", "0.150.0")
    (root / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    if condition == "broken-selection":
        (root / "current").unlink()
        (root / "current").symlink_to(root / "missing", target_is_directory=True)
    elif condition == "incomplete-selection":
        (current / "bin" / "codex").unlink()
    elif condition == "missing-exe":
        monkeypatch.setattr(codex_processes, "codex_processes", lambda: (codex_processes.CodexProcess(1, "codex", "session"),))
    elif condition == "scan-error":

        def fail_scan():
            raise psutil.AccessDenied(1)

        monkeypatch.setattr(codex_processes, "codex_processes", fail_scan)
    elif condition == "delete-error":

        def fail_remove(path, **kwargs):
            del path, kwargs
            raise PermissionError("denied")

        monkeypatch.setattr(prune_codex_packages.shutil, "rmtree", fail_remove)
    elif condition == "lock":
        (root / "install.lock.d").mkdir()
    else:
        monkeypatch.setattr(
            codex_processes, "codex_processes", lambda: (codex_processes.CodexProcess(1, "pid 1", "session", None, False),)
        )
    assert cli.main([]) == (1 if condition == "delete-error" else 0)
    assert current.is_dir()
    assert old.exists() == (condition != "unrelated-unknown")
    if condition != "unrelated-unknown":
        assert "prune-codex-packages" in capsys.readouterr().err


def test_help_preserves_packages(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """helpは整理を起動せず終了する。"""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    old = _release(tmp_path, "standalone", "0.150.0")
    with pytest.raises(SystemExit) as exception:
        cli.main(["--help"])
    assert exception.value.code == 0
    assert old.is_dir()


@pytest.mark.parametrize("running", [False, True])
def test_windows_defers_running_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    running: bool,
) -> None:
    """WindowsのOS方針を隔離入力で検証し、実際の共有不可排他はWindows実機へ分ける。"""
    current = _release(tmp_path, "standalone", "0.162.1")
    old = _release(tmp_path, "standalone", "0.150.0")
    (current.parent.parent / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(prune_codex_packages, "_install_lock", lambda *args, **kwargs: contextlib.nullcontext())
    processes = (codex_processes.CodexProcess(1, "codex", "update-loop", str(current / "codex")),) if running else ()
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: processes)
    result = prune_codex_packages.prune(home=tmp_path, windows=True)
    assert bool(result.deferred) == running
    assert old.exists() == running
