# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
"""_managed_tempの管理対象一時ディレクトリ境界を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime
import json
import os
import pathlib
import stat
import subprocess
import sys
import typing

import pytest

from agent_toolkit._atk import managed_temp as subject

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


from agent_toolkit._atk.managed_temp.test_support_test import *  # noqa: F403


def _make_junction(link: pathlib.Path, target: pathlib.Path) -> None:
    """Windowsのdirectory junctionを作成する。"""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.fixture(params=["native", "python312"])
def windows_chmod(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Readonly解除を実環境の`os.chmod`と、WindowsのPython 3.12相当の代役の両方で確かめる。

    代役はCPython 3.12のWindows版と同じく`follow_symlinks=False`の指定を`NotImplementedError`で拒否する。
    """
    if request.param == "native":
        return
    original_chmod = os.chmod

    def chmod_without_follow_symlinks(path: typing.Any, mode: int, *, follow_symlinks: bool = True) -> None:
        if not follow_symlinks:
            raise NotImplementedError("chmod: follow_symlinks unavailable on this platform")
        original_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", chmod_without_follow_symlinks)


def test_windows_ctypes_structures_match_sdk_layout() -> None:
    """Windows APIへ渡す固定幅structureのsizeとSID offsetを確認する。"""
    assert ctypes.sizeof(subject._AceHeader) == 4
    assert ctypes.sizeof(subject._AccessAllowedAce) == 12
    assert subject._AccessAllowedAce.sid_start.offset == 8
    assert ctypes.sizeof(subject._Acl) == 8
    assert ctypes.sizeof(subject._AclSizeInformation) == 12
    assert ctypes.sizeof(subject._ByHandleFileInformation) == 52


@pytest.mark.skipif(os.name != "posix", reason="POSIX固有の所有者・権限検証")
def test_force_remove_cleans_target_after_permission_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """通常の権限検証が失敗した所有対象を明示指定で強制回収する。"""
    monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
    target = subject.create_managed_temp("force-remove")
    registry = subject._registry_path(target)
    record = json.loads(registry.read_text(encoding="utf-8"))
    consuming = registry.with_name(f"{registry.name}.consuming-{record['nonce']}")
    consuming.write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o755)

    with pytest.raises(subject.ManagedTempError):
        subject.cleanup_managed_temp(target)
    assert target.exists()
    assert registry.exists()

    subject.cleanup_managed_temp(target, force_remove=True)

    assert not target.exists()
    assert not registry.exists()
    assert not consuming.exists()
    assert "--force-remove" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix", reason="POSIX固有の所有者・権限検証")
def test_force_remove_rejects_target_owned_by_another_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """実行中のOSアカウントが所有しない対象は強制回収しない。"""
    monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
    target = subject.create_managed_temp("force-owner")
    registry = subject._registry_path(target)
    validated_root = subject._validate_root(target.parent)
    monkeypatch.setattr(subject, "_validate_root", lambda _root: validated_root)
    monkeypatch.setattr(subject.os, "geteuid", lambda: target.stat().st_uid + 1)

    with pytest.raises(subject.ManagedTempError):
        subject.cleanup_managed_temp(target, force_remove=True)

    assert target.exists()
    assert registry.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX固有の所有者・権限検証")
def test_force_remove_rejects_target_below_an_unsafe_root(
    tmp_path: pathlib.Path,
) -> None:
    """親ディレクトリを一時rootとして受理できない対象は強制回収しない。"""
    unsafe_root = tmp_path / "unsafe-root"
    unsafe_root.mkdir(mode=0o777)
    unsafe_root.chmod(0o777)
    target = unsafe_root / "nested"
    target.mkdir(mode=0o700)

    with pytest.raises(subject.ManagedTempError):
        subject.cleanup_managed_temp(target, force_remove=True)

    assert target.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX固有のsymlink検証")
@pytest.mark.parametrize("replacement", ["symlink", "file"])
def test_force_remove_preserves_non_directory_replacement_and_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    replacement: str,
) -> None:
    """対象がsymlinkまたは通常ファイルへ置換された場合は実体と登録を保持する。"""
    monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
    target = subject.create_managed_temp(f"force-{replacement}")
    registry = subject._registry_path(target)
    registry_body = registry.read_text(encoding="utf-8")
    displaced = target.with_name(f"{target.name}-original")
    target.rename(displaced)
    outside = tmp_path / f"outside-{replacement}"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    if replacement == "symlink":
        target.symlink_to(outside, target_is_directory=True)
    else:
        target.write_text("keep", encoding="utf-8")

    with pytest.raises(subject.ManagedTempError):
        subject.cleanup_managed_temp(target, force_remove=True)

    assert os.path.lexists(target)
    if replacement == "symlink":
        assert target.is_symlink()
        assert target.resolve() == outside
    else:
        assert target.read_text(encoding="utf-8") == "keep"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert registry.read_text(encoding="utf-8") == registry_body


@pytest.mark.parametrize("directory", [False, True])
def test_secure_path_fails_closed_when_minimal_handle_owner_differs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    directory: bool,
) -> None:
    """`WRITE_OWNER`無しの採用ハンドルでは所有者相違時に`DACL`も変更しない。"""
    calls = _install_windows_security_doubles(
        monkeypatch,
        directory=directory,
        existing_owner=b"administrator-owner",
        full_open_error=subject._WINDOWS_ERROR_ACCESS_DENIED,
    )

    with pytest.raises(subject.ManagedTempError, match="所有者を変更できるハンドル"):
        subject._windows_secure_path(tmp_path / "target", directory=directory)

    full_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC | subject._WINDOWS_WRITE_OWNER
    minimal_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC
    assert calls.opens == [full_access, minimal_access]
    assert calls.security_reads == [202]
    assert not calls.updates


@pytest.mark.skipif(os.name != "nt", reason="Windows固有のSID・ACL・reparse検証")
class TestManagedTempWindows:
    """WindowsのSID・ACL・reparse point・cleanupを実環境で確認する。"""

    def test_normal_identity_rejects_a_reparse_point(self, tmp_path: pathlib.Path) -> None:
        """通常のidentity取得はreparse pointを受理しない。"""
        destination = tmp_path / "normal-identity-destination"
        destination.mkdir()
        junction = tmp_path / "normal-identity-junction"
        _make_junction(junction, destination)

        with pytest.raises(subject.ManagedTempError, match="reparse point"):
            subject._windows_identity(junction)

    def test_reparse_identity_identifies_the_link_object(self, tmp_path: pathlib.Path) -> None:
        """専用の処理はリンクを辿らず、reparse point自体を識別する。"""
        destination = tmp_path / "reparse-identity-destination"
        destination.mkdir()
        first = tmp_path / "first-reparse-identity-junction"
        second = tmp_path / "second-reparse-identity-junction"
        _make_junction(first, destination)
        _make_junction(second, destination)

        first_identity = subject._windows_reparse_identity(first)

        assert first_identity == subject._windows_reparse_identity(first)
        assert first_identity != subject._windows_reparse_identity(second)
        assert first_identity != subject._windows_identity(destination)

    @pytest.mark.parametrize("directory", [False, True])
    def test_cleanup_accepts_a_symbolic_link_within_the_managed_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        directory: bool,
    ) -> None:
        """managed-tempのroot内を指すfile・directory symlinkはリンク先を保持して回収する。"""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        target = subject.create_managed_temp("windows-symlink")
        destination = target.parent / "symlink-destination"
        if directory:
            destination.mkdir()
        else:
            destination.write_text("keep", encoding="utf-8")
        link = target / "link"
        link.symlink_to(destination, target_is_directory=directory)
        registry = subject._registry_path(target)

        with pytest.raises(subject.ManagedTempError, match="reparse point"):
            subject._windows_identity(link)
        identity = subject._windows_reparse_identity(link)
        assert identity == subject._windows_reparse_identity(link)
        assert identity != subject._windows_identity(destination)

        subject.cleanup_managed_temp(target)

        assert not target.exists()
        assert not registry.exists()
        assert destination.exists()
        if not directory:
            assert destination.read_text(encoding="utf-8") == "keep"

    def test_cleanup_restores_registry_from_marker_only_when_requested(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Windowsでも明示指定時だけマーカーから登録を復元する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-registry-recovery")
        registry = subject._registry_path(target)
        registry.unlink()

        with pytest.raises(subject.ManagedTempError):
            subject.cleanup_managed_temp(target)
        assert target.exists()
        subject.cleanup_managed_temp(target, recover_registry=True)
        assert not target.exists()
        assert not registry.exists()

    def test_cleanup_resumes_after_an_interrupted_consume(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Windowsでも消費途中状態から通常の後始末を再開する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-resume-consume")
        consuming, _ = _interrupt_cleanup(target)

        subject.cleanup_managed_temp(target)
        assert not target.exists()
        assert not subject._registry_path(target).exists()
        assert not consuming.exists()

    @pytest.mark.usefixtures("windows_chmod")
    def test_cleanup_removes_readonly_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通常ファイルを変えずに、Readonlyを含む管理対象を公開CLIから回収する。"""
        target = subject.create_managed_temp("windows-readonly")
        nested = target / "nested"
        nested.mkdir()
        normal = nested / "normal.txt"
        readonly = nested / "readonly.txt"
        normal.write_text("normal", encoding="utf-8")
        readonly.write_text("readonly", encoding="utf-8")
        readonly.chmod(stat.S_IREAD)
        registry = subject._registry_path(target)
        changed: list[str] = []
        original_chmod = os.chmod

        def record_chmod(path: typing.Any, mode: int, **kwargs: typing.Any) -> None:
            changed.append(pathlib.Path(path).name)
            original_chmod(path, mode, **kwargs)

        monkeypatch.setattr(os, "chmod", record_chmod)

        assert subject.main(["cleanup", "--path", str(target)]) == 0

        assert not target.exists()
        assert not registry.exists()
        assert changed == ["readonly.txt"]

    @pytest.mark.usefixtures("windows_chmod")
    def test_cleanup_resumes_quarantine_with_readonly_files(self) -> None:
        """Readonlyを含む隔離途中状態も公開CLIから回収する。"""
        target = subject.create_managed_temp("windows-readonly-resume")
        readonly = target / "readonly.txt"
        readonly.write_text("readonly", encoding="utf-8")
        readonly.chmod(stat.S_IREAD)
        consuming, quarantine = _interrupt_cleanup(target, quarantine=True)

        assert subject.main(["cleanup", "--path", str(target)]) == 0

        assert not target.exists()
        assert not quarantine.exists()
        assert not subject._registry_path(target).exists()
        assert not consuming.exists()

    @pytest.mark.usefixtures("windows_chmod")
    @pytest.mark.parametrize(("mode", "winerror"), [(stat.S_IWRITE, 5), (stat.S_IREAD, 32)])
    def test_cleanup_keeps_attributes_for_other_delete_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        mode: int,
        winerror: int,
    ) -> None:
        """Readonly以外のアクセス拒否と共有違反では属性を変えず、登録と再試行案内を保つ。"""
        target = subject.create_managed_temp("windows-delete-denied")
        content = target / "keep.txt"
        content.write_text("keep", encoding="utf-8")
        content.chmod(mode)
        before_attributes = getattr(content.lstat(), "st_file_attributes", 0)
        registry = subject._registry_path(target)
        original_unlink = os.unlink

        def deny_unlink(path: typing.Any, **kwargs: typing.Any) -> None:
            if pathlib.Path(path).name == "keep.txt":
                raise PermissionError(13, "削除拒否", str(path), winerror)
            original_unlink(path, **kwargs)

        monkeypatch.setattr(os, "unlink", deny_unlink)

        assert subject.main(["cleanup", "--path", str(target)]) == 2

        assert registry.exists()
        assert content.read_text(encoding="utf-8") == "keep"
        assert getattr(content.lstat(), "st_file_attributes", 0) == before_attributes
        assert "同じcleanupを再試行" in capsys.readouterr().err

    @pytest.mark.usefixtures("windows_chmod")
    def test_cleanup_preserves_readonly_replacement_during_delete(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """削除直前に置換されたReadonlyファイルへ属性変更を適用しない。"""
        target = subject.create_managed_temp("windows-readonly-race")
        content = target / "keep.txt"
        content.write_text("original", encoding="utf-8")
        original_unlink = os.unlink

        def replace_before_unlink(path: typing.Any, **kwargs: typing.Any) -> None:
            candidate = pathlib.Path(path)
            if candidate.name != "keep.txt":
                original_unlink(path, **kwargs)
                return
            candidate.rename(candidate.with_name("original.txt"))
            candidate.write_text("replacement", encoding="utf-8")
            candidate.chmod(stat.S_IREAD)
            raise PermissionError(13, "削除拒否", str(path), 5)

        monkeypatch.setattr(os, "unlink", replace_before_unlink)

        assert subject.main(["cleanup", "--path", str(target)]) == 2

        assert content.read_text(encoding="utf-8") == "replacement"
        assert getattr(content.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY
        assert (target / "original.txt").read_text(encoding="utf-8") == "original"
        assert subject._registry_path(target).exists()
        assert "置換" in capsys.readouterr().err

    def test_cleanup_resumes_after_an_interrupted_quarantine(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Windowsでも真正な隔離途中状態の削除を再開する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-resume-quarantine")
        (target / "content.txt").write_text("remove", encoding="utf-8")
        consuming, quarantine = _interrupt_cleanup(target, quarantine=True)

        subject.cleanup_managed_temp(target)
        assert not target.exists()
        assert not quarantine.exists()
        assert not subject._registry_path(target).exists()
        assert not consuming.exists()

    @pytest.mark.parametrize("mismatch", ["identity", "junction"])
    def test_cleanup_keeps_a_quarantine_that_does_not_match_the_registry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        mismatch: str,
    ) -> None:
        """Windowsではidentity不一致とジャンクションの隔離先を削除しない。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp(f"windows-quarantine-{mismatch}")
        _, quarantine = _interrupt_cleanup(target, quarantine=True)
        displaced = quarantine.with_name(f"{quarantine.name}-original")
        quarantine.replace(displaced)
        if mismatch == "identity":
            quarantine.mkdir()
            subject._windows_secure_path(quarantine, directory=True)
            (quarantine / "keep.txt").write_text("keep", encoding="utf-8")
            preserved = quarantine
        else:
            outside = tmp_path / "outside-junction"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep", encoding="utf-8")
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(quarantine), str(outside)],
                check=True,
                capture_output=True,
                text=True,
            )
            preserved = outside

        subject.cleanup_managed_temp(target)
        assert (preserved / "keep.txt").read_text(encoding="utf-8") == "keep"
        assert not subject._registry_path(target).exists()

    def test_create_validate_and_cleanup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-roundtrip")
        (target / "nested").mkdir()
        (target / "nested" / "data.txt").write_text("remove", encoding="utf-8")
        assert subject.validate_managed_temp(target) == target
        security = subject._windows_security_descriptor(target)
        current_sid = subject._windows_sid_bytes(subject._windows_current_sid())
        assert subject._windows_equal_sids(security.owner, current_sid)
        assert security.dacl_present
        assert security.protected
        assert len(security.aces) == 1
        assert security.aces[0].sid is not None
        assert subject._windows_equal_sids(security.aces[0].sid, current_sid)
        subject.cleanup_managed_temp(target)
        assert not target.exists()

    def test_explicit_root_round_trip_uses_secured_existing_directory(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """WindowsでもACLを整えた既存root直下の領域を検証・回収する。"""
        shared_root = tmp_path / "shared-root"
        current_root = tmp_path / "current-root"
        shared_root.mkdir()
        current_root.mkdir()
        subject._windows_secure_path(shared_root, directory=True)
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(current_root))

        target = subject.create_managed_temp("windows-shared-root", root=shared_root)

        assert target.parent == shared_root
        assert subject.validate_managed_temp(target) == target
        subject.cleanup_managed_temp(target)
        assert not target.exists()

    @pytest.mark.parametrize("tamper", ["marker", "registry-name"])
    def test_list_excludes_tampered_records_and_keeps_valid_record(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        tamper: str,
    ) -> None:
        """Windowsでもlistは不正recordを除外し、登録を残して真正な領域の列挙を継続する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        valid = subject.create_managed_temp("windows-valid")
        invalid = subject.create_managed_temp("windows-invalid")
        registry = subject._registry_path(invalid)
        if tamper == "marker":
            marker = invalid / _MARKER_NAME
            marker.write_text("{}", encoding="utf-8")
        else:

            def rename_recorded_path(record: dict[str, object]) -> None:
                record["path"] = f"{invalid}-renamed"

            _replace_registry(invalid, rename_recorded_path)

        assert {entry["path"] for entry in subject.list_managed_temp()} == {str(valid)}
        assert capsys.readouterr().err == ""
        assert registry.exists()

    def test_list_removes_registry_of_a_confirmed_missing_target_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Windowsでも実体の消滅を確定した登録は警告して回収する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        valid = subject.create_managed_temp("windows-valid")
        missing = subject.create_managed_temp("windows-missing")
        registry = subject._registry_path(missing)
        (missing / _MARKER_NAME).unlink()
        missing.rmdir()

        assert {entry["path"] for entry in subject.list_managed_temp()} == {str(valid)}
        assert "実体が失われた管理対象の登録を回収した" in capsys.readouterr().err
        assert not registry.exists()

    def test_cleanup_consumes_registry_of_a_missing_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Windowsでも実体を失った管理対象のcleanupは登録の削除だけで完了する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-missing-target")
        registry = subject._registry_path(target)
        (target / _MARKER_NAME).unlink()
        target.rmdir()

        assert subject.is_missing_registered_temp(target) is True
        subject.cleanup_managed_temp(target)
        assert not registry.exists()

    def test_cleanup_of_an_existing_untrusted_target_keeps_failing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Windowsでも実体が残り検証に失敗する管理対象は登録も実体も消費せず失敗する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-untrusted-target")
        registry = subject._registry_path(target)
        (target / _MARKER_NAME).unlink()

        assert subject.is_missing_registered_temp(target) is False
        with pytest.raises(subject.ManagedTempError):
            subject.cleanup_managed_temp(target)
        assert target.exists()
        assert registry.exists()

    def test_external_writer_acl_validate_and_cleanup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """別実行主体が、実物で確認したものと同じACEを追加した後も公開検証とcleanupが成立する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-external-writer")
        current_sid = subject._windows_sid_bytes(subject._windows_current_sid())
        external_sid = subject._windows_sid_bytes("S-1-1-0")
        flags = subject._WINDOWS_OBJECT_INHERIT_ACE | subject._WINDOWS_CONTAINER_INHERIT_ACE
        aces = (
            subject._WindowsAce(
                subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                flags,
                subject._WINDOWS_FILE_ALL_ACCESS,
                current_sid,
            ),
            subject._WindowsAce(
                subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                flags,
                subject._WINDOWS_EXTERNAL_WRITER_ACCESS,
                external_sid,
            ),
        )
        subject._windows_replace_security(target, current_sid, aces, directory=True)
        (target / "external-content.txt").write_text("remove", encoding="utf-8")

        with pytest.raises(subject.ManagedTempError):
            subject._validate_windows_security(target)
        assert subject.validate_managed_temp(target) == target

        subject.cleanup_managed_temp(target)
        assert not target.exists()

    def test_cleanup_rejects_replacement_before_acl_update_without_changing_replacement(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`ACL`再保護用ハンドル取得直前の置換先へセキュリティ更新を適用しない。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-acl-race")
        displaced = tmp_path / "windows-acl-race-displaced"
        replacement = tmp_path / "windows-acl-race-replacement"
        replacement.mkdir()
        current_sid = subject._windows_sid_bytes(subject._windows_current_sid())
        external_sid = subject._windows_sid_bytes("S-1-1-0")
        flags = subject._WINDOWS_OBJECT_INHERIT_ACE | subject._WINDOWS_CONTAINER_INHERIT_ACE
        replacement_aces = (
            subject._WindowsAce(
                subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                flags,
                subject._WINDOWS_FILE_ALL_ACCESS,
                current_sid,
            ),
            subject._WindowsAce(
                subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                flags,
                subject._WINDOWS_EXTERNAL_WRITER_ACCESS,
                external_sid,
            ),
        )
        subject._windows_replace_security(replacement, current_sid, replacement_aces, directory=True)
        replacement_security = subject._windows_security_descriptor(replacement)
        original_update_handle = subject._windows_security_update_handle

        @contextlib.contextmanager
        def replace_before_security_update(
            path: pathlib.Path,
        ) -> typing.Iterator[tuple[int, subject._ByHandleFileInformation, bool]]:
            if path != target:
                with original_update_handle(path) as opened:
                    yield opened
                return
            target.rename(displaced)
            replacement.rename(target)
            with original_update_handle(path) as opened:
                yield opened

        monkeypatch.setattr(subject, "_windows_security_update_handle", replace_before_security_update)

        with pytest.raises(subject.ManagedTempError, match="ACL再保護時に置換"):
            subject.cleanup_managed_temp(target)

        assert subject._windows_security_descriptor(target) == replacement_security
        assert (displaced / _MARKER_NAME).is_file()

    @pytest.mark.parametrize(("kind", "directory"), [("file", False), ("directory", True)])
    def test_secure_path_replaces_owner_and_all_explicit_aces(
        self,
        tmp_path: pathlib.Path,
        kind: str,
        directory: bool,
    ) -> None:
        target = tmp_path / kind
        if directory:
            target.mkdir()
        else:
            target.write_text("state", encoding="utf-8")
        current_sid = subject._windows_sid_bytes(subject._windows_current_sid())
        administrators_sid = subject._windows_sid_bytes("S-1-5-32-544")
        everyone_sid = subject._windows_sid_bytes("S-1-1-0")
        flags = subject._WINDOWS_OBJECT_INHERIT_ACE | subject._WINDOWS_CONTAINER_INHERIT_ACE if directory else 0
        initial_aces = (
            subject._WindowsAce(
                subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                flags,
                subject._WINDOWS_FILE_ALL_ACCESS,
                current_sid,
            ),
            subject._WindowsAce(subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE, flags, 0x00120089, everyone_sid),
        )
        try:
            subject._windows_replace_security(target, administrators_sid, initial_aces, directory=directory)
        except subject.ManagedTempError as error:
            error_code = error.error_code if isinstance(error, subject._WindowsApiError) else None
            cannot_change_owner = "Windowsの所有者を変更できるハンドルを取得できない" in str(error)
            if error_code in (5, 1307, 1314) or cannot_change_owner:
                pytest.skip(f"別ownerを設定できるWindows tokenではない: {error}")
            raise

        initial = subject._windows_security_descriptor(target)
        assert subject._windows_equal_sids(initial.owner, administrators_sid)
        assert len(initial.aces) == 2

        subject._windows_secure_path(target, directory=directory)

        secured = subject._windows_security_descriptor(target)
        assert subject._windows_equal_sids(secured.owner, current_sid)
        assert secured.dacl_present
        assert secured.protected
        assert len(secured.aces) == 1
        assert secured.aces[0].ace_type == subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE
        assert secured.aces[0].flags == flags
        assert secured.aces[0].mask == subject._WINDOWS_FILE_ALL_ACCESS
        assert secured.aces[0].sid is not None
        assert subject._windows_equal_sids(secured.aces[0].sid, current_sid)
        subject._validate_windows_security(target)

    @pytest.mark.parametrize(("kind", "directory"), [("file", False), ("directory", True)])
    def test_secure_path_preserves_current_owner_without_write_owner(
        self,
        tmp_path: pathlib.Path,
        kind: str,
        directory: bool,
    ) -> None:
        target = tmp_path / f"current-owner-{kind}"
        if directory:
            target.mkdir()
        else:
            target.write_text("state", encoding="utf-8")
        current_sid = subject._windows_sid_bytes(subject._windows_current_sid())
        flags = subject._WINDOWS_OBJECT_INHERIT_ACE | subject._WINDOWS_CONTAINER_INHERIT_ACE if directory else 0
        restricted_aces = (
            subject._WindowsAce(
                subject._WINDOWS_ACCESS_DENIED_ACE_TYPE,
                flags,
                subject._WINDOWS_WRITE_OWNER,
                current_sid,
            ),
            subject._WindowsAce(
                subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                flags,
                subject._WINDOWS_READ_CONTROL
                | subject._WINDOWS_WRITE_DAC
                | subject._WINDOWS_READ_ATTRIBUTES
                | subject._WINDOWS_SYNCHRONIZE,
                current_sid,
            ),
        )
        subject._windows_replace_security(target, current_sid, restricted_aces, directory=directory)

        restricted = subject._windows_security_descriptor(target)
        assert subject._windows_equal_sids(restricted.owner, current_sid)
        assert restricted.aces == restricted_aces

        subject._windows_secure_path(target, directory=directory)
        subject._validate_windows_security(target)

    def test_reparse_child_is_rejected_and_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        managed_root = tmp_path / "managed-root"
        managed_root.mkdir()
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(managed_root))
        target = subject.create_managed_temp("windows-reparse")
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        junction = target / "junction"
        _make_junction(junction, outside)
        with pytest.raises(subject.ManagedTempError, match="reparse point"):
            subject.cleanup_managed_temp(target)
        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert target.exists()

    def test_cleanup_accepts_a_junction_within_the_managed_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """managed-tempのroot内を指すJunctionはリンク先を保持して回収する。"""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        target = subject.create_managed_temp("windows-junction")
        destination = target.parent / "junction-destination"
        destination.mkdir()
        sentinel = destination / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        _make_junction(target / "junction", destination)
        registry = subject._registry_path(target)

        subject.cleanup_managed_temp(target)

        assert not target.exists()
        assert not registry.exists()
        assert sentinel.read_text(encoding="utf-8") == "keep"

    def test_cleanup_accepts_nested_junctions_independent_of_enumeration_order(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """多階層のJunctionを深い順に解除し、列挙順へ依存しない。"""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        target = subject.create_managed_temp("windows-nested-junctions")
        first_destination = target.parent / "z-destination"
        second_destination = target.parent / "a-destination"
        first_destination.mkdir()
        second_destination.mkdir()
        nested = target / "nested" / "deeper"
        nested.mkdir(parents=True)
        _make_junction(target / "a-link", first_destination)
        _make_junction(nested / "z-link", second_destination)

        subject.cleanup_managed_temp(target)

        assert not target.exists()
        assert first_destination.exists()
        assert second_destination.exists()

    def test_cleanup_accepts_a_junction_whose_target_was_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """格納値だけを検証し、到達不能なリンク先へ削除を波及させない。"""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        target = subject.create_managed_temp("windows-broken-junction")
        destination = target.parent / "removed-destination"
        destination.mkdir()
        junction = target / "junction"
        _make_junction(junction, destination)
        destination.rmdir()
        outside = tmp_path / "outside-sentinel.txt"
        outside.write_text("keep", encoding="utf-8")

        subject.cleanup_managed_temp(target)

        assert not target.exists()
        assert outside.read_text(encoding="utf-8") == "keep"

    def test_cleanup_rejects_a_junction_redirected_after_snapshot(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """走査後にmanaged-tempのroot外へ向け直されたJunctionを解除しない。"""
        managed_root = tmp_path / "managed-root"
        managed_root.mkdir()
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(managed_root))
        target = subject.create_managed_temp("windows-redirected-junction")
        destination = managed_root / "accepted-destination"
        outside = tmp_path / "outside-junction-destination"
        destination.mkdir()
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        junction = target / "junction"
        _make_junction(junction, destination)
        registry = subject._registry_path(target)
        original_snapshot = subject._tree_snapshot

        def snapshot_then_redirect(root: pathlib.Path) -> typing.Any:
            snapshot = original_snapshot(root)
            if root.name.startswith(".agent-toolkit-cleanup-"):
                redirected = root / "junction"
                os.rmdir(redirected)
                _make_junction(redirected, outside)
            return snapshot

        monkeypatch.setattr(subject, "_tree_snapshot", snapshot_then_redirect)

        with pytest.raises(subject.ManagedTempError, match="reparse point"):
            subject.cleanup_managed_temp(target)

        assert target.exists()
        assert registry.exists()
        assert sentinel.read_text(encoding="utf-8") == "keep"

    def test_cleanup_resumes_a_quarantine_containing_an_accepted_junction(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """隔離済み状態からもmanaged-tempのroot内を指すJunctionを回収する。"""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        target = subject.create_managed_temp("windows-quarantine-junction")
        destination = target.parent / "quarantine-destination"
        destination.mkdir()
        _make_junction(target / "junction", destination)
        consuming, quarantine = _interrupt_cleanup(target, quarantine=True)

        subject.cleanup_managed_temp(target)

        assert not target.exists()
        assert not quarantine.exists()
        assert not subject._registry_path(target).exists()
        assert not consuming.exists()
        assert destination.exists()

    @pytest.mark.parametrize("tamper", ["wrong-mask", "deny", "multiple", "current-user-extra"])
    def test_acl_tamper_is_rejected_and_preserved(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        tamper: str,
    ) -> None:
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("windows-acl")
        current_sid = subject._windows_sid_bytes(subject._windows_current_sid())
        everyone_sid = subject._windows_sid_bytes("S-1-1-0")
        authenticated_users_sid = subject._windows_sid_bytes("S-1-5-11")
        unrelated_sid = subject._windows_sid_bytes("S-1-5-21-1-2-3-1001")
        flags = subject._WINDOWS_OBJECT_INHERIT_ACE | subject._WINDOWS_CONTAINER_INHERIT_ACE
        expected = subject._WindowsAce(
            subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
            flags,
            subject._WINDOWS_FILE_ALL_ACCESS,
            current_sid,
        )
        valid_external = subject._WindowsAce(
            subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
            flags,
            subject._WINDOWS_EXTERNAL_WRITER_ACCESS,
            everyone_sid,
        )
        altered = {
            "wrong-mask": (
                expected,
                subject._WindowsAce(subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE, flags, 0x00120089, everyone_sid),
            ),
            "deny": (
                subject._WindowsAce(
                    subject._WINDOWS_ACCESS_DENIED_ACE_TYPE,
                    flags,
                    0x00000001,
                    unrelated_sid,
                ),
                expected,
            ),
            "multiple": (
                expected,
                valid_external,
                subject._WindowsAce(
                    subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                    flags,
                    subject._WINDOWS_EXTERNAL_WRITER_ACCESS,
                    authenticated_users_sid,
                ),
            ),
            "current-user-extra": (
                expected,
                subject._WindowsAce(
                    subject._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
                    flags,
                    subject._WINDOWS_EXTERNAL_WRITER_ACCESS,
                    current_sid,
                ),
            ),
        }[tamper]
        subject._windows_replace_security(target, current_sid, altered, directory=True)
        with pytest.raises(subject.ManagedTempError):
            subject.validate_managed_temp(target)
        assert target.exists()
        subject._windows_secure_path(target, directory=True)
        subject.cleanup_managed_temp(target)

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
