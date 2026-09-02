#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "semantic-version>=2.10.0",
# ]
# ///
"""`atk serve`へ同梱するMermaidをnpmから更新する。"""

import base64
import datetime
import hashlib
import hmac
import io
import json
import os
import pathlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Mapping
from typing import Any

import semantic_version

PACKAGE_NAME = "mermaid"
METADATA_URL = f"https://registry.npmjs.org/{PACKAGE_NAME}"
VENDOR_DIRECTORY = pathlib.Path(__file__).parent.parent / "agent-toolkit" / "scripts" / "_atk_serve_static" / "vendor"
VENDOR_FILENAMES = ("mermaid.min.js", "LICENSE.mermaid.txt", "mermaid.json")

_BUNDLE_MEMBER = "package/dist/mermaid.min.js"
_LICENSE_MEMBERS = ("package/LICENSE", "package/LICENSE.txt", "package/LICENSE.md")
_MAX_BUNDLE_SIZE = 16 * 1024 * 1024
_MAX_LICENSE_SIZE = 1024 * 1024
_MAX_METADATA_SIZE = 32 * 1024 * 1024
_MAX_TARBALL_SIZE = 64 * 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS = 30

type JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def main() -> int:
    """公開後24時間を経過した最新安定版のMermaidを取得する。"""
    retrieved_at = datetime.datetime.now(datetime.UTC)
    metadata = _download_json(METADATA_URL)
    version = select_version(metadata, retrieved_at - datetime.timedelta(hours=24))
    tarball_url, integrity = _release_distribution(metadata, version)
    tarball = _download_bytes(tarball_url, _MAX_TARBALL_SIZE)
    artifacts = prepare_artifacts(metadata, version, tarball, integrity, tarball_url, retrieved_at)
    replace_vendor_atomically(VENDOR_DIRECTORY, artifacts)
    print(f"Mermaid {version}を{VENDOR_DIRECTORY}へ更新した。")
    return 0


def select_version(metadata: Mapping[str, Any], cutoff: datetime.datetime) -> semantic_version.Version:
    """公開日時がcutoff以前である最新の安定版を選ぶ。"""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoffにはタイムゾーン情報が必要である。")

    versions = metadata.get("versions")
    published_times = metadata.get("time")
    if not isinstance(versions, dict) or not isinstance(published_times, dict):
        raise ValueError("npm metadataのversionsまたはtimeが不正である。")

    candidates: list[semantic_version.Version] = []
    for version_text in versions:
        if not isinstance(version_text, str):
            raise ValueError("npm metadataのバージョン名が文字列ではない。")
        try:
            version = semantic_version.Version(version_text)
        except ValueError as exc:
            raise ValueError(f"npm metadataに不正なSemVerがある: {version_text!r}") from exc

        published_text = published_times.get(version_text)
        if not isinstance(published_text, str):
            raise ValueError(f"npm metadataに公開日時がない: {version_text}")
        published_at = _parse_utc_datetime(published_text)
        if not version.prerelease and published_at <= cutoff:
            candidates.append(version)

    if not candidates:
        raise ValueError("公開後24時間を経過した安定版がない。")
    return max(candidates)


def verify_integrity(tarball: bytes, integrity: str) -> None:
    """npmのSubresource Integrity値でtarballのSHA-512を検証する。"""
    if not isinstance(integrity, str):
        raise TypeError("integrityは文字列である必要がある。")

    actual = base64.b64encode(hashlib.sha512(tarball).digest()).decode("ascii")
    expected_values = [value.removeprefix("sha512-") for value in integrity.split() if value.startswith("sha512-")]
    if not expected_values or not any(hmac.compare_digest(actual, expected) for expected in expected_values):
        raise ValueError("tarballのSHA-512 integrityが一致しない。")


def extract_artifacts(tarball: bytes) -> tuple[bytes, bytes]:
    """tarballから単一ファイルbundleとライセンスを取得する。"""
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:*") as archive:
            members = archive.getmembers()
            bundle_member = _select_member(members, (_BUNDLE_MEMBER,), "bundle")
            license_member = _select_member(members, _LICENSE_MEMBERS, "LICENSE")
            bundle = _read_member(archive, bundle_member, _MAX_BUNDLE_SIZE)
            license_text = _read_member(archive, license_member, _MAX_LICENSE_SIZE)
    except tarfile.TarError as exc:
        raise ValueError("npm tarballを読み込めない。") from exc
    return bundle, license_text


def build_provenance(
    version: semantic_version.Version,
    tarball_url: str,
    integrity: str,
    retrieved_at: datetime.datetime,
    bundle: bytes,
) -> dict[str, JsonValue]:
    """同梱物の取得元と完全性情報を構築する。"""
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("取得日時にはタイムゾーン情報が必要である。")
    return {
        "package": PACKAGE_NAME,
        "version": str(version),
        "metadata_url": METADATA_URL,
        "tarball_url": tarball_url,
        "integrity": integrity,
        "retrieved_at": retrieved_at.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z"),
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
    }


def prepare_artifacts(
    metadata: Mapping[str, Any],
    version: semantic_version.Version,
    tarball: bytes,
    integrity: str,
    tarball_url: str,
    retrieved_at: datetime.datetime,
) -> dict[str, bytes]:
    """検証済みのvendor用3ファイルを生成する。"""
    version_text = str(version)
    versions = metadata.get("versions")
    if not isinstance(versions, dict) or version_text not in versions:
        raise ValueError(f"npm metadataに選択版がない: {version_text}")

    verify_integrity(tarball, integrity)
    bundle, license_text = extract_artifacts(tarball)
    provenance = build_provenance(version, tarball_url, integrity, retrieved_at, bundle)
    return {
        "mermaid.min.js": bundle,
        "LICENSE.mermaid.txt": license_text,
        "mermaid.json": (json.dumps(provenance, ensure_ascii=False, indent=2) + "\n").encode(),
    }


def replace_vendor_atomically(destination: pathlib.Path, artifacts: Mapping[str, bytes]) -> None:
    """vendor用3ファイルを更新し、失敗時は更新前の状態へ戻す。"""
    if set(artifacts) != set(VENDOR_FILENAMES) or not all(isinstance(content, bytes) for content in artifacts.values()):
        raise ValueError("artifactsにはvendor用3ファイルのbytesが必要である。")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".mermaid-update-", dir=destination.parent))
    staged = temporary / "staged"
    backup = temporary / "backup"
    staged.mkdir()
    backup.mkdir()
    before = _snapshot(destination)
    backed_up: set[str] = set()
    installed: set[str] = set()

    try:
        for filename in VENDOR_FILENAMES:
            _write_fsynced(staged / filename, artifacts[filename])

        destination.mkdir(exist_ok=True)
        for filename in VENDOR_FILENAMES:
            target = destination / filename
            if target.exists():
                os.replace(target, backup / filename)
                backed_up.add(filename)
        for filename in VENDOR_FILENAMES:
            os.replace(staged / filename, destination / filename)
            installed.add(filename)
        _fsync_directory(destination)
    except Exception as update_error:
        try:
            _restore_vendor(destination, backup, backed_up, installed)
        except Exception as restore_error:
            raise RuntimeError(f"vendorの復旧に失敗した。退避先: {backup}") from restore_error
        if _snapshot(destination) != before:
            raise RuntimeError(f"更新失敗後のvendorが更新前の状態と一致しない。退避先: {backup}") from update_error
        shutil.rmtree(temporary)
        raise
    else:
        shutil.rmtree(temporary)


def _release_distribution(metadata: Mapping[str, Any], version: semantic_version.Version) -> tuple[str, str]:
    versions = metadata.get("versions")
    if not isinstance(versions, dict):
        raise ValueError("npm metadataのversionsが不正である。")
    release = versions.get(str(version))
    if not isinstance(release, dict):
        raise ValueError(f"npm metadataに選択版がない: {version}")
    dist = release.get("dist")
    if not isinstance(dist, dict):
        raise ValueError(f"npm metadataのdistが不正である: {version}")
    tarball_url = dist.get("tarball")
    integrity = dist.get("integrity")
    if not isinstance(tarball_url, str) or not isinstance(integrity, str):
        raise ValueError(f"npm metadataのtarballまたはintegrityが不正である: {version}")
    return tarball_url, integrity


def _parse_utc_datetime(value: str) -> datetime.datetime:
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"npm metadataの公開日時が不正である: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"npm metadataの公開日時にタイムゾーンがない: {value!r}")
    return parsed.astimezone(datetime.UTC)


def _select_member(members: list[tarfile.TarInfo], names: tuple[str, ...], label: str) -> tarfile.TarInfo:
    selected = [member for member in members if member.name in names]
    if len(selected) != 1:
        raise ValueError(f"npm tarball内の{label}候補が1件ではない。")
    member = selected[0]
    if not member.isfile():
        raise ValueError(f"npm tarball内の{label}が通常ファイルではない。")
    return member


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo, size_limit: int) -> bytes:
    if member.size < 0 or member.size > size_limit:
        raise ValueError(f"npm tarball内の{member.name}が展開上限を超えている。")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValueError(f"npm tarball内の{member.name}を取得できない。")
    content = extracted.read(size_limit + 1)
    if len(content) > size_limit or len(content) != member.size:
        raise ValueError(f"npm tarball内の{member.name}のサイズが不正である。")
    return content


def _snapshot(destination: pathlib.Path) -> dict[str, str | None]:
    return {
        filename: hashlib.sha256(path.read_bytes()).hexdigest() if (path := destination / filename).is_file() else None
        for filename in VENDOR_FILENAMES
    }


def _restore_vendor(
    destination: pathlib.Path,
    backup: pathlib.Path,
    backed_up: set[str],
    installed: set[str],
) -> None:
    for filename in installed:
        (destination / filename).unlink()
    for filename in backed_up:
        os.replace(backup / filename, destination / filename)
    _fsync_directory(destination)


def _write_fsynced(path: pathlib.Path, content: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _download_json(url: str) -> dict[str, Any]:
    try:
        value = json.loads(_download_bytes(url, _MAX_METADATA_SIZE))
    except json.JSONDecodeError as exc:
        raise ValueError("npm registry metadataがJSONではない。") from exc
    if not isinstance(value, dict):
        raise ValueError("npm registry metadataのルートがobjectではない。")
    return value


def _download_bytes(url: str, size_limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dotfiles-mermaid-updater/1"})
    with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
        content = response.read(size_limit + 1)
    if len(content) > size_limit:
        raise ValueError(f"ダウンロードサイズが上限を超えている: {url}")
    return content


if __name__ == "__main__":
    sys.exit(main())
