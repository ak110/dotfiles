"""CI失敗の通知が、検知時に取得した失敗の診断と起動契機を本文へ保持することを検証する。"""

import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk.wi import alerts
from agent_toolkit._common import process_tree


@pytest.fixture(autouse=True)
def _isolate_branch_head(monkeypatch: pytest.MonkeyPatch) -> None:
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
        expected = ["起動契機: schedule", "失敗したstep: Run tests"]
    else:
        pipeline = {"id": 24, "status": "failed", "source": "push", "sha": "old"}
        result = alerts.collect_gitlab_ci_failures(
            "gitlab.example.org/group/repo", "main", ci_list_fn=lambda *_args: [pipeline]
        )
        expected = ["起動契機: push", "script_failure"]
    body = result[0].details()
    assert all(text in body for text in expected)
    assert "job `test`" in body and "job `lint`" not in body
    assert "FAILED tests/test_x.py::test_y" in body
    assert "##[error]Process completed with exit code 2." in body
    assert "通常の出力0\n" not in body
    assert "ghp_secretvalue" not in body
    assert len(calls) == 2


def test_alert_is_saved_when_diagnostics_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"HTTP 404")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(process_tree, "run_with_timeout", run)
    run_record = {"databaseId": 21, "workflowName": "CI", "status": "completed", "conclusion": "failure"}
    result = alerts.collect_github_ci_failures("owner/repo", "main", run_list_fn=lambda *_args: [run_record])
    body = result[0].details()
    assert "失敗の診断を取得できなかった" in body and "HTTP 404" in body


def _record_commands(monkeypatch: pytest.MonkeyPatch, log: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[:3] == ["gh", "run", "view"]:
            jobs = [{"databaseId": 6, "name": "test", "conclusion": "failure", "steps": []}]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"jobs": jobs}).encode(), stderr=b"")
        return subprocess.CompletedProcess(command, 0, stdout=log.encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(process_tree, "run_with_timeout", run)
    return calls


_FAILED_RUN = {"databaseId": 21, "workflowName": "CI", "status": "completed", "conclusion": "failure"}


def test_known_failure_skips_diagnostic_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """通知済みの失敗は重複除外で除き、`gh run view`とjobログを取得しない。"""
    calls = _record_commands(monkeypatch, _FAILED_JOB_LOG)
    adopted = tmp_path / "adopted"
    adopted.mkdir()
    (adopted / "known.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\nalert_keys: github-run:21\n---\n\n本文\n", encoding="utf-8"
    )
    result = alerts.collect_new_alerts(
        "github.com/owner/repo", "main", tmp_path, forge="github", run_list_fn=lambda *_args: [_FAILED_RUN]
    )
    assert not result
    assert not calls


def test_log_excerpt_redacts_before_truncating_lines() -> None:
    token = "ghp_" + "A" * 40
    prefix = "x" * 280 + " https://"
    # 過去版にも同じ入力を渡すため、通知の取得方式に依存しない抜粋関数を検査する。
    # pylint: disable-next=protected-access
    result = alerts._log_excerpt(f"{prefix}{token}@github.com/owner/repo\n正常な行\n")
    assert result == f"        {prefix}[REDACTED]@\n        正常な行"


def test_log_excerpt_redacts_multiline_key_outside_tail() -> None:
    body = "secret-body\n" * 200
    key_type = "PRIVATE KEY"
    text = f"-----BEGIN {key_type}-----\n{body}-----END {key_type}-----\n正常な行"

    # pylint: disable-next=protected-access
    assert alerts._log_excerpt(text) == "        [REDACTED]\n        正常な行"


def test_ci_diagnostics_keep_error_after_empty_secret_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    line = "ERROR: 最初の検査が失敗した"
    boundary = "x" * 280 + " https://" + "A" * 45 + "@example.org"
    _record_commands(monkeypatch, f"EXAMPLE_TOKEN=\n{line}\n" + "後処理\n" * 20 + f"{boundary}\n")
    result = alerts.collect_github_ci_failures("github.com/owner/repo", "main", run_list_fn=lambda *_args: [_FAILED_RUN])

    assert line in result[0].details()
    assert "[REDACTED]" in result[0].details()
    assert "A" * 10 not in result[0].details()


def test_malformed_response_redacts_before_truncating(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = ["x" * 170 + " https://" + "A" * 45 + "@example.org"]
    monkeypatch.setattr(alerts._json_command, "run", lambda *_args, **_kwargs: payload)  # pylint: disable=protected-access

    with pytest.raises(alerts.AlertCollectError) as caught:
        # pylint: disable-next=protected-access
        alerts._run_alert_json_command(["gh"], timeout=1, operation="gh run view 1", expected_type=dict)
    assert "A" * 10 not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)


def test_schedule_list_malformed_response_redacts_before_truncating() -> None:
    payload = {"detail": "x" * 160 + " https://" + "A" * 45 + "@example.org"}

    with pytest.raises(alerts.AlertCollectError) as caught:
        alerts.collect_gitlab_schedule_failures("gitlab.example.org", "owner/repo", api_fn=lambda *_args: payload)
    assert "A" * 10 not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
