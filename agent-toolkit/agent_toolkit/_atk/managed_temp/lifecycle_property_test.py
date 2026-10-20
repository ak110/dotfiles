"""managed-tempの実体と真正性状態の変化を、実装と独立したモデルの予測と比較する。"""

# pylint: disable=protected-access,unused-import

from __future__ import annotations

import datetime
import json
import os
import pathlib
import tempfile

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import registry as managed_temp_registry
from agent_toolkit._atk.managed_temp import windows_security as managed_temp_windows_security
from agent_toolkit._testing.managed_temp_support import _interrupt_cleanup

_OPERATIONS = ("create", "write", "cleanup", "tamper-marker", "tamper-registry")


def _prepare_explicit_root(path: pathlib.Path) -> pathlib.Path:
    """明示rootを実行OSの所有者・権限契約へ適合させる。"""
    if os.name == "nt":
        managed_temp_windows_security._windows_secure_path(path, directory=True)
    else:
        path.chmod(0o700)
    return path


def _replace_nonce(path: pathlib.Path, nonce: str) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["nonce"] = nonce
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(st.sampled_from(_OPERATIONS), min_size=1, max_size=15))
def test_lifecycle_sequences_match_reference_model(operations: list[str], tmp_path: pathlib.Path) -> None:
    """作成、内容追加、真正性不一致と後始末の操作列で安全な収束を確かめる。"""
    root = _prepare_explicit_root(pathlib.Path(tempfile.mkdtemp(prefix="lifecycle-", dir=tmp_path)))
    target: pathlib.Path | None = None
    valid = False
    payload = b""
    for operation in operations:
        if operation == "create":
            if target is None:
                target = managed_temp.create_managed_temp("property", root=root)
                valid = True
                payload = b""
        elif operation == "write" and target is not None:
            payload += b"x"
            (target / "payload").write_bytes(payload)
        elif operation == "tamper-marker" and target is not None:
            _replace_nonce(target / managed_temp_registry._MARKER_NAME, "a" * 64)
            valid = False
        elif operation == "tamper-registry" and target is not None:
            _replace_nonce(managed_temp_registry._registry_path(target), "b" * 64)
            valid = False
        elif operation == "cleanup" and target is not None:
            registry = managed_temp_registry._registry_path(target)
            if valid:
                managed_temp.cleanup_managed_temp(target)
                assert not target.exists()
                assert not registry.exists()
                target = None
                payload = b""
            else:
                with pytest.raises(managed_temp.ManagedTempError):
                    managed_temp.cleanup_managed_temp(target)
                assert target.exists()
                assert registry.exists()
        if target is not None:
            assert target.is_dir()
            if payload:
                assert (target / "payload").read_bytes() == payload


def test_cleanup_in_one_namespace_preserves_other_registration(tmp_path: pathlib.Path) -> None:
    """既知事例: 別の名前空間の登録と実体は指定対象の後始末で削除しない。"""
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _prepare_explicit_root(root)
    first = managed_temp.create_managed_temp("first", root=root)
    second = managed_temp.create_managed_temp("second", root=root)
    second_registry = managed_temp_registry._registry_path(second)
    managed_temp.cleanup_managed_temp(first)
    assert second.is_dir()
    assert second_registry.is_file()
    managed_temp.cleanup_managed_temp(second)


def test_missing_marker_failure_keeps_target_and_registry_for_retry(tmp_path: pathlib.Path) -> None:
    """既知事例: marker欠落後の失敗は対象と登録を残し、非破壊として報告する。"""
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    _prepare_explicit_root(root)
    target = managed_temp.create_managed_temp("missing-marker", root=root)
    registry = managed_temp_registry._registry_path(target)
    (target / managed_temp_registry._MARKER_NAME).unlink()
    with pytest.raises(managed_temp.ManagedTempError):
        managed_temp.cleanup_managed_temp(target)
    assert target.is_dir()
    assert registry.is_file()


@settings(max_examples=16, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(quarantine=st.booleans(), payload_size=st.integers(min_value=0, max_value=8))
def test_interrupted_cleanup_converges_from_consuming_and_quarantine(
    quarantine: bool,
    payload_size: int,
    tmp_path: pathlib.Path,
) -> None:
    """登録消費直後と隔離直後の中断は、同じcleanupの再試行で残存なしへ収束する。"""
    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        root = _prepare_explicit_root(pathlib.Path(directory))
        target = managed_temp.create_managed_temp("interrupted", root=root)
        (target / "payload").write_bytes(b"x" * payload_size)
        registry = managed_temp_registry._registry_path(target)
        consuming, quarantine_path = _interrupt_cleanup(target, quarantine=quarantine)

        managed_temp.cleanup_managed_temp(target)

        assert not target.exists()
        assert not registry.exists()
        assert not consuming.exists()
        assert not quarantine_path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIXの実ファイル権限を検証する")
@example(modes=[0], route="cleanup")
@example(modes=[0], route="force")
@example(modes=[0], route="recover")
@example(modes=[0], route="sweep")
@example(modes=[0], route="unregistered")
@example(modes=[0], route="child")
@example(modes=[0], route="quarantine")
@settings(max_examples=24, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    modes=st.lists(st.sampled_from([0, 0o100, 0o300, 0o500, 0o555, 0o700]), min_size=1, max_size=5),
    route=st.sampled_from(["cleanup", "force", "recover", "sweep", "unregistered", "child", "quarantine"]),
)
def test_restricted_trees_are_reclaimed(
    modes: list[int], route: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """権限と深さを変えても、所有木の全回収経路が登録と実体を残さない。"""
    root = _prepare_explicit_root(pathlib.Path(tempfile.mkdtemp(prefix="permissions-", dir=tmp_path)))
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    target = managed_temp.create_managed_temp("restricted")
    parent = target
    if route == "child":
        parent = target / "child"
        parent.mkdir(mode=0o700)
    removed = parent
    directories = []
    for index, mode in enumerate(modes):
        child = parent / f"d{index}"
        child.mkdir()
        payload = child / "payload"
        payload.write_text("owned", encoding="utf-8")
        payload.chmod(0)
        directories.append((child, mode))
        parent = child
    old = 1_000_000_000
    for child, mode in reversed(directories):
        os.utime(child / "payload", (old, old))
        os.utime(child, (old, old))
        child.chmod(mode)
    for child in target.iterdir():
        os.utime(child, (old, old), follow_symlinks=False)
    os.utime(target, (old, old))
    if route == "child":
        removed.chmod(modes[0])
    if route == "force":
        (target / managed_temp_registry._MARKER_NAME).unlink()
        managed_temp.cleanup_managed_temp(target, force_remove=True)
    elif route == "recover":
        managed_temp_registry._registry_path(target).unlink()
        managed_temp.cleanup_managed_temp(target, recover_registry=True)
    elif route == "quarantine":
        _interrupt_cleanup(target, quarantine=True)
        managed_temp.cleanup_managed_temp(target)
    elif route in ("sweep", "unregistered"):
        if route == "unregistered":
            managed_temp_registry._registry_path(target).unlink()
        result = managed_temp.sweep_expired_managed_temp(now=datetime.datetime.now(datetime.UTC))
        assert target in result
    else:
        managed_temp.cleanup_managed_temp(removed)
    assert not removed.exists()
    if route == "child":
        assert target.exists()
        managed_temp.cleanup_managed_temp(target)
    assert not managed_temp_registry._registry_path(target).exists()
