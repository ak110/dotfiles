import dataclasses
import functools
import http.client
import io
import json
import os
import pathlib
import re
import subprocess
import tarfile
from collections.abc import Callable, Iterator
from typing import Literal

from agent_toolkit._common.service_code_identity import code_digest
from agent_toolkit._git import command as git_command

_MANIFEST = "agent-toolkit/.claude-plugin/plugin.json"
_DOTFILES_MARKER = "libexec/update_dotfiles.py"
_MODULES = "agent-toolkit/agent_toolkit"
_OID = re.compile(r"[a-f0-9]{40}|[a-f0-9]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
IDENTITY_PATH = "/api/runtime-identity"
_GIT_ERRORS = (OSError, subprocess.SubprocessError, ValueError)
Service = Literal["atk serve", "agents_server"]
type AgentsVersion = Callable[[], tuple[bool, str | None]]


@dataclasses.dataclass(frozen=True)
class VersionCheck:
    service: Service
    running: str | None
    published: str | None

    @property
    def status(self) -> Literal["unavailable", "match", "mismatch"]:
        if self.running is None or self.published is None:
            return "unavailable"
        return "match" if self.running == self.published else "mismatch"

    def message(self) -> str:
        status = {"match": "一致", "mismatch": "公開バージョンと不一致（未反映）", "unavailable": "バージョンを取得できません"}[
            self.status
        ]
        return f"{self.service}: {status}（稼働バージョン: {_label(self.running)}、公開バージョン: {_label(self.published)}）"


def _label(identity: str | None) -> str:
    if identity is None:
        return "取得不能"
    version, _, generation = identity.partition("/")
    return f"{version}（世代 {generation[:12]}）" if generation else version


def package_checkout() -> pathlib.Path | None:
    candidate = pathlib.Path(__file__).resolve().parents[4]
    return candidate if (candidate / _DOTFILES_MARKER).is_file() else None


def dotfiles_checkout(home: pathlib.Path) -> pathlib.Path | None:
    candidate = home / "dotfiles"
    return candidate if (candidate / _DOTFILES_MARKER).is_file() else package_checkout()


def _git(repo: pathlib.Path, *args: str) -> str:
    return git_command.run(
        ["--no-optional-locks", *args],
        repo,
        capture_output=True,
        check=True,
        text=True,
        timeout=5,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        recover_stale_lock=False,
    ).stdout.strip()


def full_oid(value: object) -> str | None:
    return value if isinstance(value, str) and _OID.fullmatch(value) else None


def _release_version(value: object) -> str | None:
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def startup_revision(repo: pathlib.Path) -> str | None:
    try:
        revision = full_oid(_git(repo, "rev-parse", "--verify", "HEAD"))
        if (
            revision
            and not _git(repo, "status", "--porcelain", "--untracked-files=all", "--", "agent-toolkit")
            and _git(repo, "rev-parse", "--verify", "HEAD") == revision
        ):
            return revision
    except _GIT_ERRORS:
        pass
    return None


def latest_published(repo: pathlib.Path) -> str | None:
    try:
        branch = _git(repo, "symbolic-ref", "HEAD")
        remote, ref = _git(repo, "for-each-ref", "--format=%(upstream:remotename) %(upstream:remoteref)", branch).split()
        if remote != "origin" or not ref.startswith("refs/heads/"):
            return None
        oid, returned_ref = _git(repo, "ls-remote", "--exit-code", "--refs", "origin", ref).split()
        if returned_ref == ref:
            return full_oid(oid)
    except _GIT_ERRORS:
        pass
    return None


@functools.lru_cache(maxsize=4)
def _published_plugin(repo: pathlib.Path, revision: str) -> str:
    manifest = json.loads(_git(repo, "show", f"{revision}:{_MANIFEST}"))
    version = _release_version(manifest.get("version")) if isinstance(manifest, dict) else None
    if version is None:
        raise ValueError("agent-toolkitの配布バージョンが不正")
    archive = git_command.run(
        ["archive", revision, _MODULES], repo, capture_output=True, check=True, timeout=5, recover_stale_lock=False
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as package:
        modules = {
            pathlib.Path(member.name).relative_to("agent-toolkit"): member
            for member in package
            if member.isfile() and os.path.normcase(member.name).endswith(".py") and not member.name.endswith("_test.py")
        }
        return f"{version}/{code_digest(version, _module_contents(package, modules))}"


def _module_contents(
    package: tarfile.TarFile, modules: dict[pathlib.Path, tarfile.TarInfo]
) -> Iterator[tuple[pathlib.Path, bytes]]:
    for path, member in sorted(modules.items()):
        contents = package.extractfile(member)
        if contents is None:
            raise ValueError("公開モジュールを取得できない")
        with contents:
            yield path, contents.read()


def published_plugin(repo: pathlib.Path, revision: str | None) -> str | None:
    if revision is None or full_oid(revision) is None:
        return None
    try:
        return _published_plugin(repo, revision)
    except (*_GIT_ERRORS, tarfile.TarError):
        return None


def serve_running(host: str, port: int) -> str | None:
    connection = http.client.HTTPConnection({"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host), port, timeout=5)
    try:
        connection.request("GET", IDENTITY_PATH)
        with connection.getresponse() as response:
            if response.status == 200:
                result = json.loads(response.read(4096))
                return full_oid(result.get("revision")) if isinstance(result, dict) else None
    except OSError, http.client.HTTPException, ValueError:
        pass
    finally:
        connection.close()
    return None


def check_versions(
    repo: pathlib.Path,
    published: str | None,
    *,
    serve_revision: str | None,
    include_serve: bool,
    agents: tuple[bool, str | None],
) -> list[VersionCheck]:
    checks = []
    if include_serve:
        checks.append(VersionCheck("atk serve", serve_revision, published))
    include_agents, agents_version = agents
    if include_agents:
        checks.append(VersionCheck("agents_server", agents_version, published_plugin(repo, published)))
    return checks


def show_versions(
    repo: pathlib.Path,
    *,
    published: str | None,
    serve_address: tuple[str, int] | None,
    agents: tuple[bool, str | None],
) -> int:
    target = latest_published(repo) if published is None else published
    checks = check_versions(
        repo,
        target,
        serve_revision=serve_running(*serve_address) if serve_address else None,
        include_serve=serve_address is not None,
        agents=agents,
    )
    print(f"公開コミット: {target or '取得不能'}")
    for check in checks:
        print(check.message())
    if not checks or any(check.status != "match" for check in checks):
        print("接続と稼働状態を確認し、必要な更新・再起動の後に同じコマンドで再確認してください。")
        return 1
    return 0
