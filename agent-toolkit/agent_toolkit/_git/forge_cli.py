"""forge CLIの接続先と、非対話の失敗ログ取得案内を共有する。"""

import os
import platform
import shlex
from urllib.parse import urlsplit

from agent_toolkit._git import remote

_GLAB_HOST_VARIABLES = ("GITLAB_HOST", "GITLAB_URI", "GL_HOST")

_GLAB_HOST_API_VARIABLES = (
    "GITLAB_API_HOST",
    "GLAB_API_PROTOCOL",
    "API_PROTOCOL",
    "GITLAB_SUBFOLDER",
    "GITLAB_TOKEN",
    "GITLAB_ACCESS_TOKEN",
    "OAUTH_TOKEN",
)

_GLAB_CI_AUTOLOGIN = "GLAB_ENABLE_CI_AUTOLOGIN"

GITHUB_JOB_LIST = ".jobs[] | [.databaseId, .name, .conclusion] | @tsv"


def glab_environment(
    repository: str | remote.RemoteLocation, *, prefer_environment: bool = False, inferred_host: bool = False
) -> dict[str, str]:
    """glabの子環境へ、待機では明示指定、通知では公開環境指定を優先して反映する。"""
    return _glab_environment_and_unset(repository, prefer_environment=prefer_environment, inferred_host=inferred_host)[0]


def _glab_environment_and_unset(
    repository: str | remote.RemoteLocation, *, prefer_environment: bool, inferred_host: bool
) -> tuple[dict[str, str], tuple[str, ...]]:
    target = remote.parse_remote_location(repository) if isinstance(repository, str) else repository

    authority = (
        remote.RemoteLocation(target.hostname, target.project_path).authority if target.protocol == "ssh" else target.authority
    )
    env = os.environ.copy()
    inherited_host = next((env[name] for name in _GLAB_HOST_VARIABLES if env.get(name)), None)
    unset: tuple[str, ...] = ()
    if inferred_host:
        try:
            value = inherited_host or ""
            inherited_authority = urlsplit(value if "://" in value else f"//{value}").netloc
        except ValueError:
            inherited_authority = ""
        if inherited_authority.lower() != authority:
            unset = (*_GLAB_HOST_VARIABLES, *_GLAB_HOST_API_VARIABLES)
            inherited_host = None
        if (
            env.get(_GLAB_CI_AUTOLOGIN, "").lower() in {"true", "1"}
            and env.get("GITLAB_CI", "").lower() in {"true", "1"}
            and env.get("CI_SERVER_FQDN", "").lower() != authority
        ):
            unset += (_GLAB_CI_AUTOLOGIN,)
        for name in unset:
            env.pop(name, None)
    if inherited_host and (prefer_environment or authority is None):
        env["GITLAB_HOST"] = inherited_host
    elif authority is not None:
        env["GITLAB_HOST"] = authority

    explicit_endpoint = target.protocol in {"http", "https"} and not (prefer_environment and inherited_host)
    if authority is not None and explicit_endpoint:
        env["GITLAB_API_HOST"] = authority
    if explicit_endpoint and target.protocol is not None:
        env["GLAB_API_PROTOCOL"] = target.protocol
    return env, unset


def glab_repository(repository: str, *, prefer_environment: bool = False, environment: dict[str, str] | None = None) -> str:
    target = remote.parse_remote_location(repository)
    env = environment if environment is not None else glab_environment(repository, prefer_environment=prefer_environment)
    host = env.get("GITLAB_HOST")
    if not host:
        return target.project_path
    if "://" in host:
        return f"{host.rstrip('/')}/{target.project_path}"
    protocol = env.get("GLAB_API_PROTOCOL") or env.get("API_PROTOCOL") or "https"
    return f"{protocol}://{host}/{target.project_path}"


def failure_log_commands(
    forge: str,
    repository: str,
    identifier: str,
    *,
    job: bool = False,
    prefer_environment: bool = False,
    inferred_host: bool = False,
) -> tuple[tuple[str, ...], ...]:
    """リポジトリを指定したログ取得argvを返す。job未確定なら取得とtraceを順に返す。"""
    target = remote.parse_remote_location(repository)
    if forge == "github":
        command = ["gh", "api", f"repos/{target.project_path}/actions/jobs/{identifier if job else '<job ID>'}/logs"]
        command.append("--allow-escape-sequences")
        if target.hostname:
            command.extend(("--hostname", target.hostname))
        if job:
            return (tuple(command),)
        repo = f"{target.authority}/{target.project_path}" if target.authority else target.project_path
        jobs = ("gh", "run", "view", identifier, "--repo", repo, "--json", "jobs", "--jq", GITHUB_JOB_LIST)
        return (jobs, tuple(command))
    environment, unset = _glab_environment_and_unset(target, prefer_environment=prefer_environment, inferred_host=inferred_host)
    repo = glab_repository(repository, environment=environment)
    assignments: list[str] = []
    if inferred_host and "GITLAB_HOST" in unset:
        assignments.append(f"GITLAB_HOST={environment['GITLAB_HOST']}")
    inherited_host = any(os.environ.get(name) for name in _GLAB_HOST_VARIABLES)
    explicit_endpoint = target.protocol in {"http", "https"} and not (prefer_environment and inherited_host)
    if target.authority is not None and explicit_endpoint:
        assignments.append(f"GITLAB_API_HOST={environment['GITLAB_API_HOST']}")
    if explicit_endpoint:
        protocol = environment.get("GLAB_API_PROTOCOL") or environment.get("API_PROTOCOL")
        if protocol:
            assignments.append(f"GLAB_API_PROTOCOL={protocol}")
    prefix = ("env", *(arg for name in unset for arg in ("-u", name)), *assignments) if unset or assignments else ()
    if job:
        return ((*prefix, "glab", "ci", "trace", identifier, "--repo", repo),)
    return (
        (*prefix, "glab", "ci", "get", "--repo", repo, "-p", identifier, "-d", "--status=failed", "-F", "json"),
        (*prefix, "glab", "ci", "trace", "<job ID>", "--repo", repo),
    )


def _powershell_quote(value: str) -> str:
    return "'" + value.translate({ord(char): char * 2 for char in "'‘’"}) + "'"


def _powershell_glab_commands(commands: tuple[tuple[str, ...], ...]) -> tuple[str, list[str]]:
    prepare = "PowerShellで、"
    if commands[0][0] == "env":
        prefix = commands[0][1 : commands[0].index("glab")]
        setup: list[str] = []
        remaining = iter(prefix)
        for argument in remaining:
            if argument == "-u":
                setup.append(f"Remove-Item Env:{next(remaining)} -ErrorAction SilentlyContinue")
            else:
                name, _, value = argument.partition("=")
                setup.append(f"$env:{name} = {_powershell_quote(value)}")
        prepare = "専用のPowerShellを開き、`" + "; ".join(setup) + "` を実行してから、"
    rendered = [
        "`glab " + " ".join(_powershell_quote(arg) for arg in command[command.index("glab") + 1 :]) + "`"
        for command in commands
    ]
    return prepare, rendered


def failure_next_action(
    forge: str,
    repository: str,
    identifier: str,
    *,
    job: bool = False,
    prefer_environment: bool = False,
    inferred_host: bool = False,
) -> str:
    commands = failure_log_commands(
        forge, repository, identifier, job=job, prefer_environment=prefer_environment, inferred_host=inferred_host
    )
    rendered = [f"`{shlex.join(command)}`" for command in commands]
    if forge == "github":
        save = "全jobログをmanaged-tempの中のファイルへ保存し、失敗箇所を読んで原因を調べる"
        if job:
            return f"{rendered[0]}で{save}"
        return f"{rendered[0]}で失敗jobのIDを取得し、{rendered[1]}の`<job ID>`を置換して失敗jobごとに{save}"
    prepare = ""
    if inferred_host and platform.system() == "Windows":
        prepare, rendered = _powershell_glab_commands(commands)
    if len(rendered) == 2:
        return f"{prepare}{rendered[0]}で失敗jobのIDを取得し、{rendered[1]}の`<job ID>`を置換してログを取得し原因を調べる"
    return f"{prepare}{rendered[0]}で失敗ログを取得して原因を調べる"
