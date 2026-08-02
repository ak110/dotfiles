"""GitリモートURLの取得と正規化を提供する。"""

from __future__ import annotations

import pathlib
import re
import subprocess
import urllib.parse

_SCP_LIKE_REMOTE_RE = re.compile(r"^[^@\s]+@(?P<host>[^:\s]+):(?P<path>.+)$")
_NORMALIZED_REMOTE_RE = re.compile(r"[^/]+(?:/[^/]+){2,}")


def normalize_remote_url(remote_url: str) -> str:
    """GitリモートURLを`host/owner/repository`形式へ正規化する。

    HTTPS、SSH URI、SSH短縮、正規化済み識別子を受理する。受理外はValueErrorを送出する。
    """
    value = remote_url.strip()
    if not value:
        raise ValueError(f"リモートURLとして解析できません: {remote_url!r}")

    scp_match = _SCP_LIKE_REMOTE_RE.fullmatch(value)
    if scp_match is not None:
        host = scp_match.group("host")
        path = scp_match.group("path")
    elif _NORMALIZED_REMOTE_RE.fullmatch(value) is not None and "://" not in value and "@" not in value:
        host, path = value.split("/", maxsplit=1)
    else:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https", "ssh"} or parsed.hostname is None:
            raise ValueError(f"リモートURLとして解析できません: {remote_url!r}")
        host = parsed.hostname
        path = parsed.path

    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path or "/" not in normalized_path:
        raise ValueError(f"リモートURLとして解析できません: {remote_url!r}")
    return f"{host.lower()}/{normalized_path.lower()}"


def get_normalized_origin(repository: pathlib.Path) -> str | None:
    """リポジトリのoriginを取得して正規化する。取得不能時はNoneを返す。"""
    if not repository.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return normalize_remote_url(result.stdout)
    except ValueError:
        return None
