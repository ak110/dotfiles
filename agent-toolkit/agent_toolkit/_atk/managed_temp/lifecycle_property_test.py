"""managed-tempの実体と真正性状態の生存期を独立モデルと比較する。"""

# pylint: disable=protected-access,unused-import

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp.test_support_test import _interrupt_cleanup, isolated_state_root  # noqa: F401

_OPERATIONS = ("create", "write", "cleanup", "tamper-marker", "tamper-registry")


def _replace_nonce(path: pathlib.Path, nonce: str) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["nonce"] = nonce
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(st.sampled_from(_OPERATIONS), min_size=1, max_size=15))
def test_lifecycle_sequences_match_reference_model(operations: list[str], tmp_path: pathlib.Path) -> None:
    """作成、内容追加、真正性不一致と後始末の操作列で安全な収束を確かめる。"""
    root = pathlib.Path(tempfile.mkdtemp(prefix="lifecycle-", dir=tmp_path))
    root.chmod(0o700)
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
            _replace_nonce(target / managed_temp._MARKER_NAME, "a" * 64)
            valid = False
        elif operation == "tamper-registry" and target is not None:
            _replace_nonce(managed_temp._registry_path(target), "b" * 64)
            valid = False
        elif operation == "cleanup" and target is not None:
            registry = managed_temp._registry_path(target)
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
    first = managed_temp.create_managed_temp("first", root=root)
    second = managed_temp.create_managed_temp("second", root=root)
    second_registry = managed_temp._registry_path(second)
    managed_temp.cleanup_managed_temp(first)
    assert second.is_dir()
    assert second_registry.is_file()
    managed_temp.cleanup_managed_temp(second)


def test_missing_marker_failure_keeps_target_and_registry_for_retry(tmp_path: pathlib.Path) -> None:
    """既知事例: marker欠落後の失敗は対象と登録を残し、非破壊として報告する。"""
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    target = managed_temp.create_managed_temp("missing-marker", root=root)
    registry = managed_temp._registry_path(target)
    (target / managed_temp._MARKER_NAME).unlink()
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
        root = pathlib.Path(directory)
        root.chmod(0o700)
        target = managed_temp.create_managed_temp("interrupted", root=root)
        (target / "payload").write_bytes(b"x" * payload_size)
        registry = managed_temp._registry_path(target)
        consuming, quarantine_path = _interrupt_cleanup(target, quarantine=quarantine)

        managed_temp.cleanup_managed_temp(target)

        assert not target.exists()
        assert not registry.exists()
        assert not consuming.exists()
        assert not quarantine_path.exists()
