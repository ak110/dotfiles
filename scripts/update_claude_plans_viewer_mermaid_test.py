"""Mermaid同梱物更新スクリプトのテスト。"""

import base64
import datetime
import hashlib
import io
import json
import os
import pathlib
import tarfile
from collections.abc import Mapping
from typing import Any

import pytest
import semantic_version
import update_claude_plans_viewer_mermaid as updater

_NOW = datetime.datetime(2026, 7, 29, 12, tzinfo=datetime.UTC)
_TARBALL_URL = "https://registry.npmjs.org/mermaid/-/mermaid-10.0.0.tgz"
_BUNDLE = b"window.mermaid={version:'10.0.0'};\n"
_LICENSE = b"MIT License\n"
_VENDOR_DIR = pathlib.Path(__file__).resolve().parents[1] / "pytools" / "claude_plans_viewer" / "vendor"


def _metadata(integrity: str = "sha512-placeholder") -> dict[str, object]:
    return {
        "versions": {
            "2.0.0": {"dist": {"tarball": _TARBALL_URL, "integrity": integrity}},
            "9.0.0": {"dist": {"tarball": _TARBALL_URL, "integrity": integrity}},
            "10.0.0": {"dist": {"tarball": _TARBALL_URL, "integrity": integrity}},
            "10.1.0-beta.1": {"dist": {"tarball": _TARBALL_URL, "integrity": integrity}},
            "11.0.0": {"dist": {"tarball": _TARBALL_URL, "integrity": integrity}},
        },
        "time": {
            "2.0.0": "2026-07-01T00:00:00.000Z",
            "9.0.0": "2026-07-01T00:00:00.000Z",
            "10.0.0": "2026-07-28T12:00:00.000Z",
            "10.1.0-beta.1": "2026-07-01T00:00:00.000Z",
            "11.0.0": "2026-07-28T12:00:00.001Z",
        },
    }


def _make_tarball(
    bundle: bytes = _BUNDLE,
    license_text: bytes | None = _LICENSE,
    extra_members: Mapping[str, bytes] | None = None,
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        members: dict[str, bytes] = {"package/dist/mermaid.min.js": bundle}
        if license_text is not None:
            members["package/LICENSE"] = license_text
        if extra_members is not None:
            members.update(extra_members)
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def _integrity(tarball: bytes) -> str:
    digest = base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    return f"sha256-unused sha512-{digest}"


def _write_existing_vendor(destination: pathlib.Path) -> dict[str, bytes]:
    destination.mkdir()
    existing = {
        "mermaid.min.js": b"old bundle",
        "LICENSE.mermaid.txt": b"old license",
        "mermaid.json": b'{"old": true}\n',
    }
    for filename, content in existing.items():
        (destination / filename).write_bytes(content)
    return existing


def _assert_vendor(destination: pathlib.Path, expected: Mapping[str, bytes]) -> None:
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == expected


def test_select_version_uses_semver_age_boundary_and_stable_releases() -> None:
    cutoff = _NOW - datetime.timedelta(hours=24)

    selected = updater.select_version(_metadata(), cutoff)

    assert selected == semantic_version.Version("10.0.0")


@pytest.mark.parametrize(
    "metadata",
    [
        {"versions": {}, "time": {}},
        {"versions": {"invalid": {}}, "time": {"invalid": "2026-07-01T00:00:00Z"}},
        {"versions": {"1.0.0": {}}, "time": {}},
    ],
)
def test_select_version_rejects_invalid_metadata(metadata: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        updater.select_version(metadata, _NOW)


def test_verify_integrity_accepts_only_matching_sha512() -> None:
    tarball = _make_tarball()

    updater.verify_integrity(tarball, _integrity(tarball))
    with pytest.raises(ValueError, match="一致しない"):
        updater.verify_integrity(tarball, "sha512-invalid")
    with pytest.raises(ValueError, match="一致しない"):
        updater.verify_integrity(tarball, "sha256-unused")


def test_extract_artifacts_requires_unique_regular_files() -> None:
    assert updater.extract_artifacts(_make_tarball()) == (_BUNDLE, _LICENSE)

    with pytest.raises(ValueError, match="LICENSE候補"):
        updater.extract_artifacts(_make_tarball(license_text=None))
    with pytest.raises(ValueError, match="LICENSE候補"):
        updater.extract_artifacts(_make_tarball(extra_members={"package/LICENSE.txt": b"duplicate"}))


@pytest.mark.parametrize("failure", ["integrity", "missing-license"])
def test_prepare_failure_does_not_change_vendor(tmp_path: pathlib.Path, failure: str) -> None:
    destination = tmp_path / "vendor"
    existing = _write_existing_vendor(destination)
    tarball = _make_tarball(license_text=None if failure == "missing-license" else _LICENSE)
    integrity = "sha512-invalid" if failure == "integrity" else _integrity(tarball)
    metadata = _metadata(integrity)

    with pytest.raises(ValueError):
        updater.prepare_artifacts(
            metadata,
            semantic_version.Version("10.0.0"),
            tarball,
            integrity,
            _TARBALL_URL,
            _NOW,
        )

    _assert_vendor(destination, existing)


def test_successful_update_produces_consistent_artifacts(tmp_path: pathlib.Path) -> None:
    destination = tmp_path / "vendor"
    _write_existing_vendor(destination)
    tarball = _make_tarball()
    integrity = _integrity(tarball)
    metadata = _metadata(integrity)
    version = updater.select_version(metadata, _NOW - datetime.timedelta(hours=24))

    artifacts = updater.prepare_artifacts(metadata, version, tarball, integrity, _TARBALL_URL, _NOW)
    updater.replace_vendor_atomically(destination, artifacts)

    assert (destination / "mermaid.min.js").read_bytes() == _BUNDLE
    assert (destination / "LICENSE.mermaid.txt").read_bytes() == _LICENSE
    provenance = json.loads((destination / "mermaid.json").read_text(encoding="utf-8"))
    assert provenance == {
        "package": "mermaid",
        "version": "10.0.0",
        "metadata_url": "https://registry.npmjs.org/mermaid",
        "tarball_url": _TARBALL_URL,
        "integrity": integrity,
        "retrieved_at": "2026-07-29T12:00:00Z",
        "bundle_sha256": hashlib.sha256(_BUNDLE).hexdigest(),
    }


def test_tracked_bundle_matches_provenance_digest() -> None:
    """追跡済みbundleが同梱由来情報のSHA-256と一致する。"""
    metadata_path = _VENDOR_DIR / "mermaid.json"
    bundle_path = metadata_path.with_name("mermaid.min.js")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == metadata["bundle_sha256"]


def test_atomic_replace_restores_existing_vendor_when_backup_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "vendor"
    existing = _write_existing_vendor(destination)
    artifacts = {
        "mermaid.min.js": b"new bundle",
        "LICENSE.mermaid.txt": b"new license",
        "mermaid.json": b'{"new": true}\n',
    }
    original_replace = os.replace
    backup_replacements = 0

    def fail_during_backup(source: os.PathLike[str], target: os.PathLike[str], **kwargs: Any) -> None:
        nonlocal backup_replacements
        if pathlib.Path(target).parent.name == "backup":
            backup_replacements += 1
            if backup_replacements == 2:
                raise OSError("injected backup failure")
        original_replace(source, target, **kwargs)

    monkeypatch.setattr(updater.os, "replace", fail_during_backup)

    with pytest.raises(OSError, match="injected"):
        updater.replace_vendor_atomically(destination, artifacts)

    _assert_vendor(destination, existing)


def test_atomic_replace_restores_existing_vendor_when_install_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "vendor"
    existing = _write_existing_vendor(destination)
    artifacts = {
        "mermaid.min.js": b"new bundle",
        "LICENSE.mermaid.txt": b"new license",
        "mermaid.json": b'{"new": true}\n',
    }
    original_replace = os.replace
    new_file_replacements = 0

    def fail_during_install(source: os.PathLike[str], target: os.PathLike[str], **kwargs: Any) -> None:
        nonlocal new_file_replacements
        if pathlib.Path(source).parent.name == "staged":
            new_file_replacements += 1
            if new_file_replacements == 2:
                raise OSError("injected replacement failure")
        original_replace(source, target, **kwargs)

    monkeypatch.setattr(updater.os, "replace", fail_during_install)

    with pytest.raises(OSError, match="injected"):
        updater.replace_vendor_atomically(destination, artifacts)

    _assert_vendor(destination, existing)
