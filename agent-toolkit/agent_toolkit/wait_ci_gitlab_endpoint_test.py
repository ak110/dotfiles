"""CI待機の主題別テスト。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._common import json_command
from agent_toolkit._git import forge_cli
from agent_toolkit._testing.wait_ci_support import (
    _FULL_SHA,
    _assert_port_endpoint,
    _port_endpoint_options,
)
from agent_toolkit._testing.wait_ci_support import (
    _restore_signal_handlers as _restore_wait_ci_signal_handlers,  # noqa: F401  # pytestがfixtureとして収集する。
)


@pytest.mark.parametrize(
    "repository", ["http://10.230.22.47:40108/AIWG/aitools", "https://gitlab.example.com:8443/group/sub/repo"]
)
def test_port_endpoint_baseline_wait_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], repository: str
) -> None:
    """baselineから部分失敗・再開まで、一覧・bridge・downstream・jobが同じAPI接続先を使う。"""
    # 応答はGitLabの公開スキーマ https://docs.gitlab.com/api/pipelines/ と
    # https://docs.gitlab.com/api/jobs/ のid・project_id・downstream_pipeline・allow_failureを使う。
    expected_host = "10.230.22.47:40108" if repository.startswith("http:") else "gitlab.example.com:8443"
    expected_project = "AIWG%2Faitools" if repository.startswith("http:") else "group%2Fsub%2Frepo"
    monkeypatch.setenv("GITLAB_HOST", "other.example.com:9443")
    monkeypatch.setenv("GITLAB_API_HOST", "other-api.example.com:9443")
    parent_environment = os.environ.copy()
    phase = "baseline"
    calls: list[list[str]] = []

    def pipeline(identifier: int) -> dict[str, Any]:
        return {
            "id": identifier,
            "project_id": 7,
            "sha": "a" * 40,
            "status": "running" if phase == "failure" and identifier == 2 else "failed" if phase == "failure" else "success",
        }

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        _assert_port_endpoint(command, kwargs["env"], repository, expected_host)
        calls.append(command)
        if command[1] == "api" and "/ci/lint?" in command[2]:
            return subprocess.CompletedProcess(command, 0, b'{"valid":true,"jobs":[],"errors":[]}', b"")
        if command[1:3] == ["ci", "list"]:
            payload: object = [pipeline(1 if phase == "baseline" else 2)]
        else:
            endpoint = command[2]
            assert endpoint.startswith(f"projects/{expected_project}/pipelines/")
            if "/bridges?" in endpoint:
                payload = [{"downstream_pipeline": {"id": 3, "project_id": 7}}] if "/2/" in endpoint else []
            elif "/jobs?" in endpoint:
                payload = [
                    {"id": 100, "name": "test", "allow_failure": False, "status": "failed" if phase == "failure" else "success"}
                ]
            else:
                payload = pipeline(3)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(json_command.subprocess, "run", run)
    monkeypatch.setattr(wait_ci, "_resolve_sha", lambda *_args: "a" * 40)
    baseline = tmp_path / "baseline.json"
    options = _port_endpoint_options(repository)
    assert wait_ci.main(["--write-baseline", str(baseline), *options]) == 0
    phase = "failure"
    assert wait_ci.main(["--baseline", str(baseline), *options]) == 6
    assert forge_cli.failure_next_action("gitlab", repository, "100", job=True) in capsys.readouterr().err
    phase = "success"
    assert wait_ci.main(["--baseline", str(baseline), *options]) == 0
    assert os.environ == parent_environment
    assert any(command[2].endswith("/3") for command in calls if command[1] == "api")
    assert any("/jobs?" in command[2] for command in calls if command[1] == "api")


@pytest.mark.parametrize(
    "repository", ["http://10.230.22.47:40108/AIWG/aitools", "https://gitlab.example.com:8443/group/sub/repo"]
)
@pytest.mark.parametrize("case", ["success", "failed", "partial", "running"])
def test_port_endpoint_wait_sha_covers_downstream_states(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], repository: str, case: str
) -> None:
    """ポート付きSHA待機が親・下流・jobを取得し、成功・終了失敗・部分失敗・未終了を区別する。"""
    expected_host = "10.230.22.47:40108" if repository.startswith("http:") else "gitlab.example.com:8443"
    project = "projects/AIWG%2Faitools" if repository.startswith("http:") else "projects/group%2Fsub%2Frepo"
    monkeypatch.setenv("GITLAB_HOST", "other.example.com:9443")
    monkeypatch.setenv("GITLAB_API_HOST", "other-api.example.com:9443")
    parent_environment = os.environ.copy()
    calls: list[list[str]] = []

    def pipeline(identifier: int) -> dict[str, Any]:
        return {
            "id": identifier,
            "project_id": 7,
            "sha": _FULL_SHA,
            "status": (
                {"success": "success", "failed": "failed", "partial": "failed", "running": "running"}[case]
                if identifier == 2
                else "running"
                if case == "partial"
                else "success"
            ),
        }

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        # GitLab Pipelines/Jobs APIの公開fieldを、CLI子プロセスの境界で応答する。
        _assert_port_endpoint(command, kwargs["env"], repository, expected_host)
        calls.append(command)
        if command[1] == "api" and "/ci/lint?" in command[2]:
            return subprocess.CompletedProcess(command, 0, b'{"valid":true,"jobs":[],"errors":[]}', b"")
        if command[1:3] == ["ci", "list"]:
            assert command[command.index("--sha") + 1] == _FULL_SHA
            assert command[command.index("--ref") + 1] == "main"
            payload: object = [pipeline(1)]
        else:
            endpoint = command[2]
            assert endpoint.startswith(f"{project}/pipelines/")
            if endpoint == f"{project}/pipelines/1/bridges?per_page=100":
                payload = [{"downstream_pipeline": {"id": 2, "project_id": 7}}]
            elif endpoint == f"{project}/pipelines/2/bridges?per_page=100":
                payload = []
            elif endpoint == f"{project}/pipelines/2":
                payload = pipeline(2)
            else:
                assert endpoint in {
                    f"{project}/pipelines/{identifier}/jobs?include_retried=false&per_page=100" for identifier in (1, 2)
                }
                identifier = int(endpoint.split("/")[-2])
                payload = [
                    {
                        "id": 100 + identifier,
                        "name": f"test-{identifier}",
                        "allow_failure": False,
                        "status": pipeline(identifier)["status"],
                    }
                ]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(json_command.subprocess, "run", run)
    assert (
        wait_ci.main(
            [
                "--wait-sha",
                _FULL_SHA,
                *_port_endpoint_options(repository),
                "--timeout",
                "0.03",
            ]
        )
        == {
            "success": wait_ci.EXIT_SUCCESS,
            "failed": wait_ci.EXIT_CI_FAILED,
            "partial": wait_ci.EXIT_NEW_FAILURE,
            "running": wait_ci.EXIT_TIMEOUT,
        }[case]
    )
    endpoints = {command[2] for command in calls if command[1] == "api"}
    assert {
        f"{project}/pipelines/1/bridges?per_page=100",
        f"{project}/pipelines/2/bridges?per_page=100",
        f"{project}/pipelines/2",
        f"{project}/pipelines/1/jobs?include_retried=false&per_page=100",
        f"{project}/pipelines/2/jobs?include_retried=false&per_page=100",
    } <= endpoints
    assert os.environ == parent_environment
    output = capsys.readouterr()
    if case == "partial":
        assert forge_cli.failure_next_action("gitlab", repository, "102", job=True) in output.err
    elif case == "failed":
        assert forge_cli.failure_next_action("gitlab", repository, "2") in output.err
        assert "job test-2: failure" in output.out
    elif case == "running":
        assert "タイムアウト" in output.err
