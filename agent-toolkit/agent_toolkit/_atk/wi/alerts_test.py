"""`alerts`モジュールのテスト。公開APIへ外部コマンドの代用を注入して検証する。"""

import argparse
import contextlib
import datetime
import http.server
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk, wait_ci
from agent_toolkit._atk.wi import (
    alerts,  # noqa: E402  # pylint: disable=wrong-import-position
    process_loop_alerts,
)
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._common import json_command as _json_command  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._git import forge_cli
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.isolation import restore_host_environment

_github_head = alerts._github_head  # pylint: disable=protected-access
_gitlab_head = alerts._gitlab_head  # pylint: disable=protected-access


@pytest.fixture(name="api_branch_heads")
def _api_branch_heads(monkeypatch: pytest.MonkeyPatch) -> None:
    """API境界の結合試験では実際のHEAD取得処理を使う。"""
    monkeypatch.setattr(alerts, "_github_head", _github_head)
    monkeypatch.setattr(alerts, "_gitlab_head", _gitlab_head)


@pytest.fixture(autouse=True)
def _isolate_branch_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """一覧を注入する既存試験ではHEADの外部通信も隔離する。"""
    monkeypatch.setattr(alerts, "_github_head", lambda _repo, _branch: "current-head")
    monkeypatch.setattr(alerts, "_gitlab_head", lambda _repo, _branch: "current-head")


def test_collect_github_ci_failures_latest_completed_only() -> None:
    """ワークフローごとに直近の完了runだけを確認し、失敗している場合だけアラート候補を返す。"""
    runs = [
        {"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 2},
        {"workflowName": "CI", "status": "completed", "conclusion": "success", "databaseId": 1},
        {"workflowName": "Docs", "status": "completed", "conclusion": "success", "databaseId": 3},
    ]
    result = alerts.collect_github_ci_failures("owner/repo", "master", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:2",)]


def test_collect_github_ci_failures_skips_in_progress_latest() -> None:
    """進行中runを除外し、直近の完了runを判定する。"""
    runs = [
        {"workflowName": "CI", "status": "in_progress", "databaseId": 3},
        {"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 2},
    ]
    result = alerts.collect_github_ci_failures("owner/repo", "master", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:2",)]


def test_collect_github_ci_failures_keeps_schedule_failure_after_push_success() -> None:
    """同じワークフローの`push`の成功が後にあっても、`schedule`の直近完了runの失敗をアラート候補として返す。

    `event`は`alerts._run_gh_run_list`が`gh run list --json`で取得する公開フィールドである。
    """
    runs = [
        {"workflowName": "Audit", "event": "push", "status": "completed", "conclusion": "success", "databaseId": 5},
        {"workflowName": "Audit", "event": "schedule", "status": "completed", "conclusion": "failure", "databaseId": 4},
        {"workflowName": "Audit", "event": "schedule", "status": "completed", "conclusion": "success", "databaseId": 3},
    ]
    result = alerts.collect_github_ci_failures("owner/repo", "master", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:4",)]


def _gitlab_schedule_api(endpoints: list[str]) -> alerts.GlabApiFn:
    """Pipeline Scheduleの一覧と個別の取得へ応答する`glab api`の代用を返す。

    応答の形は2026年10月6日の`glab api 'projects/:id/pipeline_schedules'`（一覧は`active`・`ref`・`cron`を持ち
    `last_pipeline`を持たない）と`pipeline_schedules/<ID>`（`last_pipeline`の`id`・`status`・`web_url`）の観測から写した。
    """
    project = "projects/group%2Fsub%2Frepo/pipeline_schedules"
    responses: dict[str, object] = {
        f"{project}?per_page=100": [
            {
                "id": 494,
                "description": "model deprecation weekday check",
                "ref": "develop",
                "cron": "30 8 * * 1-5",
                "active": True,
            },
            {"id": 523, "description": "daily log monitor", "ref": "develop", "cron": "0 12 * * *", "active": True},
            {"id": 9, "description": "retired", "ref": "develop", "cron": "0 0 * * *", "active": False},
        ],
        f"{project}/494": {"id": 494, "last_pipeline": {"id": 437746, "status": "success", "ref": "develop", "web_url": "u1"}},
        f"{project}/523": {"id": 523, "last_pipeline": {"id": 437813, "status": "failed", "ref": "develop", "web_url": "u2"}},
        "projects/group%2Fsub%2Frepo/repository/branches/develop": {"commit": {"id": "current-head"}},
    }

    def api(host: str, endpoint: str) -> object:
        assert host == "gitlab.example.com"
        endpoints.append(endpoint)
        return responses[endpoint]

    return api


def test_collect_new_alerts_reports_schedule_failure_hidden_by_later_pipeline(tmp_path: pathlib.Path) -> None:
    """後に起動したpushのパイプラインが実行中でも、有効なスケジュールの失敗したパイプラインを1件のアラート候補として返す。

    無効なスケジュールは照会しない。ブランチの直近1件だけを確かめると、失敗が後の実行に隠れてAWIへ入らない。
    """
    endpoints: list[str] = []
    branch_pipelines = [{"id": 437860, "status": "running"}, {"id": 437813, "status": "failed"}]
    result = alerts.collect_new_alerts(
        "gitlab.example.com/group/sub/repo",
        "develop",
        tmp_path,
        forge="gitlab",
        ci_list_fn=lambda _r, _b: branch_pipelines,
        glab_api_fn=_gitlab_schedule_api(endpoints),
    )
    assert [alert.keys for alert in result] == [("gitlab-pipeline:437813",)]
    assert "daily log monitor" in result[0].body
    assert not any(endpoint.endswith("/9") for endpoint in endpoints)


def test_check_and_submit_alerts_submits_one_awi_per_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """ブランチの確認と定期実行の確認が同じパイプラインを返しても1件だけ投入し、次の確認では投入しない。"""
    notes = tmp_path / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)

    def submit() -> alerts.AlertCheckResult:
        return alerts.check_and_submit_alerts(
            notes,
            "gitlab.example.com/group/sub/repo",
            tmp_path / "repo",
            forge="gitlab",
            now=datetime.datetime(2026, 1, 1),
            git_fn=lambda _p, args: (
                "origin" if args == ["remote"] else ("refs/remotes/origin/develop" if args[0] == "symbolic-ref" else None)
            ),
            ci_list_fn=lambda _r, _b: [{"id": 437813, "status": "failed"}],
            glab_api_fn=_gitlab_schedule_api([]),
        )

    assert submit().submitted == 1
    assert submit().submitted == 0
    assert list(_saved_awis_by_heading(notes)) == ["# パイプライン437813失敗"]


def test_collect_gitlab_ci_failures_only_when_latest_failed() -> None:
    """最新パイプラインが失敗した場合だけアラート候補を返す。"""
    success = [{"id": 1, "status": "success"}]
    assert not alerts.collect_gitlab_ci_failures("owner/repo", "master", ci_list_fn=lambda _r, _b: success)
    failed = [{"id": 2, "status": "failed", "web_url": "u", "sha": "abc12345"}]
    result = alerts.collect_gitlab_ci_failures("owner/repo", "master", ci_list_fn=lambda _r, _b: failed)
    assert [alert.keys for alert in result] == [("gitlab-pipeline:2",)]


def test_resolve_target_branch_paths() -> None:
    """追跡先を優先し、追跡先が無ければ`origin/HEAD`が指すbranchを使い、どちらも無ければNoneを返す。"""

    def upstream_git(_path: pathlib.Path, args: list[str]) -> str | None:
        if args[0] == "rev-parse":
            return "refs/remotes/origin/feature/foo"
        if args == ["remote"]:
            return "origin"
        raise AssertionError(args)

    assert alerts.resolve_target_branch(pathlib.Path("/repo"), git_fn=upstream_git) == "feature/foo"

    def fallback_git(_path: pathlib.Path, args: list[str]) -> str | None:
        if args[0] == "rev-parse":
            return None
        if args == ["remote"]:
            return "origin"
        return "refs/remotes/origin/master"

    assert alerts.resolve_target_branch(pathlib.Path("/repo"), git_fn=fallback_git) == "master"
    assert alerts.resolve_target_branch(pathlib.Path("/repo"), git_fn=lambda _p, _a: None) is None


def test_collect_new_alerts_filters_keys_per_repository(tmp_path: pathlib.Path) -> None:
    """同一リポジトリの既出キーだけを重複除外する。"""
    notes = tmp_path / "private-notes"
    adopted = notes / "adopted"
    adopted.mkdir(parents=True)
    (adopted / "other.md").write_text(
        "---\ntarget_repo: github.com/other/repo\ntype: awi\nalert_keys: github-run:21\n---\n\n本文\n",
        encoding="utf-8",
    )
    runs = [{"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]
    result = alerts.collect_new_alerts("github.com/owner/repo", "main", notes, forge="github", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:21",)]
    (adopted / "same.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\nalert_keys: github-run:21\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert not alerts.collect_new_alerts(
        "github.com/owner/repo", "main", notes, forge="github", run_list_fn=lambda _r, _b: runs
    )


def test_existing_alert_keys_parses_absent_multiple_and_empty(tmp_path: pathlib.Path) -> None:
    """`alert_keys`未指定・カンマ区切り複数・空文字列の各書式を公開関数経由で検証する。

    `_parse_alert_keys`はモジュール非公開のため、フロントマターを持つAWIファイルを
    実際に配置して`existing_alert_keys`経由で検証する
    （`agent-toolkit:writing-standards`の`references/testing.md`「private関数の直接テスト禁止」節に従う）。
    """
    notes = tmp_path / "private-notes"
    inbox = notes / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "absent.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == set()

    (inbox / "absent.md").unlink()
    (inbox / "multiple.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\n"
        "alert_keys: github-dependabot:21, github-dependabot:22\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == {
        "github-dependabot:21",
        "github-dependabot:22",
    }

    (inbox / "multiple.md").unlink()
    (inbox / "empty.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\nalert_keys: \n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == set()


def _prepare_alert_submission(monkeypatch: pytest.MonkeyPatch, notes: pathlib.Path) -> None:
    """外部更新を無効化し、保存した本文を確認できるAWI領域を準備する。"""
    (notes / "inbox").mkdir(parents=True)

    def no_repo_lock(*_args: object, **_kwargs: object) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def no_repository_update(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(_wi_sync, "repo_lock", no_repo_lock)
    monkeypatch.setattr(_wi_sync, "pull", no_repository_update)
    monkeypatch.setattr(_wi_sync, "commit_and_push", no_repository_update)


def _saved_awis_by_heading(notes: pathlib.Path) -> dict[str, str]:
    """保存された通常AWIをH1ごとに返す。"""
    awis: dict[str, str] = {}
    for _path, _repo, content, _state, _kind in _wi_entries.iter_entries(notes, ("inbox",), None, "awi"):
        heading = next(line for line in content.splitlines() if line.startswith("# "))
        awis[heading] = content
    return awis


def test_check_and_submit_alerts_invokes_add_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """新規アラートをAWIへ投入し、件数とfrontmatterを返す。"""
    monkeypatch.setenv("AI_AGENT", "1")
    notes = tmp_path / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)
    runs = [{"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]
    count = alerts.check_and_submit_alerts(
        notes,
        "github.com/owner/repo",
        tmp_path / "repo",
        forge="github",
        now=datetime.datetime(2026, 1, 1),
        git_fn=lambda _p, args: (
            "origin"
            if args == ["remote"]
            else ("refs/remotes/origin/main" if args == ["symbolic-ref", "refs/remotes/origin/HEAD"] else None)
        ),
        run_list_fn=lambda _r, _b: runs,
    )
    assert count.submitted == 1
    content = next(iter(_saved_awis_by_heading(notes).values()))
    assert "alert_keys: github-run:21" in content
    assert "source: alert-monitor" in content
    assert "# ワークフローCI失敗" in content
    assert "## 反映内容と反映先" in content
    assert "反映先は`github.com/owner/repo`とする。" in content
    assert "## メリット" not in content
    assert "## デメリット" not in content
    assert "調査と変更の検証に作業が必要になる。" not in content
    assert "## 完成条件" in content


def test_check_and_submit_alerts_writes_kind_specific_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開された収集コマンドがアラート種別ごとに外部から観測できる完成条件を保存する。"""
    notes = tmp_path / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)

    def git_fn(_path: pathlib.Path, args: list[str]) -> str | None:
        if args == ["remote"]:
            return "origin"
        if args == ["symbolic-ref", "refs/remotes/origin/HEAD"]:
            return "refs/remotes/origin/main"
        return None

    github_count = alerts.check_and_submit_alerts(
        notes,
        "github.com/owner/repo",
        tmp_path / "github-repo",
        forge="github",
        now=datetime.datetime(2026, 1, 1),
        git_fn=git_fn,
        run_list_fn=lambda _repo, _branch: [
            {"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 100}
        ],
    )
    gitlab_count = alerts.check_and_submit_alerts(
        notes,
        "gitlab.com/owner/repo",
        tmp_path / "gitlab-repo",
        forge="gitlab",
        now=datetime.datetime(2026, 1, 1),
        git_fn=git_fn,
        ci_list_fn=lambda _repo, _branch: [{"status": "failed", "id": 200}],
        glab_api_fn=lambda _host, _endpoint: [],
    )

    assert github_count.submitted == 1
    assert gitlab_count.submitted == 1
    awis = _saved_awis_by_heading(notes)
    workflow_completion = (
        "## 完成条件\n\n対象ワークフロー`CI`の失敗が解消し、ブランチ`main`でそのワークフローが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    pipeline_completion = (
        "## 完成条件\n\n対象パイプライン`200`の失敗が解消し、ブランチ`main`で後続のパイプラインが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    assert workflow_completion in awis["# ワークフローCI失敗"]
    assert pipeline_completion in awis["# パイプライン200失敗"]
    assert set(awis) == {"# ワークフローCI失敗", "# パイプライン200失敗"}


def test_check_and_submit_alerts_returns_zero_when_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """新規アラートが無い場合は投入しない。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    calls: list[int] = []

    def fake_add(*_args: object, **_kwargs: object) -> list[str]:
        calls.append(1)
        return []

    monkeypatch.setattr(alerts._add, "add_entries", fake_add)  # pylint: disable=protected-access
    count = alerts.check_and_submit_alerts(
        notes,
        "github.com/owner/repo",
        tmp_path / "repo",
        forge="github",
        now=datetime.datetime(2026, 1, 1),
        git_fn=lambda _p, _a: None,
    )
    assert count.submitted == 0
    assert not calls


def test_collect_new_alerts_warns_on_generic_failure(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CI状態の取得失敗は待機を止めずに警告を出力する。"""

    def failing_fn(_repo: str, _branch: str) -> list[dict]:
        raise alerts.AlertCollectError("gh run list（o/r）が失敗しました（exit=1）")

    result = alerts.collect_new_alerts(
        "github.com/o/r",
        "main",
        tmp_path,
        forge="github",
        run_list_fn=failing_fn,
    )
    assert not result
    stderr = capsys.readouterr().err
    assert "GitHub CI状態の取得に失敗しました" in stderr
    # 認証の確認手段を次の操作として続ける。
    assert "gh auth status" in stderr.split("\n次の操作: ", 1)[1]


def test_collect_new_alerts_decodes_utf8_json_bytes_without_locale_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開された収集コマンドが非ASCIIのUTF-8 JSON bytesをアラートへ変換する。"""
    payload = [{"workflowName": "日本語CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "text" not in _kwargs
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload, ensure_ascii=False).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    result = alerts.collect_new_alerts("github.com/owner/repo", "main", tmp_path, forge="github")

    assert [alert.keys for alert in result] == [("github-run:21",)]
    assert "日本語CI" in result[0].body


def test_collect_new_alerts_warns_when_json_stdout_is_not_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開された収集コマンドが不正UTF-8 stdoutを原因付き収集エラーとして警告する。"""

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, stdout=b"\xff", stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    assert not alerts.collect_new_alerts("github.com/owner/repo", "main", tmp_path, forge="github")
    assert "標準出力をUTF-8としてデコードできません" in capsys.readouterr().err


def test_collect_new_alerts_keeps_non_utf8_stderr_as_bytes_notation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開された収集コマンドが非UTF-8診断bytesを警告へ残す。"""

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"failed: \x81")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    assert not alerts.collect_new_alerts("github.com/owner/repo", "main", tmp_path, forge="github")
    assert "\\x81" in capsys.readouterr().err


def _prepare_alert_cli(
    monkeypatch: pytest.MonkeyPatch, root: pathlib.Path, *, forge: str = "github", branch: bool = True
) -> tuple[pathlib.Path, pathlib.Path]:
    """実Git対象と保存領域を作成し、private-notesの外部同期だけを隔離する。"""
    host = "github.com" if forge == "github" else "gitlab.example.com"
    repo = git_repository.init_repository(root / "repo", origin=f"https://{host}/owner/repo.git")
    if branch:
        git_repository.run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    notes = root / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    monkeypatch.setattr(alerts, "_now_iso", lambda: "2026-01-01T00:00:00+00:00")
    return repo, notes


def _forge_responses(monkeypatch: pytest.MonkeyPatch, response: Callable[[list[str]], object], calls: list[list[str]]) -> None:
    """forgeの公開コマンドだけを差し替え、対象解決のGit操作は実行する。"""
    real_run = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if command[0] not in {"gh", "glab"}:
            return real_run(command, check=kwargs.pop("check", False), **kwargs)
        calls.append(command)
        payload = response(command)
        if isinstance(payload, Exception):
            raise payload
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", run)


def _invoke_alert_cli(repo: pathlib.Path | None = None, *, forge: str = "auto") -> int:
    """公開CLIを呼び、プロセスの終了値を検収する。"""
    argv = ["wi", "check-alerts", "--forge", forge]
    if repo is not None:
        argv.extend(("--target-repo", str(repo)))
    with pytest.raises(SystemExit) as result:
        atk.main(argv, now=datetime.datetime(2026, 1, 1))
    assert isinstance(result.value.code, int)
    return result.value.code


def _invoke_loop_alerts(repo: pathlib.Path, notes: pathlib.Path, forge: str) -> int:
    """常駐側の公開監視処理を呼び、process-wiの1回の実行とDependabot監査を起動しない。"""
    args = argparse.Namespace(no_alerts=False, alert_interval=0, alert_forge=forge)
    host = "github.com" if forge == "github" else "gitlab.example.com"
    return process_loop_alerts.check_process_loop_alerts(args, notes, f"{host}/owner/repo", repo, None, count_dependabot=False)[
        1
    ]


@pytest.mark.parametrize("mode", ["single", "loop"])
@pytest.mark.parametrize("authority", ["gitlab.example.com:40108", "gitlab.example.com"])
def test_local_origin_preserves_monitor_api_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, mode: str, authority: str
) -> None:
    """単発・常駐監視の一覧と詳細が、ポートを含むoriginのHTTP接続先を使う。"""
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path, forge="gitlab")
    origin = f"http://{authority}/owner/repo.git"
    git_repository.run_git(repo, "remote", "set-url", "origin", origin)
    for name in ("GITLAB_HOST", "GITLAB_URI", "GL_HOST", "GITLAB_API_HOST", "GLAB_API_PROTOCOL", "API_PROTOCOL"):
        monkeypatch.delenv(name, raising=False)
    real_run = subprocess.run
    endpoints: list[str] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if command[0] != "glab":
            return real_run(command, check=kwargs.pop("check", False), **kwargs)
        assert "--hostname" not in command
        assert kwargs["env"]["GITLAB_HOST"] == authority
        assert kwargs["env"]["GLAB_API_PROTOCOL"] == "http"
        if command[1:3] == ["ci", "list"]:
            assert command[command.index("-R") + 1] == f"http://{authority}/owner/repo"
            payload: object = []
        else:
            endpoint = command[2]
            endpoints.append(endpoint)
            if endpoint.endswith("?per_page=100"):
                payload = [{"id": 1, "active": True, "ref": "main"}]
            elif "/repository/branches/" in endpoint:
                payload = {"commit": {"id": "head"}}
            else:
                payload = {"last_pipeline": {"id": 23, "status": "failed", "ref": "main"}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", run)
    if mode == "single":
        assert _invoke_alert_cli(repo, forge="gitlab") == 0
    else:
        assert _invoke_loop_alerts(repo, notes, "gitlab") == 1
    assert "projects/owner%2Frepo/pipeline_schedules?per_page=100" in endpoints
    assert "projects/owner%2Frepo/pipeline_schedules/1" in endpoints
    saved = _saved_awis_by_heading(notes)
    assert list(saved) == ["# パイプライン23失敗"]
    assert f"http://{authority}/owner/repo" in next(iter(saved.values()))


@pytest.mark.parametrize("explicit_target", [False, True], ids=["cwd", "target-worktree"])
def test_check_alerts_cli_submits_and_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], explicit_target: bool
) -> None:
    """単発CLIは対象を解決し、CI失敗を保存して終了する。"""
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path)
    monkeypatch.chdir(repo if not explicit_target else tmp_path)
    calls: list[list[str]] = []
    _forge_responses(
        monkeypatch,
        lambda _command: [
            {"workflowName": "CI", "event": "push", "status": "completed", "conclusion": "failure", "databaseId": 21}
        ],
        calls,
    )
    assert _invoke_alert_cli(repo if explicit_target else None) == 0
    output = capsys.readouterr()
    assert "成功: CI失敗監視: 対象=github.com/owner/repo AWI投入=1件" in output.out
    assert output.err == ""
    assert len(calls) == 1 and calls[0][:3] == ["gh", "run", "list"]
    content = next(iter(_saved_awis_by_heading(notes).values()))
    assert "source: alert-monitor" in content and "alert_keys: github-run:21" in content


def test_check_alerts_cli_and_loop_deduplicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """全保存状態を除外し、別対象のキーを残して、単発と常駐の反復で二重保存しない。"""
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path)
    runs = []
    for number, state in enumerate(WI_STATES, start=1):
        directory = notes / state
        directory.mkdir(exist_ok=True)
        (directory / f"saved-{number}.md").write_text(
            f"---\ntarget_repo: github.com/owner/repo\ntype: awi\nalert_keys: github-run:{number}\n---\n\n# 保存済み\n",
            encoding="utf-8",
        )
        runs.append({"workflowName": f"CI-{number}", "status": "completed", "conclusion": "failure", "databaseId": number})
    (notes / "adopted" / "other.md").write_text(
        "---\ntarget_repo: github.com/other/repo\ntype: awi\nalert_keys: github-run:99\n---\n\n# 別対象\n", encoding="utf-8"
    )
    runs.append({"workflowName": "新規CI", "status": "completed", "conclusion": "failure", "databaseId": 99})
    _forge_responses(monkeypatch, lambda _command: runs, [])
    assert _invoke_alert_cli(repo) == 0
    assert "AWI投入=1件" in capsys.readouterr().out
    assert _invoke_alert_cli(repo) == 0
    assert "AWI投入=0件" in capsys.readouterr().out
    assert _invoke_loop_alerts(repo, notes, "github") == 0
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == {
        *(f"github-run:{number}" for number in range(1, len(WI_STATES) + 1)),
        "github-run:99",
    }


@pytest.mark.parametrize("case", ["github-push", "github-schedule", "gitlab-normal", "gitlab-schedule", "gitlab-disabled"])
def test_check_alerts_cli_matches_loop_by_forge(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, case: str) -> None:
    """各収集種別で、単発と常駐が同じ本文・出所・キーを保存する。"""
    forge = "github" if case.startswith("github") else "gitlab"

    def response(command: list[str]) -> object:
        if forge == "github":
            return [
                {
                    "workflowName": "CI",
                    "event": case.split("-")[1],
                    "status": "completed",
                    "conclusion": "failure",
                    "databaseId": 21,
                }
            ]
        if command[1:3] == ["ci", "list"]:
            return [{"id": 22, "status": "failed"}] if case == "gitlab-normal" else []
        if command[-1].endswith("?per_page=100"):
            return [{"id": 1, "ref": "main", "active": case == "gitlab-schedule"}]
        return {"last_pipeline": {"id": 23, "status": "failed", "ref": "main"}}

    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path / "single", forge=forge)
    _forge_responses(monkeypatch, response, [])
    assert _invoke_alert_cli(repo) == 0
    single = _saved_awis_by_heading(notes)
    # 同じ入力を別保存領域へ渡し、重複除外で本文比較が省かれないようにする。
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path / "loop", forge=forge)
    assert _invoke_loop_alerts(repo, notes, forge) == (0 if case == "gitlab-disabled" else 1)
    loop = _saved_awis_by_heading(notes)

    # 保存日時は呼び出しごとに異なるため除いて比べ、要求本文・source・keysが単発と常駐で一致することを確かめる。
    def normalize(text):
        return "\n".join(line for line in text.splitlines() if not line.startswith("created_at:"))

    assert {heading: normalize(text) for heading, text in single.items()} == {
        heading: normalize(text) for heading, text in loop.items()
    }
    assert len(single) == (0 if case == "gitlab-disabled" else 1)


@pytest.mark.parametrize("case", ["empty", "github-unavailable", "gitlab-partial", "branch-unresolved", "save-failure"])
def test_check_alerts_cli_reports_collection_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """正常0件と取得不能を区別し、部分成功の実件数と保存失敗を報告する。"""
    forge = "gitlab" if case == "gitlab-partial" else "github"
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path, forge=forge, branch=case != "branch-unresolved")

    def response(command: list[str]) -> object:
        if case == "github-unavailable" or (case == "gitlab-partial" and command[1:3] == ["ci", "list"]):
            return FileNotFoundError(command[0])
        if case == "save-failure":
            return [{"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]
        if case == "gitlab-partial":
            if command[-1].endswith("?per_page=100"):
                return [{"id": 1, "active": True, "ref": "main"}]
            return {"last_pipeline": {"id": 23, "status": "failed", "ref": "main"}}
        return []

    _forge_responses(monkeypatch, response, [])
    if case == "save-failure":

        def failed_pull(*_args: object, **_kwargs: object) -> None:
            raise subprocess.CalledProcessError(1, ["git", "pull"], stderr="保存先同期失敗")

        monkeypatch.setattr(_wi_sync, "pull", failed_pull)
    assert _invoke_alert_cli(repo, forge=forge) == (0 if case == "empty" else 1)
    output = capsys.readouterr()
    if case == "empty":
        assert "成功: CI失敗監視:" in output.out and "AWI投入=0件" in output.out
        assert output.err == ""
    else:
        assert "成功: CI失敗監視:" not in output.out
        if case == "save-failure":
            assert "Git操作に失敗した" in output.err
            assert "git" in output.err and "pull" in output.err
        else:
            assert f"AWI投入={1 if case == 'gitlab-partial' else 0}件" in output.err
            assert "次の操作:" in output.err
            assert ("ブランチを解決できません" if case == "branch-unresolved" else "取得に失敗しました") in output.err
    assert len(_saved_awis_by_heading(notes)) == (1 if case == "gitlab-partial" else 0)


@pytest.mark.parametrize("target", ["https://github.com/owner/repo", "missing", "not-a-repo"])
def test_check_alerts_cli_rejects_invalid_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    """ローカル対象の不正は収集せず終了コード2となる。"""
    _repo, notes = _prepare_alert_cli(monkeypatch, tmp_path)
    if target == "not-a-repo":
        (tmp_path / target).mkdir()
    monkeypatch.chdir(tmp_path)
    assert _invoke_alert_cli(pathlib.Path(target)) == 2
    assert "失敗:" in capsys.readouterr().err
    assert not _saved_awis_by_heading(notes)


@pytest.mark.parametrize("collector", ["github", "gitlab", "schedule"])
@pytest.mark.parametrize("head", ["new", "old", None])
def test_collectors_report_stale_run_context(collector: str, head: str | None) -> None:
    """3収集とも実行事実とHEAD比較を渡し、後続実行が無い場合も完成を判断できる。"""
    # GitLab Pipelines API / GitHub branches APIの公開フィールドを使い、古い失敗実行だけが残る応答を検証する。
    created = "2019-01-01T21:37:15+09:00"
    pipeline = {"id": 24, "status": "failed", "ref": "topic/ref", "sha": "old", "created_at": created}

    def head_fn(_repo: str, branch: str) -> str:
        assert branch == "topic/ref"
        if head is None:
            raise alerts.AlertCollectError("branch APIへ到達不能")
        return head

    if collector == "github":
        repository = "github.com/group/repo"
        run = {
            "databaseId": 24,
            "workflowName": "CI",
            "status": "completed",
            "conclusion": "failure",
            "headSha": "old",
            "createdAt": created,
        }
        result = alerts.collect_github_ci_failures(repository, "topic/ref", run_list_fn=lambda *_args: [run], head_fn=head_fn)
    elif collector == "gitlab":
        repository = "gitlab.example.org/group/repo"
        result = alerts.collect_gitlab_ci_failures(
            repository, "topic/ref", ci_list_fn=lambda *_args: [pipeline], head_fn=head_fn
        )
    else:
        repository = "gitlab.example.org/group/repo"

        def api(_host: str, endpoint: str) -> object:
            if "/repository/branches/" in endpoint:
                assert endpoint.endswith("topic%2Fref")
                return {"commit": {"id": head_fn(repository, "topic/ref")}}
            if endpoint.endswith("?per_page=100"):
                return [{"id": 5, "active": True, "ref": "different-default"}]
            return {"last_pipeline": pipeline}

        result = alerts.collect_gitlab_schedule_failures("gitlab.example.org", "group/repo", api_fn=api)
    assert len(result) == 1
    alert = result[0]
    assert created in alert.body and "対象コミット: old" in alert.body
    if head is None:
        assert "取得不能（branch APIへ到達不能）" in alert.body
        assert "判定不能" in alert.body
    else:
        assert f"ブランチHEAD: {head}" in alert.body
        assert f"HEADとの比較: {'一致' if head == 'old' else '不一致'}" in alert.body
    if head != "old":
        assert "CI定義の有無と起動条件" in alert.body
        assert "原因がブランチHEADに残るか" in alert.body
        assert "追加の変更や後続の実行を要しない" in alert.completion
    forge = "github" if collector == "github" else "gitlab"
    commands = forge_cli.failure_log_commands(forge, repository, "24", prefer_environment=True)
    assert all(shlex.join(command) in alert.body for command in commands)


def test_successful_collection_does_not_fetch_head() -> None:
    """失敗候補が無い通常の監視回はHEAD APIを呼ばない。"""

    def unexpected_head(*_args: object) -> str:
        pytest.fail("失敗候補の無い回にHEADを取得した")

    assert not alerts.collect_github_ci_failures("group/repo", "main", run_list_fn=lambda *_args: [], head_fn=unexpected_head)
    assert not alerts.collect_gitlab_ci_failures("group/repo", "main", ci_list_fn=lambda *_args: [], head_fn=unexpected_head)


@pytest.mark.usefixtures("api_branch_heads")
@pytest.mark.parametrize("variable", ["GITLAB_HOST", "GITLAB_URI", "GL_HOST"])
def test_alerts_preserve_public_endpoint_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, variable: str
) -> None:
    """通知のブランチ・スケジュール・HEAD取得は公開環境指定を優先し、親環境を保つ。"""
    for name in ("GITLAB_HOST", "GITLAB_URI", "GL_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "api.example.net:9443")
    parent_environment = os.environ.copy()
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert "--hostname" not in command
        assert kwargs["env"]["GITLAB_HOST"] == "api.example.net:9443"
        calls.append(command)
        pipeline = {
            "id": 24,
            "status": "failed",
            "ref": "release/topic",
            "sha": "old",
            "created_at": "2019-01-01T21:37:15+09:00",
        }
        if command[1:3] == ["ci", "list"]:
            assert command[command.index("-R") + 1] == "https://api.example.net:9443/group/sub/repo"
            payload: object = [pipeline]
        elif "repository/branches/" in command[2]:
            assert command[2].endswith("release%2Ftopic")
            payload = {"commit": {"id": "new"}}
        elif command[2].endswith("?per_page=100"):
            payload = [{"id": 1, "active": True, "ref": "release/topic"}]
        else:
            payload = {"last_pipeline": pipeline}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", run)
    result = alerts.collect_new_alerts("git.example.org/group/sub/repo", "release/topic", tmp_path, forge="gitlab")
    assert len(result) == 1
    assert "api.example.net:9443/group/sub/repo" in result[0].body
    assert "不一致" in result[0].body
    assert len(calls) == 5
    assert os.environ == parent_environment


@pytest.mark.usefixtures("api_branch_heads")
@pytest.mark.parametrize("forge", ["github", "gitlab-branch", "gitlab-schedule"])
def test_collectors_report_branch_head_context(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, forge: str) -> None:
    """公開収集関数collect_new_alertsが実行日時・SHA・リモートHEADを本文と完成条件へ反映する。"""
    created = "2019-01-01T21:37:15+09:00"
    pipeline = {"id": 24, "status": "failed", "ref": "main", "sha": "old", "created_at": created}
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[:3] == ["gh", "run", "list"]:
            payload: object = [
                {
                    "databaseId": 24,
                    "workflowName": "CI",
                    "status": "completed",
                    "conclusion": "failure",
                    "headSha": "old",
                    "createdAt": created,
                }
            ]
        elif command[:3] == ["glab", "ci", "list"]:
            payload = [pipeline] if forge == "gitlab-branch" else []
        elif any("/branches/" in arg for arg in command):
            payload = {"commit": {"id": "new", "sha": "new"}}
        elif any("pipeline_schedules?" in arg for arg in command):
            payload = [{"id": 1, "active": True, "ref": "main"}] if forge == "gitlab-schedule" else []
        else:
            payload = {"last_pipeline": pipeline}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", run)
    resolved = "github" if forge == "github" else "gitlab"
    repository = f"{resolved}.example.org/group/repo"
    result = alerts.collect_new_alerts(repository, "main", tmp_path, forge=resolved)
    assert len(result) == 1
    assert created in result[0].body
    assert "対象コミット: old" in result[0].body
    assert "ブランチHEAD: new" in result[0].body
    assert "HEADとの比較: 不一致" in result[0].body
    assert "追加の変更や後続の実行を要しない" in result[0].completion
    assert sum(any("/branches/" in arg for arg in command) for command in calls) == 1


@pytest.mark.usefixtures("api_branch_heads")
@pytest.mark.parametrize("forge", ["github", "gitlab"])
def test_alert_log_guidance_uses_shared_commands(monkeypatch: pytest.MonkeyPatch, forge: str) -> None:
    """通知のログ案内はwait_ciと同じ生成元を通り、Git外で対象を一意に指定できる。"""
    pipeline = {"id": 24, "status": "failed"}
    run_record = {"databaseId": 24, "workflowName": "CI", "status": "completed", "conclusion": "failure"}

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        payload: object = (
            {"commit": {"sha": "new", "id": "new"}} if command[1] == "api" else [run_record if forge == "github" else pipeline]
        )
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", run)
    repository = f"{forge}.example.org/group/repo"
    collector = alerts.collect_github_ci_failures if forge == "github" else alerts.collect_gitlab_ci_failures
    result = collector(repository, "main")
    assert forge_cli.failure_next_action(forge, repository, "24", prefer_environment=True) in result[0].body


@pytest.mark.usefixtures("api_branch_heads")
@pytest.mark.parametrize("selection", ["identifier", "ssh", "environment", "explicit", "override"])
def test_real_glab_resolves_configuration_through_ci_and_failure_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    selection: str,
) -> None:
    """実glabでbaseline・待機・通知・失敗ログを取得し、ホスト別API設定と認証を確かめる。"""
    # glab 1.122.0のconfig --helpにあるホスト別api_host/api_protocol/tokenと
    # https://docs.gitlab.com/api/pipelines/ 、jobs/ の応答を使う。
    runtime_environment = host_environ()
    executable = shutil.which("glab", path=runtime_environment["PATH"])
    if executable is None:
        pytest.skip("実glabの結合試験にはglabの導入が必要")
    restore_host_environment(monkeypatch)
    config = tmp_path / "glab-config"
    config.mkdir()
    monkeypatch.setenv("GLAB_CONFIG_DIR", str(config))
    for name in (
        "GITLAB_HOST",
        "GITLAB_URI",
        "GL_HOST",
        "GITLAB_API_HOST",
        "GLAB_API_PROTOCOL",
        "API_PROTOCOL",
        "GITLAB_TOKEN",
        "GITLAB_ACCESS_TOKEN",
        "OAUTH_TOKEN",
        "GITLAB_CI",
        "GLAB_ENABLE_CI_AUTOLOGIN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GLAB_CHECK_UPDATE", "false")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    requests: list[tuple[str, str | None]] = []
    phase = "baseline"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            requests.append((self.path, self.headers.get("PRIVATE-TOKEN")))
            pipeline = {"id": 1 if phase == "baseline" else 2, "status": "failed", "sha": "a" * 40}
            if "/jobs/9/trace" in self.path:
                data = b"test failure log\n"
            else:
                job = {"id": 9, "name": "test", "status": "failed", "allow_failure": False}
                payload: Any
                if "/repository/branches/" in self.path:
                    payload = {"commit": {"id": "a" * 40}}
                elif "/pipeline_schedules" in self.path:
                    payload = []
                elif self.path.endswith("/jobs/9"):
                    payload = job
                elif "/jobs" in self.path:
                    payload = [job]
                elif self.path.endswith("/pipelines/2"):
                    payload = pipeline
                else:
                    payload = [pipeline]
                data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        authority = f"127.0.0.1:{server.server_port}"
        host_key = authority if selection == "explicit" else "configured.example.org"
        api_host = "unused.example.org:1" if selection == "explicit" else authority
        api_protocol = "https" if selection == "explicit" else "http"
        (config / "config.yml").write_text(
            f"hosts:\n  {host_key}:\n    api_host: {api_host}\n    api_protocol: {api_protocol}\n    token: test-host-token\n",
            encoding="utf-8",
        )
        (config / "config.yml").chmod(0o600)
        repository = {
            "identifier": f"{host_key}/group/repo",
            "ssh": f"ssh://git@{host_key}:2222/group/repo.git",
            "environment": "group/repo",
            "explicit": f"http://{authority}/group/repo",
            "override": "http://unused.example.org:1/group/repo",
        }[selection]
        if selection in {"environment", "override"}:
            monkeypatch.setenv("GL_HOST", host_key)
        prefer_environment = selection == "override"
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            options = ["--repo", repository, "--forge=gitlab", "--ref=main", "--source-ref=HEAD", "--registration-grace=0"]
            baseline = tmp_path / "baseline.json"
            monkeypatch.setattr(wait_ci, "_resolve_sha", lambda *_args: "a" * 40)
            monkeypatch.setattr(wait_ci, "_install_signal_handlers", lambda: None)
            if not prefer_environment:
                assert wait_ci.main(["--write-baseline", str(baseline), *options]) == 0
                phase = "failure"
                assert wait_ci.main(["--baseline", str(baseline), *options]) == 1
                assert "ci trace" in capsys.readouterr().err
            phase = "failure"
            assert alerts.collect_gitlab_ci_failures(repository, "main")
            command = forge_cli.failure_log_commands("gitlab", repository, "2", prefer_environment=prefer_environment)[0]
            result = subprocess.run(command, env=os.environ.copy(), capture_output=True, timeout=10, check=False)
            assert result.returncode == 0, result.stderr
            assert [job["id"] for job in json.loads(result.stdout)["jobs"]] == [9]
            command = forge_cli.failure_log_commands(
                "gitlab", repository, "9", job=True, prefer_environment=prefer_environment
            )[0]
            result = subprocess.run(command, env=os.environ.copy(), capture_output=True, timeout=10, check=False)
            assert result.returncode == 0, result.stderr
            assert b"test failure log\n" in result.stdout
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()
    assert requests and all(token == "test-host-token" for _path, token in requests)
    assert any("/jobs/9/trace" in path for path, _token in requests)
