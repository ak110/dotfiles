"""_managed_tempの管理対象一時ディレクトリ境界を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import sys
import typing

import _managed_temp as subject
import pytest

_SCRIPT = pathlib.Path(subject.__file__).resolve()
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


@pytest.fixture(autouse=True)
def isolated_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部真正性状態を各テストの専用領域へ分離する。"""
    monkeypatch.setattr(subject, "_state_root_path", lambda: tmp_path / "external-state")


def _path_state(path: pathlib.Path) -> tuple[object, ...]:
    """pathの種別、内容、所有・権限状態を比較可能な値で返す。"""
    metadata = path.lstat()
    content = path.read_bytes() if path.is_file() else None
    if os.name == "nt":
        security: object = subject._windows_security_descriptor(path)
    else:
        security = (metadata.st_uid, stat.S_IMODE(metadata.st_mode))
    return stat.S_IFMT(metadata.st_mode), content, security


def _path_sort_key(path: pathlib.Path) -> str:
    """pathの安定した並べ替えキーを返す。"""
    return str(path)


def _managed_state(target: pathlib.Path, registry: pathlib.Path) -> tuple[object, ...]:
    """対象tree、marker、外部registryの実在・内容・権限を取得する。"""
    tree = tuple(
        (str(path.relative_to(target)), _path_state(path)) for path in sorted((target, *target.rglob("*")), key=_path_sort_key)
    )
    return tree, _path_state(target / _MARKER_NAME), _path_state(registry)


@pytest.mark.skipif(os.name != "posix", reason="POSIX固有の権限・dirfd検証")
class TestManagedTempPosix:
    """POSIXの作成・検証・後始末を実ファイルで確認する。"""

    def test_create_validate_and_cleanup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))

        target = subject.create_managed_temp("plan-review-snapshot")

        assert target.parent == tmp_path
        assert stat.S_IMODE(target.stat().st_mode) == 0o700
        assert subject.validate_managed_temp(target) == target
        subject.cleanup_managed_temp(target)
        assert not target.exists()

    @pytest.mark.parametrize("prefix", ["", "UPPER", "under_score", "leading-", "-leading", "dot.name"])
    def test_create_rejects_invalid_prefix(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        prefix: str,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        with pytest.raises(subject.ManagedTempError, match="prefix"):
            subject.create_managed_temp(prefix)

    def test_validate_rejects_relative_and_non_direct_child(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        with pytest.raises(subject.ManagedTempError, match="絶対パス"):
            subject.validate_managed_temp(pathlib.Path("relative"))
        nested = tmp_path / "parent" / "child"
        nested.mkdir(parents=True)
        with pytest.raises(subject.ManagedTempError, match="直下"):
            subject.validate_managed_temp(nested)

    def test_create_rejects_non_directory_temp_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        root_file = tmp_path / "temp-root-file"
        root_file.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(root_file))
        with pytest.raises(subject.ManagedTempError, match="ディレクトリではない"):
            subject.create_managed_temp("invalid-root")

    def test_validate_rejects_symlink(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("owned")
        link = tmp_path / "owned-link"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(subject.ManagedTempError):
            subject.validate_managed_temp(link)
        assert target.exists()

    def test_validate_rejects_directory_mode_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("owned")
        target.chmod(0o755)
        with pytest.raises(subject.ManagedTempError, match="権限"):
            subject.validate_managed_temp(target)

    def test_validate_rejects_marker_mode_and_content_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        mode_target = subject.create_managed_temp("mode")
        mode_marker = mode_target / _MARKER_NAME
        mode_marker.chmod(0o644)
        with pytest.raises(subject.ManagedTempError, match="管理情報"):
            subject.validate_managed_temp(mode_target)

        content_target = subject.create_managed_temp("content")
        content_marker = content_target / _MARKER_NAME
        payload = json.loads(content_marker.read_text())
        payload["path"] = str(tmp_path / "different")
        content_marker.write_text(json.dumps(payload), encoding="utf-8")
        content_marker.chmod(0o600)
        with pytest.raises(subject.ManagedTempError, match="内容"):
            subject.validate_managed_temp(content_target)

    def test_validate_rejects_marker_symlink(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("marker-link")
        marker = target / _MARKER_NAME
        marker.unlink()
        marker.symlink_to(tmp_path / "missing-marker")
        with pytest.raises(subject.ManagedTempError, match="管理情報"):
            subject.validate_managed_temp(target)

    def test_validate_rejects_broken_marker_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("broken-marker")
        marker = target / _MARKER_NAME
        marker.write_text("{", encoding="utf-8")
        marker.chmod(0o600)
        with pytest.raises(subject.ManagedTempError, match="管理情報"):
            subject.validate_managed_temp(target)

    def test_validate_rejects_handmade_marker_without_external_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = tmp_path / "handmade"
        target.mkdir(mode=0o700)
        metadata = target.stat()
        marker = {
            "schema_version": 1,
            "path": str(target),
            "platform": os.name,
            "owner": {"kind": "uid", "id": os.geteuid()},
            "identity": [metadata.st_dev, metadata.st_ino],
            "nonce": "0" * 64,
        }
        marker_path = target / _MARKER_NAME
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        marker_path.chmod(0o600)
        with pytest.raises(subject.ManagedTempError, match="外部状態"):
            subject.validate_managed_temp(target)
        assert target.exists()

    def test_validate_rejects_external_state_mode_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("state-mode")
        registry = next((tmp_path / "external-state").glob("*.json"))
        registry.chmod(0o644)
        with pytest.raises(subject.ManagedTempError, match="外部状態"):
            subject.cleanup_managed_temp(target)
        assert target.exists()

    def test_validate_rejects_broken_external_state_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("state-json")
        nested = target / "nested"
        nested.mkdir()
        (nested / "data.txt").write_text("keep", encoding="utf-8")
        registry = next((tmp_path / "external-state").glob("*.json"))
        registry.write_text("{", encoding="utf-8")
        registry.chmod(0o600)
        before = _managed_state(target, registry)
        with pytest.raises(subject.ManagedTempError, match="外部状態"):
            subject.cleanup_managed_temp(target)
        assert _managed_state(target, registry) == before

    def test_cleanup_failure_preserves_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("owned")
        (target / _MARKER_NAME).unlink()
        with pytest.raises(subject.ManagedTempError):
            subject.cleanup_managed_temp(target)
        assert target.exists()

    def test_cleanup_without_symlink_safe_primitive_preserves_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("unsafe-platform")

        monkeypatch.setattr(subject.shutil.rmtree, "avoids_symlink_attacks", False)
        with pytest.raises(subject.ManagedTempError, match="symlink attack耐性"):
            subject.cleanup_managed_temp(target)
        assert target.exists()

    def test_cleanup_removes_nested_content_without_following_symlink(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        target = subject.create_managed_temp("nested")
        nested = target / "one" / "two"
        nested.mkdir(parents=True)
        (nested / "data.txt").write_text("remove", encoding="utf-8")
        (target / "outside-link").symlink_to(outside, target_is_directory=True)

        subject.cleanup_managed_temp(target)

        assert not target.exists()
        assert sentinel.read_text(encoding="utf-8") == "keep"

    def test_root_replacement_before_isolation_preserves_both_trees(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("root-race")
        original_sentinel = target / "original.txt"
        original_sentinel.write_text("original", encoding="utf-8")
        displaced = tmp_path / "displaced"
        replacement_sentinel = target / "replacement.txt"
        original_consume = subject._consume_registry

        def replace_root(validated: typing.Any) -> pathlib.Path:
            consuming = original_consume(validated)
            target.rename(displaced)
            target.mkdir(mode=0o700)
            replacement_sentinel.write_text("replacement", encoding="utf-8")
            return consuming

        monkeypatch.setattr(subject, "_consume_registry", replace_root)
        with pytest.raises(subject.ManagedTempError, match="置換"):
            subject.cleanup_managed_temp(target)
        assert (displaced / "original.txt").read_text(encoding="utf-8") == "original"
        assert replacement_sentinel.read_text(encoding="utf-8") == "replacement"

    @pytest.mark.parametrize("kind", ["leaf", "directory"])
    def test_child_replacement_before_isolation_preserves_both_versions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        kind: str,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("child-race")
        child = target / "child"
        displaced = target / "displaced-child"
        if kind == "directory":
            child.mkdir()
            (child / "original.txt").write_text("original", encoding="utf-8")
        else:
            child.write_text("original", encoding="utf-8")
        original_consume = subject._consume_registry

        def replace_child(validated: typing.Any) -> pathlib.Path:
            consuming = original_consume(validated)
            if kind == "directory":
                child.rename(displaced)
                child.mkdir()
                (child / "replacement.txt").write_text("replacement", encoding="utf-8")
            else:
                child.rename(displaced)
                child.write_text("replacement", encoding="utf-8")
            return consuming

        monkeypatch.setattr(subject, "_consume_registry", replace_child)
        with pytest.raises(subject.ManagedTempError, match="置換"):
            subject.cleanup_managed_temp(target)
        if kind == "directory":
            assert (displaced / "original.txt").read_text(encoding="utf-8") == "original"
            assert (child / "replacement.txt").read_text(encoding="utf-8") == "replacement"
        else:
            assert displaced.read_text(encoding="utf-8") == "original"
            assert child.read_text(encoding="utf-8") == "replacement"

    def test_create_failure_removes_only_its_new_empty_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))

        def reject(_path: pathlib.Path) -> pathlib.Path:
            raise subject.ManagedTempError("validation failed")

        monkeypatch.setattr(subject, "validate_managed_temp", reject)
        with pytest.raises(subject.ManagedTempError, match="validation failed"):
            subject.create_managed_temp("create-failure")
        assert not list(tmp_path.glob("create-failure-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows固有のSID・ACL・reparse検証")
class TestManagedTempWindows:
    """WindowsのSID・ACL・reparse point・cleanupを実環境で確認する。"""

    def test_create_validate_and_cleanup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-roundtrip")
        (target / "nested").mkdir()
        (target / "nested" / "data.txt").write_text("remove", encoding="utf-8")
        assert subject.validate_managed_temp(target) == target
        owner, dacl = subject._windows_security_descriptor(target)
        assert owner == subject._windows_current_sid()
        assert str(dacl).find(subject._windows_current_sid()) >= 0
        subject.cleanup_managed_temp(target)
        assert not target.exists()

    def test_reparse_child_is_rejected_and_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-reparse")
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        junction = target / "junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        with pytest.raises(subject.ManagedTempError, match="reparse point"):
            subject.cleanup_managed_temp(target)
        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert target.exists()

    def test_acl_tamper_is_rejected_and_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-acl")
        result = subprocess.run(
            ["icacls", str(target), "/grant", "*S-1-1-0:R"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        with pytest.raises(subject.ManagedTempError, match="ACL"):
            subject.validate_managed_temp(target)
        assert target.exists()

    def test_handmade_marker_without_registry_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = tmp_path / "handmade-windows"
        target.mkdir()
        subject._windows_secure_path(target, directory=True)
        identity = subject._windows_identity(target)
        marker = {
            "schema_version": 1,
            "path": str(target),
            "platform": os.name,
            "owner": {"kind": "sid", "id": subject._windows_current_sid()},
            "identity": list(identity),
            "nonce": "0" * 64,
        }
        subject._write_private_json(target / _MARKER_NAME, marker)
        with pytest.raises(subject.ManagedTempError, match="外部状態"):
            subject.validate_managed_temp(target)
        assert target.exists()

    def test_broken_external_state_json_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-state-json")
        nested = target / "nested"
        nested.mkdir()
        (nested / "data.txt").write_text("keep", encoding="utf-8")
        registry = next((tmp_path / "external-state").glob("*.json"))
        registry.write_text("{", encoding="utf-8")
        before = _managed_state(target, registry)
        with pytest.raises(subject.ManagedTempError, match="外部状態"):
            subject.cleanup_managed_temp(target)
        assert _managed_state(target, registry) == before

    def test_root_replacement_before_isolation_preserves_both_trees(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-race")
        (target / "original.txt").write_text("original", encoding="utf-8")
        displaced = tmp_path / "windows-displaced"
        original_consume = subject._consume_registry

        def replace_root(validated: typing.Any) -> pathlib.Path:
            consuming = original_consume(validated)
            target.rename(displaced)
            target.mkdir()
            subject._windows_secure_path(target, directory=True)
            (target / "replacement.txt").write_text("replacement", encoding="utf-8")
            return consuming

        monkeypatch.setattr(subject, "_consume_registry", replace_root)
        with pytest.raises(subject.ManagedTempError, match="置換"):
            subject.cleanup_managed_temp(target)
        assert (displaced / "original.txt").read_text(encoding="utf-8") == "original"
        assert (target / "replacement.txt").read_text(encoding="utf-8") == "replacement"

    @pytest.mark.parametrize("kind", ["leaf", "directory"])
    def test_child_replacement_before_isolation_preserves_both_versions(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        kind: str,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-child-race")
        child = target / "child"
        displaced = target / "original-child"
        if kind == "directory":
            child.mkdir()
            (child / "original.txt").write_text("original", encoding="utf-8")
        else:
            child.write_text("original", encoding="utf-8")
        original_consume = subject._consume_registry

        def replace_child(validated: typing.Any) -> pathlib.Path:
            consuming = original_consume(validated)
            child.rename(displaced)
            if kind == "directory":
                child.mkdir()
                (child / "replacement.txt").write_text("replacement", encoding="utf-8")
            else:
                child.write_text("replacement", encoding="utf-8")
            return consuming

        monkeypatch.setattr(subject, "_consume_registry", replace_child)
        with pytest.raises(subject.ManagedTempError, match="置換"):
            subject.cleanup_managed_temp(target)
        if kind == "directory":
            assert (displaced / "original.txt").read_text(encoding="utf-8") == "original"
            assert (child / "replacement.txt").read_text(encoding="utf-8") == "replacement"
        else:
            assert displaced.read_text(encoding="utf-8") == "original"
            assert child.read_text(encoding="utf-8") == "replacement"


def test_cli_round_trip_uses_exit_codes(tmp_path: pathlib.Path) -> None:
    """CLI正常系と修正可能エラーの終了コードを確認する。"""
    env = os.environ.copy()
    if os.name == "nt":
        env["TEMP"] = str(tmp_path)
        env["TMP"] = str(tmp_path)
        env["LOCALAPPDATA"] = str(tmp_path / "local-app-data")
    else:
        env["TMPDIR"] = str(tmp_path)
        env["XDG_STATE_HOME"] = str(tmp_path / "state")
    created = subprocess.run(
        [sys.executable, str(_SCRIPT), "create", "--prefix", "cli-test"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    target = pathlib.Path(created.stdout.strip())
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    rejected = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert created.returncode == 0
    assert cleaned.returncode == 0
    assert rejected.returncode == 2
    assert not target.exists()
