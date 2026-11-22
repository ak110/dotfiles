# pylint: disable=protected-access  # 対象分類・削除境界の内部関数を隔離環境で検証する。

import os
import types
import typing
from pathlib import Path

import psutil
import pytest

from pytools._internal import (
    _test_helpers,
    prune_installation,
)
from pytools._internal.prune_installation_test_helpers import UV_CACHE_MAINTENANCE, maintain, prepare


def test_active_old_environment_survives_prune(tmp_path, monkeypatch):
    _, calls = prepare(tmp_path, monkeypatch, "minimal")
    environment = tmp_path / "old-environment"
    environment.mkdir()
    (environment / "python").write_text("in use")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (environment,))
    monkeypatch.setattr(prune_installation, "_active_users", lambda paths: ["agents_server (pid 123)"])
    result = prune_installation.run()
    assert result.failure is not None
    assert result.next_action is not None
    assert "update-dotfiles --prune" in result.next_action
    assert (environment / "python").read_text() == "in use"
    assert not calls


def test_active_rustup_defers_minimal_cleanup(tmp_path, monkeypatch):
    active_users = prune_installation._active_users
    _, calls = prepare(tmp_path, monkeypatch, "minimal")
    executable = Path(os.environ["CARGO_HOME"]) / "bin/rustup"
    process = types.SimpleNamespace(
        pid=123,
        username=lambda: "fixture",
        as_dict=lambda _attributes: {
            "name": "rustup",
            "exe": str(executable),
            "cmdline": [str(executable), "component", "list"],
            "username": "fixture",
            "uids": types.SimpleNamespace(real=os.getuid() if os.name != "nt" else None),
            "status": psutil.STATUS_RUNNING,
        },
    )
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: [])
    monkeypatch.setattr(psutil, "process_iter", lambda: [process])
    monkeypatch.setattr(psutil, "Process", lambda _pid=None: process)
    monkeypatch.setattr(prune_installation, "_active_users", active_users)
    result = prune_installation.run()
    assert result.failure is not None and "rustup" in result.failure
    assert not calls


@pytest.mark.parametrize("legacy_uvx", [False, True])
@pytest.mark.parametrize("late_reference", [False, True])
def test_process_scan_detects_python_from_old_shared_environment(tmp_path, monkeypatch, legacy_uvx, late_reference):
    environment = tmp_path / "environments/old"
    process = types.SimpleNamespace(
        pid=123,
        info={
            "name": "python",
            "exe": "/runtime/python",
            "cmdline": (
                [str(tmp_path / "uv-cache/bin/pyfltr"), "mcp"]
                if legacy_uvx
                else (
                    ["/runtime/python", "-X", "utf8", "/wrapper/dispatch.py", "--target", str(environment / "bin/python")]
                    if late_reference
                    else [str(environment / "bin/python"), "/plugin/agent_toolkit/agents_server_mcp.py"]
                )
            ),
            "uids": types.SimpleNamespace(real=os.getuid()) if os.name != "nt" else None,
            "username": prune_installation.psutil.Process().username(),
            "status": "running",
        },
    )
    own = prune_installation.psutil.Process()
    current = types.SimpleNamespace(pid=process.pid, as_dict=lambda _attributes: process.info)
    monkeypatch.setattr(prune_installation.psutil, "process_iter", lambda: [process])
    monkeypatch.setattr(prune_installation.psutil, "Process", lambda pid=None: own if pid is None else current)
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: ())
    assert prune_installation._active_users((environment,)) == ["python (pid 123)"]


def test_full_keeps_project_development_tools_and_old_environments(tmp_path, monkeypatch):
    root, calls = prepare(tmp_path, monkeypatch, "full")
    assert "actionlint" not in prune_installation.excluded_tools(root, minimal=False)
    result = prune_installation.run()
    assert result.failure is None
    assert all("uninstall" not in command for command in calls)
    assert UV_CACHE_MAINTENANCE in calls
    assert not [command for command in calls if command[1:2] in (["cache"], ["store"])]
    maintain(monkeypatch, tmp_path)
    assert ["/fixture/mise", "cache", "prune"] in calls
    assert ["/fixture/npm", "cache", "verify"] in calls
    assert ["/fixture/pnpm", "store", "prune"] in calls


@pytest.mark.parametrize("missing", ["exe", "cmdline", "both"])
@pytest.mark.parametrize("referenced", [False, True])
def test_minimal_cleanup_uses_available_process_references(tmp_path, monkeypatch, missing, referenced):
    """argvでexeの取得失敗を補い、argv不明の間接利用と実参照は保持する。"""
    active_users = prune_installation._active_users
    _, calls = prepare(tmp_path, monkeypatch, "minimal")
    target = tmp_path / "old-environment"
    executable = str(target / "python") if referenced else str(tmp_path / "unrelated/program")
    process = types.SimpleNamespace(
        pid=123,
        username=lambda: "fixture",
        as_dict=lambda _attributes: {
            "name": "python",
            "exe": executable if missing == "cmdline" else None,
            "cmdline": [executable] if missing == "exe" else None,
            "username": "fixture",
            "uids": types.SimpleNamespace(real=os.getuid() if os.name != "nt" else None),
            "status": psutil.STATUS_RUNNING,
        },
    )
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (target,))
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: [])
    monkeypatch.setattr(psutil, "process_iter", lambda: [process])
    monkeypatch.setattr(psutil, "Process", lambda _pid=None: process)
    monkeypatch.setattr(prune_installation, "_active_users", active_users)
    result = prune_installation.run()
    if missing != "exe" or referenced:
        assert result.failure is not None
        assert not calls
    else:
        assert result.failure is None
        assert calls


def test_invalid_runtime_home_preserves_entire_worktree(tmp_path, monkeypatch):
    root, calls = prepare(tmp_path, monkeypatch, "minimal")
    monkeypatch.setattr(prune_installation, "removal_paths", lambda: (root,))
    result = prune_installation.run()
    assert result.failure is not None
    assert "作業ツリー全体" in result.failure
    assert (root / "mise.toml").is_file()
    assert not calls


@pytest.mark.parametrize("name", ["RUSTUP_HOME", "CARGO_HOME"])
def test_shared_runtime_root_guard_applies_before_child_cleanup(tmp_path, monkeypatch, name):
    root, calls = prepare(tmp_path, monkeypatch, "minimal")
    monkeypatch.setenv(name, str(root))
    result = prune_installation.run()
    assert result.failure is not None
    assert not calls


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
def test_active_users_reads_current_generation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    environment = tmp_path / "environments/old"
    monkeypatch.setattr(prune_installation.codex_processes, "codex_processes", lambda: ())
    with _test_helpers.ResidentProcesses(tmp_path, monkeypatch) as resident:
        child = resident.start("python")

        _inject_previous_generation(monkeypatch, child.pid, name="python", exe=str(environment / "bin/python"))
        active = prune_installation._active_users((environment,))
    assert not active
