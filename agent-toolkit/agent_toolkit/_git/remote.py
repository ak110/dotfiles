from __future__ import annotations

import dataclasses
import pathlib
import re
lazy import urllib.parse

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command as _git_command

_SCP_LIKE_REMOTE_RE = re.compile(r"^[^@\s]+@(?P<host>[^:\s]+):(?P<path>.+)$")
_NORMALIZED_REMOTE_RE = re.compile(r"[^/]+(?:/[^/]+){2,}")
_REMOTE_URL_NEXT_ACTION = (
    "`git remote get-url origin`の値を確認し、HTTPS・SSHのURLか`host/owner/repository`形式の値を指定し直す"
)


def normalize_remote_url(remote_url: str) -> str:
    value = remote_url.strip()
    if not value:
        raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)

    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https", "ssh"} or parsed.hostname is None:
            raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)
        host = parsed.hostname
        path = parsed.path
    elif (scp_match := _SCP_LIKE_REMOTE_RE.fullmatch(value)) is not None:
        host = scp_match.group("host")
        path = scp_match.group("path")
    elif _NORMALIZED_REMOTE_RE.fullmatch(value) is not None and "@" not in value:
        host, path = value.split("/", maxsplit=1)
    else:
        raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)

    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path or "/" not in normalized_path:
        raise ActionableError(f"リモートURLとして解析できません: {remote_url!r}", next_action=_REMOTE_URL_NEXT_ACTION)
    return f"{host.lower()}/{normalized_path.lower()}"


def resolve_repo_identifier(value: str) -> str | None:
    try:
        return normalize_remote_url(value)
    except ValueError:
        pass
    local_path = pathlib.Path(value).expanduser()
    try:
        if not local_path.exists():
            return None
    except OSError:
        return None
    url = origin_url(local_path)
    if url is None:
        return None
    try:
        return normalize_remote_url(url)
    except ValueError:
        return None


def canonical_repo(value: str, cache: dict[str, str | None]) -> str | None:
    if value not in cache:
        cache[value] = resolve_repo_identifier(value)
    return cache[value]


def origin_url(worktree: str | pathlib.Path, *, timeout: float | None = None) -> str | None:
    result = _git_command.run(
        ["-C", str(worktree), "remote", "get-url", "origin"], capture_output=True, text=True, check=False, timeout=timeout
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def remote_urls(cwd: str | pathlib.Path | None, *, timeout: float) -> list[str]:
    stdout = _git_command.optional_stdout(["config", "--get-regexp", r"^remote\..*\.url$"], cwd, timeout=timeout)
    if stdout is None:
        return []
    return [line.partition(" ")[2] for line in stdout.splitlines()]


@dataclasses.dataclass(frozen=True)
class RemoteLocation:
    hostname: str | None
    project_path: str
    port: int | None = None
    protocol: str | None = None

    @property
    def authority(self) -> str | None:
        if self.hostname is None:
            return None
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        return f"{host}:{self.port}" if self.port is not None else host


def parse_remote_location(value: str) -> RemoteLocation:
    stripped = value.strip()
    hostname: str | None = None
    port: int | None = None
    protocol: str | None = None
    project_path = stripped
    if "://" in stripped:
        parsed = urllib.parse.urlparse(stripped)
        hostname = parsed.hostname
        port = parsed.port
        protocol = parsed.scheme
        project_path = parsed.path
    elif match := re.fullmatch(r"([^/@:]+):(\d+)/(.+)", stripped):
        hostname = match.group(1)
        port = int(match.group(2))
        project_path = match.group(3)
    elif match := re.match(r"^(?:[^@/]+@)?([^:/]+):(.+)$", stripped):
        hostname = match.group(1)
        project_path = match.group(2)
    else:
        parts = stripped.strip("/").split("/")
        if len(parts) >= 3 and "." in parts[0]:
            hostname = parts[0]
            project_path = "/".join(parts[1:])
    project_path = project_path.strip("/")
    if project_path.endswith(".git"):
        project_path = project_path[:-4]
    if not project_path or "/" not in project_path:
        raise ValueError(f"リモートのプロジェクトのパスを取り出せない: {value!r}")
    return RemoteLocation(hostname=hostname, project_path=project_path, port=port, protocol=protocol)
