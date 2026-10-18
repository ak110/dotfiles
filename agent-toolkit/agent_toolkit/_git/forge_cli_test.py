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
        assert len(commands) == 1
        if job:
            assert commands[0] == (
                "gh",
                "api",
                "repos/group/repo/actions/jobs/24/logs",
                "--allow-escape-sequences",
                "--hostname",
                "github.example.com",
            )
        else:
            assert commands[0] == ("gh", "run", "view", "24", "--repo", repository, "--log-failed")
    elif job:
        assert commands == (
            ("env", "GITLAB_API_HOST=gitlab.example.com", "glab", "ci", "trace", "24", "--repo", f"https://{repository}"),
        )
    else:
        assert commands[0] == (
            "env",
            "GITLAB_API_HOST=gitlab.example.com",
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
            "env",
            "GITLAB_API_HOST=gitlab.example.com",
            "glab",
            "ci",
            "trace",
            "<job ID>",
            "--repo",
            f"https://{repository}",
        )
    action = forge_cli.failure_next_action(forge, repository, "24", job=job)
    assert all(shlex.join(command) in action for command in commands)


def test_hostless_repository_uses_glab_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """ホスト省略時は公開環境の値を保ち、API側のDNSとポート指定を継承する。"""
    monkeypatch.setenv("GITLAB_HOST", "git.example.org")
    monkeypatch.setenv("GITLAB_API_HOST", "api.example.org:8443")
    environment = forge_cli.glab_environment("group/sub/repo")
    assert environment["GITLAB_HOST"] == "git.example.org"
    assert environment["GITLAB_API_HOST"] == "api.example.org:8443"


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
    for name in ("GITLAB_HOST", "GITLAB_URI", "GITLAB_URL", "GITLAB_API_HOST", "GLAB_API_PROTOCOL", "API_PROTOCOL"):
        monkeypatch.delenv(name, raising=False)
    environment = forge_cli.glab_environment(repository)
    assert environment["GITLAB_HOST"] == host
    assert environment["GITLAB_API_HOST"] == host
    assert environment.get("GLAB_API_PROTOCOL") == protocol
    assert forge_cli.glab_repository(repository) == f"{protocol or 'https'}://{host}/group/repo"
