import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from agent_toolkit._common import environment_usage, plugin_environment
from agent_toolkit._common import plugin_environment_cleanup as subject

pytestmark = pytest.mark.windows


def _register_released(*targets: Path) -> None:
    for target in targets:
        record = target / ".agent-toolkit-registered-roots.json"
        record.write_text(json.dumps({str(target.parent / "removed-source"): "0" * 64}), encoding="utf-8")


def test_cleanup_uses_launch_arguments_when_optional_process_fields_are_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    live, unused = [tmp_path / "environments" / name for name in ("live", "unused")]
    for path in (live, unused):
        path.mkdir(parents=True)

    def denied() -> str:
        raise subject.psutil.AccessDenied()

    process = types.SimpleNamespace(
        username=subject.psutil.Process().username,
        status=lambda: subject.psutil.STATUS_RUNNING,
        cmdline=lambda: [str(live / "bin/python")],
        exe=denied,
        environ=dict,
    )
    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [process])
    _register_released(unused)
    subject.cleanup([], environment, python_executable=Path(sys.executable))
    assert live.is_dir() and not unused.exists()


def test_cleanup_retains_required_and_live_then_collects_released_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    current, old, legacy = [tmp_path / "environments" / name for name in ("current", "old", "legacy")]
    for path in (current, old, legacy):
        path.mkdir(parents=True)
        (path / "payload").write_bytes(b"dependency")
    (current / ".agent-toolkit-sources.json").write_text(
        json.dumps({"first-root": "1.0.0", "second-root": "2.0.0"}), encoding="utf-8"
    )

    _register_released(old, legacy)

    def no_legacy_processes(**_kwargs: Any) -> tuple[list[str], bool]:
        return [], False

    monkeypatch.setattr(subject, "_process_references", no_legacy_processes)
    with environment_usage.lock(old), environment_usage.lock(old):
        results = subject.cleanup([current], environment, python_executable=Path(sys.executable))
        assert current.is_dir() and old.is_dir()
        assert not legacy.exists()
        assert next(row for row in results if row["path"] == str(current))["sources"] == {
            "first-root": "1.0.0",
            "second-root": "2.0.0",
        }
    subject.cleanup([current], environment, python_executable=Path(sys.executable))
    subject.cleanup([current], environment, python_executable=Path(sys.executable))
    assert current.is_dir() and not old.exists()


def test_cleanup_preserves_preparation_and_legacy_process_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新しい使用ロックを持たない旧版と、準備途中の環境も保持する。"""
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    preparing, legacy = [tmp_path / "environments" / name for name in ("preparing", "legacy")]
    for path in (preparing, legacy):
        path.mkdir(parents=True)

    def legacy_reference(**_kwargs: Any) -> tuple[list[str], bool]:
        return [os.path.normcase(str(legacy / "bin/python"))], False

    monkeypatch.setattr(subject, "_process_references", legacy_reference)
    with plugin_environment.prepare_lock(preparing):
        subject.cleanup([], environment, python_executable=Path(sys.executable))
    assert preparing.is_dir() and legacy.is_dir()


def test_cleanup_retains_unknown_usage_and_continues_after_delete_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """使用状態の不明は保護し、個別の削除失敗が解消したら次回に回収する。"""
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    blocked, removable = [tmp_path / "environments" / name for name in ("blocked", "removable")]
    for path in (blocked, removable):
        path.mkdir(parents=True)

    _register_released(blocked, removable)

    def unknown() -> tuple[list[str], bool]:
        return [], True

    monkeypatch.setattr(subject, "_process_references", unknown)
    subject.cleanup([], environment, python_executable=Path(sys.executable))
    assert blocked.is_dir() and removable.is_dir()

    def known() -> tuple[list[str], bool]:
        return [], False

    remove = subject.shutil.rmtree

    def deny_one(path: Path, **_kwargs: Any) -> None:
        if path == blocked:
            raise PermissionError("試験用の削除拒否")
        remove(path)

    monkeypatch.setattr(subject, "_process_references", known)
    monkeypatch.setattr(subject.shutil, "rmtree", deny_one)
    result = subject.cleanup([], environment, python_executable=Path(sys.executable))
    assert blocked.is_dir() and not removable.exists()
    assert any(row["status"] == "deferred" and "削除拒否" in str(row["reason"]) for row in result)
    monkeypatch.setattr(subject.shutil, "rmtree", remove)
    subject.cleanup([], environment, python_executable=Path(sys.executable))
    assert not blocked.exists()


def test_cleanup_skips_processes_whose_owner_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    required, live, unused = [tmp_path / "environments" / name for name in ("required", "live", "unused")]
    for path in (required, live, unused):
        path.mkdir(parents=True)

    def denied() -> str:
        raise subject.psutil.AccessDenied()

    foreign = types.SimpleNamespace(
        username=denied,
        status=denied,
        cmdline=denied,
        exe=denied,
    )
    own = types.SimpleNamespace(
        username=subject.psutil.Process().username,
        status=lambda: subject.psutil.STATUS_RUNNING,
        cmdline=lambda: [str(live / "bin/python")],
        exe=lambda: str(live / "bin/python"),
        environ=dict,
    )
    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [foreign, own, foreign])
    _register_released(unused)
    subject.cleanup([required], environment, python_executable=Path(sys.executable))
    assert required.is_dir() and live.is_dir() and not unused.exists()


def test_cleanup_retains_all_when_own_process_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じアカウントのプロセスの参照を読めない場合は、使用中かを判定できないため全環境を残す。"""
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    unused = tmp_path / "environments" / "unused"
    unused.mkdir(parents=True)

    def denied() -> str:
        raise subject.psutil.AccessDenied()

    own = types.SimpleNamespace(
        username=subject.psutil.Process().username,
        status=lambda: subject.psutil.STATUS_RUNNING,
        cmdline=denied,
        exe=denied,
    )
    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [own])
    subject.cleanup([], environment, python_executable=Path(sys.executable))
    assert unused.is_dir()


def test_cleanup_checks_registration_and_usage_at_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未知の環境と利用ロックを保ち、配布元と利用を失った登録済み環境だけを回収する。"""
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    root = tmp_path / "installed"
    lockfile = plugin_environment.dependency_lock(root)
    lockfile.parent.mkdir(parents=True)
    lockfile.write_text("# original lock", encoding="utf-8")
    executable = Path(sys.executable)
    target = plugin_environment.project_environment(root, environment, python_executable=executable)
    unknown = target.with_name("unknown")
    target.mkdir(parents=True)
    unknown.mkdir()
    with plugin_environment.prepare_lock(target):
        plugin_environment.register_source(root, target, environment, python_executable=executable)
    monkeypatch.setattr(subject, "_process_references", lambda: ([], False))
    subject.cleanup([], environment, python_executable=executable)
    assert target.is_dir()
    lockfile.write_text("# updated lock", encoding="utf-8")
    with environment_usage.lock(target):
        subject.cleanup([], environment, python_executable=executable)
    assert target.is_dir()
    subject.cleanup([], environment, python_executable=executable)
    assert not target.exists() and unknown.is_dir()


def test_cleanup_keeps_active_virtualenv_and_collects_proven_legacy_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    root = tmp_path / "old-install"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "agent-toolkit"\n', encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    executable = Path(sys.executable)
    target = plugin_environment.legacy_project_environment(root, environment, python_executable=executable)
    target.mkdir(parents=True)
    (target / ".agent-toolkit-sources.json").write_text(json.dumps({str(root): "2.0.0"}), encoding="utf-8")
    process = types.SimpleNamespace(
        username=subject.psutil.Process().username,
        status=lambda: subject.psutil.STATUS_RUNNING,
        cmdline=lambda: ["python", "-c", "pass"],
        exe=lambda: str(executable),
        environ=lambda: {"VIRTUAL_ENV": str(target)},
    )
    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [process])
    subject.cleanup([], environment, python_executable=executable)
    assert target.is_dir()
    monkeypatch.setattr(subject.psutil, "process_iter", list)
    subject.cleanup([], environment, python_executable=executable)
    assert not target.exists()
