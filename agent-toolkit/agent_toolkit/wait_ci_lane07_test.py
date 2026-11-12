"""`wait_ci`のdownstream pipelineとbaseline無し待機の回帰テスト。"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from collections.abc import Callable
from unittest import mock

from agent_toolkit import wait_ci
from agent_toolkit._atk import wait_ci_forge


def test_gitlab_pipeline_list_includes_same_project_downstream(monkeypatch) -> None:
    """同一projectの子pipelineだけを実行集合へ展開する。"""
    responses = [
        [{"id": 10, "project_id": 20, "status": "success", "sha": "a" * 40}],
        [
            {"downstream_pipeline": {"id": 11, "project_id": 20}},
            {"downstream_pipeline": {"id": 12, "project_id": 99}},
        ],
        {"id": 11, "project_id": 20, "status": "failed", "sha": "a" * 40},
        [
            {"downstream_pipeline": {"id": 13, "project_id": 20}},
            {"downstream_pipeline": {"id": 10, "project_id": 20}},
        ],
        {"id": 13, "project_id": 20, "status": "success", "sha": "a" * 40},
        [],
    ]
    commands: list[list[str]] = []

    def fake_run(command, _timeout, _description, **_kwargs: object):
        commands.append(command)
        return responses.pop(0)

    monkeypatch.setattr(wait_ci_forge, "_run_forge_json_command", fake_run)

    runs = wait_ci._glab_pipeline_list(  # pylint: disable=protected-access
        "gitlab.example.com/group/project",
        "refs/heads/main",
        "a" * 40,
        60.0,
    )

    assert [run["databaseId"] for run in runs] == [10, 11, 13]
    assert runs[1]["conclusion"] == "failure"
    assert any("/pipelines/10/bridges?" in part for part in commands[1])
    assert any("/pipelines/11" in part for part in commands[2])
    assert any("/pipelines/11/bridges?" in part for part in commands[3])
    assert any("/pipelines/13" in part for part in commands[4])


def test_main_wait_sha_uses_all_runs(monkeypatch) -> None:
    """完全長SHAモードは正規化したSHAの全runをbaseline無しで待つ。"""
    waited = mock.Mock(return_value=wait_ci.EXIT_SUCCESS)
    monkeypatch.setattr(wait_ci, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(wait_ci, "wait_for_ci", waited)

    result = wait_ci.main(
        [
            "--wait-sha",
            "A" * 40,
            "--forge=github",
            "--repo",
            "github.com/owner/repository",
            "--ref",
            "refs/heads/main",
            "--source-ref",
            "HEAD",
        ]
    )

    assert result == wait_ci.EXIT_SUCCESS
    assert waited.call_args.args[0] == "a" * 40
    assert waited.call_args.kwargs["baseline_ids"] == frozenset()


def test_main_emits_utf8_in_non_utf8_environment(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """ASCII標準出力で起動しても、公開CLIの引数エラーは日本語をUTF-8で出力する。"""
    environment = host_environ()
    environment.update(PYTHONIOENCODING="ascii", PYTHONUTF8="0")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(wait_ci.__file__).resolve()), "--wait-sha", "invalid"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == wait_ci.EXIT_USAGE
    assert result.stdout == b""
    error = result.stderr.decode("utf-8")
    assert "40桁の完全長commit SHAを指定してください" in error
    assert "次の操作:" in error
    assert "UnicodeEncodeError" not in error
