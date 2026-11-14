"""更新が共有環境を回収しても、実行中・構築中の利用を壊さないことを確かめる。"""

import json
import types
from pathlib import Path
from typing import Any

import pytest

from agent_toolkit._common import environment_usage, plugin_environment
from agent_toolkit._common import plugin_environment_cleanup as subject

pytestmark = pytest.mark.windows


def test_cleanup_uses_launch_arguments_when_optional_process_fields_are_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """environの継承や読取拒否で全回収を止めず、実launcherの絶対パスを保持する。"""
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
        environ=lambda: pytest.fail("継承した環境を使用中の根拠にしない"),
    )
    monkeypatch.setattr(subject.psutil, "process_iter", lambda: [process])
    subject.cleanup([], environment)
    assert live.is_dir() and not unused.exists()


def test_cleanup_retains_required_and_live_then_collects_released_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """更新を反復し、共有する現行版と利用終了後の旧版の寿命を区別する。"""
    environment = {plugin_environment.RUNTIME_DATA_ENV: str(tmp_path)}
    current, old, legacy = [tmp_path / "environments" / name for name in ("current", "old", "legacy")]
    for path in (current, old, legacy):
        path.mkdir(parents=True)
        (path / "payload").write_bytes(b"dependency")
    (current / ".agent-toolkit-sources.json").write_text(
        json.dumps({"first-root": "1.0.0", "second-root": "2.0.0"}), encoding="utf-8"
    )

    def no_legacy_processes(**_kwargs: Any) -> tuple[list[str], bool]:
        return [], False

    monkeypatch.setattr(subject, "_process_references", no_legacy_processes)
    with environment_usage.lock(old), environment_usage.lock(old):
        results = subject.cleanup([current], environment)
        assert current.is_dir() and old.is_dir()
        assert not legacy.exists()
        assert next(row for row in results if row["path"] == str(current))["sources"] == {
            "first-root": "1.0.0",
            "second-root": "2.0.0",
        }
    subject.cleanup([current], environment)
    subject.cleanup([current], environment)
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
        return [str(legacy / "bin/python")], False

    monkeypatch.setattr(subject, "_process_references", legacy_reference)
    with plugin_environment.prepare_lock(preparing):
        subject.cleanup([], environment)
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

    def unknown() -> tuple[list[str], bool]:
        return [], True

    monkeypatch.setattr(subject, "_process_references", unknown)
    subject.cleanup([], environment)
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
    result = subject.cleanup([], environment)
    assert blocked.is_dir() and not removable.exists()
    assert any(row["status"] == "retained" and "削除拒否" in str(row["reason"]) for row in result)
    monkeypatch.setattr(subject.shutil, "rmtree", remove)
    subject.cleanup([], environment)
    assert not blocked.exists()
