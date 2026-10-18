"""forge CLIの接続先と、非対話の失敗ログ取得案内を共有する。"""

import os
import shlex

from agent_toolkit._git import remote

_GLAB_HOST_VARIABLES = ("GITLAB_HOST", "GITLAB_URI", "GITLAB_URL")


def glab_environment(repository: str | remote.RemoteLocation, *, prefer_environment: bool = False) -> dict[str, str]:
    """glabの子環境へ、待機では明示指定、通知では公開環境指定を優先して反映する。"""
    target = remote.parse_remote_location(repository) if isinstance(repository, str) else repository
    # SSHのポートはGit転送用であり、HTTP APIの接続先へ引き継がない。
    authority = (
        remote.RemoteLocation(target.hostname, target.project_path).authority if target.protocol == "ssh" else target.authority
    )
    env = os.environ.copy()
    inherited_host = next((env[name] for name in _GLAB_HOST_VARIABLES if env.get(name)), None)
    if prefer_environment and inherited_host:
        env["GITLAB_HOST"] = inherited_host
    elif authority is not None:
        env["GITLAB_HOST"] = authority
    if authority is not None and not prefer_environment:
        env["GITLAB_API_HOST"] = authority
    if target.protocol in {"http", "https"} and (
        not prefer_environment or not inherited_host and not (env.get("GLAB_API_PROTOCOL") or env.get("API_PROTOCOL"))
    ):
        env["GLAB_API_PROTOCOL"] = target.protocol
    return env


def glab_repository(repository: str, *, prefer_environment: bool = False) -> str:
    """ciサブコマンドにもAPIと同じ接続先を明示する。"""
    target = remote.parse_remote_location(repository)
    env = glab_environment(repository, prefer_environment=prefer_environment)
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
) -> tuple[tuple[str, ...], ...]:
    """リポジトリを指定したログ取得argvを返す。job未確定なら取得とtraceを順に返す。"""
    target = remote.parse_remote_location(repository)
    if forge == "github":
        if job:
            command = ["gh", "api", f"repos/{target.project_path}/actions/jobs/{identifier}/logs", "--allow-escape-sequences"]
            if target.hostname:
                command.extend(("--hostname", target.hostname))
            return (tuple(command),)
        repo = f"{target.authority}/{target.project_path}" if target.authority else target.project_path
        return (("gh", "run", "view", identifier, "--repo", repo, "--log-failed"),)
    repo = glab_repository(repository, prefer_environment=prefer_environment)
    assignments: list[str] = []
    environment = glab_environment(repository, prefer_environment=prefer_environment)
    if target.authority is not None and not prefer_environment:
        assignments.append(f"GITLAB_API_HOST={environment['GITLAB_API_HOST']}")
    if target.protocol in {"http", "https"}:
        protocol = environment.get("GLAB_API_PROTOCOL") or environment.get("API_PROTOCOL")
        if protocol:
            assignments.append(f"GLAB_API_PROTOCOL={protocol}")
    prefix = ("env", *assignments) if assignments else ()
    if job:
        return ((*prefix, "glab", "ci", "trace", identifier, "--repo", repo),)
    return (
        (*prefix, "glab", "ci", "get", "--repo", repo, "-p", identifier, "-d", "--status=failed", "-F", "json"),
        (*prefix, "glab", "ci", "trace", "<job ID>", "--repo", repo),
    )


def failure_next_action(
    forge: str, repository: str, identifier: str, *, job: bool = False, prefer_environment: bool = False
) -> str:
    """共通argvを引用可能な案内へ変換する。"""
    commands = failure_log_commands(forge, repository, identifier, job=job, prefer_environment=prefer_environment)
    rendered = [f"`{shlex.join(command)}`" for command in commands]
    if len(rendered) == 2:
        return f"{rendered[0]}で失敗jobのIDを取得し、{rendered[1]}の`<job ID>`を置換してログを取得し原因を調べる"
    if forge == "github" and job:
        return f"{rendered[0]}で全jobログをmanaged-tempの中のファイルへ保存し、失敗箇所を読んで原因を調べる"
    return f"{rendered[0]}で失敗ログを取得して原因を調べる"
