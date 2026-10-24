"""CI待機と通知が共有するforge起動・ログ取得の契約を検証する。"""

import shlex

import pytest

from agent_toolkit._git import forge_cli


@pytest.mark.parametrize("forge,job", [("github", False), ("github", True), ("gitlab", False), ("gitlab", True)])
def test_failure_commands_are_explicit_and_noninteractive(forge: str, job: bool) -> None:
    """共通案内が対象と識別子を指定し、job未確定時は取得とtraceの順になる。"""
    repository = f"{forge}.example.com/group/repo"
    commands = forge_cli.failure_log_commands(forge, repository, "24", job=job)
    if forge == "github":
        job_log = ("gh", "api", "repos/group/repo/actions/jobs/{}/logs", "--allow-escape-sequences")
        job_log += ("--hostname", "github.example.com")
        if job:
            assert commands == (tuple(part.format("24") for part in job_log),)
        else:
            # 64KiB超の行で後続を黙って欠落させる`--log-failed`を案内せず、job IDを経由して全ログを取得させる。
            assert commands == (
                ("gh", "run", "view", "24", "--repo", repository, "--json", "jobs", "--jq", forge_cli.GITHUB_JOB_LIST),
                tuple(part.format("<job ID>") for part in job_log),
            )
        assert not any("--log-failed" in command for command in commands)
    elif job:
        assert commands == (("glab", "ci", "trace", "24", "--repo", f"https://{repository}"),)
    else:
        assert commands[0] == (
            "glab",
            "ci",
            "get",
            "--repo",
            "https://gitlab.example.com/group/repo",
            "-p",
            "24",
            "-d",
            "--status=failed",
            "-F",
            "json",
        )
        assert commands[1] == (
            "glab",
            "ci",
            "trace",
            "<job ID>",
            "--repo",
            f"https://{repository}",
        )
    action = forge_cli.failure_next_action(forge, repository, "24", job=job)
    assert all(shlex.join(command) in action for command in commands)


@pytest.mark.parametrize(
    "repository", ["group/sub/repo", "git.example.org/group/sub/repo", "ssh://git@git.example.org:2222/group/sub/repo.git"]
)
def test_repository_selector_preserves_api_environment(monkeypatch: pytest.MonkeyPatch, repository: str) -> None:
    """識別用のrepositoryはAPI側のDNS・ポート・protocolの環境指定を継承する。"""
    monkeypatch.setenv("GITLAB_HOST", "git.example.org")
    monkeypatch.setenv("GITLAB_API_HOST", "api.example.org:8443")
    monkeypatch.setenv("GLAB_API_PROTOCOL", "http")
    environment = forge_cli.glab_environment(repository)
    assert environment["GITLAB_HOST"] == "git.example.org"
    assert environment["GITLAB_API_HOST"] == "api.example.org:8443"
    assert environment["GLAB_API_PROTOCOL"] == "http"


@pytest.mark.parametrize(
    "variables,expected",
    [
        ({"GL_HOST": "third.example.org"}, "third.example.org"),
        ({"GITLAB_URI": "second.example.org", "GL_HOST": "third.example.org"}, "second.example.org"),
        (
            {"GITLAB_HOST": "first.example.org", "GITLAB_URI": "second.example.org", "GL_HOST": "third.example.org"},
            "first.example.org",
        ),
    ],
)
def test_public_host_environment_priority(monkeypatch: pytest.MonkeyPatch, variables: dict[str, str], expected: str) -> None:
    """glab config --helpのhost変数の優先順を、通知とログ案内で保持する。"""
    for name in ("GITLAB_HOST", "GITLAB_URI", "GL_HOST", "GITLAB_API_HOST", "GLAB_API_PROTOCOL", "API_PROTOCOL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    repository = "http://unused.example.org:1234/group/repo"
    environment = forge_cli.glab_environment(repository, prefer_environment=True)
    assert environment["GITLAB_HOST"] == expected
    assert "GITLAB_API_HOST" not in environment
    assert "GLAB_API_PROTOCOL" not in environment
    assert forge_cli.failure_log_commands("gitlab", repository, "9", job=True, prefer_environment=True) == (
        ("glab", "ci", "trace", "9", "--repo", f"https://{expected}/group/repo"),
    )


@pytest.mark.parametrize(
    "repository,host,protocol",
    [
        ("http://git.example.org:40108/group/repo", "git.example.org:40108", "http"),
        ("https://git.example.org/group/repo", "git.example.org", "https"),
        ("ssh://git@git.example.org:2222/group/repo.git", "git.example.org", None),
    ],
)
def test_api_endpoint_distinguishes_http_and_ssh_ports(
    monkeypatch: pytest.MonkeyPatch, repository: str, host: str, protocol: str | None
) -> None:
    """HTTPのポートを保持し、SSH転送用のポートをAPIへ転用しない。"""
    for name in ("GITLAB_HOST", "GITLAB_URI", "GL_HOST", "GITLAB_API_HOST", "GLAB_API_PROTOCOL", "API_PROTOCOL"):
        monkeypatch.delenv(name, raising=False)
    environment = forge_cli.glab_environment(repository)
    assert environment["GITLAB_HOST"] == host
    assert environment.get("GITLAB_API_HOST") == (host if protocol else None)
    assert environment.get("GLAB_API_PROTOCOL") == protocol
    assert forge_cli.glab_repository(repository) == f"{protocol or 'https'}://{host}/group/repo"
