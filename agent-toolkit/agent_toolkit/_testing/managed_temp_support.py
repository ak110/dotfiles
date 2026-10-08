"""managed-tempのテストが共有する、Windowsのセキュリティ操作の擬似、記録の改変、状態の比較と差し替えの補助。"""

# pylint: disable=protected-access

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import stat
import tempfile
import types
import typing

import pytest

from agent_toolkit._atk import managed_temp as subject
from agent_toolkit._atk.managed_temp import cli as managed_temp_cli
from agent_toolkit._atk.managed_temp import creation as managed_temp_creation
from agent_toolkit._atk.managed_temp import errors as managed_temp_errors
from agent_toolkit._atk.managed_temp import inventory as managed_temp_inventory
from agent_toolkit._atk.managed_temp import registry as managed_temp_registry
from agent_toolkit._atk.managed_temp import validation as managed_temp_validation
from agent_toolkit._atk.managed_temp import windows_security as managed_temp_windows_security

MANAGED_TEMP_MODULES = (
    managed_temp_errors,
    managed_temp_windows_security,
    managed_temp_registry,
    managed_temp_validation,
    managed_temp_creation,
    managed_temp_inventory,
    managed_temp_cli,
)
"""managed-tempのサブモジュール。差し替える名前を束縛しうる全モジュール。"""

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


def setattr_in_managed_temp_modules(monkeypatch: pytest.MonkeyPatch, name: str, value: object) -> None:
    """パッケージと、名前を束縛する全サブモジュールで同じ値へ差し替える。

    各サブモジュールは定義元から名前を自らimportして束縛するため、1つのモジュールの差し替えは他へ届かない。
    """
    targets: list[types.ModuleType] = [module for module in MANAGED_TEMP_MODULES if name in vars(module)]
    if hasattr(subject, name):
        targets.append(subject)
    assert targets, f"managed-tempのどのモジュールも{name}を持たない"
    for module in targets:
        monkeypatch.setattr(module, name, value)


class _WindowsSecurityCalls(typing.NamedTuple):
    opens: list[int]
    security_reads: list[int]
    updates: list[tuple[int, int, bytes | None]]


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
    full_access = (
        managed_temp_windows_security._WINDOWS_READ_CONTROL
        | managed_temp_windows_security._WINDOWS_WRITE_DAC
        | managed_temp_windows_security._WINDOWS_WRITE_OWNER
    )

    @contextlib.contextmanager
    def fake_path_handle(
        path: pathlib.Path,
        access: int,
        **_kwargs: object,
    ) -> typing.Generator[tuple[int, managed_temp_windows_security._ByHandleFileInformation]]:
        opens.append(access)
        if access == full_access and full_open_error is not None:
            raise managed_temp_windows_security._WindowsHandleOpenError("handle open failed", path, full_open_error)
        information = managed_temp_windows_security._ByHandleFileInformation()
        information.attributes = managed_temp_windows_security._WINDOWS_FILE_ATTRIBUTE_DIRECTORY if directory else 0
        handle = (
            202
            if access == managed_temp_windows_security._WINDOWS_READ_CONTROL | managed_temp_windows_security._WINDOWS_WRITE_DAC
            else 101
        )
        yield handle, information

    def fake_current_sid(**_kwargs: object) -> str:
        return "S-1-current"

    def fake_sid_bytes(_sid_text: str, **_kwargs: object) -> bytes:
        return b"current-owner"

    def fake_acl_buffer(
        _path: pathlib.Path,
        _aces: tuple[managed_temp_windows_security._WindowsAce, ...],
        **_kwargs: object,
    ) -> object:
        return object()

    def fake_security_from_handle(
        handle: int,
        _information: managed_temp_windows_security._ByHandleFileInformation,
        _path: pathlib.Path,
        **_kwargs: object,
    ) -> managed_temp_windows_security._WindowsSecurity:
        security_reads.append(handle)
        return managed_temp_windows_security._WindowsSecurity(existing_owner, True, True, directory, ())

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

    setattr_in_managed_temp_modules(monkeypatch, "_windows_path_handle", fake_path_handle)
    setattr_in_managed_temp_modules(monkeypatch, "_windows_current_sid", fake_current_sid)
    setattr_in_managed_temp_modules(monkeypatch, "_windows_sid_bytes", fake_sid_bytes)
    setattr_in_managed_temp_modules(monkeypatch, "_windows_acl_buffer", fake_acl_buffer)
    setattr_in_managed_temp_modules(monkeypatch, "_windows_security_from_handle", fake_security_from_handle)
    setattr_in_managed_temp_modules(monkeypatch, "_windows_equal_sids", fake_equal_sids)
    setattr_in_managed_temp_modules(monkeypatch, "_windows_set_security", fake_set_security)
    return _WindowsSecurityCalls(opens, security_reads, updates)


def _path_state(path: pathlib.Path) -> tuple[object, ...]:
    """pathの種別、内容、所有・権限状態を比較可能な値で返す。"""
    metadata = path.lstat()
    content = path.read_bytes() if path.is_file() else None
    if os.name == "nt":
        security: object = managed_temp_windows_security._windows_security_descriptor(path)
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
    registry = managed_temp_registry._registry_path(target)
    record = json.loads(registry.read_text(encoding="utf-8"))
    transform(record)
    registry.write_text(json.dumps(record), encoding="utf-8")
    if os.name == "posix":
        registry.chmod(0o600)


def _replace_records(target: pathlib.Path, transform: typing.Callable[[dict[str, object]], None]) -> None:
    """マーカーファイルと登録簿へ同じ改変を保存してデータ契約を検証可能にする。"""
    marker = target / _MARKER_NAME
    registry = managed_temp_registry._registry_path(target)
    for path in (marker, registry):
        record = json.loads(path.read_text(encoding="utf-8"))
        transform(record)
        path.write_text(json.dumps(record), encoding="utf-8")
        if os.name == "posix":
            path.chmod(0o600)


def _interrupt_cleanup(target: pathlib.Path, *, quarantine: bool = False) -> tuple[pathlib.Path, pathlib.Path]:
    """登録を消費途中へ移し、必要なら実体も隔離した状態を再現する。"""
    registry = managed_temp_registry._registry_path(target)
    record = managed_temp_registry._load_private_json(registry)
    nonce = typing.cast(str, record["nonce"])
    consuming = registry.with_name(f"{registry.name}.consuming-{nonce}")
    registry.replace(consuming)
    quarantine_path = target.parent / f".agent-toolkit-cleanup-{nonce}"
    if quarantine:
        target.replace(quarantine_path)
    return consuming, quarantine_path


def _registry_recovery_is_accepted(target: pathlib.Path) -> bool:
    """`--recover-registry`が指定された管理対象の後始末を受理したかを返す。"""
    try:
        subject.cleanup_managed_temp(target, recover_registry=True)
    except subject.ManagedTempError:
        return False
    return True


def _set_tree_mtime(path: pathlib.Path, timestamp_ns: int) -> None:
    """対象ツリー全体の最終更新日時を同じ値へ固定する。"""
    for entry in path.rglob("*"):
        os.utime(entry, ns=(timestamp_ns, timestamp_ns))
    os.utime(path, ns=(timestamp_ns, timestamp_ns))


def _isolated_cli_environment(tmp_path: pathlib.Path) -> tuple[dict[str, str], pathlib.Path]:
    """CLI subprocessの一時領域と外部状態をOS別の専用領域へ分離する。"""
    env = os.environ.copy()
    for name in ("TMPDIR", "TEMP", "TMP"):
        env[name] = str(tmp_path)
    if os.name == "nt":
        env["LOCALAPPDATA"] = str(tmp_path / "local-app-data")
        state_root = tmp_path / "local-app-data" / "agent-toolkit" / "managed-temp"
    else:
        env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
        env["XDG_STATE_HOME"] = str(tmp_path / "state")
        state_root = tmp_path / "state" / "agent-toolkit" / "managed-temp"
    return env, state_root


def _assert_child_replacement_preserves_both_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, kind: str, prefix: str, displaced_name: str
) -> None:
    """登録簿の消費の直後に子（ファイルかディレクトリ）を置き換えると、回収が置換を拒否して両方の内容を残すことを確かめる。"""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    target = subject.create_managed_temp(prefix)
    child = target / "child"
    displaced = target / displaced_name
    if kind == "directory":
        child.mkdir()
        (child / "original.txt").write_text("original", encoding="utf-8")
    else:
        child.write_text("original", encoding="utf-8")
    original_consume = managed_temp_inventory._consume_registry

    def replace_child(validated: typing.Any) -> pathlib.Path:
        consuming = original_consume(validated)
        child.rename(displaced)
        if kind == "directory":
            child.mkdir()
            (child / "replacement.txt").write_text("replacement", encoding="utf-8")
        else:
            child.write_text("replacement", encoding="utf-8")
        return consuming

    setattr_in_managed_temp_modules(monkeypatch, "_consume_registry", replace_child)
    with pytest.raises(subject.ManagedTempError, match="置換"):
        subject.cleanup_managed_temp(target)
    if kind == "directory":
        assert (displaced / "original.txt").read_text(encoding="utf-8") == "original"
        assert (child / "replacement.txt").read_text(encoding="utf-8") == "replacement"
    else:
        assert displaced.read_text(encoding="utf-8") == "original"
        assert child.read_text(encoding="utf-8") == "replacement"


def _owner_and_external_writer_aces(
    current_sid: bytes, external_sid: bytes
) -> tuple[managed_temp_windows_security._WindowsAce, ...]:
    """現在のユーザーへ全権限、外部の書き手へ書込権限を子へ継承させて与えるACEの組を返す。"""
    flags = (
        managed_temp_windows_security._WINDOWS_OBJECT_INHERIT_ACE | managed_temp_windows_security._WINDOWS_CONTAINER_INHERIT_ACE
    )
    return (
        managed_temp_windows_security._WindowsAce(
            managed_temp_windows_security._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
            flags,
            managed_temp_windows_security._WINDOWS_FILE_ALL_ACCESS,
            current_sid,
        ),
        managed_temp_windows_security._WindowsAce(
            managed_temp_windows_security._WINDOWS_ACCESS_ALLOWED_ACE_TYPE,
            flags,
            managed_temp_windows_security._WINDOWS_EXTERNAL_WRITER_ACCESS,
            external_sid,
        ),
    )
