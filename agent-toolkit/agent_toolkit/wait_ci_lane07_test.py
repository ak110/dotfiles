"""`wait_ci`のdownstream pipelineとbaseline無し待機の回帰テスト。"""

from __future__ import annotations

from unittest import mock

from agent_toolkit import wait_ci


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

    def fake_run(command, _timeout, _description):
        commands.append(command)
        return responses.pop(0)

    monkeypatch.setattr(wait_ci, "_run_forge_json_command", fake_run)

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


def test_main_wait_sha_uses_all_runs_and_initializes_utf8_first(monkeypatch) -> None:
    """完全長SHAモードはbaselineを除外せず、argparse前にUTF-8を初期化する。"""
    events: list[str] = []
    waited = mock.Mock(return_value=wait_ci.EXIT_SUCCESS)
    monkeypatch.setattr(wait_ci._outcome, "force_utf8_stdio", lambda: events.append("utf8"))  # pylint: disable=protected-access
    monkeypatch.setattr(wait_ci, "_install_signal_handlers", lambda: events.append("signals"))
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
    assert events == ["utf8", "signals"]
    assert waited.call_args.args[0] == "a" * 40
    assert waited.call_args.kwargs["baseline_ids"] == frozenset()
