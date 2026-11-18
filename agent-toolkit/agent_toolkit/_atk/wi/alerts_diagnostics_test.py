"""CI失敗の通知が、検知時に取得した失敗の診断と起動契機を本文へ保持することを検証する。"""

import json
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk.wi import alerts
from agent_toolkit._common import process_tree


@pytest.fixture(autouse=True)
def _isolate_branch_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """HEADの取得は本書の対象外のため外部通信を隔離し、診断の取得は実際の処理を通す。"""
    monkeypatch.setattr(alerts, "_github_head", lambda _repo, _branch: "current-head")
    monkeypatch.setattr(alerts, "_gitlab_head", lambda _repo, _branch: "current-head")


_FAILED_JOB_LOG = (
    "2026-11-18T00:00:00Z ##[group]Run make test\n"
    "2026-11-18T00:00:01Z FAILED tests/test_x.py::test_y - AssertionError: 期待と異なる\n"
    + "".join(f"2026-11-18T00:00:02Z 通常の出力{index}\n" for index in range(200))
    + "2026-11-18T00:00:03Z env GH_TOKEN=ghp_secretvalue\n"
    "2026-11-18T00:00:04Z ##[error]Process completed with exit code 2.\n"
)


@pytest.mark.parametrize("forge", ["github", "gitlab"])
def test_alert_body_keeps_failure_diagnostics_and_trigger(monkeypatch: pytest.MonkeyPatch, forge: str) -> None:
    """検知時に取得した失敗job・step・ログの診断と起動契機を本文へ保持し、秘匿値と大量の通常出力は転載しない。"""
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[:3] == ["gh", "run", "view"]:
            jobs = [
                {"databaseId": 5, "name": "lint", "conclusion": "success", "steps": []},
                {
                    "databaseId": 6,
                    "name": "test",
                    "conclusion": "failure",
                    "steps": [{"name": "Checkout", "conclusion": "success"}, {"name": "Run tests", "conclusion": "failure"}],
                },
            ]
            stdout = json.dumps({"jobs": jobs}).encode()
        elif command[:3] == ["glab", "ci", "get"]:
            jobs = [{"id": 6, "name": "test", "stage": "test", "status": "failed", "failure_reason": "script_failure"}]
            stdout = json.dumps({"jobs": jobs}).encode()
        elif command[:2] == ["gh", "api"] or command[:3] == ["glab", "ci", "trace"]:
            assert "6" in " ".join(command)
            stdout = _FAILED_JOB_LOG.encode()
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(process_tree, "run_with_timeout", run)
    if forge == "github":
        run_record = {
            "databaseId": 21,
            "workflowName": "CI",
            "status": "completed",
            "conclusion": "failure",
            "event": "schedule",
            "headSha": "old",
        }
        result = alerts.collect_github_ci_failures("github.com/owner/repo", "main", run_list_fn=lambda *_args: [run_record])
        assert "起動契機: schedule" in result[0].body
        assert "失敗したstep: Run tests" in result[0].body
    else:
        pipeline = {"id": 24, "status": "failed", "source": "push", "sha": "old"}
        result = alerts.collect_gitlab_ci_failures(
            "gitlab.example.org/group/repo", "main", ci_list_fn=lambda *_args: [pipeline]
        )
        assert "起動契機: push" in result[0].body
        assert "script_failure" in result[0].body
    body = result[0].body
    assert "job `test`" in body and "job `lint`" not in body
    assert "FAILED tests/test_x.py::test_y" in body
    assert "##[error]Process completed with exit code 2." in body
    assert "通常の出力0\n" not in body
    assert "ghp_secretvalue" not in body
    assert len(calls) == 2


def test_alert_is_saved_when_diagnostics_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """診断を取得できなくても通知は作り、取得できなかった理由を本文へ残す。"""

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"HTTP 404")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(process_tree, "run_with_timeout", run)
    run_record = {"databaseId": 21, "workflowName": "CI", "status": "completed", "conclusion": "failure"}
    result = alerts.collect_github_ci_failures("owner/repo", "main", run_list_fn=lambda *_args: [run_record])
    assert "失敗の診断を取得できなかった" in result[0].body and "HTTP 404" in result[0].body
