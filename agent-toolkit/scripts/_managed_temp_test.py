"""_managed_tempの管理対象一時ディレクトリ境界を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import argparse
import contextlib
import ctypes
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


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("agent-work", True),
        ("a1", True),
        ("", False),
        ("UPPER", False),
        ("under_score", False),
        ("leading-", False),
        ("-leading", False),
        ("dot.name", False),
    ],
)
def test_is_valid_prefix(prefix: str, expected: bool) -> None:
    """createとPermissionRequestが共有するprefix規則を確認する。"""
    assert subject.is_valid_prefix(prefix) is expected


def test_list_managed_temp_returns_validated_jsonl_record(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`list`は登録簿ではなく真正性検証済みの領域だけを返す。"""
    monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
    target = subject.create_managed_temp("publish-group")

    created_at = subject._load_private_json(subject._registry_path(target))["created_at"]
    assert subject.list_managed_temp("publish-group") == [
        {
            "path": str(target),
            "prefix": "publish-group",
            "created_at": created_at,
        }
    ]


class _WindowsSecurityCalls(typing.NamedTuple):
    opens: list[int]
    security_reads: list[int]
    updates: list[tuple[int, int, bytes | None]]


def test_windows_ctypes_structures_match_sdk_layout() -> None:
    """Windows APIへ渡す固定幅structureのsizeとSID offsetを確認する。"""
    assert ctypes.sizeof(subject._AceHeader) == 4
    assert ctypes.sizeof(subject._AccessAllowedAce) == 12
    assert subject._AccessAllowedAce.sid_start.offset == 8
    assert ctypes.sizeof(subject._Acl) == 8
    assert ctypes.sizeof(subject._AclSizeInformation) == 12
    assert ctypes.sizeof(subject._ByHandleFileInformation) == 52


def _install_windows_security_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    directory: bool,
    existing_owner: bytes,
    full_open_error: int | None,
) -> _WindowsSecurityCalls:
    """Windows security更新のAPI境界を決定論的な記録関数へ置換する。"""
    opens: list[int] = []
    security_reads: list[int] = []
    updates: list[tuple[int, int, bytes | None]] = []
    full_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC | subject._WINDOWS_WRITE_OWNER

    @contextlib.contextmanager
    def fake_path_handle(
        path: pathlib.Path,
        access: int,
        **_kwargs: object,
    ) -> typing.Iterator[tuple[int, subject._ByHandleFileInformation]]:
        opens.append(access)
        if access == full_access and full_open_error is not None:
            raise subject._WindowsHandleOpenError("handle open failed", path, full_open_error)
        information = subject._ByHandleFileInformation()
        information.attributes = subject._WINDOWS_FILE_ATTRIBUTE_DIRECTORY if directory else 0
        handle = 202 if access == subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC else 101
        yield handle, information

    def fake_current_sid(**_kwargs: object) -> str:
        return "S-1-current"

    def fake_sid_bytes(_sid_text: str, **_kwargs: object) -> bytes:
        return b"current-owner"

    def fake_acl_buffer(
        _path: pathlib.Path,
        _aces: tuple[subject._WindowsAce, ...],
        **_kwargs: object,
    ) -> object:
        return object()

    def fake_security_from_handle(
        handle: int,
        _information: subject._ByHandleFileInformation,
        _path: pathlib.Path,
        **_kwargs: object,
    ) -> subject._WindowsSecurity:
        security_reads.append(handle)
        return subject._WindowsSecurity(existing_owner, True, True, directory, ())

    def fake_equal_sids(first: bytes, second: bytes, **_kwargs: object) -> bool:
        return first == second

    def fake_set_security(
        handle: int,
        _path: pathlib.Path,
        security_information: int,
        owner_sid: bytes | None,
        _acl_buffer: object,
        **_kwargs: object,
    ) -> None:
        updates.append((handle, security_information, owner_sid))

    monkeypatch.setattr(subject, "_windows_path_handle", fake_path_handle)
    monkeypatch.setattr(subject, "_windows_current_sid", fake_current_sid)
    monkeypatch.setattr(subject, "_windows_sid_bytes", fake_sid_bytes)
    monkeypatch.setattr(subject, "_windows_acl_buffer", fake_acl_buffer)
    monkeypatch.setattr(subject, "_windows_security_from_handle", fake_security_from_handle)
    monkeypatch.setattr(subject, "_windows_equal_sids", fake_equal_sids)
    monkeypatch.setattr(subject, "_windows_set_security", fake_set_security)
    return _WindowsSecurityCalls(opens, security_reads, updates)


@pytest.mark.parametrize(
    ("directory", "existing_owner", "full_open_error", "expected_handle", "owner_changed"),
    [
        (False, b"current-owner", None, 101, False),
        (True, b"administrator-owner", None, 101, True),
        (False, b"current-owner", subject._WINDOWS_ERROR_ACCESS_DENIED, 202, False),
        (True, b"current-owner", subject._WINDOWS_ERROR_ACCESS_DENIED, 202, False),
    ],
)
def test_secure_path_uses_adopted_handle_for_owner_check_and_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    directory: bool,
    existing_owner: bytes,
    full_open_error: int | None,
    expected_handle: int,
    owner_changed: bool,
) -> None:
    """採用ハンドルを所有者判定からセキュリティ更新まで再利用する契約を確認する。"""
    calls = _install_windows_security_doubles(
        monkeypatch,
        directory=directory,
        existing_owner=existing_owner,
        full_open_error=full_open_error,
    )

    subject._windows_secure_path(tmp_path / "target", directory=directory)

    full_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC | subject._WINDOWS_WRITE_OWNER
    minimal_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC
    expected_opens = [full_access, minimal_access] if full_open_error is not None else [full_access]
    expected_information = subject._WINDOWS_DACL_SECURITY_INFORMATION | subject._WINDOWS_PROTECTED_DACL_SECURITY_INFORMATION
    expected_owner = None
    if owner_changed:
        expected_information |= subject._WINDOWS_OWNER_SECURITY_INFORMATION
        expected_owner = b"current-owner"
    assert calls.opens == expected_opens
    assert calls.security_reads == [expected_handle]
    assert calls.updates == [(expected_handle, expected_information, expected_owner)]


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


def test_secure_path_does_not_fallback_for_other_open_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """ACCESS_DENIED以外のfull access取得失敗をminimal accessへ変換しない。"""
    calls = _install_windows_security_doubles(
        monkeypatch,
        directory=False,
        existing_owner=b"current-owner",
        full_open_error=32,
    )

    with pytest.raises(subject._WindowsHandleOpenError) as captured:
        subject._windows_secure_path(tmp_path / "target", directory=False)

    full_access = subject._WINDOWS_READ_CONTROL | subject._WINDOWS_WRITE_DAC | subject._WINDOWS_WRITE_OWNER
    assert captured.value.error_code == 32
    assert calls.opens == [full_access]
    assert not calls.security_reads
    assert not calls.updates


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
    """対象ツリー、マーカーファイル、外部の登録簿の実在・内容・権限を取得する。"""
    tree = tuple(
        (str(path.relative_to(target)), _path_state(path)) for path in sorted((target, *target.rglob("*")), key=_path_sort_key)
    )
    return tree, _path_state(target / _MARKER_NAME), _path_state(registry)


def _replace_registry(target: pathlib.Path, transform: typing.Callable[[dict[str, object]], None]) -> None:
    """登録簿だけへ改変を保存し、登録ファイル名と`path`の対応を検証可能にする。"""
    registry = subject._registry_path(target)
    record = json.loads(registry.read_text(encoding="utf-8"))
    transform(record)
    registry.write_text(json.dumps(record), encoding="utf-8")
    if os.name == "posix":
        registry.chmod(0o600)


def _replace_records(target: pathlib.Path, transform: typing.Callable[[dict[str, object]], None]) -> None:
    """マーカーファイルと登録簿へ同じ改変を保存してデータ契約を検証可能にする。"""
    marker = target / _MARKER_NAME
    registry = subject._registry_path(target)
    for path in (marker, registry):
        record = json.loads(path.read_text(encoding="utf-8"))
        transform(record)
        path.write_text(json.dumps(record), encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)


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

    def test_validate_accepts_matching_v1_records(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """旧スキーマのマーカーファイルと登録簿が同じ旧フィールド集合なら互換検証する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("v1-record")

        def convert_to_v1(record: dict[str, object]) -> None:
            record["schema_version"] = 1
            del record["prefix"]
            del record["created_at"]

        _replace_records(target, convert_to_v1)

        assert subject.validate_managed_temp(target) == target

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("prefix", None),
            ("prefix", "UPPER"),
            ("prefix", 1),
            ("created_at", None),
            ("created_at", "2026-08-12T00:00:00"),
            ("created_at", "2026-08-12T00:00:00+09:00"),
            ("created_at", "2026-08-12X00:00:00+00:00"),
            ("created_at", "2026-08-12 00:00:00+00:00"),
            ("created_at", 1),
        ],
    )
    def test_validate_rejects_invalid_v2_prefix_or_created_at(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        field: str,
        value: object,
    ) -> None:
        """v2のprefixとUTC作成時刻は双方で必須かつ型・値を検証する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("invalid-v2")

        def set_invalid(record: dict[str, object]) -> None:
            if value is None:
                del record[field]
            else:
                record[field] = value

        _replace_records(target, set_invalid)

        with pytest.raises(subject.ManagedTempError, match="内容"):
            subject.validate_managed_temp(target)

    @pytest.mark.parametrize("marker_only", [True, False])
    def test_validate_rejects_version_mismatch_and_partial_v2_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        marker_only: bool,
    ) -> None:
        """version混在と片側だけのv2更新を真正性エラーとして拒否する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("record-mismatch")
        marker = target / _MARKER_NAME
        registry = subject._registry_path(target)
        changed = marker if marker_only else registry
        record = json.loads(changed.read_text(encoding="utf-8"))
        if marker_only:
            record["schema_version"] = 1
            del record["prefix"]
            del record["created_at"]
        else:
            del record["created_at"]
        changed.write_text(json.dumps(record), encoding="utf-8")
        changed.chmod(0o600)

        with pytest.raises(subject.ManagedTempError, match="内容"):
            subject.validate_managed_temp(target)

    def test_list_mixes_v1_and_v2_as_sorted_jsonl_and_skips_invalid_registry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`list`は`v1`を最古としてJSONL出力し、不正な登録簿を診断して正常項目を継続する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        v1_target = subject.create_managed_temp("legacy")
        v2_target = subject.create_managed_temp("publish-group")

        def convert_to_v1(record: dict[str, object]) -> None:
            record["schema_version"] = 1
            del record["prefix"]
            del record["created_at"]

        _replace_records(v1_target, convert_to_v1)
        invalid_registry = subject._state_root() / "invalid.json"
        invalid_registry.write_text("{}", encoding="utf-8")
        invalid_registry.chmod(0o600)

        parser = argparse.ArgumentParser()
        subject.build_parser(parser)
        assert subject.dispatch(parser.parse_args(["list"])) == 0
        lines = capsys.readouterr()
        assert [json.loads(line) for line in lines.out.splitlines()] == [
            {"created_at": None, "path": str(v1_target), "prefix": None},
            {
                "created_at": subject._load_private_json(subject._registry_path(v2_target))["created_at"],
                "path": str(v2_target),
                "prefix": "publish-group",
            },
        ]
        assert "warning: 管理対象を列挙できない" in lines.err

    def test_list_sorts_same_created_at_by_path_and_excludes_v1_prefix_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """同時刻のv2はpath順に並べ、prefix指定ではv1を返さない。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        first = subject.create_managed_temp("publish-group")
        second = subject.create_managed_temp("publish-group")
        legacy = subject.create_managed_temp("legacy")
        created_at = "2026-08-12T00:00:00+00:00"

        def set_created_at(record: dict[str, object]) -> None:
            record["created_at"] = created_at

        def convert_to_v1(record: dict[str, object]) -> None:
            record["schema_version"] = 1
            del record["prefix"]
            del record["created_at"]

        _replace_records(first, set_created_at)
        _replace_records(second, set_created_at)
        _replace_records(legacy, convert_to_v1)

        assert [entry["path"] for entry in subject.list_managed_temp("publish-group")] == sorted((str(first), str(second)))

    def test_list_returns_exit_one_for_an_empty_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """該当管理領域が無いlistは慣例どおり終了状態1で終える。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        parser = argparse.ArgumentParser()
        subject.build_parser(parser)

        assert subject.dispatch(parser.parse_args(["list"])) == 1
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("tamper", ["marker", "registry-name", "symlink", "registry-mode"])
    def test_list_excludes_untrusted_records_and_keeps_valid_records(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        tamper: str,
    ) -> None:
        """listは改変・不対応・link・権限不正を出力せず、登録を残して正常項目を継続する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        valid = subject.create_managed_temp("valid")
        invalid = subject.create_managed_temp("invalid")
        marker = invalid / _MARKER_NAME
        registry = subject._registry_path(invalid)
        if tamper == "marker":
            marker.write_text("{}", encoding="utf-8")
            marker.chmod(0o600)
        elif tamper == "registry-name":

            def rename_recorded_path(record: dict[str, object]) -> None:
                record["path"] = f"{invalid}-renamed"

            _replace_registry(invalid, rename_recorded_path)
        elif tamper == "symlink":
            marker.unlink()
            marker.symlink_to(tmp_path / "outside-marker")
        else:
            registry.chmod(0o644)

        assert subject.list_managed_temp() == [
            {
                "path": str(valid),
                "prefix": "valid",
                "created_at": subject._load_private_json(subject._registry_path(valid))["created_at"],
            }
        ]
        assert "warning: 管理対象を列挙できない" in capsys.readouterr().err
        assert registry.exists()

    def test_list_removes_registry_of_a_missing_target_without_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """実体を失った登録は警告を出力せず登録ファイルごと回収する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        valid = subject.create_managed_temp("valid")
        missing = subject.create_managed_temp("missing")
        registry = subject._registry_path(missing)
        (missing / _MARKER_NAME).unlink()
        missing.rmdir()

        assert subject.list_managed_temp() == [
            {
                "path": str(valid),
                "prefix": "valid",
                "created_at": subject._load_private_json(subject._registry_path(valid))["created_at"],
            }
        ]
        assert capsys.readouterr().err == ""
        assert not registry.exists()

    def test_list_removes_registry_of_a_missing_target_recorded_under_another_temp_root(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """記録時と列挙時で一時領域が異なる実体不在の登録も、警告を出力せずに回収する。"""
        recorded_root = tmp_path / "recorded"
        listed_root = tmp_path / "listed"
        recorded_root.mkdir()
        listed_root.mkdir()
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(recorded_root))
        missing = subject.create_managed_temp("moved-root")
        registry = subject._registry_path(missing)
        (missing / _MARKER_NAME).unlink()
        missing.rmdir()
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(listed_root))

        # 一時領域直下を要求する厳格判定は、cleanupと権限判定のために従来どおり維持する。
        assert subject.is_missing_registered_temp(missing) is False
        assert subject.list_managed_temp() == []
        assert capsys.readouterr().err == ""
        assert not registry.exists()

    def test_cleanup_consumes_registry_of_a_missing_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """実体を失った管理対象のcleanupは、真正性検証を経ず登録の削除だけで完了する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("missing-target")
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
        """実体が残り真正性検証に失敗する管理対象は、登録も実体も消費せず失敗する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        target = subject.create_managed_temp("untrusted-target")
        registry = subject._registry_path(target)
        (target / _MARKER_NAME).unlink()

        assert subject.is_missing_registered_temp(target) is False
        with pytest.raises(subject.ManagedTempError):
            subject.cleanup_managed_temp(target)
        assert target.exists()
        assert registry.exists()

    def test_cleanup_of_a_missing_target_without_registry_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """登録簿に一致する記録を持たない不在パスは消滅と判定せず失敗する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        unregistered = tmp_path / "unregistered-target"

        assert subject.is_missing_registered_temp(unregistered) is False
        with pytest.raises(subject.ManagedTempError):
            subject.cleanup_managed_temp(unregistered)

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
        assert "warning: 管理対象を列挙できない" in capsys.readouterr().err
        assert registry.exists()

    def test_list_removes_registry_of_a_missing_target_without_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Windowsでも実体を失った登録は警告を出力せず登録ファイルごと回収する。"""
        monkeypatch.setattr(subject.tempfile, "gettempdir", lambda: str(tmp_path))
        valid = subject.create_managed_temp("windows-valid")
        missing = subject.create_managed_temp("windows-missing")
        registry = subject._registry_path(missing)
        (missing / _MARKER_NAME).unlink()
        missing.rmdir()

        assert {entry["path"] for entry in subject.list_managed_temp()} == {str(valid)}
        assert capsys.readouterr().err == ""
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
        """別実行主体の実測相当ACE追加後も公開検証とcleanupが成立する。"""
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
