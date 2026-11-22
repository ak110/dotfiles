"""公開整理CLIから、公式の選択先とプロセスの保持契約を検証する。"""

import contextlib
import json
import os
import pathlib
import types

import psutil
import pytest

from pytools import prune_codex_packages as cli
from pytools._internal import codex_processes, prune_codex_packages

pytestmark = pytest.mark.windows

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
    monkeypatch.setattr(psutil, "pids", lambda: [])
    assert cli.main(["--dry-run"]) == 0
    assert all(path.is_dir() for path in obsolete)
    assert cli.main([]) == 0
    assert all(not path.exists() for path in obsolete)
    assert all(path.is_dir() for path in [*live, *selected])
    assert "使用中 pid 10" in capsys.readouterr().out
    processes.clear()
    assert cli.main([]) == 0
    assert all(not path.exists() for path in live)
    assert all(not path.exists() for path in obsolete)
    assert all(path.is_dir() for path in selected)


@pytest.mark.parametrize(
    "condition",
    [
        "broken-selection",
        "missing-current",
        "incomplete-selection",
        "broken-migration",
        "invalid-auto-version",
        "unicode-auto-version",
        "missing-exe",
        "scan-error",
        "delete-error",
        "lock-link",
        "lock-open-error",
        "lock",
        "unrelated-unknown",
    ],
)
def test_cli_failure_and_deferral_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    condition: str,
) -> None:
    """運用不良は保持してCLIと後処理へ失敗を渡し、更新競合は正常延期する。"""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    root = tmp_path / "packages" / "standalone"
    current = _release(tmp_path, "standalone", "0.162.1")
    old = _release(tmp_path, "standalone", "0.150.0")
    (root / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    if condition == "broken-selection":
        (root / "current").unlink()
        (root / "current").symlink_to(root / "missing", target_is_directory=True)
    elif condition == "missing-current":
        (root / "current").unlink()
    elif condition == "incomplete-selection":
        (current / "bin" / "codex").unlink()
    elif condition == "broken-migration":
        (root / ".migration-current").symlink_to(root / "missing", target_is_directory=True)
    elif condition == "invalid-auto-version":
        (root / "auto-update-version").write_text("../outside", encoding="utf-8")
    elif condition == "unicode-auto-version":
        (root / "auto-update-version").write_bytes(b"\xff")
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
    elif condition == "lock-link":
        (root / "install.lock").symlink_to(root / "missing")
    elif condition == "lock-open-error":
        if os.name == "nt":

            def fail_create(*_args, **_kwargs):
                return prune_codex_packages.ctypes.c_void_p(-1).value

            kernel = types.SimpleNamespace(CreateFileW=fail_create)
            monkeypatch.setattr(prune_codex_packages.ctypes, "WinDLL", lambda *args, **kwargs: kernel)
            monkeypatch.setattr(prune_codex_packages.ctypes, "get_last_error", lambda: 5)
        else:
            original_open = pathlib.Path.open

            def fail_open(path, *args, **kwargs):
                if path == root / "install.lock":
                    raise PermissionError(13, "denied", str(path))
                return original_open(path, *args, **kwargs)

            monkeypatch.setattr(pathlib.Path, "open", fail_open)
    else:
        monkeypatch.setattr(
            codex_processes, "codex_processes", lambda: (codex_processes.CodexProcess(1, "pid 1", "session", None, False),)
        )
    deferred = condition == "lock" or (condition == "missing-exe" and os.name == "nt")
    failed = not deferred and condition != "unrelated-unknown"
    assert cli.main([]) == int(failed)
    assert current.is_dir()
    assert old.exists() == (condition != "unrelated-unknown")
    if condition != "unrelated-unknown":
        error = capsys.readouterr().err
        assert str(root) in error
        assert "prune-codex-packages" in error
        assert ("整理を延期" in error) == deferred
        if condition == "missing-exe":
            assert ("Codex実行中" if os.name == "nt" else "実行パスを取得できない") in error
    outcome = prune_codex_packages.run()
    assert bool(outcome.failure) == failed
    assert bool(outcome.next_action) == failed
    if failed:
        assert str(root) in (outcome.failure or "")
        assert "prune-codex-packages" in (outcome.next_action or "")
    if condition == "missing-exe":
        monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
        assert cli.main([]) == 0
        assert not old.exists()
        assert current.is_dir()


def test_absent_packages_succeed(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """未導入の正常な空集合はCLIと後処理の成功として返す。"""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert cli.main([]) == 0
    assert not prune_codex_packages.run().failure


def test_help_preserves_packages(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """helpは整理を起動せず終了する。"""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    old = _release(tmp_path, "standalone", "0.150.0")
    with pytest.raises(SystemExit) as exception:
        cli.main(["--help"])
    assert exception.value.code == 0
    assert old.is_dir()


@pytest.mark.parametrize(
    ("role", "deferred"),
    [(None, False), ("update-loop", False), ("managed-daemon", False), ("session", True)],
)
def test_windows_defers_only_for_codex_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    role: codex_processes.CodexRole | None,
    deferred: bool,
) -> None:
    """Windowsでは会話のCodexだけで整理を延期し、常駐する管理daemonと更新ループの版は使用中として保持する。

    WindowsのOS方針を隔離入力で検証し、実際の共有不可排他はWindows実機へ分ける。
    """
    current = _release(tmp_path, "standalone", "0.162.1")
    busy = _release(tmp_path, "standalone", "0.160.0")
    old = _release(tmp_path, "standalone", "0.150.0")
    (current.parent.parent / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(prune_codex_packages, "_install_lock", lambda *args, **kwargs: contextlib.nullcontext())
    processes = () if role is None else (codex_processes.CodexProcess(1, "codex", role, str(busy / "bin" / "codex")),)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: processes)
    result = prune_codex_packages.prune(home=tmp_path, windows=True)
    assert bool(result.deferred) is deferred
    assert old.exists() is deferred
    assert busy.exists() is (role is not None)


@pytest.mark.parametrize("package", ["standalone", "app-server-daemon"])
@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("windows", [False, True])
def test_previous_selection_in_history_is_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, package: str, running: bool, windows: bool
) -> None:
    """公式の整理と同じく、selection-historyの直前の版を実行中のプロセスの有無に関係なく残す。

    2行目の直前の版は公式のrelease_pruning.rsとinstall.shが残す版であり、選択中の版の更新の直後に必ず生じる。
    """
    current = _release(tmp_path, package, "0.170.0")
    previous = _release(tmp_path, package, "0.169.0")
    old = _release(tmp_path, package, "0.168.0")
    root = current.parent.parent
    (root / "current").symlink_to(current, target_is_directory=True)
    # 公式install.shとrelease_pruning.rsの書式（選択した版、直前の版、currentの識別子）。
    (root / "selection-history").write_text(f"{current.name}\n{previous.name}\n2049 1234 1795023560.0\n", encoding="utf-8")
    monkeypatch.setattr(prune_codex_packages, "_install_lock", lambda *args, **kwargs: contextlib.nullcontext())
    executable = str(current / "bin" / "codex")
    processes = (
        (
            codex_processes.CodexProcess(1, "codex", "update-loop", executable),
            codex_processes.CodexProcess(2, "codex app-server --managed-daemon", "managed-daemon", executable),
        )
        if running
        else ()
    )
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: processes)

    result = prune_codex_packages.prune(home=tmp_path, windows=windows)

    assert not result.failures
    assert not result.deferred
    assert result.removed == [old]
    assert current.is_dir()
    assert previous.is_dir()
    assert "selection-history" in result.retained[previous]


def test_stale_selection_history_skips_only_its_package(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """選択中の版の記録ではない古いselection-historyでは、公式の整理と同じくそのパッケージの整理を見送る。

    記録の後にcurrentが切り替わると、記録に無い実際の直前の版を特定できない。
    """
    current = _release(tmp_path, "app-server-daemon", "0.172.0")
    unrecorded_previous = _release(tmp_path, "app-server-daemon", "0.171.0")
    recorded = _release(tmp_path, "app-server-daemon", "0.170.0")
    old = _release(tmp_path, "app-server-daemon", "0.169.0")
    root = current.parent.parent
    (root / "current").symlink_to(current, target_is_directory=True)
    (root / "selection-history").write_text(f"{recorded.name}\n{old.name}\n2049 1234 1795023560.0\n", encoding="utf-8")
    standalone = _release(tmp_path, "standalone", "0.172.0")
    standalone_old = _release(tmp_path, "standalone", "0.169.0")
    (standalone.parent.parent / "current").symlink_to(standalone, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    monkeypatch.setattr(prune_codex_packages, "_install_lock", lambda *args, **kwargs: contextlib.nullcontext())

    result = prune_codex_packages.prune(home=tmp_path, windows=False)

    assert not result.failures
    assert result.removed == [standalone_old]
    assert all(path.is_dir() for path in (current, unrecorded_previous, recorded, old))


@pytest.mark.parametrize(
    "history",
    [
        "0.170.0-x86_64-unknown-linux-musl\n../outside\n",
        "../outside\n\n",
        # releases直下にある、releasesの外へのリンク。
        "0.170.0-x86_64-unknown-linux-musl\nlinked\n",
    ],
)
def test_invalid_selection_history_fails_without_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], history: str
) -> None:
    """releasesの外を指すselection-historyは不正として整理せず、CLIへ失敗を返す。"""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    current = _release(tmp_path, "standalone", "0.170.0")
    old = _release(tmp_path, "standalone", "0.150.0")
    root = current.parent.parent
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "releases" / "linked").symlink_to(outside, target_is_directory=True)
    (root / "current").symlink_to(current, target_is_directory=True)
    (root / "selection-history").write_text(history, encoding="utf-8")
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())

    assert cli.main([]) == 1
    assert old.is_dir()
    assert outside.is_dir()
    assert "selection-history" in capsys.readouterr().err
